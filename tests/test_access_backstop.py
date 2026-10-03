"""No job reaches the fleet for an account without hosted_compute."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from splitsmith import match_model
from splitsmith.access import Feature, FeatureRequiredError
from splitsmith.db import (
    ComputeJobRow,
    PostgresJobBackend,
    ProjectStateStore,
    User,
    create_engine,
    sessionmaker,
)
from splitsmith.match_project import MatchProject, StageEntry, StageVideo

# ``hosted_app`` / ``hosted_env`` are registered in conftest.py.
from tests.hosted_helpers import _CapturingSender, login, seed_match
from tests.test_access_gates import set_tier


def _job_count(db_url: str) -> int:
    async def go() -> int:
        engine = create_engine(db_url)
        async with sessionmaker(engine)() as s:
            n = (await s.execute(select(func.count()).select_from(ComputeJobRow))).scalar_one()
        await engine.dispose()
        return n

    return asyncio.run(go())


def test_every_registered_kind_is_refused(hosted_app, hosted_env) -> None:  # noqa: ANN001
    """Enumerates the live registry: a kind added later is covered with no edit."""
    client, _ = hosted_app
    bodies = client.app.state.splitsmith_state.job_bodies
    kinds = list(bodies.kinds())
    assert len(kinds) >= 10, kinds
    deferred: list[str] = []

    async def deferrer(**kw):  # noqa: ANN003, ANN202
        deferred.append(kw["kind"])

    async def never() -> bool:
        return False

    async def go() -> None:
        engine = create_engine(hosted_env)
        backend = PostgresJobBackend(
            sessionmaker(engine),
            user_id="u1",
            deferrer=deferrer,
            sweep_on_boot=False,
            bodies=bodies,
            submit_allowed=never,
        )
        for kind in kinds:
            with pytest.raises(FeatureRequiredError) as exc:
                await backend.submit(kind=kind)
            assert exc.value.feature is Feature.hosted_compute
        await engine.dispose()

    asyncio.run(go())
    assert _job_count(hosted_env) == 0
    assert deferred == []


def test_registry_lists_its_kinds() -> None:
    from splitsmith.ui.jobs import JobBodyRegistry

    reg = JobBodyRegistry()
    reg.register("a", lambda handle: None)
    reg.register("b", lambda handle: None)
    assert sorted(reg.kinds()) == ["a", "b"]


def test_build_tenant_wires_the_backstop(hosted_app, hosted_env) -> None:  # noqa: ANN001
    """The real seam: a tenant built for a sharing user refuses submit."""
    client, sender = hosted_app
    login(client, sender, "friend@x.se")
    set_tier(hosted_env, "friend@x.se", "sharing")
    state = client.app.state.splitsmith_state
    user_id = client.get("/api/me").json()["id"]
    tenant = state._build_tenant(user_id)
    with pytest.raises(FeatureRequiredError):
        asyncio.run(tenant.jobs.submit(kind="trim"))


def test_backstop_reads_the_tier_per_submit(hosted_app, hosted_env) -> None:  # noqa: ANN001
    """A tenant built while the account was full refuses once it is downgraded."""
    client, sender = hosted_app
    login(client, sender, "friend@x.se")
    state = client.app.state.splitsmith_state
    tenant = state._build_tenant(client.get("/api/me").json()["id"])
    # First submit while full: passes the gate and fails later (the sqlite
    # harness has no queue), so an answer cached here would let the next one through.
    with pytest.raises(Exception) as first:
        asyncio.run(tenant.jobs.submit(kind="trim"))
    assert not isinstance(first.value, FeatureRequiredError)
    set_tier(hosted_env, "friend@x.se", "sharing")
    with pytest.raises(FeatureRequiredError):
        asyncio.run(tenant.jobs.submit(kind="trim"))


def test_full_tenant_is_not_refused_by_the_backstop(hosted_app) -> None:  # noqa: ANN001
    client, sender = hosted_app
    login(client, sender, "me@x.se")
    state = client.app.state.splitsmith_state
    tenant = state._build_tenant(client.get("/api/me").json()["id"])
    try:
        asyncio.run(tenant.jobs.submit(kind="trim"))
    except FeatureRequiredError:  # pragma: no cover - the failure this test exists for
        pytest.fail("a full account was refused by the backstop")
    except Exception:
        pass  # anything past the gate (deferrer, missing args) is not this test's concern


MID = "brm-backstop-01"
SLUG = "anna"
OWNER = "owner@example.com"


def _seed_trimmable_stage(db_url: str, user_email: str, source: Path) -> None:
    """A stage that passes every trim preflight: beep, stage time, source present."""
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            user_id = (await s.execute(select(User).where(User.email == user_email))).scalar_one().id
        store = ProjectStateStore(sf, user_id=user_id)
        match = match_model.Match(
            match_id=MID,
            name="Backstop match",
            shooters=[SLUG],
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")],
        )
        await store.save_match(MID, match.model_dump(mode="json"), expected_version=0)
        project = MatchProject(
            name="Anna",
            stages=[
                StageEntry(
                    stage_number=1,
                    stage_name="Stage 1",
                    time_seconds=12.5,
                    videos=[StageVideo(path=source, role="primary", beep_time=3.0)],
                )
            ],
        )
        await store.save_project(MID, SLUG, project.model_dump(mode="json"), expected_version=0)
        await engine.dispose()

    asyncio.run(_seed())


def test_trim_route_after_downgrade_is_refused_with_the_backstop_body(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    tmp_path: Path,
) -> None:
    """A route with no gate of its own: the backstop's 403 reaches the client."""
    client, sender = hosted_app
    login(client, sender, OWNER)
    seed_match(hosted_env, OWNER, MID)
    source = tmp_path / "v.mp4"
    source.write_bytes(b"x")
    _seed_trimmable_stage(hosted_env, OWNER, source)
    set_tier(hosted_env, OWNER, "sharing")

    resp = client.post(f"/api/matches/{MID}/shooters/{SLUG}/stages/1/trim")

    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": {"code": "feature_required", "feature": "hosted_compute"}}
    assert _job_count(hosted_env) == 0
