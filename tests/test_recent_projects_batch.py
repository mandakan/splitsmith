"""The hosted picker loads its detail in a fixed number of queries (#1179).

``GET /api/me/recent-projects?detail=true`` ran 3 + shooters queries per
match, in sequence: 8.9 s on production. The route now loads every match's
docs in one query and every match row in one more, so the count does not
grow with the list. Content is pinned alongside the count: the batch must
derive exactly what the per-match path derived.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy import select as _select
from sqlalchemy import update as _update
from sqlalchemy.engine import Engine

from splitsmith.db import MatchRow, ProjectStateStore, User, create_engine, sessionmaker
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from tests.hosted_helpers import _CapturingSender, login

OWNER = "owner@example.com"


def _create_match(client: TestClient, name: str) -> str:
    resp = client.post(
        "/api/match/create-manual",
        json={
            "name": name,
            "stages": [
                {"stage_number": 1, "stage_name": "S1"},
                {"stage_number": 2, "stage_name": "S2"},
            ],
            "primary_shooter": {"name": f"{name} shooter"},
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["match_id"]


def _user_id(db_url: str, email: str) -> str:
    sf = sessionmaker(create_engine(db_url))

    async def _load() -> str:
        async with sf() as s:
            return (await s.execute(_select(User).where(User.email == email))).scalar_one().id

    return asyncio.run(_load())


def _add_shooter_with_audits(db_url: str, uid: str, match_id: str) -> None:
    """Add a second shooter ``bea`` with footage on both stages and both
    audited (a saved audit event). The first shooter's stage 1 gets an
    audit doc too, but with no footage it can never count as audited --
    ``stage_audit_status`` needs a primary video first -- which is what
    makes the per-shooter average below 2 // 2 = 1, not 3 // 2."""
    sf = sessionmaker(create_engine(db_url))
    store = ProjectStateStore(sf, user_id=uid)

    async def _seed() -> None:
        match_doc, version = await store.load_match(match_id)
        assert match_doc is not None
        (first_slug,) = match_doc["shooters"]
        match_doc["shooters"] = [first_slug, "bea"]
        await store.save_match(match_id, match_doc, expected_version=version)
        bea = MatchProject(name="bea", competitor_name="Bea")
        bea.stages = [
            StageEntry(
                stage_number=n,
                stage_name=f"S{n}",
                time_seconds=10.0 + n,
                videos=[StageVideo(path=Path(f"raw/bea{n}.mp4"), role="primary", beep_time=5.0)],
            )
            for n in (1, 2)
        ]
        await store.save_project(match_id, "bea", bea.model_dump(mode="json"), expected_version=0)
        audited = {
            "beep_time": 5.0,
            "shots": [{"shot_number": 1, "ms_after_beep": 500}],
            "audit_events": [{"kind": "save"}],
        }
        await store.save_audit(match_id, "bea", 1, audited, expected_version=0)
        await store.save_audit(match_id, "bea", 2, audited, expected_version=0)
        await store.save_audit(match_id, first_slug, 1, audited, expected_version=0)

    asyncio.run(_seed())


def _mark_desktop_origin(db_url: str, uid: str, match_id: str) -> None:
    """Flip the match row to ``desktop`` origin, as a row a desktop sync push
    created would carry. ``upsert`` applies ``origin`` on INSERT only, so
    this writes the column directly."""
    sf = sessionmaker(create_engine(db_url))

    async def _flip() -> None:
        async with sf() as s:
            await s.execute(
                _update(MatchRow)
                .where(MatchRow.user_id == uid, MatchRow.match_id == match_id)
                .values(origin="desktop")
            )
            await s.commit()

    asyncio.run(_flip())


@pytest.fixture
def select_counter():
    """Count SELECT statements across every engine in the process while armed."""
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", _record)


def _detail(client: TestClient) -> dict[str, dict]:
    resp = client.get("/api/me/recent-projects?detail=true")
    assert resp.status_code == 200, resp.text
    return {p["match_id"]: p for p in resp.json()["projects"]}


def test_detail_content_is_derived_from_the_batched_docs(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    client, sender = hosted_app
    login(client, sender, OWNER)
    uid = _user_id(hosted_env, OWNER)
    m1 = _create_match(client, "Alpha")
    m2 = _create_match(client, "Bravo")
    m3 = _create_match(client, "Charlie")
    _add_shooter_with_audits(hosted_env, uid, m1)
    _mark_desktop_origin(hosted_env, uid, m2)

    by_id = _detail(client)

    alpha = by_id[m1]
    assert alpha["kind"] == "match"
    assert alpha["name"] == "Alpha"
    assert alpha["origin"] == "hosted"
    assert alpha["shooter_count"] == 2
    assert alpha["stage_count"] == 2
    assert alpha["shooter_names"] == ["Alpha shooter", "Bea"]
    # Bea's two stages are audited, the first shooter's none (no footage):
    # 2 audited across 2 shooters -> 1.
    assert alpha["stages_audited"] == 1
    assert alpha["video_count"] == 2
    assert alpha["status"] == "in_progress"
    # No stage is ready or in progress, so the Continue card points at the
    # first shooter's first stage, which still needs footage.
    assert alpha["next_step"]["kind"] == "footage"
    assert alpha["next_step"]["stage_number"] == 1
    assert alpha["next_step"]["stage_name"] == "S1"

    bravo = by_id[m2]
    assert bravo["origin"] == "desktop"
    assert bravo["shooter_count"] == 1
    assert bravo["stages_audited"] == 0

    charlie = by_id[m3]
    assert charlie["name"] == "Charlie"
    assert charlie["shooter_names"] == ["Charlie shooter"]


def test_query_count_does_not_grow_with_the_number_of_matches(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender], select_counter: list[str]
) -> None:
    client, sender = hosted_app
    login(client, sender, OWNER)
    uid = _user_id(hosted_env, OWNER)
    m1 = _create_match(client, "Alpha")
    _create_match(client, "Bravo")
    _create_match(client, "Charlie")
    _add_shooter_with_audits(hosted_env, uid, m1)

    select_counter.clear()
    _detail(client)
    with_three = len(select_counter)

    _create_match(client, "Delta")
    _create_match(client, "Echo")
    select_counter.clear()
    _detail(client)
    with_five = len(select_counter)

    # Auth (session + user), the recent list, the docs, the match rows: a
    # handful, and the same handful whatever the list holds.
    assert with_three == with_five, (with_three, with_five, select_counter)
    assert with_three <= 6, select_counter
