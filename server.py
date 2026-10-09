#!/usr/bin/env python3
"""FastMCP server exposing OSM tools for Claude Code."""

import os
import json
import xml.etree.ElementTree as ET
from datetime import date
from urllib.request import urlopen, Request
from urllib.parse import urlencode

from fastmcp import FastMCP, Context

mcp = FastMCP(
    "OSM Tools",
    instructions="""
You are an assistant that updates OpenStreetMap tags from images or text (flyers, menus, opening hours).

## Workflow

### 0. Find the element (if the ID is unknown)

Call `search_osm` with the place name and optionally a city.
You can derive the city from GPS EXIF data or a visible address in the image.

If the element does not exist on OSM, use `create_osm_node` (see below) and skip to step 5.

### 1. Read current tags

Call `get_osm_element` with the type ("node", "way", or "relation") and numeric ID.

### 2. Extract data from the source

Analyse the image or text and map values to OSM tags.
Do not invent values: only use what is explicitly visible in the source.

Common tags:
- `name` — place name
- `opening_hours` — hours (see format below)
- `phone` — international format (`+39 02 1234567`)
- `website` — URL
- `cuisine` — type of cuisine (`italian`, `pizza`, `seafood`, ...)
- `addr:street`, `addr:housenumber`, `addr:city`, `addr:postcode`
- `contact:email`, `contact:facebook`, `contact:instagram`, `contact:twitter`
- `amenity` / `shop` / `tourism` — type of business

### 3. Show the diff and ask for confirmation

Before calling `update_osm_tags`, always show what will change and ask for explicit confirmation.

### 4. Apply the changes

Call `update_osm_tags` with the element type, ID, tags to set, and optionally keys to remove.
Include a short `changeset_comment` describing the change.

### 5. Post the OSM link

After the update, show the link returned by the tool.

## Creating a new node

When the element does not exist on OSM:
1. Show the proposed tags and ask for confirmation
2. Call `create_osm_node` with lat, lon, and tags

## Creating a new way (area)

For areas (parking lots, buildings, flower beds, ...) use `create_osm_way`:
1. Show the vertices ([lat, lon] list) and the proposed tags, then ask for confirmation
2. Call `create_osm_way` with the ordered vertex list and tags (do not repeat the
   first vertex at the end: the ring is closed automatically when `closed=True`)

## opening_hours format

```
Mo-Fr 09:00-18:00
Mo 10:00-17:00; Tu off; We-Su 10:00-23:00
Mo-Su 00:00-24:00
```

- Days: `Mo Tu We Th Fr Sa Su`
- Closed day: `off` (never "closed" or "chiuso")
- Consecutive days with same hours: `Th-Su 10:00-23:00`
- Multiple rules separated by `;`

## Quality rules

- You may create new geometries (nodes and simple closed ways/areas). Never modify
  the geometry of **existing** elements, and never touch relations
- Use standard tags from the OSM wiki
- Do not add `source=*` unless the user asks
- For complex `opening_hours`, suggest validating at https://openingh.openstreetmap.de
- Refuse to add unsupported or invented tags
- Refuse to analyse screenshots from Google Maps
""",
)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
API_URL = os.environ.get("OSM_API_URL", "https://api.openstreetmap.org")
CREATED_BY = "https://github.com/AlessandroLorenzi/osm-mcp"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# X-API-Key is one of the header names claude.ai connectors accept; X-OSM-Token
# stays for clients configured before the switch.
TOKEN_HEADERS = ("x-api-key", "x-osm-token")


def _token_from_context(ctx: Context) -> str | None:
    try:
        headers = ctx.request_context.request.headers
        for name in TOKEN_HEADERS:
            osm_token = headers.get(name, "")
            if osm_token:
                return osm_token
    except (AttributeError, TypeError):
        pass
    return None


