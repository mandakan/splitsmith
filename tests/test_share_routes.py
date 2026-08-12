"""HTTP-surface tests for the share-token management routes (issue #349).

Tests the GET/POST/DELETE /api/match/shares routes via the
/api/matches/{match_id}/match/shares alias prefix.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete as _delete
from sqlalchemy import select as _select

from splitsmith.db import MatchRow, ProjectStateStore, ShareTokenRow, User, create_engine, sessionmaker
from tests.hosted_helpers import _CapturingSender, login, moto_s3_storage, seed_match

MID = "test-match-abc123"
OTHER_MID = "test-match-xyz999"
SLUG = "anna"


def _url(match_id: str, suffix: str = "") -> str:
    return f"/api/matches/{match_id}/match/shares{suffix}"


# Anonymous requests (no session cookie) are rejected with 401.


def test_anonymous_get_rejected(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, _ = hosted_app
    assert client.get(_url(MID)).status_code == 401


def test_anonymous_post_rejected(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, _ = hosted_app
    assert client.post(_url(MID)).status_code == 401


def test_anonymous_delete_rejected(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, _ = hosted_app
    assert client.delete(_url(MID, "/some-id")).status_code == 401


# POST creates a share: 201, url starts with public_base_url/share/, revoked_at is None.


def test_post_creates_share(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)

    resp = client.post(_url(MID))
    assert resp.status_code == 201
    body = resp.json()
    assert body["url"].startswith("http://localhost:5174/share/")
    assert body["revoked_at"] is None
    assert "id" in body
    assert "created_at" in body


# GET lists shares; after DELETE the revoked share is still returned with revoked_at set.


def test_get_lists_shares_and_includes_revoked(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)

    create_resp = client.post(_url(MID))
    assert create_resp.status_code == 201
    share_id = create_resp.json()["id"]

    list_resp = client.get(_url(MID))
    assert list_resp.status_code == 200
    shares = list_resp.json()["shares"]
    assert len(shares) == 1
    assert shares[0]["id"] == share_id
    assert shares[0]["revoked_at"] is None

    # Revoke via DELETE, then list - revoked share is still present.
    del_resp = client.delete(_url(MID, f"/{share_id}"))
    assert del_resp.status_code == 204

    list_after = client.get(_url(MID))
    shares_after = list_after.json()["shares"]
    assert len(shares_after) == 1
    assert shares_after[0]["revoked_at"] is not None


# DELETE is idempotent: second call on the same share_id returns 204.


def test_delete_is_idempotent(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)

    share_id = client.post(_url(MID)).json()["id"]
    assert client.delete(_url(MID, f"/{share_id}")).status_code == 204
    assert client.delete(_url(MID, f"/{share_id}")).status_code == 204


# DELETE with an unknown share_id returns 404.


def test_delete_unknown_share_id_returns_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)

    assert client.delete(_url(MID, "/no-such-id")).status_code == 404


# POST against a match_id not owned by the user returns 404 (alias middleware ownership gate).


def test_post_unowned_match_id_returns_404(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    # No MatchRow for OTHER_MID - the alias middleware returns 404.
    assert client.post(_url(OTHER_MID)).status_code == 404


# User B cannot list user A's shares (alias middleware blocks by ownership).


def test_user_b_cannot_list_user_a_shares(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app

    login(client, sender, "usera@example.com")
    seed_match(hosted_env, "usera@example.com", MID)
    client.post(_url(MID))  # user A creates a share

    client.cookies.clear()
    login(client, sender, "userb@example.com")

    # User B doesn't own MID - alias middleware returns 404.
    assert client.get(_url(MID)).status_code == 404


# User B cannot revoke user A's shares (alias middleware blocks by ownership).


def test_user_b_cannot_revoke_user_a_share(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app

    login(client, sender, "usera@example.com")
    seed_match(hosted_env, "usera@example.com", MID)
    share_id = client.post(_url(MID)).json()["id"]

    client.cookies.clear()
    login(client, sender, "userb@example.com")

    # User B doesn't own MID - alias middleware returns 404.
    assert client.delete(_url(MID, f"/{share_id}")).status_code == 404


# ----------------------------------------------------------------------
# Task 5: anonymous, token-authorized read path (_share_alias middleware)
# ----------------------------------------------------------------------

NOT_FOUND = {"detail": "not found"}


def _seed_state_docs(db_url: str, user_email: str, match_id: str, slug: str) -> None:
    """Insert the match + per-shooter project state docs the read handlers
    load, as the user identified by ``user_email`` (call after login)."""
    from splitsmith import match_model
    from splitsmith.match_project import MatchProject

    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        match = match_model.Match(
            match_id=match_id,
            name=f"Test match {match_id}",
            shooters=[slug],
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")],
        )
        await store.save_match(match_id, match.model_dump(mode="json"), expected_version=0)
        project = MatchProject(name="Anna")
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)

    asyncio.run(_seed())


def _expire_token(db_url: str, token: str) -> None:
    """Force a share token's ``expires_at`` into the past."""
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _expire() -> None:
        async with sf() as s:
            row = (await s.execute(_select(ShareTokenRow).where(ShareTokenRow.token == token))).scalar_one()
            row.expires_at = datetime.now(UTC) - timedelta(days=1)
            await s.commit()

    asyncio.run(_expire())


