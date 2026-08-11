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
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith import match_model
from splitsmith.db import MatchRow, ProjectStateStore, RecentProjectRow, User, create_engine, sessionmaker
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
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


def test_mirror_still_blocks_audit_put(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The full audit PUT stays desktop-owned - only accept/attention are exempt."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JMIRRTRIAGEGATE000000003"
    _seed_mirror(client, match_id, "gate-triage-blocked")
    resp = client.put(f"/api/matches/{match_id}/shooters/alice/stages/1/audit", json={})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "read_only_mirror"


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
