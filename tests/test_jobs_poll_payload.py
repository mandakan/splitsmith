"""The jobs poll carries what the SPA uses, not the account's whole job history (#1182).

``GET /api/me/jobs`` is polled every 5 s by every open tab. Hosted, the
backend's ``list()`` returned every compute job the user ever ran, each
with its ``result`` and ``timings`` blobs: 62 KB per poll on production and
growing with every job. The SPA reads active jobs (strip, settle detection)
and unacknowledged failures (the sheet, acknowledge, retry) from the list,
and a job's result through ``getJob`` -- never from the list.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith.db import Base, ComputeJobRow, PostgresJobBackend, User, create_engine, sessionmaker
from splitsmith.db.job_backend import RECENT_FINISHED_RETAINED
from tests.hosted_helpers import _CapturingSender, login

from .test_postgres_job_backend import _noop_deferrer

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _engine_with_user(email: str = "m@thias.se"):
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    sf = sessionmaker(engine)

    async def _setup() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            user = User(email=email)
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    return sf, asyncio.run(_setup())


def _seed_rows(sf, user_id: str, rows: list[dict]) -> None:
    """Insert compute_jobs rows directly; ``rows`` are kwargs over the row model."""

    async def _insert() -> None:
        async with sf() as s:
            for i, r in enumerate(rows):
                defaults = {
                    "user_id": user_id,
                    "kind": "probe",
                    "cancel_requested": False,
                    "acknowledged": False,
                    "created_at": T0 + timedelta(minutes=i),
                    "updated_at": T0 + timedelta(minutes=i),
                }
                s.add(ComputeJobRow(**{**defaults, **r}))
            await s.commit()

    asyncio.run(_insert())


def test_list_keeps_active_and_unacknowledged_failures_and_only_the_recent_rest() -> None:
    sf, uid = _engine_with_user()
    backend = PostgresJobBackend(sf, user_id=uid, deferrer=_noop_deferrer(), sweep_on_boot=False)
    # Ids sort against creation order on purpose (the earliest row has the
    # highest id), so an ORDER BY id -- or no ORDER BY -- fails the order
    # assertion instead of passing by coincidence.
    old_done = [{"id": f"old-{59 - i:03d}", "status": "succeeded"} for i in range(60)]
    rows = [
        {"id": "zz-failed-unacked", "status": "failed", "acknowledged": False},
        {"id": "zz-failed-acked", "status": "failed", "acknowledged": True},
        {"id": "zz-cancelled", "status": "cancelled"},
        *old_done,
        {"id": "ab-pending-now", "status": "pending"},
        {"id": "aa-running-now", "status": "running"},
    ]
    _seed_rows(sf, uid, rows)

    listed = asyncio.run(backend.list())
    ids = [j.id for j in listed]

    # Active jobs and unacknowledged failures are always there, however old.
    assert {"ab-pending-now", "aa-running-now", "zz-failed-unacked"} <= set(ids)
    # The rest is capped to the most recently created RECENT_FINISHED_RETAINED.
    recent = [j.id for j in listed if j.id.startswith("old-")]
    assert recent == [f"old-{59 - i:03d}" for i in range(60 - RECENT_FINISHED_RETAINED, 60)]
    assert "zz-failed-acked" not in ids
    assert "zz-cancelled" not in ids
    # Same order the SPA has always seen: by created_at, oldest first.
    creation_order = [r["id"] for r in rows]
    assert ids == [i for i in creation_order if i in set(ids)]


def test_list_returns_everything_when_under_the_cap() -> None:
    sf, uid = _engine_with_user()
    backend = PostgresJobBackend(sf, user_id=uid, deferrer=_noop_deferrer(), sweep_on_boot=False)
    _seed_rows(sf, uid, [{"id": f"j{i}", "status": "succeeded"} for i in range(5)])

    assert [j.id for j in asyncio.run(backend.list())] == [f"j{i}" for i in range(5)]


def test_list_still_scopes_to_the_user() -> None:
    sf, uid = _engine_with_user()

    async def _other_user() -> str:
        async with sf() as s:
            user = User(email="other@example.com")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    other = asyncio.run(_other_user())
    _seed_rows(sf, uid, [{"id": "mine", "status": "running"}])
    _seed_rows(sf, other, [{"id": "theirs", "status": "running"}])
    backend = PostgresJobBackend(sf, user_id=uid, deferrer=_noop_deferrer(), sweep_on_boot=False)

    assert [j.id for j in asyncio.run(backend.list())] == ["mine"]


# ---------------------------------------------------------------------------
# The route strips the per-job blobs the poll never reads.
# ---------------------------------------------------------------------------


def _user_id(db_url: str, email: str) -> str:
    sf = sessionmaker(create_engine(db_url))

    async def _load() -> str:
        async with sf() as s:
            return (await s.execute(_select(User).where(User.email == email))).scalar_one().id

    return asyncio.run(_load())


def test_poll_list_omits_result_and_timings_but_the_single_job_route_keeps_them(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    uid = _user_id(hosted_env, "owner@example.com")
    sf = sessionmaker(create_engine(hosted_env))
    _seed_rows(
        sf,
        uid,
        [
            {
                "id": "exp-1",
                "kind": "match_export",
                "status": "succeeded",
                "result": {"fcpxml": "exports/match.fcpxml", "duration_seconds": 1234.5},
                "timings": {"total_ms": 9000, "phases": [{"name": "render", "ms": 8000}]},
            },
            {"id": "run-1", "kind": "trim", "status": "running", "progress": 0.4},
        ],
    )

    listed = client.get("/api/me/jobs")
    assert listed.status_code == 200, listed.text
    by_id = {j["id"]: j for j in listed.json()}
    assert set(by_id) == {"exp-1", "run-1"}
    for job in by_id.values():
        assert job["result"] is None
        assert job["timings"] is None
    assert by_id["run-1"]["progress"] == pytest.approx(0.4)

    single = client.get("/api/me/jobs/exp-1")
    assert single.status_code == 200, single.text
    assert single.json()["result"] == {"fcpxml": "exports/match.fcpxml", "duration_seconds": 1234.5}
    assert single.json()["timings"]["total_ms"] == 9000
