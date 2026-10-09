"""Tests for _token_from_context (OSM token from HTTP headers)."""

from types import SimpleNamespace

import server


def _ctx(headers: dict):
    request = SimpleNamespace(headers=headers)
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def test_token_from_x_api_key():
    assert server._token_from_context(_ctx({"x-api-key": "abc"})) == "abc"


def test_token_from_legacy_x_osm_token():
    assert server._token_from_context(_ctx({"x-osm-token": "legacy"})) == "legacy"


def test_x_api_key_wins_over_legacy_header():
    ctx = _ctx({"x-api-key": "new", "x-osm-token": "legacy"})
    assert server._token_from_context(ctx) == "new"


def test_no_header_returns_none():
    assert server._token_from_context(_ctx({})) is None


def test_no_request_returns_none():
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=None))
    assert server._token_from_context(ctx) is None
