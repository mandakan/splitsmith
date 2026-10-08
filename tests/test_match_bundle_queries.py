"""Triage, triage summary and beep-queue load the match in a fixed number of queries (#1181).

Each of these routes rebuilt the match from scratch per request: a
``state_docs`` query per shooter for the project doc, another per shooter
for the audit docs (triage), and the first shooter's doc a second time for
the threshold (beep-queue). The match bundle loads the match, every project
doc and every audit doc in one query, so each route is two ``state_docs``
SELECTs (the match through ``_resolve_match_context``, then the bundle)
whatever the shooter count. Content stays pinned by ``test_triage_api.py``
and the beep-queue tests in ``test_ui_server.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy import select as _select
from sqlalchemy.engine import Engine

from splitsmith.db import ProjectStateStore, User, create_engine, sessionmaker
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from tests.hosted_helpers import _CapturingSender, login

OWNER = "owner@example.com"


def _create_match(client: TestClient) -> str:
    resp = client.post(
        "/api/match/create-manual",
        json={
            "name": "Bundle",
            "stages": [
                {"stage_number": 1, "stage_name": "S1"},
                {"stage_number": 2, "stage_name": "S2"},
            ],
            "primary_shooter": {"name": "Anton"},
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


def _add_shooter(db_url: str, uid: str, match_id: str, slug: str) -> None:
    """A shooter with footage on both stages and an audit doc on stage 1."""
    sf = sessionmaker(create_engine(db_url))
    store = ProjectStateStore(sf, user_id=uid)

    async def _seed() -> None:
        match_doc, version = await store.load_match(match_id)
        assert match_doc is not None
        match_doc["shooters"] = [*match_doc["shooters"], slug]
        await store.save_match(match_id, match_doc, expected_version=version)
        project = MatchProject(name=slug, competitor_name=slug.title())
        project.stages = [
            StageEntry(
                stage_number=n,
                stage_name=f"S{n}",
                time_seconds=10.0 + n,
                videos=[
                    StageVideo(
                        path=Path(f"raw/{slug}{n}.mp4"), role="primary", beep_time=5.0, beep_confidence=0.99
                    )
                ],
            )
            for n in (1, 2)
        ]
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)
        audit = {"beep_time": 5.0, "shots": [{"shot_number": 1, "ms_after_beep": 500}], "audit_events": []}
        await store.save_audit(match_id, slug, 1, audit, expected_version=0)

    asyncio.run(_seed())


@pytest.fixture
def state_docs_selects():
    """Count SELECTs against ``state_docs`` across the process while armed."""
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT") and "FROM state_docs" in statement:
            seen.append(statement)

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", _record)


def _count(client: TestClient, counter: list[str], path: str) -> int:
    counter.clear()
    resp = client.get(path)
    assert resp.status_code == 200, resp.text
    return len(counter)


@pytest.mark.parametrize("route", ["match/triage", "match/triage/summary", "match/beep-queue"])
def test_route_loads_the_match_in_two_state_docs_queries_whatever_the_shooter_count(
    route: str,
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    state_docs_selects: list[str],
) -> None:
    client, sender = hosted_app
    login(client, sender, OWNER)
    uid = _user_id(hosted_env, OWNER)
    match_id = _create_match(client)
    _add_shooter(hosted_env, uid, match_id, "bea")

    with_two = _count(client, state_docs_selects, f"/api/matches/{match_id}/{route}")

    _add_shooter(hosted_env, uid, match_id, "carl")
    with_three = _count(client, state_docs_selects, f"/api/matches/{match_id}/{route}")

    # The match doc (``_resolve_match_context``) and the bundle.
    assert with_two == with_three == 2, (with_two, with_three, state_docs_selects)


def _add_corrupt_shooter(db_url: str, uid: str, match_id: str, slug: str) -> None:
    """A shooter whose project doc does not validate as a MatchProject."""
    sf = sessionmaker(create_engine(db_url))
    store = ProjectStateStore(sf, user_id=uid)

    async def _seed() -> None:
        match_doc, version = await store.load_match(match_id)
        assert match_doc is not None
        match_doc["shooters"] = [*match_doc["shooters"], slug]
        await store.save_match(match_id, match_doc, expected_version=version)
        await store.save_project(match_id, slug, {"stages": "not-a-list"}, expected_version=0)

    asyncio.run(_seed())


@pytest.mark.parametrize("route", ["match/triage", "match/triage/summary", "match/beep-queue"])
def test_a_corrupt_project_doc_skips_that_shooter_not_the_match(
    route: str, hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    """The per-shooter loops skipped a shooter whose doc would not load and
    answered for the rest; the bundle must keep that isolation, or one bad
    doc takes the whole Overview down."""
    client, sender = hosted_app
    login(client, sender, OWNER)
    uid = _user_id(hosted_env, OWNER)
    match_id = _create_match(client)
    _add_shooter(hosted_env, uid, match_id, "bea")
    _add_corrupt_shooter(hosted_env, uid, match_id, "broken")

    resp = client.get(f"/api/matches/{match_id}/{route}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    if route == "match/triage":
        assert {c["slug"] for c in body["cells"]} >= {"bea"}
        assert "broken" not in {c["slug"] for c in body["cells"]}
    elif route == "match/beep-queue":
        assert {i["slug"] for g in body["stages"] for i in g["items"]} == {"bea"}
    else:
        assert body["flagged_count"] == 0


def test_beep_queue_content_is_unchanged_by_the_bundle(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    """The bundled handler must list the same items with the same statuses
    as the per-shooter loads did: Bea's two primaries are detected above
    the threshold but unreviewed; Anton has no footage and no items."""
    client, sender = hosted_app
    login(client, sender, OWNER)
    uid = _user_id(hosted_env, OWNER)
    match_id = _create_match(client)
    _add_shooter(hosted_env, uid, match_id, "bea")

    resp = client.get(f"/api/matches/{match_id}/match/beep-queue")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = [
        (i["slug"], i["stage_number"], i["status"], i["shooter_name"])
        for g in body["stages"]
        for i in g["items"]
    ]
    assert items == [("bea", 1, "unreviewed", "Bea"), ("bea", 2, "unreviewed", "Bea")]
    assert body["pending_count"] == 2
    assert body["total_items"] == 2


def test_triage_content_is_unchanged_by_the_bundle(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    client, sender = hosted_app
    login(client, sender, OWNER)
    uid = _user_id(hosted_env, OWNER)
    match_id = _create_match(client)
    _add_shooter(hosted_env, uid, match_id, "bea")

    resp = client.get(f"/api/matches/{match_id}/match/triage")

    assert resp.status_code == 200, resp.text
    cells = {(c["slug"], c["stage_number"]): c for c in resp.json()["cells"]}
    anton = next(slug for slug, _ in cells if slug != "bea")
    assert cells[(anton, 1)]["status"] == "todo"
    assert cells[("bea", 1)]["status"] == "in_progress"  # audit doc without a save event
    assert cells[("bea", 1)]["shot_count"] == 1
    assert cells[("bea", 2)]["status"] == "ready"  # footage and a time, no audit yet
    assert cells[("bea", 2)]["shot_count"] == 0
    assert resp.json()["flagged_count"] == 0


def test_coach_get_adds_no_state_docs_query_for_events(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    state_docs_selects: list[str],
) -> None:
    """Stage events ride the audit doc the coach GET already loads: seeding,
    moving flags and the summary add no ``state_docs`` read."""
    client, sender = hosted_app
    login(client, sender, OWNER)
    uid = _user_id(hosted_env, OWNER)
    match_id = _create_match(client)
    _add_shooter(hosted_env, uid, match_id, "bea")
    path = f"/api/matches/{match_id}/shooters/bea/stages/1/coach"
    first = _count(client, state_docs_selects, path)  # seeds and saves
    second = _count(client, state_docs_selects, path)  # steady state
    assert second <= first
    # The route made 10 before stage events (measured on the branch head
    # before the coach GET learned about them). Events add none; the video
    # versions' roster lookup is paid for by ``_video_trim_anchor`` now
    # resolving the shooter root once instead of twice, hence 9.
    assert second <= 9, (first, second, state_docs_selects)
