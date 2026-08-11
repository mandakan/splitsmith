"""HTTP-surface tests for the read-only mirror gate (#631 Task 6).

A desktop-synced match exists hosted-side as a ``matches`` row with
``origin == "desktop"`` (see ``sync_api.py``). Every ``/api/matches/{id}/...``
request funnels through the ``_match_id_alias`` middleware in server.py,
which is the single choke point that can enforce "read-only except via
``/api/sync/*``". This file drives that gate through real HTTP requests:
a write on a mirror 403s, a read succeeds and reports ``origin``, share
management stays writable (that's the point of exposing a mirror at all),
a native hosted match is unaffected, and match deletion still tears a
mirror down.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith import match_model
from splitsmith.db import MatchRow, ProjectStateStore, RecentProjectRow, User, create_engine, sessionmaker
from tests.hosted_helpers import _CapturingSender, login, seed_match

CREATE_URL = "/api/sync/matches"


def _sync_docs_url(match_id: str, kind: str) -> str:
    return f"/api/sync/matches/{match_id}/docs/{kind}"


def _alias_url(match_id: str, rest: str) -> str:
    return f"/api/matches/{match_id}/{rest}"


def _seed_mirror(client: TestClient, match_id: str, name: str) -> None:
    """Adopt ``match_id`` as a desktop mirror and push a minimal match doc.

    Mirrors the create + doc-upsert dance real desktop sync (Task 4) does:
    ``POST /api/sync/matches`` then ``PUT .../docs/match``. An empty
    roster is enough for the gate + shooter-list surface under test.
    """
    created = client.post(CREATE_URL, json={"match_id": match_id, "name": name})
    assert created.status_code == 200, created.text
    doc = match_model.Match(match_id=match_id, name=name, shooters=[], stages=[]).model_dump(mode="json")
    put = client.put(_sync_docs_url(match_id, "match"), params={"expected_version": 0}, json=doc)
    assert put.status_code == 200, put.text


def _seed_match_doc(db_url: str, user_email: str, match_id: str, name: str) -> None:
    """Insert the match state doc a native hosted match needs for
    ``/api/match/shooters`` to resolve (``seed_match`` only inserts the
    ``matches`` row, not the doc ``state.match()`` reads)."""

    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        match = match_model.Match(match_id=match_id, name=name, shooters=[], stages=[])
        await store.save_match(match_id, match.model_dump(mode="json"), expected_version=0)

    asyncio.run(_seed())


def _seed_recent_project(db_url: str, user_email: str, *, path: str, match_id: str, name: str) -> None:
    """Insert a picker row pointing at ``match_id`` (raw SQL, like
    ``seed_match``) so ``POST /api/me/recent-projects/delete`` can resolve
    the match to delete - sync's create route never touches the picker."""

    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _insert() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        async with sf() as s:
            s.add(
                RecentProjectRow(
                    user_id=user_id,
                    path=path,
                    name=name,
                    kind="match",
                    match_id=match_id,
                )
            )
            await s.commit()

    asyncio.run(_insert())


def _match_row_exists(db_url: str, match_id: str) -> bool:
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _check() -> bool:
        async with sf() as s:
            stmt = _select(MatchRow).where(MatchRow.match_id == match_id)
            row = (await s.execute(stmt)).scalar_one_or_none()
        return row is not None

    return asyncio.run(_check())


# Writes on a mirror are rejected.


