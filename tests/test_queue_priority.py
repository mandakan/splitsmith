"""Proxies run after every pipeline job on the queue (#1670).

``generate_proxy`` is queued on every raw upload and only serves Ingest's
scrubber; beep detection, trims and shot detection are what the user waits
on. The deferrer gives a proxy a lower Procrastinate priority, and every
worker (the Cloud Run one-shot, the agent's drain, a long-lived worker)
fetches by ``priority DESC, id ASC`` across all queues, so a pipeline job
queued after a backlog of proxies still runs first, and the proxies still
drain once nothing else is waiting.

The in-memory connector sorts the same way as ``procrastinate_fetch_job_v2``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from procrastinate import App, testing

from splitsmith import queue

PIPELINE_KINDS = ("beep_detect", "trim", "shot_detect", "audit_audio", "match_export")


def _drain_order(deferred: list[tuple[str, str]]) -> list[str]:
    """Defer ``(user_id, kind)`` pairs through the real ``_defer_on`` in order,
    then run a one-shot drain (concurrency 1) and return the kinds it ran."""
    app = App(connector=testing.InMemoryConnector())
    ran: list[str] = []

    @app.task(name=queue.RUN_COMPUTE_JOB_TASK)
    async def _run(*, job_id: str, user_id: str, kind: str, args: Any = None, match_id: Any = None) -> None:
        ran.append(kind)

    async def scenario() -> None:
        async with app.open_async():
            for i, (user_id, kind) in enumerate(deferred):
                await queue._defer_on(app, job_id=f"j{i}", user_id=user_id, kind=kind, args={}, match_id=None)
            await app.run_worker_async(
                wait=False, concurrency=1, listen_notify=False, install_signal_handlers=False
            )

    asyncio.run(scenario())
    return ran


def test_pipeline_jobs_run_before_proxies_queued_earlier() -> None:
    deferred = [("u1", "generate_proxy")] * 3 + [("u1", kind) for kind in PIPELINE_KINDS]
    ran = _drain_order(deferred)
    assert ran == [*PIPELINE_KINDS, "generate_proxy", "generate_proxy", "generate_proxy"]


def test_priority_spans_tenant_queues() -> None:
    """Each user has their own queue; one user's proxies do not hold back
    another user's detection either."""
    ran = _drain_order([("u1", "generate_proxy"), ("u2", "generate_proxy"), ("u2", "beep_detect")])
    assert ran == ["beep_detect", "generate_proxy", "generate_proxy"]


def test_proxies_alone_still_drain_in_arrival_order() -> None:
    ran = _drain_order([("u1", "generate_proxy"), ("u2", "generate_proxy")])
    assert ran == ["generate_proxy", "generate_proxy"]


def test_only_background_kinds_are_lowered() -> None:
    assert queue.job_priority("generate_proxy") < 0
    for kind in PIPELINE_KINDS:
        assert queue.job_priority(kind) == 0
