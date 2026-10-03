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
from splitsmith.ui.server import _hosted_match_work_root

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


def test_job_queued_before_a_downgrade_fails_without_running(hosted_env) -> None:  # noqa: ANN001
    """The worker re-checks: a job queued while full does not run once the account lost hosted_compute."""
    from splitsmith.ui.jobs import JobBodyRegistry, JobStatus

    ran: list[str] = []
    bodies = JobBodyRegistry()
    bodies.register("probe", lambda handle: ran.append("probe"))
    allowed = {"value": True}

    async def deferrer(**kw):  # noqa: ANN003, ANN202
        return None

    async def may_submit() -> bool:
        return allowed["value"]

    async def go() -> tuple[str, object]:
        engine = create_engine(hosted_env)
        backend = PostgresJobBackend(
            sessionmaker(engine),
            user_id="u1",
            deferrer=deferrer,
            sweep_on_boot=False,
            bodies=bodies,
            submit_allowed=may_submit,
        )
        job = await backend.submit(kind="probe")
        allowed["value"] = False
        await backend.run_job(job_id=job.id, kind="probe")
        after = await backend.get(job.id)
        await engine.dispose()
        return job.id, after

    _, after = asyncio.run(go())
    assert ran == []
    assert after.status == JobStatus.FAILED
    assert "hosted_compute" in (after.error or "")


def test_job_still_allowed_runs(hosted_env) -> None:  # noqa: ANN001
    from splitsmith.ui.jobs import JobBodyRegistry, JobStatus

    ran: list[str] = []
    bodies = JobBodyRegistry()
    bodies.register("probe", lambda handle: ran.append("probe"))

    async def deferrer(**kw):  # noqa: ANN003, ANN202
        return None

    async def yes() -> bool:
        return True

    async def go() -> object:
        engine = create_engine(hosted_env)
        backend = PostgresJobBackend(
            sessionmaker(engine),
            user_id="u1",
            deferrer=deferrer,
            sweep_on_boot=False,
            bodies=bodies,
            submit_allowed=yes,
        )
        job = await backend.submit(kind="probe")
        await backend.run_job(job_id=job.id, kind="probe")
        after = await backend.get(job.id)
        await engine.dispose()
        return after

    after = asyncio.run(go())
    assert ran == ["probe"]
    assert after.status == JobStatus.SUCCEEDED


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
_SOURCE = Path("raw/v.mp4")


def _seed_trimmable_stage(db_url: str, user_email: str, *, trimmed: bool = False) -> None:
    """A stage that passes every trim preflight: beep, stage time, source present.

    The source sits in the account's own working folder under a relative
    path: a hosted project load confines paths (#1172), so an absolute
    source elsewhere on the container would answer 424 ``source_unreachable``
    before any gate or chain this file is about is reached.
    """
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            user_id = (await s.execute(select(User).where(User.email == user_email))).scalar_one().id
        source = _hosted_match_work_root(user_id, MID) / "shooters" / SLUG / _SOURCE
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"x")
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
                    videos=[
                        StageVideo(
                            path=_SOURCE,
                            role="primary",
                            beep_time=3.0,
                            processed={"beep": True, "trim": trimmed},
                        )
                    ],
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
    _seed_trimmable_stage(hosted_env, OWNER)
    set_tier(hosted_env, OWNER, "sharing")

    resp = client.post(f"/api/matches/{MID}/shooters/{SLUG}/stages/1/trim")

    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": {"code": "feature_required", "feature": "hosted_compute"}}
    assert _job_count(hosted_env) == 0


# --- API-side chains after a committed write (fix round 1) ---------------


def _project(client: TestClient) -> dict:
    resp = client.get(f"/api/matches/{MID}/shooters/{SLUG}/project")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _downgraded_trimmable_match(
    client, sender, hosted_env: str, tmp_path: Path, *, trimmed: bool = False  # noqa: ANN001
) -> str:
    login(client, sender, OWNER)
    seed_match(hosted_env, OWNER, MID)
    _seed_trimmable_stage(hosted_env, OWNER, trimmed=trimmed)
    set_tier(hosted_env, OWNER, "sharing")
    return _project(client)["stages"][0]["videos"][0]["video_id"]


def test_beep_override_saves_and_skips_the_chained_trim(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender], tmp_path: Path
) -> None:
    client, sender = hosted_app
    video_id = _downgraded_trimmable_match(client, sender, hosted_env, tmp_path)

    resp = client.post(
        f"/api/matches/{MID}/shooters/{SLUG}/stages/1/videos/{video_id}/beep", json={"beep_time": 4.25}
    )

    assert resp.status_code == 200, resp.text
    assert _project(client)["stages"][0]["videos"][0]["beep_time"] == 4.25
    assert _job_count(hosted_env) == 0


def test_beep_override_shim_saves_and_skips_the_chained_trim(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender], tmp_path: Path
) -> None:
    client, sender = hosted_app
    _downgraded_trimmable_match(client, sender, hosted_env, tmp_path)

    resp = client.post(f"/api/matches/{MID}/shooters/{SLUG}/stages/1/beep", json={"beep_time": 4.25})

    assert resp.status_code == 200, resp.text
    assert _project(client)["stages"][0]["videos"][0]["beep_time"] == 4.25
    assert _job_count(hosted_env) == 0


