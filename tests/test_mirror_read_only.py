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
import copy
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith import match_model
from splitsmith.db import MatchRow, ProjectStateStore, RecentProjectRow, User, create_engine, sessionmaker
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.shot_id import ensure_shot_ids
from splitsmith.sync.merge import merge_audit_doc
from tests.hosted_helpers import _CapturingSender, login, moto_s3_storage, seed_match

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


def _seed_mirror_with_video(
    client: TestClient,
    match_id: str,
    name: str,
    *,
    processed: dict[str, bool] | None = None,
) -> str:
    """Adopt ``match_id`` as a mirror with one shooter "alice", stage 1, and
    one primary video.

    Reuses ``_seed_mirror`` for the match-doc create, then registers
    "alice" on the roster (a second PUT to the match doc, since the
    version moved to 1 on insert) and pushes a project doc through the
    same sync-doc PUT surface. ``video_id`` on ``StageVideo`` is a
    computed field (blake2s of path + stage_number) - built from a real
    ``StageVideo``/``MatchProject`` instance rather than a hand-rolled
    dict so the value returned here always matches what the server
    resolves. Returns that real video_id for the caller's request URLs.

    ``processed`` defaults to a fully-processed video (beep+trim+shot
    detect all done); pass a different dict to seed a trim_stale case.
    """
    _seed_mirror(client, match_id, name)
    roster_doc = match_model.Match(match_id=match_id, name=name, shooters=["alice"], stages=[]).model_dump(
        mode="json"
    )
    put_roster = client.put(
        _sync_docs_url(match_id, "match"), params={"expected_version": 1}, json=roster_doc
    )
    assert put_roster.status_code == 200, put_roster.text

    video = StageVideo(
        path=Path("videos/stage1.mp4"),
        role="primary",
        stage_number=1,
        beep_time=2.0,
        beep_source="auto",
        beep_confidence=0.4,
        beep_reviewed=False,
        processed=processed or {"beep": True, "trim": True, "shot_detect": True},
    )
    stage = StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=12.5, videos=[video])
    project_doc = MatchProject(name=name, competitor_name="Alice", stages=[stage]).model_dump(mode="json")
    put_project = client.put(
        f"/api/sync/matches/{match_id}/docs/project/alice",
        params={"expected_version": 0},
        json=project_doc,
    )
    assert put_project.status_code == 200, put_project.text
    return video.video_id


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
        (
            "PUT",
            f"/api/matches/{match_id}/shooters/g/stages/1/videos/v1/beep-window",
            {"start": 0.0, "end": 5.0},
        ),
        ("POST", f"/api/matches/{match_id}/shooters/g/stages/1/videos/v1/beep/select", {"time": 1.0}),
        ("POST", f"/api/matches/{match_id}/shooters/g/stages/1/videos/v1/beep/snap", {"time": 1.0}),
        ("POST", f"/api/matches/{match_id}/shooters/g/stages/1/beep", {"beep_time": 1.0}),
    ]
    for method, url, body in blocked:
        resp = client.request(method, url, json=body)
        assert resp.status_code == 403, f"{method} {url} -> {resp.status_code}"
        assert resp.json()["detail"] == "read_only_mirror"


# Triage writes pass the read-only gate on mirrors (Slice 4); everything
# else stage-level (e.g. the audit PUT) stays blocked.


