"""The You and shooter book routes (spec 2026-10-08)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from .test_shooter_book import _png
from .test_ui_server import _seed_match_export_project

PROFILE = "/api/me/profile"
BOOK = "/api/me/shooter-book"


@pytest.fixture
def client(tmp_path: Path):
    client, _root = _seed_match_export_project(tmp_path, stage_count=1)
    return client


def test_the_brand_line_and_logo_round_trip(client) -> None:
    assert client.get(PROFILE).json() == {"brand": {"logo": None, "line": ""}}
    assert client.put(PROFILE, json={"brand_line": "  Team Axell "}).json()["brand"]["line"] == "Team Axell"
    r = client.post(f"{PROFILE}/brand-logo", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    name = r.json()["brand"]["logo"]
    assert name.startswith("brand-") and name.endswith(".png")
    served = client.get(f"{PROFILE}/brand-logo")
    assert served.status_code == 200 and served.content == _png()
    assert served.headers["x-content-type-options"] == "nosniff"
    assert client.delete(f"{PROFILE}/brand-logo").json()["brand"] == {"logo": None, "line": "Team Axell"}
    assert client.get(f"{PROFILE}/brand-logo").status_code == 404


def test_a_bad_brand_logo_is_refused(client) -> None:
    assert (
        client.post(
            f"{PROFILE}/brand-logo", files={"file": ("x.svg", b"<svg/>", "image/svg+xml")}
        ).status_code
        == 422
    )
    assert (
        client.post(f"{PROFILE}/brand-logo", files={"file": ("x.png", b"", "image/png")}).status_code == 422
    )
    big = b"\x89PNG" + b"0" * (2 * 1024 * 1024 + 10)
    assert (
        client.post(f"{PROFILE}/brand-logo", files={"file": ("x.png", big, "image/png")}).status_code == 413
    )
    assert client.put(PROFILE, json={"brand_line": "x" * 61}).status_code == 422


def test_shooter_book_entries_round_trip(client) -> None:
    r = client.put(f"{BOOK}/42", json={"accent": "#FF0000", "club": "Bromma", "label": "Anna"})
    assert r.status_code == 200, r.text
    assert r.json()["identity"] == {"accent": "#ff0000", "logo": None, "club": "Bromma"}
    assert client.put(f"{BOOK}/42", json={"accent": None}).json()["identity"]["club"] == "Bromma"
    r = client.post(f"{BOOK}/42/logo", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 200 and r.json()["identity"]["logo"].startswith("logo-")
    assert client.get(f"{BOOK}/42/logo").content == _png()
    entries = client.get(BOOK).json()["entries"]
    assert [(e["shooter_id"], e["label"]) for e in entries] == [(42, "Anna")]
    assert client.delete(f"{BOOK}/42/logo").json()["identity"]["logo"] is None
    assert client.delete(f"{BOOK}/42").status_code == 200
    assert client.get(BOOK).json() == {"entries": []}
    assert client.get(f"{BOOK}/42/logo").status_code == 404
    assert client.delete(f"{BOOK}/42/logo").status_code == 404


def test_a_bad_book_value_is_refused(client) -> None:
    assert client.put(f"{BOOK}/42", json={"accent": "red"}).status_code == 422
    assert (
        client.post(f"{BOOK}/42/logo", files={"file": ("x.gif", b"GIF89a", "image/gif")}).status_code == 422
    )
    assert client.get(BOOK).json() == {"entries": []}


def test_shooter_search_needs_no_match(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui.scoreboard import http as ssi_http

    asked: list[str] = []

    class _Ref:
        def __init__(self, name: str) -> None:
            self.name = name

        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            return {"shooter_id": 40821, "name": self.name}

    def find(self, name: str):  # noqa: ANN001, ANN202
        asked.append(name)
        return [_Ref("Mathias Axell")]

    monkeypatch.setattr(ssi_http.SsiHttpClient, "find_shooter", find)
    assert client.get("/api/me/shooter-search", params={"q": "  "}).json() == []
    assert client.get("/api/me/shooter-search", params={"q": "axell"}).json() == [
        {"shooter_id": 40821, "name": "Mathias Axell"}
    ]
    assert asked == ["axell"]


def test_no_route_here_is_on_a_share_allowlist() -> None:
    from splitsmith.ui.server import _SHARE_PATH_RE, _SHARE_WRITE_ROUTES

    paths = [
        "me/profile",
        "me/profile/brand-logo",
        "me/shooter-book",
        "me/shooter-book/42",
        "me/shooter-book/42/logo",
        "me/shooter-search",
        "shooters/me/identity",
        "shooters/me/identity/use-book",
    ]
    for path in paths:
        assert not _SHARE_PATH_RE.fullmatch(path), path
    written = " ".join(str(r) for r in _SHARE_WRITE_ROUTES)
    assert not re.search(r"\bme/|identity/use-book", written)


def test_hosted_without_its_store_refuses_writes(client) -> None:
    from splitsmith.ui.server import AppState

    state: AppState = client.app.state.splitsmith_state
    state._build_tenant = lambda user_id: None  # type: ignore[assignment,return-value]
    try:
        assert client.put(PROFILE, json={"brand_line": "x"}).status_code == 503
        assert client.put(f"{BOOK}/42", json={"club": "x"}).status_code == 503
        assert client.get(BOOK).json() == {"entries": []}
    finally:
        state._build_tenant = None