@pytest.mark.parametrize("trimmed", [False, True], ids=["chains-trim", "chains-shot-detect"])
def test_beep_review_saves_and_skips_the_chained_job(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender], tmp_path: Path, trimmed: bool
) -> None:
    client, sender = hosted_app
    video_id = _downgraded_trimmable_match(client, sender, hosted_env, tmp_path, trimmed=trimmed)

    resp = client.post(
        f"/api/matches/{MID}/shooters/{SLUG}/stages/1/videos/{video_id}/beep/review", json={"reviewed": True}
    )

    assert resp.status_code == 200, resp.text
    assert _project(client)["stages"][0]["videos"][0]["beep_reviewed"] is True
    assert _job_count(hosted_env) == 0


def test_beep_window_is_refused_before_it_is_written(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender], tmp_path: Path
) -> None:
    """The route's purpose is the detection it queues: refuse up front, write nothing."""
    client, sender = hosted_app
    video_id = _downgraded_trimmable_match(client, sender, hosted_env, tmp_path)

    resp = client.put(
        f"/api/matches/{MID}/shooters/{SLUG}/stages/1/videos/{video_id}/beep-window",
        json={"start_s": 1.0, "end_s": 5.0},
    )

    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": {"code": "feature_required", "feature": "hosted_compute"}}
    video = _project(client)["stages"][0]["videos"][0]
    assert video["beep_window"] is None
    assert video["beep_time"] == 3.0
    assert _job_count(hosted_env) == 0


def test_auto_queue_beep_skips_without_hosted_compute(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender], tmp_path: Path
) -> None:
    """The best-effort detect_beep hook (scan, assignment moves) reports a skip, not a 403."""
    from splitsmith.ui.server import current_match_id, current_match_root, current_tenant

    client, sender = hosted_app
    _downgraded_trimmable_match(client, sender, hosted_env, tmp_path)
    state = client.app.state.splitsmith_state
    tenant = state._build_tenant(client.get("/api/me").json()["id"])
    source = tmp_path / "fresh.mp4"
    source.write_bytes(b"x")
    video = StageVideo(path=source, role="primary")
    project = MatchProject(
        name="Anna",
        stages=[StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=12.5, videos=[video])],
    )
    hook = client.app.state.auto_queue_beep

    async def go() -> bool:
        t = current_tenant.set(tenant)
        m = current_match_id.set(MID)
        r = current_match_root.set(tmp_path / "match")
        try:
            return await hook(SLUG, project, 1, video)
        finally:
            current_match_root.reset(r)
            current_match_id.reset(m)
            current_tenant.reset(t)

    assert asyncio.run(go()) is False
    assert _job_count(hosted_env) == 0


def test_deleted_account_is_refused(hosted_app, hosted_env) -> None:  # noqa: ANN001
    from datetime import UTC, datetime

    from sqlalchemy import update

    client, sender = hosted_app
    login(client, sender, "me@x.se")
    state = client.app.state.splitsmith_state
    tenant = state._build_tenant(client.get("/api/me").json()["id"])

    async def mark_deleted() -> None:
        engine = create_engine(hosted_env)
        async with sessionmaker(engine)() as s:
            await s.execute(update(User).where(User.email == "me@x.se").values(deleted_at=datetime.now(UTC)))
            await s.commit()
        await engine.dispose()

    asyncio.run(mark_deleted())
    with pytest.raises(FeatureRequiredError):
        asyncio.run(tenant.jobs.submit(kind="trim"))


def test_hosted_fallback_registry_refuses_submit(hosted_app) -> None:  # noqa: ANN001
    """With no tenant pinned, hosted ``state.jobs`` is the in-process registry: it must not run jobs."""
    from splitsmith.ui.server import current_tenant

    client, _ = hosted_app
    state = client.app.state.splitsmith_state
    assert current_tenant.get() is None
    with pytest.raises(RuntimeError, match="no tenant"):
        asyncio.run(state.jobs.submit(kind="trim", args={}))


@pytest.mark.parametrize("per_video", [True, False], ids=["per-video", "primary-shim"])
def test_beep_override_skips_a_refused_take_chain(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    per_video: bool,
) -> None:
    """The take-chain advance (detect_beep for the next stage) is skipped, not a 403."""
    import splitsmith.ui.server as server_mod

    async def refused(*_a, **_kw) -> None:  # noqa: ANN002, ANN003
        raise FeatureRequiredError(Feature.hosted_compute)

    monkeypatch.setattr(server_mod, "_advance_sequential_chain", refused)
    client, sender = hosted_app
    video_id = _downgraded_trimmable_match(client, sender, hosted_env, tmp_path)
    base = f"/api/matches/{MID}/shooters/{SLUG}/stages/1"
    url = f"{base}/videos/{video_id}/beep" if per_video else f"{base}/beep"

    resp = client.post(url, json={"beep_time": 4.25})

    assert resp.status_code == 200, resp.text
    assert _project(client)["stages"][0]["videos"][0]["beep_time"] == 4.25


def test_local_beep_window_is_not_gated(tmp_path: Path) -> None:
    """Local mode never consults access: the up-front gate is a no-op there."""
    from tests.test_ui_server import _seed_match_export_project

    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    client.app.state.splitsmith_state.job_bodies.register("detect_beep", lambda handle, **_a: None)
    primary = MatchProject.load(root / "shooters" / "me").stage(1).primary()
    assert primary is not None

    resp = client.put(
        f"/api/shooters/me/stages/1/videos/{primary.video_id}/beep-window",
        json={"start_s": 1.0, "end_s": 5.0},
    )

    assert resp.status_code == 200, resp.text