def test_mirror_allows_triage_accept(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The gate no longer 403s the accept-stage triage write on a mirror.

    Only the middleware is under test: with no shooter seeded the handler
    itself 404s, which proves the request got past the 403."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRTRIAGEGATE000000001"
    _seed_mirror(client, match_id, "gate-triage-accept")
    resp = client.post(f"/api/matches/{match_id}/shooters/alice/stages/1/audit/accept")
    assert resp.status_code != 403, resp.text


def test_mirror_allows_triage_attention(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The gate no longer 403s the flag-for-desktop triage write on a mirror."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRTRIAGEGATE000000002"
    _seed_mirror(client, match_id, "gate-triage-attention")
    resp = client.post(
        f"/api/matches/{match_id}/shooters/alice/stages/1/attention",
        json={"flagged": True},
    )
    assert resp.status_code != 403, resp.text


def _legacy_audit_doc(time: float) -> dict:
    """One kept manual shot with no id and no candidate_number.

    The one shape whose derived id is not convergent across two sides:
    ``derive_shot_id`` keys it off the rounded time, so a nudge moves it.
    """
    return {
        "stage_number": 1,
        "beep_time": 5.0,
        "shots": [
            {
                "shot_number": 1,
                "candidate_number": None,
                "time": time,
                "ms_after_beep": int(round((time - 5.0) * 1000)),
            }
        ],
        "audit_events": [],
    }


def _seed_mirror_stage_with_audit(client: TestClient, match_id: str, name: str, doc: dict) -> None:
    """Mirror with shooter "alice", stage 1, and ``doc`` as her stage-1 audit."""
    _seed_mirror(client, match_id, name)
    roster = match_model.Match(match_id=match_id, name=name, shooters=["alice"], stages=[]).model_dump(
        mode="json"
    )
    put_roster = client.put(_sync_docs_url(match_id, "match"), params={"expected_version": 1}, json=roster)
    assert put_roster.status_code == 200, put_roster.text
    stage = StageEntry(stage_number=1, stage_name="Stage One", time_seconds=30.0)
    project_doc = MatchProject(name=name, competitor_name="Alice", stages=[stage]).model_dump(mode="json")
    put_project = client.put(
        f"/api/sync/matches/{match_id}/docs/project/alice",
        params={"expected_version": 0},
        json=project_doc,
    )
    assert put_project.status_code == 200, put_project.text
    put_audit = client.put(
        f"/api/sync/matches/{match_id}/docs/audit/alice/1", params={"expected_version": 0}, json=doc
    )
    assert put_audit.status_code == 200, put_audit.text


def _seed_native_stage_with_audit(db_url: str, user_email: str, match_id: str, name: str, doc: dict) -> None:
    """Same shape as ``_seed_mirror_stage_with_audit`` for a native match.

    The sync doc routes refuse a native match (409 ``not_a_mirror``), so
    the docs go in through the store the hosted app reads them from.
    """
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        match = match_model.Match(match_id=match_id, name=name, shooters=["alice"], stages=[])
        await store.save_match(match_id, match.model_dump(mode="json"), expected_version=0)
        stage = StageEntry(stage_number=1, stage_name="Stage One", time_seconds=30.0)
        project = MatchProject(name=name, competitor_name="Alice", stages=[stage])
        await store.save_project(match_id, "alice", project.model_dump(mode="json"), expected_version=0)
        await store.save_audit(match_id, "alice", 1, doc, expected_version=0)

    asyncio.run(_seed())


def test_mirror_accept_does_not_mint_a_shot_id(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The desktop is the sole minter of shot ids for a mirror (#631 Task 7).

    ``derive_shot_id`` keys a candidate-less shot off its rounded time, so
    a hosted mint here and a desktop mint on a nudged copy of the same shot
    produce two ids for one shot - which the sync merge cannot tell from
    two shots.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRSHOTIDMINT0000001"
    _seed_mirror_stage_with_audit(client, match_id, "mirror-mint", _legacy_audit_doc(6.5))

    accepted = client.post(_alias_url(match_id, "shooters/alice/stages/1/audit/accept"))
    assert accepted.status_code == 200, accepted.text

    stored = client.get(_alias_url(match_id, "shooters/alice/stages/1/audit"))
    assert stored.status_code == 200, stored.text
    shot = stored.json()["shots"][0]
    assert "id" not in shot
    # Suppressing the mint must not suppress the accept itself.
    assert shot.get("interval_class")


def test_native_match_accept_still_mints_a_shot_id(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """A native hosted match has no desktop, so there is no second minter
    and the save boundary keeps stamping exactly as it did."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "native-shot-id-mint"
    seed_match(hosted_env, "owner@example.com", match_id)
    _seed_native_stage_with_audit(
        hosted_env, "owner@example.com", match_id, "Native Mint", _legacy_audit_doc(6.5)
    )

    accepted = client.post(_alias_url(match_id, "shooters/alice/stages/1/audit/accept"))
    assert accepted.status_code == 200, accepted.text

    stored = client.get(_alias_url(match_id, "shooters/alice/stages/1/audit"))
    assert stored.status_code == 200, stored.text
    assert stored.json()["shots"][0]["id"] == "manual-t6500"


def test_mirror_allows_audit_put(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The full audit PUT is exempt now that shots merge by stable id.

    Supersedes ``test_mirror_still_blocks_audit_put``: shot membership was
    desktop-owned until the merge unit shipped, so opening this earlier would
    have let a desktop pull silently discard phone edits.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRAUDITPUTOPEN0000001"
    _seed_mirror(client, match_id, "gate-audit-open")
    resp = client.put(
        _alias_url(match_id, "shooters/alice/stages/1/audit"),
        json={"stage_number": 1, "shots": [], "audit_events": []},
    )
    assert resp.status_code != 403, resp.text


def test_mirror_audit_exemption_boundary_pins(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The audit exemption is one exact path and one method.

    A POST to the same path, a trailing slash, a sibling path and a
    non-numeric stage must all still 403.

    A trailing-newline variant (``.../audit%0a``) is deliberately NOT a
    case here even though plain ``$`` also matches just before a single
    trailing ``\\n`` (which is why the regexes were switched to ``\\Z`` --
    see the comment above ``_mirror_beep_write_re``): verified empirically
    that ``request.url.path`` can never actually carry that byte all the
    way to this middleware's ``rest`` regardless of anchor choice.
    Starlette's ``URL.path`` property is built from
    ``urllib.parse.urlsplit()``, which strips ASCII CR/LF/TAB from a URL
    unconditionally (CPython's own bpo-43882 hardening) - so
    ``.../audit%0a`` arrives at this regex as the exact clean string
    ``.../audit`` with nothing appended, indistinguishable from a
    legitimate request, and asserting 403 for it here would be pinning a
    routing path that cannot occur. Confirmed with a raw ASGI scope whose
    ``path`` was hand-set to include a literal trailing ``\\n``: even then,
    the first ``request.url.path`` access inside this app's own middleware
    already came back stripped, before any of our code ran.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRAUDITBOUNDARY000001"
    _seed_mirror(client, match_id, "gate-audit-boundary")
    for method, rest in (
        ("post", "shooters/alice/stages/1/audit"),
        ("put", "shooters/alice/stages/1/audit/"),
        ("put", "shooters/alice/stages/1/audit/extra"),
        ("put", "shooters/alice/stages/x/audit"),
    ):
        resp = getattr(client, method)(_alias_url(match_id, rest), json={})
        assert resp.status_code == 403, f"{method} {rest}"
        assert resp.json()["detail"] == "read_only_mirror"


def _mixed_audit_doc(detected_time: float, manual_time: float) -> dict:
    """One detected shot (``candidate_number`` set, no ``id``) and one
    candidate-less manual shot (no ``candidate_number``, no ``id``).

    This is exactly the shape the SPA sends: ``buildAuditJson`` omits
    ``id`` for a detected marker on purpose (``audit-doc.test.ts`` pins
    it - "omits the id for detected shots -- the server derives
    ``cand-<n>``"), and a legacy candidate-less manual shot has never
    carried one either.
    """
    return {
        "stage_number": 1,
        "beep_time": 5.0,
        "shots": [
            {
                "shot_number": 1,
                "candidate_number": 2,
                "time": detected_time,
                "ms_after_beep": int(round((detected_time - 5.0) * 1000)),
            },
            {
                "shot_number": 2,
                "candidate_number": None,
                "time": manual_time,
                "ms_after_beep": int(round((manual_time - 5.0) * 1000)),
            },
        ],
        "audit_events": [],
    }


def test_mirror_audit_put_mints_convergent_ids_but_not_manual_ones(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The full audit PUT is the path Task 7's ``_may_mint_shot_ids`` guard
    protects, but it had no end-to-end coverage until this task opened the
    gate: the read-only 403 meant this exact request never ran before.

    A PUT of the SPA's own shape (a detected shot with ``candidate_number``
    and no ``id``, plus a candidate-less manual shot with no ``id``) through
    the alias on a mirror must still derive the detected shot's ``cand-2``
    -- both sides compute it from the same ``candidate_number``, so there
    is no second-minter risk (#631 Task 6 fix round 1: the desktop-sole-
    minter guard was over-broad and suppressed this convergent derivation
    too, which meant every phone save of a stage with a detected shot
    produced a document the sync merge's unstamped-shot gate would refuse
    wholesale on the next desktop pull, reverting the phone's edit). The
    manual shot must still get no id - that derivation keys off rounded
    time, which is not convergent across a nudge. The same PUT against a
    hosted-native match still mints both, exactly as it always has.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRAUDITPUTMINT0000001"
    _seed_mirror_stage_with_audit(client, match_id, "audit-put-mint", _mixed_audit_doc(6.5, 8.0))

    put = client.put(
        _alias_url(match_id, "shooters/alice/stages/1/audit"),
        json=_mixed_audit_doc(6.5, 8.0),
    )
    assert put.status_code == 200, put.text

    stored = client.get(_alias_url(match_id, "shooters/alice/stages/1/audit"))
    assert stored.status_code == 200, stored.text
    shots = stored.json()["shots"]
    detected = next(s for s in shots if s.get("candidate_number") == 2)
    manual = next(s for s in shots if s.get("candidate_number") is None)
    assert detected["id"] == "cand-2"
    assert "id" not in manual


def test_mirror_audit_put_then_desktop_pull_keeps_the_phone_nudge(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """End to end across the alias PUT and the pull-side merge (#631 Task 6
    fix round 1, Critical).

    Reproduces the bug the review found: a phone PUT of the SPA's own doc
    shape (a detected shot with ``candidate_number`` and no ``id``, plus a
    manual shot carrying the id the SPA minted for it itself) used to come
    back from the mirror with the detected shot still unstamped, because
    the desktop-sole-minter guard suppressed *all* derivation under
    ``mint=False``, not just the non-convergent branches. That unstamped
    remote shot made ``merge_audit_doc``'s unstamped-shot gate refuse the
    whole shot section on the desktop's next pull -- reverting the phone's
    nudge and (had there been a genuinely new phone-added shot) dropping it
    too.

    Drives the real HTTP PUT against a mirror, fetches what a desktop pull
    would see, and runs the actual pull-side merge over it -- not a
    reasoned argument about what the merge would do, but the merge itself.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRAUDITPUTMERGE000001"
    # The converged state after some earlier sync: both shots already
    # stamped, exactly the shape the seed after the FIRST mint (server- or
    # desktop-derived cand-2; SPA-minted manual-shotid-1) would leave.
    converged = {
        "stage_number": 1,
        "beep_time": 5.0,
        "shots": [
            {
                "shot_number": 1,
                "candidate_number": 2,
                "id": "cand-2",
                "time": 6.5,
                "ms_after_beep": 1500,
            },
            {
                "shot_number": 2,
                "candidate_number": None,
                "id": "manual-shotid-1",
                "time": 8.0,
                "ms_after_beep": 3000,
            },
        ],
        "audit_events": [],
    }
    _seed_mirror_stage_with_audit(client, match_id, "audit-put-merge", converged)

    # The phone nudges the detected shot to 6.6s and saves. The SPA never
    # sends an id for a detected shot (buildAuditJson omits it on purpose,
    # see audit-doc.test.ts) - it re-derives cand-2 client-side for display
    # only. The manual shot round-trips unchanged, still carrying the id
    # the SPA minted for it.
    phone_put = dict(converged)
    phone_put["shots"] = [
        {"shot_number": 1, "candidate_number": 2, "time": 6.6, "ms_after_beep": 1600},
        {
            "shot_number": 2,
            "candidate_number": None,
            "id": "manual-shotid-1",
            "time": 8.0,
            "ms_after_beep": 3000,
        },
    ]
    put = client.put(_alias_url(match_id, "shooters/alice/stages/1/audit"), json=phone_put)
    assert put.status_code == 200, put.text

    # What a desktop pull fetches as "remote".
    remote = client.get(_alias_url(match_id, "shooters/alice/stages/1/audit")).json()

    # The desktop's own local copy is unchanged since the last sync, i.e.
    # identical to the pre-nudge converged doc; "base" is that same shared
    # ancestor.
    result = merge_audit_doc(
        converged,
        converged,
        remote,
        doc_key="stage1",
        local_ts=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        remote_ts=datetime(2026, 8, 12, 13, 0, tzinfo=UTC),
    )

    assert not any("without a persisted id" in note for note in result.notes), result.notes
    merged_shots = {s["id"]: s for s in result.doc["shots"]}
    assert merged_shots["cand-2"]["time"] == 6.6  # the phone's nudge survived
    assert merged_shots["manual-shotid-1"]["time"] == 8.0


def _promoted_collision_doc() -> dict:
    """Two shots snapped onto one ensemble candidate, neither carrying an id.

    Not a hypothetical: ``lab/promote.py``'s ``_find_candidate_number``
    picks the nearest ensemble candidate per snapped shot independently, so
    a promoted stage can put two shots on one candidate. This repo's own
    fixtures contain it -- ``stage-shots-blacksmith-2026-stage6-...`` has
    candidate 18, 21 and 25 twice each, all ``"source": "promoted"`` -- and
    these values are that fixture's candidate-18 pair verbatim.
    """
    return {
        "stage_number": 1,
        "beep_time": 5.0,
        "shots": [
            {
                "shot_number": 7,
                "candidate_number": 18,
                "time": 8.796,
                "ms_after_beep": 3796,
                "source": "promoted",
            },
            {
                "shot_number": 8,
                "candidate_number": 18,
                "time": 9.48,
                "ms_after_beep": 4480,
                "source": "promoted",
            },
        ],
        "audit_events": [],
    }


def test_mirror_audit_put_leaves_a_colliding_shot_for_the_desktop_to_stamp(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """A derived id that collides is not convergent, so a mirror must not
    stamp it -- end to end across the real PUT and the real merge.

    ``ensure_shot_ids``' collision fallback (two shots deriving one id) is a
    uuid4, and it used to run *after* the mint gate, so the second shot of a
    promoted candidate pair got a randomly minted id on a mirror: the exact
    non-convergent stamp ``mint=False`` exists to prevent. The mirror minted
    one uuid4, the desktop another, both documents then read as fully
    stamped, the merge's unstamped-shot gate passed, and one shot silently
    unioned into two.

    Now the colliding shot is left unstamped, the gate fires, and local's
    two shots stand -- a stated refusal instead of a silent duplicate.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRAUDITPUTCOLLIDE001"
    _seed_mirror_stage_with_audit(client, match_id, "audit-put-collide", _promoted_collision_doc())

    put = client.put(
        _alias_url(match_id, "shooters/alice/stages/1/audit"),
        json=_promoted_collision_doc(),
    )
    assert put.status_code == 200, put.text

    # What a desktop pull fetches as "remote": the first shot took cand-18,
    # the second is left for the desktop rather than randomly stamped.
    remote = client.get(_alias_url(match_id, "shooters/alice/stages/1/audit")).json()
    assert remote["shots"][0]["id"] == "cand-18"
    assert "id" not in remote["shots"][1]

    # The desktop's own copy, stamped on its own save boundary -- it may
    # mint, so the collision falls back to a uuid4 there.
    base = _promoted_collision_doc()
    local = copy.deepcopy(base)
    ensure_shot_ids(local["shots"])
    local_ids = [s["id"] for s in local["shots"]]
    assert local_ids[0] == "cand-18"
    assert local_ids[1].startswith("manual-")

    result = merge_audit_doc(
        base,
        local,
        remote,
        doc_key="stage1",
        local_ts=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        # Remote is the newer side, so nothing but the gate stops it winning.
        remote_ts=datetime(2026, 8, 12, 13, 0, tzinfo=UTC),
    )

    assert any("without a persisted id" in note for note in result.notes), result.notes
    assert [s["id"] for s in result.doc["shots"]] == local_ids  # two shots, not three


def test_native_match_audit_put_still_mints_a_shot_id(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """A native hosted match has no desktop, so there is no second minter
    and the save boundary keeps stamping the audit PUT exactly as before."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "native-audit-put-mint"
    seed_match(hosted_env, "owner@example.com", match_id)
    _seed_native_stage_with_audit(
        hosted_env, "owner@example.com", match_id, "Native Audit Mint", _legacy_audit_doc(6.5)
    )

    put = client.put(
        _alias_url(match_id, "shooters/alice/stages/1/audit"),
        json=_legacy_audit_doc(6.5),
    )
    assert put.status_code == 200, put.text

    stored = client.get(_alias_url(match_id, "shooters/alice/stages/1/audit"))
    assert stored.status_code == 200, stored.text
    assert stored.json()["shots"][0]["id"] == "manual-t6500"


@pytest.mark.parametrize(
    ("match_id", "method", "rest"),
    [
        (
            "01JMIRRTRIAGEGATEBOUND01",
            "POST",
            "shooters/alice/stages/1/attention/extra",
        ),
        (
            "01JMIRRTRIAGEGATEBOUND02",
            "PATCH",
            "shooters/alice/stages/1/attention",
        ),
        (
            "01JMIRRTRIAGEGATEBOUND03",
            "POST",
            "shooters/alice/stages/1/audit/accept/",
        ),
    ],
    ids=["attention-extra-segment", "attention-wrong-method", "accept-trailing-slash"],
)
def test_mirror_triage_exemption_boundary_pins(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    match_id: str,
    method: str,
    rest: str,
) -> None:
    """Pin the edges of ``_mirror_triage_write_re``.

    The exemption regex is anchored with ``$`` and only fires for POST -
    an extra path segment after ``attention``, the wrong HTTP method, or
    a trailing slash on ``audit/accept`` must all still 403. Any of these
    variants slipping through would silently widen the read-only
    mirror's write surface (#823)."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed_mirror(client, match_id, "gate-triage-boundary")
    resp = client.request(method, f"/api/matches/{match_id}/{rest}")
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": "read_only_mirror"}


# Coach writes pass the read-only gate on mirrors (Slice 5: mobile
# interval reclassify); everything else coach-shaped stays blocked.


def test_mirror_allows_coach_shot_patch(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The gate no longer 403s the per-shot coach PATCH on a mirror.

    Only the middleware is under test: with no shooter seeded the handler
    itself 404s, which proves the request got past the 403."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRCOACHGATE0000000001"
    _seed_mirror(client, match_id, "gate-coach-patch")
    resp = client.patch(
        f"/api/matches/{match_id}/shooters/alice/stages/1/shots/3/coach",
        json={"interval_class": "movement", "interval_class_source": "manual"},
    )
    assert resp.status_code != 403, resp.text


def test_mirror_allows_coach_reclassify(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The gate no longer 403s the bulk coach reclassify POST on a mirror."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRCOACHGATE0000000002"
    _seed_mirror(client, match_id, "gate-coach-reclassify")
    resp = client.post(f"/api/matches/{match_id}/shooters/alice/stages/1/coach/reclassify")
    assert resp.status_code != 403, resp.text


@pytest.mark.parametrize(
    ("match_id", "method", "rest"),
    [
        ("01JMIRRCOACHGATEBOUND0001", "POST", "shooters/alice/stages/1/shots/3/coach"),
        ("01JMIRRCOACHGATEBOUND0002", "PATCH", "shooters/alice/stages/1/coach/reclassify"),
        ("01JMIRRCOACHGATEBOUND0003", "PATCH", "shooters/alice/stages/1/shots/3/coach/extra"),
        ("01JMIRRCOACHGATEBOUND0004", "PATCH", "shooters/alice/stages/1/coach"),
        ("01JMIRRCOACHGATEBOUND0005", "POST", "shooters/alice/stages/1/coach/reclassify/"),
        ("01JMIRRCOACHGATEBOUND0006", "DELETE", "shooters/alice/stages/1/shots/3/coach"),
    ],
    ids=[
        "shot-patch-wrong-method",
        "reclassify-wrong-method",
        "shot-patch-extra-segment",
        "coach-root-patch",
        "reclassify-trailing-slash",
        "shot-patch-delete",
    ],
)
def test_mirror_coach_exemption_boundary_pins(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    match_id: str,
    method: str,
    rest: str,
) -> None:
    """Pin the edges of the coach exemptions.

    Each shape is exempt only for its own method (PATCH for the per-shot
    patch, POST for reclassify); the regexes are anchored with ``$``. Any
    variant slipping through would silently widen the read-only mirror's
    write surface."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _seed_mirror(client, match_id, "gate-coach-boundary")
    resp = client.request(method, f"/api/matches/{match_id}/{rest}", json={})
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": "read_only_mirror"}


def test_mirror_coach_by_id_exemption_boundary_pins(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The widened coach pattern must not open anything else.

    A by-id coach PATCH passes the gate; a by-id path that is not ``coach``,
    a trailing slash, and a non-numeric stage must all still 403. A literal
    ``..`` traversal-shaped id is a fourth case worth demonstrating rather
    than reasoning about: the character class excludes ``/`` and Starlette
    matches path segments literally rather than resolving ``..`` against
    the filesystem, so ``by-id/..`` is not a traversal at all - it routes
    to the handler exactly like ``by-id/cand-9`` does, and 404s there for
    the same reason the ``allowed`` case below would too if it named a
    slug this match's roster doesn't recognize: ``_seed_mirror`` seeds an
    empty roster, so the roster-membership check in ``state.shooter_root``
    (which ``state.shooter_project("alice")`` calls) 404s on
    "alice" before shot lookup is ever reached - not
    because no shot matched the id.

    The id is sent percent-encoded (``%2e%2e``) rather than as a literal
    ``..`` segment: httpx resolves dot-segments client-side per RFC 3986
    before a request ever leaves the test process, so a literal ``..`` in
    the URL string never reaches the server at all and would prove
    nothing about Starlette's own routing.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRCOACHBYID0000000001"
    _seed_mirror(client, match_id, "gate-coach-by-id")

    allowed = client.patch(
        _alias_url(match_id, "shooters/alice/stages/1/shots/by-id/cand-9/coach"),
        json={"coaching_note": "x"},
    )
    assert allowed.status_code != 403, allowed.text

    traversal = client.patch(
        _alias_url(match_id, "shooters/alice/stages/1/shots/by-id/%2e%2e/coach"),
        json={"coaching_note": "x"},
    )
    assert traversal.status_code != 403, traversal.text

    for rest in (
        "shooters/alice/stages/1/shots/by-id/cand-9/audit",
        "shooters/alice/stages/1/shots/by-id/cand-9/coach/",
        "shooters/alice/stages/x/shots/by-id/cand-9/coach",
    ):
        blocked = client.patch(_alias_url(match_id, rest), json={})
        assert blocked.status_code == 403, rest
        assert blocked.json()["detail"] == "read_only_mirror"


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


# Mirror beep overrides mark state only - no job chain (Slice 3, Task 2).


def test_mirror_override_marks_state_only(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """A manual beep override on a mirror writes the fields and returns,
    without chaining a trim job - there's no raw media hosted-side to
    trim against. Desktop re-derives on its next sync pull."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRBEEPSTATE000000001"
    video_id = _seed_mirror_with_video(client, match_id, "state-only")

    resp = client.post(
        f"/api/matches/{match_id}/shooters/alice/stages/1/videos/{video_id}/beep",
        json={"beep_time": 3.75},
    )
    assert resp.status_code == 200, resp.text
    video = resp.json()["stages"][0]["videos"][0]
    assert video["beep_time"] == 3.75
    assert video["beep_source"] == "manual"
    assert video["processed"]["trim"] is False
    assert video["processed"]["shot_detect"] is False

    jobs = client.get("/api/me/jobs").json()
    assert jobs == [], f"mirror override must not enqueue jobs: {jobs}"


def test_mirror_confirm_sets_reviewed(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """Beep-queue confirm on a mirror never enqueued jobs to begin with -
    covered here so the exemption stays honest alongside the override
    test above."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRBEEPSTATE000000002"
    video_id = _seed_mirror_with_video(client, match_id, "confirm-flag")

    resp = client.post(
        f"/api/matches/{match_id}/match/beep-queue/confirm",
        json={"slug": "alice", "stage_number": 1, "video_id": video_id},
    )
    assert resp.status_code == 200, resp.text
    jobs = client.get("/api/me/jobs").json()
    assert jobs == []


# Beep-queue media honesty on mirrors (mobile beep review, slice 3).


@pytest.fixture
def hosted_app_with_storage(
    hosted_env: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, _CapturingSender, dict[str, object]]]:
    """``hosted_app``, but with a moto-backed S3 bucket wired as tenant
    storage instead of ``state.storage`` staying ``None``.

    ``hosted_app`` (see ``tests/hosted_helpers.py``) boots its app before
    any storage stub could apply, so this rebuilds the app inside
    ``moto_s3_storage``'s monkeypatch of ``_tenant_s3_storage`` - same
    recipe ``tests/test_hosted_raw_upload.py`` uses. ``captured["storage"]``
    is only populated once a request has resolved a tenant, so callers
    must log in and hit any endpoint before reading it.
    """
    pytest.importorskip("moto")
    with moto_s3_storage(monkeypatch, "beep-queue-media-test") as captured:
        from splitsmith.ui.server import create_app

        app = create_app()
        sender = _CapturingSender()
        app.state.splitsmith_state.auth.backends[0]._email = sender
        with TestClient(app, follow_redirects=False) as client:
            yield client, sender, captured


def test_mirror_beep_queue_media_flags(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict[str, object]],
) -> None:
    """Mirror queue items report honest media: no proxy, snippet only when
    both R2 objects exist, origin=desktop, trim_stale from processed."""
    client, sender, captured = hosted_app_with_storage
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRBEEPQUEUE00000001"
    video_id = _seed_mirror_with_video(client, match_id, "queue-flags")

    resp = client.get(f"/api/matches/{match_id}/match/beep-queue")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["origin"] == "desktop"
    item = body["stages"][0]["items"][0]
    assert item["proxy_ready"] is False  # was falsely True before this task
    assert item["snippet_ready"] is False  # nothing uploaded yet
    assert item["trim_stale"] is False  # processed.trim is True in the seed

    # Upload both snippet objects into the fake storage, then re-query.
    storage = captured["storage"]
    base = f"matches/{match_id}/shooters/alice/beep_review/{video_id}"
    storage.write_bytes(f"{base}.m4a", b"fake-audio")
    storage.write_bytes(f"{base}.peaks.json", b"{}")
    item = client.get(f"/api/matches/{match_id}/match/beep-queue").json()["stages"][0]["items"][0]
    assert item["snippet_ready"] is True


def test_mirror_get_project_reports_proxy_not_ready(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict[str, object]],
) -> None:
    """#821: get_project's proxy_ready must agree with get_beep_queue's.
    A mirror video has no proxy object; reporting ready mounts a player
    the server can only answer with an error."""
    client, sender, _captured = hosted_app_with_storage
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRGETPROJECT0000001"
    _seed_mirror_with_video(client, match_id, "get-project-flags")

    resp = client.get(f"/api/matches/{match_id}/shooters/alice/project")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["origin"] == "desktop"
    videos = [v for s in payload["stages"] for v in s["videos"]] + list(payload.get("unassigned_videos", []))
    assert videos, "seeded mirror should have videos"
    assert all(v["proxy_ready"] is False for v in videos)


def test_beep_queue_lists_only_beep_review_prefixes(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#821: the snippet listing must not enumerate every trimmed clip.
    Pin the prefixes so a regression shows up as a wrong prefix, not as
    a silent hosted-list cost.

    Every request rebuilds an equivalent ``S3Storage`` instance against
    the same moto bucket (see ``moto_s3_storage``'s docstring), so the
    recording wrapper has to sit on the class, not on one captured
    instance - an instance-level patch would only see calls made before
    the beep-queue request builds its own storage object.
    """
    client, sender, _captured = hosted_app_with_storage
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRBEEPQUEUE00000003"
    _seed_mirror_with_video(client, match_id, "queue-prefixes")

    from splitsmith.storage import S3Storage

    prefixes: list[str] = []
    original_list = S3Storage.list

    def _recording_list(self: S3Storage, prefix: str):
        prefixes.append(prefix)
        return original_list(self, prefix)

    monkeypatch.setattr(S3Storage, "list", _recording_list)

    resp = client.get(f"/api/matches/{match_id}/match/beep-queue")
    assert resp.status_code == 200, resp.text

    snippet_prefixes = [p for p in prefixes if p.startswith("matches/")]
    assert snippet_prefixes, "expected at least one snippet listing"
    assert all(p.endswith("/beep_review/") for p in snippet_prefixes), snippet_prefixes


def test_mirror_beep_queue_trim_stale_when_trim_not_processed(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """trim_stale flips True once a beep is set but the trim step hasn't
    run against it - desktop re-trims on its next sync pull, this just
    flags the video as behind in the meantime. No storage needed here,
    trim_stale reads purely from processed/beep_time."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRBEEPQUEUE00000002"
    _seed_mirror_with_video(
        client,
        match_id,
        "trim-stale",
        processed={"beep": True, "trim": False, "shot_detect": False},
    )

    resp = client.get(f"/api/matches/{match_id}/match/beep-queue")
    assert resp.status_code == 200, resp.text
    item = resp.json()["stages"][0]["items"][0]
    assert item["trim_stale"] is True


# Beep snippet serving endpoints (mobile beep review, slice 3, Task 4).


def test_beep_snippet_endpoints_serve_pushed_artifacts(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict[str, object]],
) -> None:
    """Both snippet endpoints serve what desktop pushed - peaks as JSON,
    audio as a 200/307 media response - once both R2 objects exist."""
    client, sender, captured = hosted_app_with_storage
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRBEEPSNIP000000001"
    video_id = _seed_mirror_with_video(client, match_id, "snippet-serve")

    resp = client.get(f"/api/matches/{match_id}/match/beep-queue")
    assert resp.status_code == 200, resp.text  # forces the tenant/storage to resolve

    storage = captured["storage"]
    base = f"matches/{match_id}/shooters/alice/beep_review/{video_id}"
    peaks_doc = {
        "snippet_start": 1.0,
        "duration": 10.0,
        "bins": 4,
        "peaks": [0.1, 0.9, 0.2, 0.1],
        "beep_time": 2.0,
        "candidates": [{"time": 2.0, "confidence": 0.4}],
    }
    storage.write_bytes(f"{base}.m4a", b"fake-aac-bytes")
    storage.write_bytes(f"{base}.peaks.json", json.dumps(peaks_doc).encode())

    peaks = client.get(
        f"/api/matches/{match_id}/shooters/alice/stages/1/videos/{video_id}/beep-snippet/peaks"
    )
    assert peaks.status_code == 200, peaks.text
    assert peaks.json()["snippet_start"] == 1.0

    audio = client.get(
        f"/api/matches/{match_id}/shooters/alice/stages/1/videos/{video_id}/beep-snippet/audio",
        follow_redirects=False,
    )
    assert audio.status_code in (200, 307), audio.text


def test_beep_snippet_peaks_reflects_rewritten_object(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict[str, object]],
) -> None:
    """Desktop rewrites the peaks object under the same key when it
    regenerates a snippet (beep_time/candidates change) and re-pushes -
    the endpoint must read through to storage each request, not mirror
    once and serve a stale local copy forever."""
    client, sender, captured = hosted_app_with_storage
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRBEEPSNIP000000003"
    video_id = _seed_mirror_with_video(client, match_id, "snippet-rewrite")

    resp = client.get(f"/api/matches/{match_id}/match/beep-queue")
    assert resp.status_code == 200, resp.text  # forces the tenant/storage to resolve

    storage = captured["storage"]
    base = f"matches/{match_id}/shooters/alice/beep_review/{video_id}"
    first_doc = {
        "snippet_start": 1.0,
        "duration": 10.0,
        "bins": 4,
        "peaks": [0.1, 0.9, 0.2, 0.1],
        "beep_time": 2.0,
        "candidates": [{"time": 2.0, "confidence": 0.4}],
    }
    storage.write_bytes(f"{base}.m4a", b"fake-aac-bytes")
    storage.write_bytes(f"{base}.peaks.json", json.dumps(first_doc).encode())

    peaks_url = f"/api/matches/{match_id}/shooters/alice/stages/1/videos/{video_id}/beep-snippet/peaks"
    first = client.get(peaks_url)
    assert first.status_code == 200, first.text
    assert first.json()["snippet_start"] == 1.0

    # Desktop regenerates the snippet and re-pushes under the SAME key.
    second_doc = dict(first_doc, snippet_start=5.5, beep_time=6.0)
    storage.write_bytes(f"{base}.peaks.json", json.dumps(second_doc).encode())

    second = client.get(peaks_url)
    assert second.status_code == 200, second.text
    assert second.json()["snippet_start"] == 5.5


def test_beep_snippet_404_when_absent(
    hosted_env: str,
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict[str, object]],
) -> None:
    """Nothing pushed yet -> both endpoints 404 beep_snippet_not_available."""
    client, sender, captured = hosted_app_with_storage
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRBEEPSNIP000000002"
    video_id = _seed_mirror_with_video(client, match_id, "snippet-missing")

    audio = client.get(
        f"/api/matches/{match_id}/shooters/alice/stages/1/videos/{video_id}/beep-snippet/audio"
    )
    assert audio.status_code == 404
    assert audio.json()["detail"] == "beep_snippet_not_available"

    peaks = client.get(
        f"/api/matches/{match_id}/shooters/alice/stages/1/videos/{video_id}/beep-snippet/peaks"
    )
    assert peaks.status_code == 404
    assert peaks.json()["detail"] == "beep_snippet_not_available"
