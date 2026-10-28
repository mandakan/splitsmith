"""Hosted runs no compute for a desktop mirror beyond MIRROR_JOB_KINDS."""

from __future__ import annotations

import asyncio
import json

import pytest

from splitsmith.access import MIRROR_JOB_KINDS, FeatureRequiredError, MirrorComputeRefusedError
from splitsmith.db import PostgresJobBackend, create_engine, sessionmaker
from splitsmith.ui.context import current_match_origin


async def _yes() -> bool:
    return True


def _backend(engine, bodies, deferred: list[str]) -> PostgresJobBackend:  # noqa: ANN001
    async def deferrer(**kw):  # noqa: ANN003, ANN202
        deferred.append(kw["kind"])

    return PostgresJobBackend(
        sessionmaker(engine),
        user_id="u1",
        deferrer=deferrer,
        sweep_on_boot=False,
        bodies=bodies,
        submit_allowed=_yes,
    )


def test_a_mirror_queues_only_its_kinds_from_the_live_registry(
    hosted_app, hosted_env
) -> None:  # noqa: ANN001
    """Enumerates the live registry: a kind added later is refused on a mirror with no edit."""
    client, _ = hosted_app
    bodies = client.app.state.splitsmith_state.job_bodies
    kinds = sorted(bodies.kinds())
    assert MIRROR_JOB_KINDS <= set(kinds)
    deferred: list[str] = []
    refused: list[str] = []

    async def go() -> None:
        engine = create_engine(hosted_env)
        backend = _backend(engine, bodies, deferred)
        current_match_origin.set("desktop")
        for kind in kinds:
            try:
                await backend.submit(kind=kind)
            except MirrorComputeRefusedError as exc:
                assert exc.kind == kind
                refused.append(kind)
        await engine.dispose()

    asyncio.run(go())
    assert deferred == sorted(MIRROR_JOB_KINDS)
    assert refused == [k for k in kinds if k not in MIRROR_JOB_KINDS]


@pytest.mark.parametrize("origin", ["hosted", None])
def test_a_hosted_native_match_is_not_refused(hosted_env, origin) -> None:  # noqa: ANN001
    from splitsmith.ui.jobs import JobBodyRegistry

    bodies = JobBodyRegistry()
    bodies.register("trim", lambda handle: None)
    deferred: list[str] = []

    async def go() -> None:
        engine = create_engine(hosted_env)
        current_match_origin.set(origin)
        await _backend(engine, bodies, deferred).submit(kind="trim")
        await engine.dispose()

    asyncio.run(go())
    assert deferred == ["trim"]


def test_a_job_queued_unchecked_does_not_run_on_a_mirror(hosted_env) -> None:  # noqa: ANN001
    """The worker re-checks once ``before_body`` pins the match's origin, as queue.py does."""
    from splitsmith.ui.jobs import JobBodyRegistry, JobStatus

    ran: list[str] = []
    bodies = JobBodyRegistry()
    bodies.register("trim", lambda handle: ran.append("trim"))

    async def go() -> object:
        engine = create_engine(hosted_env)
        backend = _backend(engine, bodies, [])
        job = await backend.submit(kind="trim")
        await backend.run_job(
            job_id=job.id, kind="trim", before_body=lambda: current_match_origin.set("desktop")
        )
        after = await backend.get(job.id)
        await engine.dispose()
        return after

    after = asyncio.run(go())
    assert ran == []
    assert after.status == JobStatus.FAILED
    assert "desktop" in (after.error or "")


def test_the_refusal_is_a_feature_refusal_so_chains_skip_it() -> None:
    """Every chain site catches FeatureRequiredError and skips; this must be one."""
    assert isinstance(MirrorComputeRefusedError("trim"), FeatureRequiredError)


def test_the_app_maps_the_refusal_to_a_409(hosted_app) -> None:  # noqa: ANN001
    client, _ = hosted_app
    handler = client.app.exception_handlers[MirrorComputeRefusedError]
    response = asyncio.run(handler(None, MirrorComputeRefusedError("compare-grid")))
    assert response.status_code == 409
    detail = json.loads(response.body)["detail"]
    assert detail["code"] == "desktop_owns_compute"
    assert detail["kind"] == "compare-grid"
