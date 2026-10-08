"""The You routes on hosted (spec 2026-10-08): each account's own book and
brand, never another's, and the local fill from this machine's matches."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.identity import LOGO_DIR, ShooterIdentity, logo_name
from splitsmith.match_project import MatchProject
from splitsmith.shooter_book import BACKFILL_MARKER, JsonShooterBookStore
from splitsmith.ui.account_backfill import local_candidates
from tests.hosted_helpers import login  # hosted_app / hosted_env are registered in conftest

from .test_shooter_book import _png

BOOK = "/api/me/shooter-book"
PROFILE = "/api/me/profile"


def test_hosted_books_and_brands_are_per_account(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    r = client.put(f"{BOOK}/42", json={"accent": "#aa0000", "club": "Bromma", "label": "Anna"})
    assert r.status_code == 200, r.text
    assert client.put(PROFILE, json={"brand_line": "Team A"}).status_code == 200
    assert [e["shooter_id"] for e in client.get(BOOK).json()["entries"]] == [42]

    other = TestClient(client.app, follow_redirects=False)
    login(other, sender, "b@example.com")
    assert other.get(BOOK).json() == {"entries": []}
    assert other.get(PROFILE).json()["brand"] == {"logo": None, "line": ""}
    assert other.delete(f"{BOOK}/42").status_code == 200
    assert other.get(f"{BOOK}/42/logo").status_code == 404
    # A's own entry and brand survived B's requests.
    assert [e["shooter_id"] for e in client.get(BOOK).json()["entries"]] == [42]
    assert client.get(PROFILE).json()["brand"]["line"] == "Team A"


def test_hosted_without_a_file_store_refuses_a_logo_with_a_reason(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    r = client.post(f"{BOOK}/42/logo", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 422 and "file store" in r.json()["detail"]
    r = client.post(f"{PROFILE}/brand-logo", files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 422 and "file store" in r.json()["detail"]


def test_hosted_routes_need_a_session(hosted_app) -> None:
    client, _ = hosted_app
    assert client.get(BOOK).status_code in (401, 403)
    assert client.put(PROFILE, json={"brand_line": "x"}).status_code in (401, 403)


# --- the local fill -------------------------------------------------------------------


def _shooter(root: Path, slug: str, *, sid: int | None, club: str | None, logo: bytes | None = None) -> None:
    shooter_root = root / "shooters" / slug
    project = MatchProject.init(shooter_root, name="M")
    project.selected_shooter_id = sid
    name = None
    if logo is not None:
        name = logo_name(logo, "png")
        (shooter_root / LOGO_DIR).mkdir(parents=True, exist_ok=True)
        (shooter_root / LOGO_DIR / name).write_bytes(logo)
    project.identity = ShooterIdentity(club=club, logo=name)
    project.save(shooter_root)


def test_local_candidates_read_every_shooter_with_an_id_and_a_look(tmp_path: Path) -> None:
    match = tmp_path / "match"
    _shooter(match, "me", sid=42, club="Bromma", logo=_png())
    _shooter(match, "anon", sid=None, club="Nobody")
    _shooter(match, "plain", sid=7, club=None)
    found = {c.shooter_id: c for c in local_candidates([match])}
    assert set(found) == {42}
    assert found[42].read_logo() == _png()


def test_the_local_store_fills_once_from_its_source(tmp_path: Path) -> None:
    match = tmp_path / "match"
    _shooter(match, "me", sid=42, club="Bromma", logo=_png())
    calls: list[int] = []

    async def source():
        calls.append(1)
        return local_candidates([match])

    store = JsonShooterBookStore(tmp_path / "account", backfill_source=source)
    entries = asyncio.run(store.list())
    assert [(e.shooter_id, e.identity.club) for e in entries] == [(42, "Bromma")]
    assert asyncio.run(store.logo_file(entries[0].identity.logo)) is not None
    assert (tmp_path / "account" / BACKFILL_MARKER).exists()
    asyncio.run(store.delete(42))
    assert asyncio.run(store.list()) == [] and calls == [1], "never filled twice"


def test_the_app_store_fills_from_the_recent_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import user_config
    from splitsmith.ui.server import AppState

    match = tmp_path / "match"
    _shooter(match, "me", sid=42, club="Bromma")
    user_config.record_project_open(match, name="M")
    state = AppState()
    entries = asyncio.run(state.shooter_book.list())
    assert [e.shooter_id for e in entries] == [42]
    assert json.loads((user_config.user_config_dir() / "account" / "shooter_book.json").read_text())[
        "entries"
    ]
