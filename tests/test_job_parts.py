"""A job publishes its parts for the export progress panel (spec 2026-10-25)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from splitsmith.ui import jobs as jobs_mod
from splitsmith.ui.jobs import JobHandle, JobPart, JobRegistry
from tests.test_export_identity import _settled
from tests.test_postgres_job_backend import _build_backend_for_new_user


def _two_parts() -> list[JobPart]:
    return [
        JobPart(key="load", label="Loading", kind="load"),
        JobPart(key="render", label="Stage 1", kind="grid_stage", profile="1080-basic", footage_s=40.0),
    ]


def _body(handle: JobHandle, **_: Any) -> None:
    handle.set_parts(_two_parts())
    handle.update_part("load", status="running")
    handle.update_part("load", status="done")
    handle.update_part("render", status="running")
    handle.update_part("render", progress=0.5)
    handle.update_part("render", status="done", progress=1.0)


@pytest.fixture(params=["local", "hosted"])
def backend(request: pytest.FixtureRequest, tmp_path: Any) -> Any:
    if request.param == "local":
        registry = JobRegistry(max_concurrent=1)
        yield registry
        registry.begin_shutdown()
        registry.wait_for_drain(5)
    else:
        hosted, _factory, _user = _build_backend_for_new_user(tmp_path)
        yield hosted


def test_parts_round_trip_through_both_backends(backend: Any) -> None:
    backend.bodies.register("x", _body)
    job = asyncio.run(backend.submit(kind="x"))
    final = _settled(backend, job.id)
    assert final.parts is not None
    assert [(p.key, p.kind, p.status) for p in final.parts] == [
        ("load", "load", "done"),
        ("render", "grid_stage", "done"),
    ]
    render = final.parts[1]
    assert render.profile == "1080-basic" and render.footage_s == 40.0 and render.progress == 1.0


class _Recorder:
    def __init__(self) -> None:
        self.patches: list[dict[str, Any]] = []

    def _patch(self, job_id: str, **fields: Any) -> None:
        self.patches.append(fields)


def _handle(recorder: _Recorder) -> JobHandle:
    return JobHandle(recorder, "j1")  # type: ignore[arg-type]


def test_status_changes_stamp_times() -> None:
    recorder = _Recorder()
    handle = _handle(recorder)
    handle.set_parts(_two_parts())
    handle.update_part("load", status="running")
    handle.update_part("load", status="done")
    last = recorder.patches[-1]["parts"]
    assert last[0].started_at is not None and last[0].finished_at is not None
    assert last[1].started_at is None


def test_progress_writes_are_throttled_but_status_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 100.0}
    monkeypatch.setattr(jobs_mod.time, "monotonic", lambda: clock["now"])
    recorder = _Recorder()
    handle = _handle(recorder)
    handle.set_parts(_two_parts())
    handle.update_part("render", status="running")
    written = len(recorder.patches)
    handle.update_part("render", progress=0.1)
    handle.update_part("render", progress=0.2)  # inside the interval: held
    assert len(recorder.patches) == written
    clock["now"] += jobs_mod.PART_WRITE_INTERVAL_S
    handle.update_part("render", progress=0.3)
    assert len(recorder.patches) == written + 1
    assert recorder.patches[-1]["parts"][1].progress == 0.3
    handle.update_part("render", status="done")  # a status change always writes
    assert len(recorder.patches) == written + 2


def test_unknown_part_key_raises() -> None:
    handle = _handle(_Recorder())
    handle.set_parts(_two_parts())
    with pytest.raises(KeyError):
        handle.update_part("nope", status="running")


def test_the_first_fraction_marks_where_the_rate_starts() -> None:
    """A dressed stage runs for minutes (its overlay is drawn) before
    ffmpeg's first fraction; the rate is measured from that fraction."""
    recorder = _Recorder()
    handle = _handle(recorder)
    handle.set_parts(_two_parts())
    handle.update_part("render", status="running")
    handle.update_part("render", progress=0.2)
    handle.update_part("render", progress=0.5)
    part = handle._parts[1]
    assert part.rate_from is not None and part.rate_from_progress == 0.2
