"""The worker pins the match's origin for a job's body (#1612).

``_is_mirror()`` reads ``current_match_origin``, which the alias middleware
pins per request. A job on the Procrastinate worker has no request, so the
compute task has to pin it from the match row itself, or every match looks
hosted-native there and the audit audio job of a desktop mirror goes looking
for a raw source no mirror has.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from splitsmith import queue as queue_mod
from splitsmith.ui.context import current_match_id, current_match_origin, current_match_root


class _App:
    def __init__(self) -> None:
        self.tasks: dict[str, object] = {}

    def task(self, *, name: str, queue: str):  # type: ignore[no-untyped-def]
        def register(fn):  # type: ignore[no-untyped-def]
            self.tasks[name] = fn
            return fn

        return register


def _drive(origin: str | None, tmp_path: Path) -> dict[str, object]:
    """Run the registered compute task once for a match whose row carries
    ``origin`` and return what the body saw."""
    from splitsmith.ui import server

    seen: dict[str, object] = {}

    async def run_job(*, before_body, **_: object) -> None:  # type: ignore[no-untyped-def]
        before_body()
        seen["origin"] = current_match_origin.get()
        seen["mirror"] = server._is_mirror()
        seen["match_id"] = current_match_id.get()
        seen["root"] = current_match_root.get()

    async def get_row(match_id: str) -> object:
        return SimpleNamespace(match_id=match_id, origin=origin)

    state = SimpleNamespace(
        build_tenant=lambda _uid: SimpleNamespace(looks=None),
        jobs=SimpleNamespace(run_job=run_job),
        matches=SimpleNamespace(resolve=lambda _mid: tmp_path),
        matches_store=SimpleNamespace(get=get_row),
    )
    app = _App()
    original = server.tenant_looks_provider
    server.tenant_looks_provider = lambda _tenant: None  # type: ignore[assignment]
    try:
        queue_mod.register_compute_task(app, state)
        asyncio.run(
            app.tasks[queue_mod.RUN_COMPUTE_JOB_TASK](  # type: ignore[operator]
                job_id="j", user_id="u", kind="derive_audio", match_id="m1"
            )
        )
    finally:
        server.tenant_looks_provider = original  # type: ignore[assignment]
    return seen


def test_a_mirror_job_runs_with_is_mirror_true_on_the_worker(tmp_path: Path) -> None:
    seen = _drive("desktop", tmp_path)
    assert seen == {"origin": "desktop", "mirror": True, "match_id": "m1", "root": tmp_path}
    # The task's context ends with the task: nothing leaks into the next one.
    assert current_match_origin.get() is None


def test_a_hosted_native_job_is_not_a_mirror(tmp_path: Path) -> None:
    seen = _drive("hosted", tmp_path)
    assert seen["mirror"] is False and seen["origin"] == "hosted"
