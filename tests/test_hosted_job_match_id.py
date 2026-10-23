"""A hosted job submitted through the match alias reports its match (#1673).

``PostgresJobBackend.submit`` stamps ``compute_jobs.match_id`` from the
``current_match_id`` ContextVar the alias middleware sets, and the worker
and ``retry`` read the column back. The wire ``Job`` dropped it, so
``GET /api/me/jobs`` and ``GET /api/me/jobs/{id}`` answered
``match_id: null`` for every hosted job.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from splitsmith.db import ComputeJobRow, create_engine, sessionmaker
from tests.hosted_helpers import _CapturingSender, login, seed_match
from tests.test_access_backstop import MID, OWNER, SLUG, _seed_trimmable_stage

OTHER_MID = "brm-other-02"


@pytest.fixture
def deferred(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record each queued payload instead of deferring to Procrastinate.

    Requested before ``hosted_app`` so the stub is in place when
    ``create_app`` builds the deferrer.
    """
    from splitsmith import queue as queue_mod

    calls: list[dict[str, Any]] = []

    def fake_make_deferrer(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        async def _defer(**kw: Any) -> None:
            calls.append(kw)

        return _defer

    monkeypatch.setattr(queue_mod, "make_deferrer", fake_make_deferrer)
    return calls


def _rows(db_url: str) -> list[ComputeJobRow]:
    async def go() -> list[ComputeJobRow]:
        engine = create_engine(db_url)
        async with sessionmaker(engine)() as s:
            rows = list((await s.execute(select(ComputeJobRow).order_by(ComputeJobRow.created_at))).scalars())
        await engine.dispose()
        return rows

    return asyncio.run(go())


def _fail(db_url: str, job_id: str) -> None:
    async def go() -> None:
        engine = create_engine(db_url)
        async with sessionmaker(engine)() as s:
            await s.execute(
                update(ComputeJobRow).where(ComputeJobRow.id == job_id).values(status="failed", error="boom")
            )
            await s.commit()
        await engine.dispose()

    asyncio.run(go())


def _video_id(client: TestClient) -> str:
    resp = client.get(f"/api/matches/{MID}/shooters/{SLUG}/project")
    assert resp.status_code == 200, resp.text
    return resp.json()["stages"][0]["videos"][0]["video_id"]


def _assert_listed_with_match(client: TestClient, job_id: str, match_id: str) -> None:
    listed = {j["id"]: j for j in client.get("/api/me/jobs").json()}
    assert listed[job_id]["match_id"] == match_id
    single = client.get(f"/api/me/jobs/{job_id}")
    assert single.status_code == 200, single.text
    assert single.json()["match_id"] == match_id


@pytest.mark.parametrize("route", ["detect-beep", "beep/review"])
def test_alias_submitted_job_reports_its_match(
    deferred: list[dict[str, Any]],
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    route: str,
) -> None:
    client, sender = hosted_app
    login(client, sender, OWNER)
    seed_match(hosted_env, OWNER, MID)
    _seed_trimmable_stage(hosted_env, OWNER)
    video_id = _video_id(client)

    url = f"/api/matches/{MID}/shooters/{SLUG}/stages/1/videos/{video_id}/{route}"
    resp = client.post(url, json={"reviewed": True}) if route == "beep/review" else client.post(url)
    assert resp.status_code in (200, 202), resp.text

    rows = _rows(hosted_env)
    assert rows, "the route queued no job"
    # The column is written at submit and the queue payload carries it.
    assert {r.match_id for r in rows} == {MID}
    assert {c["match_id"] for c in deferred} == {MID}
    for row in rows:
        _assert_listed_with_match(client, row.id, MID)


def test_retry_rebinds_the_original_match_not_the_retrying_request(
    deferred: list[dict[str, Any]],
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, OWNER)
    seed_match(hosted_env, OWNER, MID)
    seed_match(hosted_env, OWNER, OTHER_MID)
    _seed_trimmable_stage(hosted_env, OWNER)
    video_id = _video_id(client)
    resp = client.post(f"/api/matches/{MID}/shooters/{SLUG}/stages/1/videos/{video_id}/detect-beep")
    assert resp.status_code in (200, 202), resp.text
    (original,) = _rows(hosted_env)
    _fail(hosted_env, original.id)
    deferred.clear()

    # Retried from another match's alias: the new job still belongs to MID.
    retried = client.post(f"/api/matches/{OTHER_MID}/me/jobs/{original.id}/retry")
    assert retried.status_code == 200, retried.text
    new_id = retried.json()["id"]
    assert new_id != original.id
    assert retried.json()["match_id"] == MID
    assert [c["match_id"] for c in deferred] == [MID]
    assert {r.id: r.match_id for r in _rows(hosted_env)}[new_id] == MID
    _assert_listed_with_match(client, new_id, MID)