def _auth_headers(token: str | None = None) -> dict:
    token = token or os.environ.get("OSM_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}
    user = os.environ.get("OSM_USER")
    pwd = os.environ.get("OSM_PASS")
    if user and pwd:
        import base64
        creds = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        return {"Authorization": f"Basic {creds}"}
    raise RuntimeError("Provide the OSM token via X-API-Key header or OSM_TOKEN env var")


def _fetch_element(elem_type: str, elem_id: int) -> dict:
    url = f"{API_URL}/api/0.6/{elem_type}/{elem_id}.json"
    with urlopen(url) as r:
        return json.loads(r.read())["elements"][0]


def _create_changeset(comment: str, headers: dict) -> int:
    root = ET.Element("osm")
    cs = ET.SubElement(root, "changeset")
    for k, v in (("created_by", CREATED_BY), ("comment", comment)):
        t = ET.SubElement(cs, "tag")
        t.set("k", k)
        t.set("v", v)
    xml = '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")
    req = Request(
        f"{API_URL}/api/0.6/changeset/create",
        data=xml.encode(),
        headers={**headers, "Content-Type": "text/xml"},
        method="PUT",
    )
    with urlopen(req) as r:
        return int(r.read().strip())


def _close_changeset(changeset_id: int, headers: dict):
    req = Request(
        f"{API_URL}/api/0.6/changeset/{changeset_id}/close",
        data=b"",
        headers=headers,
        method="PUT",
    )
    with urlopen(req) as r:
        r.read()


def _build_element_xml(element: dict, new_tags: dict, changeset_id: int) -> str:
    elem_type = element["type"]
    root = ET.Element("osm")
    el = ET.SubElement(root, elem_type)
    el.set("id", str(element["id"]))
    el.set("version", str(element["version"]))
    el.set("changeset", str(changeset_id))

    if elem_type == "node":
        el.set("lat", str(element["lat"]))
        el.set("lon", str(element["lon"]))
    if elem_type == "way":
        for ref in element.get("nodes", []):
            ET.SubElement(el, "nd").set("ref", str(ref))
    elif elem_type == "relation":
        for member in element.get("members", []):
            m = ET.SubElement(el, "member")
            m.set("type", member["type"])
            m.set("ref", str(member["ref"]))
            m.set("role", member.get("role", ""))

    for k, v in new_tags.items():
        tag = ET.SubElement(el, "tag")
        tag.set("k", k)
        tag.set("v", v)

    return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")


def _build_osmchange_xml(
    coords: list, tags: dict, changeset_id: int, closed: bool
) -> str:
    root = ET.Element("osmChange")
    root.set("version", "0.6")
    root.set("generator", CREATED_BY)
    create = ET.SubElement(root, "create")

    for i, (lat, lon) in enumerate(coords, start=1):
        node = ET.SubElement(create, "node")
        node.set("id", str(-i))
        node.set("lat", str(lat))
        node.set("lon", str(lon))
        node.set("changeset", str(changeset_id))

    way = ET.SubElement(create, "way")
    way.set("id", str(-(len(coords) + 1)))
    way.set("changeset", str(changeset_id))
    refs = list(range(-1, -(len(coords) + 1), -1))
    if closed:
        refs.append(-1)
    for ref in refs:
        ET.SubElement(way, "nd").set("ref", str(ref))
    for k, v in tags.items():
        t = ET.SubElement(way, "tag")
        t.set("k", k)
        t.set("v", v)

    return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")


def _parse_diff_result(xml_text: str) -> dict:
    mapping: dict = {}
    for el in ET.fromstring(xml_text):
        mapping.setdefault(el.tag, {})[int(el.get("old_id"))] = int(el.get("new_id"))
    return mapping


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_today_date() -> str:
    """Return today's date in ISO format (YYYY-MM-DD)."""
    return date.today().isoformat()


@mcp.tool()
def search_osm(
    query: str,
    city: str = "",
    elem_type: str = "",
) -> str:
    """Search OSM elements by name using Nominatim.

    Args:
        query: Search query, e.g. "Bar Centrale"
        city: Optional city to restrict the search, e.g. "Milano"
        elem_type: Optional element type filter: "node", "way", or "relation"
    """
    params: dict = {
        "q": f"{query}, {city}" if city else query,
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": 10,
    }
    if elem_type:
        params["osm_type"] = elem_type[0].upper()

    url = f"{NOMINATIM_URL}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "osm-fastmcp/1.0"})
    with urlopen(req) as r:
        results = json.loads(r.read())

    if not results:
        return "No results found."

    reverse_map = {"N": "node", "W": "way", "R": "relation"}
    lines = []
    for i, r in enumerate(results, 1):
        osm_type = r.get("osm_type", "?")[0].upper()
        osm_id = r.get("osm_id")
        display = r.get("display_name", "")
        full_type = reverse_map.get(osm_type, osm_type.lower())

        lines.append(f"[{i}] {full_type}/{osm_id}")
        lines.append(f"    {display}")

        tag_url = f"{API_URL}/api/0.6/{full_type}/{osm_id}.json"
        try:
            with urlopen(tag_url) as tr:
                tags = json.loads(tr.read())["elements"][0].get("tags", {})
            for k in ("name", "amenity", "shop", "tourism", "opening_hours", "phone", "website"):
                if k in tags:
                    lines.append(f"    {k} = {tags[k]}")
        except Exception:
            pass

    return "\n".join(lines)


@mcp.tool()
def get_osm_element(elem_type: str, elem_id: int) -> str:
    """Fetch and return the current tags of an OSM element. No authentication needed.

    Args:
        elem_type: "node", "way", or "relation"
        elem_id: Numeric OSM element ID
    """
    element = _fetch_element(elem_type.lower(), elem_id)
    lines = [
        f"{element['type']}/{element['id']} (v{element['version']}, changeset {element['changeset']})",
        f"Last edited by {element['user']} on {element['timestamp']}",
        "",
        "Tags:",
    ]
    for k, v in sorted(element["tags"].items()):
        lines.append(f"  {k} = {v}")
    return "\n".join(lines)


@mcp.tool()
def update_osm_tags(
    elem_type: str,
    elem_id: int,
    tags: dict[str, str],
    remove: list[str] | None = None,
    changeset_comment: str = "",
    dry_run: bool = False,
    ctx: Context = None,
) -> str:
    """Update tags on an OSM element. Requires OSM_TOKEN environment variable.

    Show the user the diff and get explicit confirmation BEFORE calling this tool.

    Args:
        elem_type: "node", "way", or "relation"
        elem_id: Numeric OSM element ID
        tags: Tags to add or update, e.g. {"opening_hours": "Mo-Fr 09:00-18:00"}
        remove: List of tag keys to remove, e.g. ["old_tag"]
        changeset_comment: Short description of the change
        dry_run: If True, compute and return the diff without uploading
    """
    element = _fetch_element(elem_type.lower(), elem_id)
    old_tags = dict(element["tags"])

    new_tags = {**old_tags, **tags}
    for k in (remove or []):
        new_tags.pop(k, None)

    all_keys = sorted(set(old_tags) | set(new_tags))
    diff_lines = ["--- current tags", "+++ proposed tags", ""]
    changed = False
    for k in all_keys:
        old_v = old_tags.get(k)
        new_v = new_tags.get(k)
        if old_v == new_v:
            diff_lines.append(f"    {k}={old_v}")
        elif old_v is None:
            diff_lines.append(f"+   {k}={new_v}")
            changed = True
        elif new_v is None:
            diff_lines.append(f"-   {k}={old_v}")
            changed = True
        else:
            diff_lines.append(f"-   {k}={old_v}")
            diff_lines.append(f"+   {k}={new_v}")
            changed = True

    if not changed:
        return "No changes — tags are already up to date."

    diff_output = "\n".join(diff_lines)

    if dry_run:
        return f"DRY RUN — no upload.\n\n{diff_output}"

    headers = _auth_headers(_token_from_context(ctx) if ctx else None)
    comment = changeset_comment or f"Tag update via {CREATED_BY}"
    changeset_id = _create_changeset(comment, headers)

    try:
        xml_body = _build_element_xml(element, new_tags, changeset_id)
        req = Request(
            f"{API_URL}/api/0.6/{elem_type.lower()}/{elem_id}",
            data=xml_body.encode(),
            headers={**headers, "Content-Type": "text/xml"},
            method="PUT",
        )
        with urlopen(req) as r:
            new_version = int(r.read().strip())
    finally:
        _close_changeset(changeset_id, headers)

    osm_url = f"https://www.openstreetmap.org/{elem_type.lower()}/{elem_id}"
    return (
        f"Updated {elem_type}/{elem_id} to version {new_version} "
        f"(changeset #{changeset_id})\n\n"
        f"{diff_output}\n\n"
        f"{osm_url}"
    )


@mcp.tool()
def create_osm_node(
    lat: float,
    lon: float,
    tags: dict[str, str],
    changeset_comment: str = "",
    dry_run: bool = False,
    ctx: Context = None,
) -> str:
    """Create a new OSM node at the given coordinates. Requires OSM_TOKEN environment variable.

    Show the user the proposed tags and get explicit confirmation BEFORE calling this tool.

    Args:
        lat: Latitude of the new node
        lon: Longitude of the new node
        tags: Tags for the new node, e.g. {"amenity": "cafe", "name": "Bar Roma"}
        changeset_comment: Short description of the change
        dry_run: If True, return the XML without uploading
    """
    tag_lines = "\n".join(f"  {k} = {v}" for k, v in tags.items())
    preview = f"New node at lat={lat}, lon={lon}\nTags:\n{tag_lines}"

    if dry_run:
        root = ET.Element("osm")
        node = ET.SubElement(root, "node")
        node.set("changeset", "0")
        node.set("lat", str(lat))
        node.set("lon", str(lon))
        for k, v in tags.items():
            t = ET.SubElement(node, "tag")
            t.set("k", k)
            t.set("v", v)
        xml = '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")
        return f"DRY RUN — no upload.\n\n{preview}\n\nXML:\n{xml}"

    headers = _auth_headers(_token_from_context(ctx) if ctx else None)
    comment = changeset_comment or f"Add node via {CREATED_BY}"
    changeset_id = _create_changeset(comment, headers)

    try:
        root = ET.Element("osm")
        node_el = ET.SubElement(root, "node")
        node_el.set("changeset", str(changeset_id))
        node_el.set("lat", str(lat))
        node_el.set("lon", str(lon))
        for k, v in tags.items():
            t = ET.SubElement(node_el, "tag")
            t.set("k", k)
            t.set("v", v)
        xml_body = '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")

        req = Request(
            f"{API_URL}/api/0.6/node/create",
            data=xml_body.encode(),
            headers={**headers, "Content-Type": "text/xml"},
            method="PUT",
        )
        with urlopen(req) as r:
            node_id = int(r.read().strip())
    finally:
        _close_changeset(changeset_id, headers)

    osm_url = f"https://www.openstreetmap.org/node/{node_id}"
    return (
        f"Created node/{node_id} (changeset #{changeset_id})\n\n"
        f"{preview}\n\n"
        f"{osm_url}"
    )


@mcp.tool()
def create_osm_way(
    coords: list[list[float]],
    tags: dict[str, str],
    closed: bool = True,
    changeset_comment: str = "",
    dry_run: bool = False,
    ctx: Context = None,
) -> str:
    """Create a new OSM way (polygon/area if closed) with its nodes in one atomic upload.

    Show the user the vertices and tags and get explicit confirmation BEFORE calling this tool.

    Args:
        coords: Ordered list of [lat, lon] vertices. Do NOT repeat the first
            vertex at the end: closing the ring is handled automatically.
        tags: Tags for the way, e.g. {"amenity": "parking", "parking": "surface"}
        closed: If True (default), create a closed way (area/polygon)
        changeset_comment: Short description of the change
        dry_run: If True, return a preview and the osmChange XML without uploading
    """
    vertices = [(float(lat), float(lon)) for lat, lon in coords]
    if closed and len(vertices) >= 2 and vertices[0] == vertices[-1]:
        vertices = vertices[:-1]

    if closed and len(set(vertices)) < 3:
        raise ValueError("A closed way needs at least 3 distinct vertices")
    if not closed and len(vertices) < 2:
        raise ValueError("An open way needs at least 2 vertices")

    tag_lines = "\n".join(f"  {k} = {v}" for k, v in tags.items())
    kind = "closed way (area)" if closed else "open way"
    preview = f"New {kind} with {len(vertices)} vertices\nTags:\n{tag_lines}"

    if dry_run:
        xml = _build_osmchange_xml(vertices, tags, changeset_id=0, closed=closed)
        return f"DRY RUN — no upload.\n\n{preview}\n\nXML:\n{xml}"

    headers = _auth_headers(_token_from_context(ctx) if ctx else None)
    comment = changeset_comment or f"Add way via {CREATED_BY}"
    changeset_id = _create_changeset(comment, headers)

    try:
        xml_body = _build_osmchange_xml(vertices, tags, changeset_id, closed)
        req = Request(
            f"{API_URL}/api/0.6/changeset/{changeset_id}/upload",
            data=xml_body.encode(),
            headers={**headers, "Content-Type": "text/xml"},
            method="POST",
        )
        with urlopen(req) as r:
            diff_result = r.read().decode()
        way_id = _parse_diff_result(diff_result)["way"][-(len(vertices) + 1)]
    finally:
        _close_changeset(changeset_id, headers)

    osm_url = f"https://www.openstreetmap.org/way/{way_id}"
    return (
        f"Created way/{way_id} (changeset #{changeset_id})\n\n"
        f"{preview}\n\n"
        f"{osm_url}"
    )


if __name__ == "__main__":
    mcp.run(show_banner=False)