def _create_share_token(client: TestClient, match_id: str) -> str:
    """Create a share via the owner management route and return the raw token."""
    resp = client.post(_url(match_id))
    assert resp.status_code == 201, f"share create failed: {resp.status_code} {resp.text}"
    return resp.json()["url"].rsplit("/share/", 1)[1]


def _share_url(token: str, rest: str) -> str:
    return f"/api/share/{token}/{rest}"


def _setup_shared_match(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> str:
    """Login as owner, seed a match + shooter + state docs, mint a share
    token, then drop the session cookie so the client is anonymous. Returns
    the raw token."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
    token = _create_share_token(client, MID)
    client.cookies.clear()
    return token


def _seed_state_docs_with_scan_dir(
    db_url: str, user_email: str, match_id: str, slug: str, scan_dir: str
) -> None:
    """Seed match + project state docs with last_scanned_dir set on the project."""
    from splitsmith import match_model
    from splitsmith.match_project import MatchProject

    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        match = match_model.Match(
            match_id=match_id,
            name=f"Test match {match_id}",
            shooters=[slug],
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")],
        )
        await store.save_match(match_id, match.model_dump(mode="json"), expected_version=0)
        project = MatchProject(name="Anna", last_scanned_dir=scan_dir)
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)

    asyncio.run(_seed())


def _delete_match_row(db_url: str, user_email: str, match_id: str) -> None:
    """Delete the MatchRow for match_id directly (simulates a deleted match)."""
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _del() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        async with sf() as s:
            await s.execute(
                _delete(MatchRow).where(
                    MatchRow.user_id == user_id,
                    MatchRow.match_id == match_id,
                )
            )
            await s.commit()

    asyncio.run(_del())


# -- uniform 404s --------------------------------------------------------


def test_share_unknown_token_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    resp = client.get(_share_url("garbage-token", "match/shooters"))
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND
    assert not client.cookies


def test_share_revoked_token_404_on_every_path(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
    create = client.post(_url(MID))
    token = create.json()["url"].rsplit("/share/", 1)[1]
    share_id = create.json()["id"]
    assert client.delete(_url(MID, f"/{share_id}")).status_code == 204
    client.cookies.clear()

    for rest in (
        "match/shooters",
        f"shooters/{SLUG}/project",
        f"shooters/{SLUG}/stages/1/coach",
        f"shooters/{SLUG}/videos/stream",
        "match/stage/1/compare",
        f"match/shooters/{SLUG}/videos/stream",
    ):
        resp = client.get(_share_url(token, rest))
        assert resp.status_code == 404, rest
        assert resp.json() == NOT_FOUND, rest


def test_share_expired_token_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    token = _setup_shared_match(hosted_env, hosted_app)
    _expire_token(hosted_env, token)
    client, _ = hosted_app
    resp = client.get(_share_url(token, "match/shooters"))
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


@pytest.mark.parametrize(
    "rest",
    [
        "match",  # no such whitelisted rest
        f"shooters/{SLUG}/videos",  # prefix of a whitelisted path
        "me",
        f"shooters/{SLUG}/project/extra",
    ],
)
def test_share_valid_token_non_whitelisted_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    rest: str,
) -> None:
    token = _setup_shared_match(hosted_env, hosted_app)
    client, _ = hosted_app
    resp = client.get(_share_url(token, rest))
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


def test_share_whitelisted_non_get_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    token = _setup_shared_match(hosted_env, hosted_app)
    client, _ = hosted_app
    for rest in (
        "match/shooters",
        "match/stage/1/compare",
        f"match/shooters/{SLUG}/videos/stream",
    ):
        resp = client.post(_share_url(token, rest))
        assert resp.status_code == 404, rest
        assert resp.json() == NOT_FOUND, rest


# -- happy paths ---------------------------------------------------------


def test_share_match_shooters_happy_path(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    token = _setup_shared_match(hosted_env, hosted_app)
    client, _ = hosted_app
    resp = client.get(_share_url(token, "match/shooters"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    slugs = [entry["slug"] for entry in body["shooters"]]
    assert slugs == [SLUG]
    # Authorization was the token alone - no session cookie was set.
    assert not client.cookies


def test_share_project_happy_path(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    token = _setup_shared_match(hosted_env, hosted_app)
    client, _ = hosted_app
    resp = client.get(_share_url(token, f"shooters/{SLUG}/project"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Anna"
    assert not client.cookies


def _seed_stage_audit(db_url: str, user_email: str, match_id: str, slug: str, doc: dict) -> None:
    """Insert one stage-1 audit doc into state_docs, plus a stage entry on
    the shooter project so the coach route's stage lookup succeeds."""
    from splitsmith.match_project import MatchProject, StageEntry

    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        project_doc, version = await store.load_project(match_id, slug)
        project = MatchProject.model_validate(project_doc)
        project.stages = [StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=30.0)]
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=version)
        await store.save_audit(match_id, slug, 1, doc, expected_version=0)

    asyncio.run(_seed())


