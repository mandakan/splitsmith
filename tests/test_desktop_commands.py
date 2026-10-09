"""Desktop command queue, hosted side (#1100 S1, spec 2026-09-28).

The phone requests, lists and cancels under the match alias; the desktop
claims, heartbeats and completes under /api/sync with its bearer. These
run against the hosted app on SQLite, the same fixture the sync routes use.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from splitsmith import match_model
from splitsmith.audit_revision import audit_revision
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.capabilities import REVIEW, required_capability
from tests.hosted_helpers import _CapturingSender, login, seed_match

from .test_sync_api import CREATE_URL, _bearer_for, _db_url_for, _put_doc

EMAIL = "owner@example.com"
MATCH = "m1"
SLUG = "anna"
PHONE = f"/api/matches/{MATCH}/match/desktop-commands"


def _mirror(client: TestClient, *, beep: float | None = 12.0) -> None:
    """A desktop-synced match with one shooter and one stage."""
    assert client.post(CREATE_URL, json={"match_id": MATCH, "name": "Match 1"}).status_code == 200
    match = match_model.Match(match_id=MATCH, name="Match 1", shooters=[SLUG], stages=[])
    assert (
        _put_doc(client, MATCH, "match", body=match.model_dump(mode="json"), expected_version=0).status_code
        == 200
    )
    video = StageVideo(path="raw/s1.mp4", role="primary", stage_number=1, beep_time=beep)
    project = MatchProject(
        name="Match 1",
        stages=[StageEntry(stage_number=1, stage_name="S1", time_seconds=10.0, videos=[video])],
    )
    resp = _put_doc(
        client, MATCH, f"project/{SLUG}", body=project.model_dump(mode="json"), expected_version=0
    )
    assert resp.status_code == 200, resp.text


def _request(client: TestClient, stage: int = 1):
    return client.post(PHONE, json={"kind": "shot_detect", "slug": SLUG, "stage_number": stage})


def _store(client: TestClient):
    """The store for EMAIL's user, for lease tests that need a clock."""
    from sqlalchemy import select

    from splitsmith.db import create_engine, sessionmaker, tenant_session_factory
    from splitsmith.db.desktop_commands import DesktopCommandStore
    from splitsmith.db.models import User

    sf = sessionmaker(create_engine(_db_url_for(client)))

    async def _uid() -> str:
        async with sf() as s:
            return (await s.execute(select(User).where(User.email == EMAIL))).scalar_one().id

    uid = asyncio.run(_uid())
    return DesktopCommandStore(tenant_session_factory(sf, uid), user_id=uid)


def test_the_request_routes_are_review_actions_a_mirror_may_take() -> None:
    assert required_capability("POST", "match/desktop-commands") == REVIEW
    assert required_capability("POST", "match/desktop-commands/01ABC/cancel") == REVIEW


def test_a_phone_request_is_queued_once_and_listed(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)

    first = _request(client)
    assert first.status_code == 201, first.text
    body = first.json()
    assert (body["kind"], body["slug"], body["stage_number"], body["status"]) == (
        "shot_detect",
        SLUG,
        1,
        "pending",
    )
    assert body["args"] == {"reset": True}
    # No audit doc yet: the revision of "no doc" is what the desktop checks.
    assert body["expected_revision"] == audit_revision(None)

    again = _request(client)
    assert again.status_code == 200 and again.json()["id"] == body["id"]

    listing = client.get(PHONE).json()
    assert [c["id"] for c in listing["commands"]] == [body["id"]]
    assert listing["presence"] == {"linked": False, "last_seen_at": None, "around": False}


