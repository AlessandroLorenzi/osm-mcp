"""Tests for create_osm_way (osmChange diff upload)."""

import xml.etree.ElementTree as ET

import pytest

import server


SQUARE = [
    [45.5980, 8.7990],
    [45.5981, 8.7995],
    [45.5979, 8.7996],
    [45.5978, 8.7991],
]
TAGS = {"amenity": "parking", "parking": "surface"}


# ---------------------------------------------------------------------------
# _build_osmchange_xml
# ---------------------------------------------------------------------------

def test_osmchange_closed_way_structure():
    xml = server._build_osmchange_xml(SQUARE, TAGS, changeset_id=42, closed=True)
    root = ET.fromstring(xml)
    assert root.tag == "osmChange"

    create = root.find("create")
    nodes = create.findall("node")
    assert len(nodes) == 4
    node_ids = [n.get("id") for n in nodes]
    assert node_ids == ["-1", "-2", "-3", "-4"]
    for n, (lat, lon) in zip(nodes, SQUARE):
        assert n.get("lat") == str(lat)
        assert n.get("lon") == str(lon)
        assert n.get("changeset") == "42"

    ways = create.findall("way")
    assert len(ways) == 1
    way = ways[0]
    assert way.get("id") == "-5"
    assert way.get("changeset") == "42"

    refs = [nd.get("ref") for nd in way.findall("nd")]
    # closed: last ref repeats the first node, no duplicate node created
    assert refs == ["-1", "-2", "-3", "-4", "-1"]

    tags = {t.get("k"): t.get("v") for t in way.findall("tag")}
    assert tags == TAGS


def test_osmchange_open_way_does_not_repeat_first_ref():
    xml = server._build_osmchange_xml(SQUARE[:2], {}, changeset_id=7, closed=False)
    way = ET.fromstring(xml).find("create").find("way")
    refs = [nd.get("ref") for nd in way.findall("nd")]
    assert refs == ["-1", "-2"]


# ---------------------------------------------------------------------------
# _parse_diff_result
# ---------------------------------------------------------------------------

def test_parse_diff_result_maps_old_to_new_ids():
    diff = """<?xml version="1.0" encoding="UTF-8"?>
    <diffResult version="0.6">
      <node old_id="-1" new_id="1001" new_version="1"/>
      <node old_id="-2" new_id="1002" new_version="1"/>
      <way old_id="-3" new_id="2001" new_version="1"/>
    </diffResult>"""
    mapping = server._parse_diff_result(diff)
    assert mapping["node"][-1] == 1001
    assert mapping["node"][-2] == 1002
    assert mapping["way"][-3] == 2001


# ---------------------------------------------------------------------------
# create_osm_way tool (validation + dry run, no network)
# ---------------------------------------------------------------------------

def _tool(**kwargs):
    return server.create_osm_way(**kwargs)


def test_closed_way_requires_three_distinct_vertices():
    with pytest.raises(ValueError, match="3 distinct"):
        _tool(coords=[[45.0, 9.0], [45.1, 9.1]], tags=TAGS)


def test_open_way_requires_two_vertices():
    with pytest.raises(ValueError, match="2"):
        _tool(coords=[[45.0, 9.0]], tags=TAGS, closed=False)


def test_closed_way_tolerates_explicitly_closed_input():
    # first == last: the duplicate is dropped instead of creating two nodes
    coords = SQUARE + [SQUARE[0]]
    out = _tool(coords=coords, tags=TAGS, dry_run=True)
    assert out.count("<node") == 4


def test_dry_run_returns_preview_and_xml_without_upload():
    out = _tool(coords=SQUARE, tags=TAGS, dry_run=True)
    assert "DRY RUN" in out
    assert "4 vertices" in out
    assert "amenity = parking" in out
    assert "<osmChange" in out