def _seed_stage_video_and_audit(db_url: str, user_email: str, match_id: str, slug: str, doc: dict) -> None:
    """Like ``_seed_stage_audit``, but the stage entry also carries a
    primary video with ``beep_time`` set - the compare endpoint's
    ``video_ref``/``shots`` blocks are both gated on a primary beep, so
    compare-happy-path tests need this instead of the coach helper above."""
    from splitsmith.match_project import MatchProject, StageEntry, StageVideo

    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        project_doc, version = await store.load_project(match_id, slug)
        project = MatchProject.model_validate(project_doc)
        project.stages = [
            StageEntry(
                stage_number=1,
                stage_name="Stage 1",
                time_seconds=30.0,
                videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
            )
        ]
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=version)
        await store.save_audit(match_id, slug, 1, doc, expected_version=0)

    asyncio.run(_seed())


def _load_stage_audit(db_url: str, user_email: str, match_id: str, slug: str) -> dict:
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _load() -> dict:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        store = ProjectStateStore(sf, user_id=user_id)
        doc, _version = await store.load_audit(match_id, slug, 1)
        assert doc is not None
        return doc

    return asyncio.run(_load())


def test_share_coach_read_classifies_in_memory_without_persisting(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """#775: a share-token coach read of a legacy (unclassified) doc gets
    classified shots in the response but must not write the heal back -
    anonymous readers never mutate owner state. #778: the same read also
    strips ``coaching_note``/``improvement_flag`` - a coach's private
    annotations are not part of the anonymous viewer's surface - while an
    owner read of the same stage still sees the real values (the strip is
    share-scoped, not global)."""
    token = _setup_shared_match(hosted_env, hosted_app)
    legacy_doc = {
        "stage_number": 1,
        "shots": [
            {
                "shot_number": 1,
                "ms_after_beep": 1500,
                "coaching_note": "private!",
                "improvement_flag": True,
            },
            {"shot_number": 2, "ms_after_beep": 1800},
        ],
    }
    _seed_stage_audit(hosted_env, "owner@example.com", MID, SLUG, legacy_doc)

    client, sender = hosted_app
    resp = client.get(_share_url(token, f"shooters/{SLUG}/stages/1/coach"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [s["interval_class"] for s in body["shots"]] == ["first_shot", "split"]
    assert all(s["coaching_note"] is None for s in body["shots"])
    assert all(s["improvement_flag"] is False for s in body["shots"])

    stored = _load_stage_audit(hosted_env, "owner@example.com", MID, SLUG)
    assert all(s.get("interval_class") is None for s in stored["shots"])

    # The strip is share-scoped, not global: an owner read of the same
    # stage still returns the real coaching_note/improvement_flag.
    login(client, sender, "owner@example.com")
    owner_resp = client.get(f"/api/matches/{MID}/shooters/{SLUG}/stages/1/coach")
    assert owner_resp.status_code == 200, owner_resp.text
    owner_shots = owner_resp.json()["shots"]
    assert owner_shots[0]["coaching_note"] == "private!"
    assert owner_shots[0]["improvement_flag"] is True
    client.cookies.clear()


# -- stage compare (#700 task 3) -----------------------------------------


def test_share_stage_compare_happy_path(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The compare payload needs no share-conditional stripping (#700
    design doc, backend section 3): it carries the decided minimal
    surface already. Assert that surface directly - ``video_ref``
    present-or-null, ``video_path`` never present, and shot dicts carry
    exactly the four documented keys."""
    token = _setup_shared_match(hosted_env, hosted_app)
    doc = {
        "stage_number": 1,
        "beep_time": 5.0,
        "shots": [
            {"shot_number": 1, "time": 5.5, "ms_after_beep": 500, "source": "detected"},
            {"shot_number": 2, "time": 5.9, "ms_after_beep": 900, "source": "manual"},
        ],
    }
    _seed_stage_video_and_audit(hosted_env, "owner@example.com", MID, SLUG, doc)

    client, _ = hosted_app
    resp = client.get(_share_url(token, "match/stage/1/compare"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    (shooter,) = body["shooters"]
    assert shooter["slug"] == SLUG
    assert "video_ref" in shooter
    assert shooter["video_ref"] is None or isinstance(shooter["video_ref"], str)
    assert "video_path" not in shooter
    assert shooter["shots"], "expected at least one shot in the response"
    for shot in shooter["shots"]:
        assert set(shot.keys()) == {"shot_number", "time_after_beep", "source", "interval_class"}
    assert not client.cookies


def test_share_stream_trim_happy_path(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end anonymous streaming happy path (#700): share token ->
    ``_share_alias`` -> ``stream_shooter_video``'s logical-ref fallback ->
    ``storage.exists`` -> 307 presigned redirect. Also ties the compare
    payload's ``video_ref`` for the same shooter to the ref actually
    streamed, so the two surfaces are proven consistent end-to-end."""
    client, sender = hosted_app
    with moto_s3_storage(monkeypatch, "share-stream-happy-path-bucket") as captured:
        login(client, sender, "owner@example.com")
        seed_match(hosted_env, "owner@example.com", MID)
        _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
        doc = {
            "stage_number": 1,
            "beep_time": 5.0,
            "shots": [{"shot_number": 1, "time": 5.5, "ms_after_beep": 500, "source": "detected"}],
        }
        _seed_stage_video_and_audit(hosted_env, "owner@example.com", MID, SLUG, doc)

        # Drive one authenticated request so the per-request tenant storage
        # builds and is captured for the direct write below (same trick as
        # test_media_presign_serving.py's s3_stream_client fixture).
        client.get("/api/me/recent-projects")
        storage = captured["storage"]

        # video_id formula: hashlib.blake2s("<path>#<stage_number>",
        # digest_size=6) - same derivation _seed_stage_video_and_audit's
        # StageVideo(path=Path("raw/v.mp4"), ...) on stage 1 produces.
        video_id = hashlib.blake2s(b"raw/v.mp4#1", digest_size=6).hexdigest()
        trim_name = f"stage1_cam_{video_id}_trimmed.mp4"
        trim_key = f"matches/{MID}/shooters/{SLUG}/trimmed/{trim_name}"
        storage.write_bytes(trim_key, b"TRIMDATA")

        token = _create_share_token(client, MID)
        client.cookies.clear()

        resp = client.get(
            _share_url(token, f"match/shooters/{SLUG}/videos/stream"),
            params={"path": f"trimmed/{trim_name}", "kind": "auto"},
        )
        assert resp.status_code == 307, resp.text
        location = resp.headers["location"]
        # Presigned URL: the key (and its unique video-id segment) shows up
        # in the path, and a signature query param is present.
        assert trim_name in location
        assert "Signature=" in location
        assert not client.cookies

        # Tie the stream ref back to the compare payload for the same
        # shooter/stage - the two surfaces must agree end-to-end.
        compare_resp = client.get(_share_url(token, "match/stage/1/compare"))
        assert compare_resp.status_code == 200, compare_resp.text
        (shooter,) = compare_resp.json()["shooters"]
        assert shooter["video_ref"] == f"trimmed/{trim_name}"


def test_share_stream_malformed_ref_matches_unknown_token_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """A traversal-shaped ``path`` fails the ref grammar before any
    filesystem/storage touch, and the middleware's uniform-404 seam
    (server.py:6359-6364) means it is indistinguishable from an unknown
    token."""
    token = _setup_shared_match(hosted_env, hosted_app)
    client, _ = hosted_app
    resp = client.get(
        _share_url(token, f"match/shooters/{SLUG}/videos/stream"),
        params={"path": "../trimmed/evil.mp4"},
    )
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


def test_share_stream_absent_ref_matches_unknown_token_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """A well-formed ref that resolves to nothing (no such trim) also 404s
    through the same uniform seam, and its body is identical to the
    malformed-ref and unknown-token cases."""
    token = _setup_shared_match(hosted_env, hosted_app)
    client, _ = hosted_app
    resp = client.get(
        _share_url(token, f"match/shooters/{SLUG}/videos/stream"),
        params={"path": "trimmed/does-not-exist.mp4"},
    )
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


def test_share_url_match_id_is_ignored(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The match read is driven entirely by the token row; the URL carries
    no match id, so there is no URL surface to influence which match loads.
    A second, unshared match owned by the same user stays unreachable."""
    token = _setup_shared_match(hosted_env, hosted_app)
    # Seed a second, un-shared match owned by the same owner.
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", OTHER_MID)
    _seed_state_docs(hosted_env, "owner@example.com", OTHER_MID, "bob")
    client.cookies.clear()
    # The share token resolves to MID (shooter "anna"), never OTHER_MID.
    resp = client.get(_share_url(token, "match/shooters"))
    assert resp.status_code == 200, resp.text
    slugs = [entry["slug"] for entry in resp.json()["shooters"]]
    assert slugs == [SLUG]


# -- path disclosure hardening ------------------------------------------


def test_share_match_root_blanked_for_anonymous_viewer(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """match_root is blank on the anonymous share path; populated for the owner."""
    token = _setup_shared_match(hosted_env, hosted_app)
    client, sender = hosted_app

    # Via share: match_root must not expose a server path.
    resp = client.get(_share_url(token, "match/shooters"))
    assert resp.status_code == 200
    assert resp.json()["match_root"] in ("", None)

    # Via owner session: match_root is populated.
    login(client, sender, "owner@example.com")
    resp = client.get(f"/api/matches/{MID}/match/shooters")
    assert resp.status_code == 200
    assert resp.json()["match_root"]
    client.cookies.clear()


def test_share_last_scanned_dir_nulled_for_anonymous_viewer(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """last_scanned_dir is None on the anonymous share path; populated for the owner."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs_with_scan_dir(hosted_env, "owner@example.com", MID, SLUG, "/owner/scan/dir")
    token = _create_share_token(client, MID)
    client.cookies.clear()

    # Via share: last_scanned_dir must be None.
    resp = client.get(_share_url(token, f"shooters/{SLUG}/project"))
    assert resp.status_code == 200
    assert resp.json()["last_scanned_dir"] is None

    # Via owner session: last_scanned_dir is populated.
    login(client, sender, "owner@example.com")
    resp = client.get(f"/api/matches/{MID}/shooters/{SLUG}/project")
    assert resp.status_code == 200
    assert resp.json()["last_scanned_dir"] == "/owner/scan/dir"
    client.cookies.clear()


# -- byte-identity net over the share whitelist (Task 5, #779) ----------


def _dump_state_docs(db_url: str, user_email: str) -> list[tuple]:
    """Serialize every row of the owner's tenant-store tables (state_docs,
    matches, recent_projects) as sorted tuples of all mapped columns -
    byte-identity means nothing changed, version and timestamps included.
    Each tuple is prefixed with a table-name element so rows from
    different tables can never collide."""
    import json

    from sqlalchemy import inspect as sa_inspect

    from splitsmith.db.models import MatchRow, RecentProjectRow, StateDocRow

    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _dump() -> list[tuple]:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        dumped: list[tuple] = []
        for table_name, model in (
            ("state_docs", StateDocRow),
            ("matches", MatchRow),
            ("recent_projects", RecentProjectRow),
        ):
            cols = sorted(c.key for c in sa_inspect(model).mapper.column_attrs)
            async with sf() as s:
                model_rows = (await s.execute(_select(model).where(model.user_id == user_id))).scalars().all()
            dumped.extend(
                (table_name, *(json.dumps(getattr(r, k), default=str, sort_keys=True) for k in cols))
                for r in model_rows
            )
        return sorted(dumped)

    return asyncio.run(_dump())


# One concrete instantiation per _SHARE_PATH_RE alternative (server.py).
# When the whitelist grows an entry, this list must grow one too - the
# assertion below is the promise every share route is write-free.
_SHARE_WHITELIST_INSTANCES = [
    "match/shooters",
    f"shooters/{SLUG}/project",
    f"shooters/{SLUG}/stages/1/coach",
    f"shooters/{SLUG}/coach/distributions",
    f"shooters/{SLUG}/videos/stream",
    "match/stage/1/compare",
    f"match/shooters/{SLUG}/videos/stream",
    "og.png",
    f"og/{SLUG}/1.png",
    "og-meta",
    f"og-meta/{SLUG}/1",
]


def test_share_whitelist_instances_cover_every_alternative() -> None:
    """Couples the net's instance list to the regex: when the whitelist
    grows an alternative without a matching byte-identity instantiation,
    this fails loudly instead of the net silently covering less ground."""
    from splitsmith.ui.server import _SHARE_PATH_RE

    alternatives = _SHARE_PATH_RE.pattern.count("|") + 1
    assert (
        len(_SHARE_WHITELIST_INSTANCES) == alternatives
    ), "share whitelist grew an alternative without a byte-identity instantiation"


@pytest.mark.parametrize("rest", _SHARE_WHITELIST_INSTANCES)
def test_share_whitelist_routes_leave_state_docs_untouched(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    rest: str,
) -> None:
    """#779 test net: walk every whitelisted share shape against a
    seeded match carrying a legacy (unclassified) audit doc - the shape
    known to tempt read paths into healing writes (#775) - and assert
    the owner's state_docs rows are byte-identical before and after."""
    token = _setup_shared_match(hosted_env, hosted_app)
    legacy_doc = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 1500},
            {"shot_number": 2, "ms_after_beep": 1800},
        ],
    }
    _seed_stage_audit(hosted_env, "owner@example.com", MID, SLUG, legacy_doc)

    client, _ = hosted_app
    before = _dump_state_docs(hosted_env, "owner@example.com")
    resp = client.get(_share_url(token, rest))
    # Routes without seeded media legitimately 404/422; the invariant
    # under test is the absence of writes, not the status code.
    assert resp.status_code < 500, f"{rest}: {resp.status_code} {resp.text[:200]}"
    after = _dump_state_docs(hosted_env, "owner@example.com")
    assert after == before, f"share GET {rest!r} mutated state_docs"


def test_share_deleted_match_uniform_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """A live token whose match row was deleted returns the uniform 404, not a leaky body."""
    token = _setup_shared_match(hosted_env, hosted_app)
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    _delete_match_row(hosted_env, "owner@example.com", MID)
    client.cookies.clear()

    resp = client.get(_share_url(token, "match/shooters"))
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


# -- local mode: no share surface ---------------------------------------


def test_share_local_mode_404() -> None:
    from splitsmith.ui.server import create_app

    # Unbound local app: no hosted env, so state.resolve_share_token is None
    # and the whole share surface is a uniform 404.
    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        resp = client.get(_share_url("anything", "match/shooters"))
        assert resp.status_code == 404
        assert resp.json() == NOT_FOUND


def test_share_management_routes_local_mode_404() -> None:
    """GET and POST /api/matches/{id}/match/shares return 404 in local mode."""
    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        assert client.get(_url(MID)).status_code == 404
        assert client.post(_url(MID)).status_code == 404


# -- whitelist regex lock ------------------------------------------------


@pytest.mark.parametrize(
    "rest",
    [
        "match/shooters",
        "shooters/anna/project",
        "shooters/anna/stages/1/coach",
        "shooters/s_ab12/stages/12/coach",
        "shooters/anna/videos/stream",
        "shooters/anna/coach/distributions",
        "shooters/s_ab12/coach/distributions",
        "match/stage/1/compare",
        "match/shooters/some-slug/videos/stream",
        "og.png",
        "og/anna/1.png",
        "og-meta",
        "og-meta/anna/1",
        "og-meta/s_ab12/12",
    ],
)
def test_share_path_re_accepts(rest: str) -> None:
    from splitsmith.ui.server import _SHARE_PATH_RE

    assert _SHARE_PATH_RE.fullmatch(rest) is not None


@pytest.mark.parametrize(
    "rest",
    [
        "",
        "match",
        "match/shooters/",
        "shooters//project",
        "shooters/anna/videos",
        "shooters/anna/project/extra",
        "shooters/a/stages/x/coach",
        "SHOOTERS/a/project",
        "shooters/a/stages/1/coach/distributions",
        "shooters/a/b/project",
        "shooters/anna/stages/1/coach/reclassify",
        "shooters/anna/coach/reclassify",
        "shooters//coach/distributions",
        "shooters/anna/coach/distributions/extra",
        "me",
        "match/shares",
        # match/stage/compare + match/shooters/videos/stream near-misses -
        # the allowlist widened by exactly the two intended shapes, not by
        # a looser pattern.
        "match/stage/x/compare",
        "match/shooters/videos/stream",
        "match/stage/1/compare/extra",
        # og-meta near-misses -- the allowlist widened by exactly the two
        # intended shapes, not by a prefix.
        "og-meta/",
        "og-meta/anna",
        "og-meta/anna/1/extra",
        "og-meta/anna/x",
        "og-meta//1",
        "OG-META",
        "og-metax",
        "og-meta/anna/",
    ],
)
def test_share_path_re_rejects(rest: str) -> None:
    from splitsmith.ui.server import _SHARE_PATH_RE

    assert _SHARE_PATH_RE.fullmatch(rest) is None


def test_share_request_carries_read_scope(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#779: _share_alias must pin the resolved token's scope for the
    duration of the request, and reset it afterwards. Probed from inside
    the request via the store loader the project route calls."""
    from splitsmith.db.project_state import ProjectStateStore
    from splitsmith.db.share_guard import current_share_scope

    token = _setup_shared_match(hosted_env, hosted_app)
    client, sender = hosted_app

    seen: list[str | None] = []
    orig = ProjectStateStore.load_project

    async def probe(self, match_id: str, slug: str):
        seen.append(current_share_scope.get())
        return await orig(self, match_id, slug)

    monkeypatch.setattr(ProjectStateStore, "load_project", probe)

    resp = client.get(_share_url(token, f"shooters/{SLUG}/project"))
    assert resp.status_code == 200, resp.text
    assert seen == ["read"]

    # Owner path: same route, no share scope.
    seen.clear()
    login(client, sender, "owner@example.com")
    owner = client.get(f"/api/matches/{MID}/shooters/{SLUG}/project")
    assert owner.status_code == 200, owner.text
    assert seen == [None]
    client.cookies.clear()