def test_mirror_add_shooter_blocked(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed_mirror(client, "mirror1", "Mirror Match")

    resp = client.post(_alias_url("mirror1", "match/shooters"), json={"name": "Anna"})
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": "read_only_mirror"}


# Reads on a mirror succeed and report origin="desktop".


def test_mirror_get_shooters_reports_origin_desktop(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed_mirror(client, "mirror2", "Mirror Match 2")

    resp = client.get(_alias_url("mirror2", "match/shooters"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["origin"] == "desktop"


# Share management stays writable on a mirror - that's the feature.


def test_mirror_create_share_allowed(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed_mirror(client, "mirror3", "Mirror Match 3")

    resp = client.post(_alias_url("mirror3", "match/shares"))
    assert resp.status_code == 201, resp.text
    assert resp.json()["revoked_at"] is None


def test_mirror_share_exemption_is_segment_anchored(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """A rest path that merely extends the "match/shares" string is NOT exempt.

    The exemption must cover match/shares and match/shares/... only - a
    future route like match/shares-report must stay read-only gated.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed_mirror(client, "mirror5", "Mirror Match 5")

    resp = client.post(_alias_url("mirror5", "match/shares-report"))
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": "read_only_mirror"}


# A native hosted match is unaffected by the gate.


def test_native_match_add_shooter_unchanged(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", "native1")
    _seed_match_doc(hosted_env, "owner@example.com", "native1", "Native Match")

    resp = client.post(_alias_url("native1", "match/shooters"), json={"name": "Bob"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["origin"] == "hosted"
    assert [s["name"] for s in body["shooters"]] == ["Bob"]


# Beep write paths pass the read-only gate on mirrors (Slice 3).


def test_mirror_beep_confirm_passes_gate(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The gate no longer 403s beep-queue confirm on a mirror.

    Only the middleware is under test: with no shooter seeded the handler
    itself 404s, which proves the request got past the 403."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRBEEPGATE0000000001"
    _seed_mirror(client, match_id, "gate-confirm")
    resp = client.post(
        f"/api/matches/{match_id}/match/beep-queue/confirm",
        json={"slug": "ghost", "stage_number": 1, "video_id": "v1"},
    )
    assert resp.status_code != 403, resp.text


def test_mirror_beep_override_passes_gate(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The gate no longer 403s per-video beep override on a mirror."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRBEEPGATE0000000002"
    _seed_mirror(client, match_id, "gate-override")
    resp = client.post(
        f"/api/matches/{match_id}/shooters/ghost/stages/1/videos/v1/beep",
        json={"beep_time": 1.25},
    )
    assert resp.status_code != 403, resp.text


def test_mirror_destructive_beep_paths_still_blocked(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """detect-beep, beep-window, select, and snap stay read-only on mirrors."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRBEEPGATE0000000003"
    _seed_mirror(client, match_id, "gate-blocked")
    blocked = [
        ("POST", f"/api/matches/{match_id}/shooters/g/stages/1/videos/v1/detect-beep", None),
        ("PUT", f"/api/matches/{match_id}/shooters/g/stages/1/videos/v1/beep-window",
         {"start": 0.0, "end": 5.0}),
        ("POST", f"/api/matches/{match_id}/shooters/g/stages/1/videos/v1/beep/select",
         {"time": 1.0}),
        ("POST", f"/api/matches/{match_id}/shooters/g/stages/1/videos/v1/beep/snap",
         {"time": 1.0}),
        ("POST", f"/api/matches/{match_id}/shooters/g/stages/1/beep", {"beep_time": 1.0}),
    ]
    for method, url, body in blocked:
        resp = client.request(method, url, json=body)
        assert resp.status_code == 403, f"{method} {url} -> {resp.status_code}"
        assert resp.json()["detail"] == "read_only_mirror"


# Deleting a mirror still works - delete-match is a non-alias-routed
# picker action (POST /api/me/recent-projects/delete), untouched by the
# gate, but covered here so the exemption stays honest.


def test_mirror_delete_match_succeeds(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    tmp_path: Path,
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed_mirror(client, "mirror4", "Mirror Match 4")

    # Hosted delete never touches this path on disk (ephemeral container
    # fs) - it's only the picker-row key the delete route resolves
    # match_id from.
    path = str((tmp_path / "picker-entry" / "mirror4").resolve())
    _seed_recent_project(
        hosted_env, "owner@example.com", path=path, match_id="mirror4", name="Mirror Match 4"
    )
    assert _match_row_exists(hosted_env, "mirror4")

    resp = client.post("/api/me/recent-projects/delete", json={"path": path})
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"]["match_row_removed"] is True
    assert not _match_row_exists(hosted_env, "mirror4")
