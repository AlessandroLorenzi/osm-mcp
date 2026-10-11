"""Tests for delete_osm_node (parent check, XML, dry run)."""

import xml.etree.ElementTree as ET

import pytest

import server


NODE = {
    "type": "node",
    "id": 123,
    "version": 4,
    "changeset": 1,
    "user": "someone",
    "timestamp": "2026-01-01T00:00:00Z",
    "lat": 45.5,
    "lon": 8.8,
    "tags": {"shop": "bakery", "name": "Forno Rossi"},
}


@pytest.fixture
def fake_fetch(monkeypatch):
    monkeypatch.setattr(server, "_fetch_element", lambda t, i: NODE)


def test_build_delete_node_xml():
    root = ET.fromstring(server._build_delete_node_xml(NODE, changeset_id=42))
    node = root.find("node")
    assert node.get("id") == "123"
    assert node.get("version") == "4"
    assert node.get("changeset") == "42"
    assert node.get("lat") == "45.5"
    assert node.get("lon") == "8.8"
    assert node.findall("tag") == []


def test_delete_refuses_node_used_by_way_or_relation(fake_fetch, monkeypatch):
    monkeypatch.setattr(
        server, "_fetch_parents",
        lambda t, i: [{"type": "way", "id": 7}, {"type": "relation", "id": 8}],
    )
    with pytest.raises(ValueError, match="way/7, relation/8"):
        server.delete_osm_node(123, dry_run=True)


def test_delete_dry_run(fake_fetch, monkeypatch):
    monkeypatch.setattr(server, "_fetch_parents", lambda t, i: [])
    out = server.delete_osm_node(123, dry_run=True)
    assert "DRY RUN" in out
    assert "Delete node/123 (v4)" in out
    assert "name = Forno Rossi" in out
