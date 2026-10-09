"""``GET / PUT / DELETE /api/looks/{name}`` and the catalog's own Looks,
local and hosted (issue #1263)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith import looks
from splitsmith.ui import server
from tests.hosted_helpers import login  # hosted_app / hosted_env are registered in conftest

from .test_ui_server import _match_create_app, _MatchClient


@pytest.fixture
def client(tmp_path: Path):
    app = _match_create_app(project_root=tmp_path / "match", project_name="Looks")
    return _MatchClient(app)


def _body(**over) -> dict:
    colors = {k: list(v) for k, v in looks.load_look("clean").manifest.colors.items()}
    return {"label": "Club", "base": "splitsmith", "colors": colors, **over}


def _catalog(client) -> dict[str, dict]:
    return {info["name"]: info for info in client.get("/api/looks").json()["looks"]}


def test_put_get_and_the_catalog_lists_it_editable(client) -> None:
    r = client.put("/api/looks/club", json=_body(styles={"slate": "rise"}))
    assert r.status_code == 201, r.text
    assert client.put("/api/looks/club", json=_body(label="Club 2")).status_code == 200
    got = client.get("/api/looks/club").json()
    assert got["name"] == "club" and got["body"]["label"] == "Club 2"
    catalog = _catalog(client)
    assert catalog["club"]["editable"] is True and catalog["club"]["source"] == "user"
    assert catalog["splitsmith"]["editable"] is False


def test_a_bad_body_is_a_422_naming_the_field(client) -> None:
    r = client.put("/api/looks/club", json=_body(accent_series=["red"]))
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"][-1] == "accent_series"


def test_a_bad_name_is_a_422_and_unknown_is_404(client) -> None:
    r = client.put("/api/looks/Bad%20Name", json=_body())
    assert r.status_code == 422 and "Look name" in r.json()["detail"]
    assert client.get("/api/looks/nope").status_code == 404
    assert client.delete("/api/looks/nope").status_code == 404


def test_a_shipped_look_is_not_editable_through_the_api(client) -> None:
    assert client.get("/api/looks/splitsmith").status_code == 404
    assert client.delete("/api/looks/splitsmith").status_code == 404


def test_delete_removes_it_from_the_catalog(client) -> None:
    client.put("/api/looks/club", json=_body())
    assert client.delete("/api/looks/club").status_code == 204
    assert "club" not in _catalog(client)


def test_hosted_looks_are_per_user_and_reach_the_catalog(
    hosted_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "user_looks_cache_root", lambda: tmp_path / "user-looks")
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    r = client.put("/api/looks/club", json=_body(styles={"slate": "rise"}))
    assert r.status_code == 201, r.text
    assert _catalog(client)["club"]["editable"] is True

    other = TestClient(client.app, follow_redirects=False)
    login(other, sender, "b@example.com")
    assert "club" not in _catalog(other)
    assert other.get("/api/looks/club").status_code == 404
    assert other.delete("/api/looks/club").status_code == 404
    assert "club" in _catalog(client)


def test_hosted_refuses_a_shipped_name_or_base(hosted_app, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "user_looks_cache_root", lambda: tmp_path / "user-looks")
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    r = client.put("/api/looks/clean", json=_body())
    assert r.status_code == 422 and "shipped" in r.json()["detail"]
    r = client.put("/api/looks/club", json=_body(base="mine"))
    assert r.status_code == 422 and "shipped" in r.json()["detail"]


def test_hosted_looks_need_a_session(hosted_app) -> None:
    client, _ = hosted_app
    assert client.put("/api/looks/club", json=_body()).status_code in (401, 403)
