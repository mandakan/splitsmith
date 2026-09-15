"""The export-preset settings routes, local and hosted.

Local mode: one operator, the JSON file under ``SPLITSMITH_HOME`` (the
autouse ``_isolate_user_config`` fixture points it at a tmp dir). Hosted
mode: per signed-in user, through ``tests.hosted_helpers``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.export_presets import BUILTIN_PRESETS
from tests.hosted_helpers import login  # hosted_app / hosted_env are registered in conftest

from .test_ui_server import _match_create_app, _MatchClient

BUILTIN_IDS = [p.preset_id for p in BUILTIN_PRESETS]
ROUTE = "/api/settings/export-presets"


@pytest.fixture
def client(tmp_path: Path):
    app = _match_create_app(project_root=tmp_path / "match", project_name="Presets")
    return _MatchClient(app)


def _body(**over):
    return {"mode": "single", "output_format": "mp4", **over}


def _own(client) -> list[dict]:
    return [p for p in client.get(ROUTE).json()["presets"] if not p["builtin"]]


def test_list_leads_with_the_builtins(client) -> None:
    r = client.get(ROUTE)
    assert r.status_code == 200
    presets = r.json()["presets"]
    assert [p["preset_id"] for p in presets] == BUILTIN_IDS
    assert all(p["builtin"] for p in presets)
    assert presets[1]["body"]["output_format"] == "mp4"


def test_put_new_generates_an_id_and_lists_after_the_builtins(client) -> None:
    r = client.put(f"{ROUTE}/new", json={"name": "Club night", "body": _body()})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["builtin"] is False and created["name"] == "Club night"
    assert not created["preset_id"].startswith("builtin:")
    listed = client.get(ROUTE).json()["presets"]
    assert [p["preset_id"] for p in listed] == [*BUILTIN_IDS, created["preset_id"]]


def test_put_existing_replaces_name_and_body(client) -> None:
    pid = client.put(f"{ROUTE}/new", json={"name": "A", "body": _body()}).json()["preset_id"]
    r = client.put(f"{ROUTE}/{pid}", json={"name": "B", "body": _body(mode="trims")})
    assert r.status_code == 200
    assert [(p["name"], p["body"]["mode"]) for p in _own(client)] == [("B", "trims")]


def test_put_ignores_unknown_body_fields(client) -> None:
    r = client.put(f"{ROUTE}/new", json={"name": "X", "body": _body(laser_wipe=True)})
    assert r.status_code == 201
    assert "laser_wipe" not in r.json()["body"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
def test_put_rejects_a_blank_or_long_name(client, name: str) -> None:
    r = client.put(f"{ROUTE}/new", json={"name": name, "body": _body()})
    assert r.status_code == 422


def test_put_trims_the_name(client) -> None:
    r = client.put(f"{ROUTE}/new", json={"name": "  Club  ", "body": _body()})
    assert r.json()["name"] == "Club"


def test_builtins_are_immutable(client) -> None:
    assert client.put(f"{ROUTE}/{BUILTIN_IDS[0]}", json={"name": "X", "body": _body()}).status_code == 403
    assert client.delete(f"{ROUTE}/{BUILTIN_IDS[0]}").status_code == 403
    assert [p["preset_id"] for p in client.get(ROUTE).json()["presets"]] == BUILTIN_IDS


def test_delete_removes_and_unknown_is_404(client) -> None:
    pid = client.put(f"{ROUTE}/new", json={"name": "A", "body": _body()}).json()["preset_id"]
    assert client.delete(f"{ROUTE}/{pid}").status_code == 204
    assert client.delete(f"{ROUTE}/{pid}").status_code == 404
    assert [p["preset_id"] for p in client.get(ROUTE).json()["presets"]] == BUILTIN_IDS


def test_hosted_presets_are_per_user(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    r = client.put(f"{ROUTE}/new", json={"name": "A's", "body": _body()})
    assert r.status_code == 201, r.text
    pid = r.json()["preset_id"]
    assert [p["name"] for p in _own(client)] == ["A's"]

    other = TestClient(client.app, follow_redirects=False)
    login(other, sender, "b@example.com")
    assert _own(other) == []
    assert other.delete(f"{ROUTE}/{pid}").status_code == 404
    assert [p["name"] for p in _own(client)] == ["A's"]


def test_hosted_presets_need_a_session(hosted_app) -> None:
    client, _ = hosted_app
    assert client.get(ROUTE).status_code in (401, 403)
