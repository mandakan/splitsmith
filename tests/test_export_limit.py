"""How many exports a hosted account runs at once (spec 2026-10-25, plan Task 6)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import update

from splitsmith.db import ComputeJobRow, User, create_engine, sessionmaker
from splitsmith.ui.export_slot import ExportLimitReachedError, admit
from tests.test_admin_access_routes import admin_app
from tests.test_compare_grid_endpoint import match_client_with_trims
from tests.test_hosted_grid_stage_jobs import (
    TRIM_BYTES,
    hosted_client,
    hosted_client_with_match,
    hosted_db,
    hosted_grid,
    inline_deferrer,
    two_stages,
)
from tests.test_postgres_job_backend import _build_backend_for_new_user

__all__ = [
    "admin_app",
    "hosted_client",
    "hosted_client_with_match",
    "hosted_db",
    "hosted_grid",
    "inline_deferrer",
    "match_client_with_trims",
    "two_stages",
]


def _start_grid(client: Any, match_id: str, slug: str) -> Any:
    return client.post(
        f"/api/matches/{match_id}/match/compare-export", json={"stage_numbers": [1, 2], "audio_from": slug}
    )


def _db(url: str, statement: Any) -> None:
    engine = create_engine(url)

    async def run() -> None:
        async with sessionmaker(engine)() as session:
            await session.execute(statement)
            await session.commit()
        await engine.dispose()

    asyncio.run(run())


def test_a_second_export_is_refused_with_the_running_one(two_stages: Any, inline_deferrer: Any) -> None:
    client, _storage, match_id, slug, *_ = two_stages
    inline_deferrer["hold"] = True  # the first export stays queued
    first = _start_grid(client, match_id, slug)
    assert first.status_code == 200, first.text
    second = _start_grid(client, match_id, slug)
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "export_limit"
    assert detail["running"]["export_id"] == first.json()["export_id"]
    assert detail["running"]["kind"] == "compare-grid"
    assert detail["running"]["match_id"] == match_id


def test_the_override_raises_the_limit(two_stages: Any, inline_deferrer: Any, hosted_db: str) -> None:
    client, _storage, match_id, slug, *_ = two_stages
    _db(hosted_db, update(User).values(concurrent_exports=2))
    inline_deferrer["hold"] = True
    assert _start_grid(client, match_id, slug).status_code == 200
    assert _start_grid(client, match_id, slug).status_code == 200
    assert _start_grid(client, match_id, slug).status_code == 409


def test_a_tier_number_is_used_without_an_override(two_stages: Any, inline_deferrer: Any) -> None:
    client, _storage, match_id, slug, *_ = two_stages
    state = client.app.state.splitsmith_state
    state.access = state.access.model_copy(update={"concurrent_exports": {"full": 2}})
    inline_deferrer["hold"] = True
    assert _start_grid(client, match_id, slug).status_code == 200
    assert _start_grid(client, match_id, slug).status_code == 200
    assert _start_grid(client, match_id, slug).status_code == 409


def test_a_finished_export_frees_the_slot(two_stages: Any, inline_deferrer: Any, hosted_db: str) -> None:
    client, _storage, match_id, slug, *_ = two_stages
    inline_deferrer["hold"] = True
    assert _start_grid(client, match_id, slug).status_code == 200
    _db(hosted_db, update(ComputeJobRow).values(status="succeeded"))
    assert _start_grid(client, match_id, slug).status_code == 200


def test_trims_only_is_one_export(two_stages: Any, inline_deferrer: Any) -> None:
    client, storage, match_id, slug, *_ = two_stages
    storage.write_bytes("raw/v.mp4", TRIM_BYTES)
    inline_deferrer["hold"] = True
    r = client.post(f"/api/matches/{match_id}/shooters/{slug}/exports/trims", json={"stage_numbers": [1, 2]})
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    assert len(jobs) == 2
    assert len({j["export_id"] for j in jobs}) == 1 and jobs[0]["export_id"] is not None
    assert _start_grid(client, match_id, slug).status_code == 409


def test_joining_a_running_stage_export_is_not_a_new_export(two_stages: Any, inline_deferrer: Any) -> None:
    client, storage, match_id, slug, *_ = two_stages
    storage.write_bytes("raw/v.mp4", TRIM_BYTES)  # a reachable source, as the hosted grid tests place it
    inline_deferrer["hold"] = True
    url = f"/api/matches/{match_id}/shooters/{slug}/stages/1/export"
    first = client.post(url, json={})
    assert first.status_code == 200, first.text
    again = client.post(url, json={})
    assert again.status_code == 200, again.text
    assert again.json()["id"] == first.json()["id"]


def test_two_concurrent_starts_admit_one(tmp_path: Any) -> None:
    backend, _factory, _user = _build_backend_for_new_user(tmp_path, inline=False)
    backend.bodies.register("x", lambda handle, **_: None)

    async def start(export_id: str) -> str:
        async with admit(backend, limit=1, export_id=export_id):
            await asyncio.sleep(0.01)  # both would see an empty slot without the lock
            await backend.submit(kind="x")
        return export_id

    async def race() -> list[Any]:
        return await asyncio.gather(start("a"), start("b"), return_exceptions=True)

    outcomes = asyncio.run(race())
    assert sorted(type(o).__name__ for o in outcomes) == ["ExportLimitReachedError", "str"]
    refused = next(o for o in outcomes if isinstance(o, ExportLimitReachedError))
    assert refused.running.export_id in ("a", "b")


def test_local_mode_has_no_limit(match_client_with_trims: Any) -> None:
    for _ in range(3):
        r = match_client_with_trims.post(
            "/api/match/compare-export", json={"stage_numbers": [1], "audio_from": "mathias"}
        )
        assert r.status_code == 200, r.text


def test_scope_and_limit_agree_on_the_export_id(tmp_path: Any) -> None:
    """``admit`` stamps the jobs it admits with the id it was given."""
    backend, _factory, _user = _build_backend_for_new_user(tmp_path, inline=False)
    backend.bodies.register("x", lambda handle, **_: None)

    async def scenario() -> str | None:
        async with admit(backend, limit=1, export_id="e9"):
            job = await backend.submit(kind="x")
        return job.export_id

    assert asyncio.run(scenario()) == "e9"


@pytest.mark.parametrize("value", [3, None])
def test_admin_sets_the_override(admin_app: Any, value: int | None) -> None:
    from tests.hosted_helpers import login

    client, sender, _state = admin_app
    login(client, sender, "friend@x.se")
    client.cookies.clear()
    login(client, sender, "boss@x.se")
    fid = next(u["id"] for u in client.get("/api/admin/users").json() if u["email"] == "friend@x.se")
    changed = client.patch(f"/api/admin/users/{fid}", json={"concurrent_exports": value})
    assert changed.status_code == 200, changed.text
    assert changed.json()["concurrent_exports"] == value
    assert changed.json()["access_tier"] == "full"  # a body without a tier leaves it
