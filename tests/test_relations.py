"""Tests for relation editing: member operations, relation XML, tools in dry run."""

import xml.etree.ElementTree as ET

import pytest

import server
from server import Member, MemberAdd, MemberMatch


MEMBERS = [
    {"type": "way", "ref": 10, "role": "outer"},
    {"type": "way", "ref": 11, "role": "inner"},
    {"type": "node", "ref": 20, "role": "label"},
]

RELATION = {
    "type": "relation",
    "id": 99,
    "version": 3,
    "changeset": 1,
    "user": "someone",
    "timestamp": "2026-01-01T00:00:00Z",
    "members": MEMBERS,
    "tags": {"type": "multipolygon", "landuse": "grass"},
}


# ---------------------------------------------------------------------------
# _apply_member_ops
# ---------------------------------------------------------------------------

def test_remove_member_by_type_and_ref():
    new, changes = server._apply_member_ops(
        MEMBERS, remove=[MemberMatch(type="way", ref=11)]
    )
    assert new == [MEMBERS[0], MEMBERS[2]]
    assert changes == ["-   way/11 (inner)"]


def test_remove_with_role_only_matches_that_role():
    members = [
        {"type": "way", "ref": 10, "role": "forward"},
        {"type": "way", "ref": 10, "role": "backward"},
    ]
    new, _ = server._apply_member_ops(
        members, remove=[MemberMatch(type="way", ref=10, role="backward")]
    )
    assert new == [members[0]]


def test_remove_missing_member_raises():
    with pytest.raises(ValueError, match="way/404"):
        server._apply_member_ops(MEMBERS, remove=[MemberMatch(type="way", ref=404)])


def test_set_role_changes_existing_member():
    new, changes = server._apply_member_ops(
        MEMBERS, set_roles=[Member(type="way", ref=11, role="outer")]
    )
    assert new[1] == {"type": "way", "ref": 11, "role": "outer"}
    assert changes == ["~   way/11 (inner -> outer)"]
    # input list is not mutated
    assert MEMBERS[1]["role"] == "inner"


def test_set_role_missing_member_raises():
    with pytest.raises(ValueError, match="node/1"):
        server._apply_member_ops(MEMBERS, set_roles=[Member(type="node", ref=1)])


def test_add_appends_by_default():
    new, changes = server._apply_member_ops(
        MEMBERS, add=[MemberAdd(type="way", ref=12, role="inner")]
    )
    assert new[-1] == {"type": "way", "ref": 12, "role": "inner"}
    assert changes == ["+   way/12 (inner) at position 3"]


def test_add_at_position():
    new, changes = server._apply_member_ops(
        MEMBERS, add=[MemberAdd(type="node", ref=21, role="admin_centre", position=0)]
    )
    assert new[0] == {"type": "node", "ref": 21, "role": "admin_centre"}
    assert len(new) == 4
    assert changes == ["+   node/21 (admin_centre) at position 0"]


def test_add_position_out_of_range_raises():
    with pytest.raises(ValueError, match="position"):
        server._apply_member_ops(MEMBERS, add=[MemberAdd(type="way", ref=1, position=9)])


def test_ops_order_is_remove_then_set_roles_then_add():
    new, _ = server._apply_member_ops(
        MEMBERS,
        remove=[MemberMatch(type="node", ref=20)],
        set_roles=[Member(type="way", ref=11, role="outer")],
        add=[MemberAdd(type="way", ref=11, role="inner", position=2)],
    )
    assert new == [
        {"type": "way", "ref": 10, "role": "outer"},
        {"type": "way", "ref": 11, "role": "outer"},
        {"type": "way", "ref": 11, "role": "inner"},
    ]


# ---------------------------------------------------------------------------
# XML builders
# ---------------------------------------------------------------------------

def test_build_element_xml_uses_member_override():
    new_members = [{"type": "way", "ref": 10, "role": "outer"}]
    xml = server._build_element_xml(RELATION, RELATION["tags"], 5, members=new_members)
    rel = ET.fromstring(xml).find("relation")
    assert rel.get("version") == "3"
    assert [(m.get("type"), m.get("ref"), m.get("role")) for m in rel.findall("member")] == [
        ("way", "10", "outer")
    ]


def test_build_element_xml_keeps_members_by_default():
    xml = server._build_element_xml(RELATION, RELATION["tags"], 5)
    assert len(ET.fromstring(xml).find("relation").findall("member")) == 3


def test_build_new_relation_xml():
    members = [Member(type="way", ref=10, role="outer")]
    xml = server._build_new_relation_xml(members, {"type": "multipolygon"}, 8)
    rel = ET.fromstring(xml).find("relation")
    assert rel.get("changeset") == "8"
    assert rel.get("id") is None
    assert rel.find("member").get("role") == "outer"
    assert rel.find("tag").get("k") == "type"


# ---------------------------------------------------------------------------
# Tools (dry run, no network)
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_fetch(monkeypatch):
    monkeypatch.setattr(server, "_fetch_element", lambda t, i: RELATION)


def test_get_osm_element_lists_relation_members(fake_fetch):
    out = server.get_osm_element("relation", 99)
    assert "Members (3):" in out
    assert "[0] way/10 (outer)" in out
    assert "[2] node/20 (label)" in out


def test_get_osm_element_without_tags(monkeypatch):
    node = {**RELATION, "type": "node", "id": 1, "lat": 1.0, "lon": 2.0}
    del node["tags"], node["members"]
    monkeypatch.setattr(server, "_fetch_element", lambda t, i: node)
    assert "Tags:" in server.get_osm_element("node", 1)


def test_update_members_dry_run(fake_fetch):
    out = server.update_osm_relation_members(
        99, remove=[MemberMatch(type="node", ref=20)], dry_run=True
    )
    assert "DRY RUN" in out
    assert "-   node/20 (label)" in out


def test_update_members_without_ops_is_noop(fake_fetch):
    assert "No changes" in server.update_osm_relation_members(99, dry_run=True)


def test_create_relation_requires_type_tag():
    with pytest.raises(ValueError, match="type"):
        server.create_osm_relation(
            members=[Member(type="way", ref=1)], tags={"name": "x"}, dry_run=True
        )


def test_create_relation_requires_members():
    with pytest.raises(ValueError, match="member"):
        server.create_osm_relation(members=[], tags={"type": "site"}, dry_run=True)


def test_create_relation_dry_run():
    out = server.create_osm_relation(
        members=[Member(type="way", ref=1, role="outer")],
        tags={"type": "multipolygon", "landuse": "grass"},
        dry_run=True,
    )
    assert "DRY RUN" in out
    assert "way/1 (outer)" in out
    assert "<relation" in out