def test_a_hosted_native_match_gets_no_commands(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    seed_match(_db_url_for(client), EMAIL, "native")
    resp = client.post(
        "/api/matches/native/match/desktop-commands",
        json={"kind": "shot_detect", "slug": SLUG, "stage_number": 1},
    )
    assert resp.status_code == 409 and resp.json()["detail"] == "not_a_mirror"


def test_the_stage_is_checked_before_queueing(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client, beep=None)
    resp = _request(client)
    assert resp.status_code == 400 and "beep" in resp.json()["detail"]
    assert _request(client, stage=9).status_code == 404
    bad = client.post(PHONE, json={"kind": "render_everything", "slug": SLUG, "stage_number": 1})
    assert bad.status_code == 422


def test_a_render_upload_is_queued_with_its_validated_request(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    resp = _render_upload(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["kind"], body["slug"], body["stage_number"]) == ("render_upload", SLUG, None)
    assert body["expected_revision"] is None
    # Stored as the full, defaulted request, so the desktop renders what the
    # phone saw rather than its own defaults.
    assert body["args"]["request"]["youtube_upload"] is True
    assert body["args"]["request"]["head_pad_seconds"] is not None
    # One per shooter at a time.
    again = _render_upload(client)
    assert again.status_code == 200 and again.json()["id"] == body["id"]


def test_a_render_upload_must_upload_an_mp4(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    base = {"stage_numbers": [1], "output_format": "mp4", "youtube_sidecar": True}
    for request in (
        {**base, "youtube_upload": False},
        {**base, "output_format": "fcpxml", "youtube_upload": True},
        {"youtube_upload": True},
    ):
        resp = client.post(PHONE, json={"kind": "render_upload", "slug": SLUG, "args": {"request": request}})
        assert resp.status_code == 422, (request, resp.text)
    no_slug = client.post(
        PHONE, json={"kind": "render_upload", "args": {"request": {**base, "youtube_upload": True}}}
    )
    assert no_slug.status_code == 422


def test_a_desktop_sees_claims_heartbeats_and_completes(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _request(client).json()["id"]
    bearer = _bearer_for(client)

    fps = {m["match_id"]: m for m in client.get("/api/sync/fingerprints", headers=bearer).json()["matches"]}
    assert fps[MATCH]["pending_commands"] == 1

    claimed = client.post("/api/sync/commands/claim", json={"match_ids": [MATCH]}, headers=bearer).json()
    assert [c["id"] for c in claimed["commands"]] == [command_id]
    assert claimed["commands"][0]["status"] == "claimed"
    # Claimed: no longer pending, and a second claim gets nothing.
    fps = {m["match_id"]: m for m in client.get("/api/sync/fingerprints", headers=bearer).json()["matches"]}
    assert fps[MATCH]["pending_commands"] == 0
    assert client.post("/api/sync/commands/claim", json={"match_ids": [MATCH]}, headers=bearer).json() == {
        "commands": []
    }

    hb = client.post(
        f"/api/sync/commands/{command_id}/heartbeat", json={"message": "detecting"}, headers=bearer
    )
    assert hb.status_code == 200 and hb.json() == {"cancel_requested": False}

    done = client.post(
        f"/api/sync/commands/{command_id}/complete",
        json={"status": "succeeded", "result": {"shots": 23}},
        headers=bearer,
    )
    assert done.status_code == 200 and done.json()["status"] == "succeeded"
    # Idempotent: a retried completion changes nothing.
    again = client.post(
        f"/api/sync/commands/{command_id}/complete",
        json={"status": "failed", "error": "late"},
        headers=bearer,
    )
    assert again.json()["status"] == "succeeded" and again.json()["result"] == {"shots": 23}
    # A heartbeat on a finished command tells the desktop to stop.
    stale = client.post(f"/api/sync/commands/{command_id}/heartbeat", json={}, headers=bearer)
    assert stale.status_code == 409


def test_claims_are_limited_to_the_matches_a_desktop_watches(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    _request(client)
    bearer = _bearer_for(client)
    resp = client.post("/api/sync/commands/claim", json={"match_ids": ["some-other-match"]}, headers=bearer)
    assert resp.json() == {"commands": []}


def test_cancel_pending_is_immediate_and_claimed_is_asked(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    first = _request(client).json()["id"]
    cancelled = client.post(f"{PHONE}/{first}/cancel").json()
    assert cancelled["status"] == "cancelled"

    second = _request(client).json()["id"]
    assert second != first  # the cancelled one no longer dedupes
    raw = client.post("/api/me/desktop-tokens", json={"name": "rig"}).json()["token"]
    bearer = {"Authorization": f"Bearer {raw}"}
    with TestClient(client.app) as desktop:
        desktop.post("/api/sync/commands/claim", json={"match_ids": [MATCH]}, headers=bearer)
        asked = client.post(f"{PHONE}/{second}/cancel").json()
        assert (asked["status"], asked["cancel_requested"]) == ("claimed", True)
        hb = desktop.post(f"/api/sync/commands/{second}/heartbeat", json={}, headers=bearer)
        assert hb.json() == {"cancel_requested": True}


def test_an_expired_lease_is_claimable_again(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    """A desktop that quit mid-run must not strand the request."""
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _request(client).json()["id"]
    store = _store(client)
    now = datetime.now(UTC)
    assert [c.id for c in asyncio.run(store.claim([MATCH], token_id=None, now=now))] == [command_id]
    assert asyncio.run(store.claim([MATCH], token_id=None, now=now + timedelta(minutes=9))) == []
    assert asyncio.run(store.pending_counts(now=now + timedelta(minutes=11))) == {MATCH: 1}
    again = asyncio.run(store.claim([MATCH], token_id=None, now=now + timedelta(minutes=11)))
    assert [c.id for c in again] == [command_id]


def test_commands_are_per_user(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _request(client).json()["id"]
    client.cookies.clear()
    login(client, sender, "other@example.com")
    assert client.post(f"{PHONE}/{command_id}/cancel").status_code == 404
    bearer = _bearer_for(client)
    assert client.post("/api/sync/commands/claim", json={"match_ids": [MATCH]}, headers=bearer).json() == {
        "commands": []
    }
    assert (
        client.post(
            f"/api/sync/commands/{command_id}/complete", json={"status": "failed"}, headers=bearer
        ).status_code
        == 404
    )


def test_a_sync_token_cannot_request_and_marks_the_desktop_around(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    raw = client.post("/api/me/desktop-tokens", json={"name": "rig"}).json()["token"]
    bearer = {"Authorization": f"Bearer {raw}"}
    with TestClient(client.app) as desktop:
        # The account page mints sync-scoped tokens (#719): the scope gate
        # keeps them on /api/sync, so a desktop cannot queue work for itself.
        refused = desktop.post(
            PHONE, json={"kind": "shot_detect", "slug": SLUG, "stage_number": 1}, headers=bearer
        )
        assert refused.status_code == 403, refused.text
        desktop.get("/api/sync/fingerprints", headers=bearer)
    presence = client.get("/api/me/desktop-presence").json()
    assert presence["linked"] is True and presence["around"] is True and presence["last_seen_at"]


def test_deleting_the_match_on_hosted_sweeps_its_commands(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    _request(client)
    (row,) = [
        p for p in client.get("/api/me/recent-projects").json()["projects"] if p.get("match_id") == MATCH
    ]
    resp = client.post("/api/me/recent-projects/delete", json={"path": row["path"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"]["desktop_commands_removed"] == 1
    assert asyncio.run(_store(client).pending_counts()) == {}


def _render_upload(client: TestClient):
    return client.post(
        PHONE,
        json={
            "kind": "render_upload",
            "slug": SLUG,
            "args": {
                "request": {
                    "stage_numbers": [1],
                    "output_format": "mp4",
                    "youtube_sidecar": True,
                    "youtube_upload": True,
                }
            },
        },
    )


def test_a_lapsed_render_upload_goes_back_only_to_the_desktop_that_held_it(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """Re-running an upload on a second machine would publish it twice."""
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _render_upload(client).json()["id"]
    store = _store(client)
    now = datetime.now(UTC)
    later = now + timedelta(minutes=11)
    assert [c.id for c in asyncio.run(store.claim([MATCH], token_id="tok-a", now=now))] == [command_id]

    assert asyncio.run(store.claim([MATCH], token_id="tok-b", now=later)) == []
    # Counted only for the desktop that may take it: another desktop that
    # saw it would sync every poll and claim nothing, forever.
    assert asyncio.run(store.pending_counts(token_id="tok-b", now=later)) == {}
    assert asyncio.run(store.pending_counts(token_id="tok-a", now=later)) == {MATCH: 1}
    again = asyncio.run(store.claim([MATCH], token_id="tok-a", now=later))
    assert [c.id for c in again] == [command_id]


def test_a_lapsed_shot_detect_is_still_claimable_by_any_desktop(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The pin is per kind: a re-detect is safe anywhere (its revision
    guard refuses a doubled run)."""
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _request(client).json()["id"]
    store = _store(client)
    now = datetime.now(UTC)
    later = now + timedelta(minutes=11)
    asyncio.run(store.claim([MATCH], token_id="tok-a", now=now))
    assert asyncio.run(store.pending_counts(token_id="tok-b", now=later)) == {MATCH: 1}
    assert [c.id for c in asyncio.run(store.claim([MATCH], token_id="tok-b", now=later))] == [command_id]


def test_a_waiting_render_upload_is_claimable_by_any_desktop(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _render_upload(client).json()["id"]
    store = _store(client)
    assert asyncio.run(store.pending_counts(token_id="tok-b")) == {MATCH: 1}
    assert [c.id for c in asyncio.run(store.claim([MATCH], token_id="tok-b"))] == [command_id]


def test_cancelling_a_lapsed_render_upload_ends_it_at_once(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """A pinned command whose desktop never came back must not be stuck:
    only its holder may re-claim it and only its heartbeat would act on
    ``cancel_requested``, so without this it dedupes every later request
    for the shooter forever."""
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _render_upload(client).json()["id"]
    store = _store(client)
    now = datetime.now(UTC)
    later = now + timedelta(minutes=11)
    asyncio.run(store.claim([MATCH], token_id="tok-a", now=now))

    cancelled = asyncio.run(store.cancel(command_id, match_id=MATCH, now=later))
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    # The holder coming back later is told to stop, and a failure or cancel
    # it reports leaves the row cancelled.
    assert asyncio.run(store.heartbeat(command_id, message=None, now=later)) is None
    for status in ("failed", "cancelled"):
        done = asyncio.run(store.complete(command_id, status=status, error="x", now=later))
        assert done is not None and (done.status, done.error) == ("cancelled", None)

    fresh = _render_upload(client).json()
    assert fresh["id"] != command_id
    assert fresh["status"] == "pending"


def test_a_late_success_on_a_lapsed_cancel_is_recorded(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The holder lost contact with hosted, not with YouTube (#1116): its
    upload finished, the user cancelled the lapsed request, and the
    completion that arrived afterwards was dropped. The phone showed
    "Cancelled." for a live video, and asking again uploaded a second
    copy. The video exists, so the row says so."""
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _render_upload(client).json()["id"]
    store = _store(client)
    now = datetime.now(UTC)
    later = now + timedelta(minutes=11)
    back = later + timedelta(minutes=5)
    asyncio.run(store.claim([MATCH], token_id="tok-a", now=now))
    asyncio.run(store.cancel(command_id, match_id=MATCH, now=later))

    video = {"video_id": "abc", "url": "https://youtu.be/abc", "channel_title": "Anna"}
    done = asyncio.run(store.complete(command_id, status="succeeded", result=video, now=back))

    assert done is not None
    assert (done.status, done.result, done.finished_at) == ("succeeded", video, back)
    listed = client.get(PHONE).json()["commands"]
    assert [(c["id"], c["status"], c["result"]) for c in listed] == [(command_id, "succeeded", video)]


def test_a_success_never_reopens_a_request_cancelled_while_waiting(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """No desktop held it, so no desktop can have run it."""
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _render_upload(client).json()["id"]
    store = _store(client)
    asyncio.run(store.cancel(command_id, match_id=MATCH))

    done = asyncio.run(store.complete(command_id, status="succeeded", result={"video_id": "abc"}))

    assert done is not None and (done.status, done.result) == ("cancelled", None)


def test_cancelling_a_live_render_upload_only_asks(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _render_upload(client).json()["id"]
    store = _store(client)
    now = datetime.now(UTC)
    asyncio.run(store.claim([MATCH], token_id="tok-a", now=now))

    asked = asyncio.run(store.cancel(command_id, match_id=MATCH, now=now + timedelta(minutes=9)))
    assert asked is not None
    assert (asked.status, asked.cancel_requested) == ("claimed", True)


def _lapse_lease(client: TestClient, command_id: str) -> None:
    """Move a claimed command's lease into the past, as if its desktop
    quit ten minutes ago (the routes claim with the real clock)."""
    from sqlalchemy import update

    from splitsmith.db import create_engine, sessionmaker
    from splitsmith.db.models import DesktopCommandRow

    sf = sessionmaker(create_engine(_db_url_for(client)))

    async def _run() -> None:
        async with sf() as s:
            await s.execute(
                update(DesktopCommandRow)
                .where(DesktopCommandRow.id == command_id)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(minutes=1))
            )
            await s.commit()

    asyncio.run(_run())


def test_the_fingerprint_poll_counts_a_lapsed_render_upload_only_for_its_holder(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The route passes the bearer's token to the count: a desktop that
    cannot claim a pinned command must not be woken by it every poll."""
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _render_upload(client).json()["id"]
    holder = {
        "Authorization": f"Bearer {client.post('/api/me/desktop-tokens', json={'name': 'a'}).json()['token']}"
    }
    other = {
        "Authorization": f"Bearer {client.post('/api/me/desktop-tokens', json={'name': 'b'}).json()['token']}"
    }
    client.cookies.clear()
    claimed = client.post("/api/sync/commands/claim", json={"match_ids": [MATCH]}, headers=holder).json()
    assert [c["id"] for c in claimed["commands"]] == [command_id]
    _lapse_lease(client, command_id)

    def pending(bearer: dict[str, str]) -> int:
        matches = client.get("/api/sync/fingerprints", headers=bearer).json()["matches"]
        return {m["match_id"]: m for m in matches}[MATCH]["pending_commands"]

    assert pending(holder) == 1
    assert pending(other) == 0
