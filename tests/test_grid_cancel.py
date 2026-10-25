"""A cancel stops a hosted grid's running encode (staging run G10).

On hosted a cancel is a flag on the job's row, read by whoever asks: the
worker running the encode is a different process from serve, so nothing
terminates its ffmpeg. The grid's runner asks while ffmpeg runs and stops
it, so a cancelled export ends cancelled instead of encoding every stage
and the stitch for half an hour.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Any

import pytest

from splitsmith.compare import mp4_grid as mp4_grid_mod
from splitsmith.ui import server as server_mod
from splitsmith.ui.jobs import JobCancelled
from tests.test_hosted_compare_grid import hosted_grid, inline_deferrer
from tests.test_hosted_grid_stage_jobs import _grid, _stage_jobs, two_stages
from tests.test_hosted_raw_upload import hosted_client, hosted_client_with_match, hosted_db

__all__ = [
    "hosted_client",
    "hosted_client_with_match",
    "hosted_db",
    "hosted_grid",
    "inline_deferrer",
    "two_stages",
]

_SLEEP = [sys.executable, "-c", "import time; time.sleep(20)"]


class _HostedHandle:
    """A hosted job's handle as the worker sees it: the cancel is a flag it
    reads, and attaching a process terminates it only when the flag is
    already set."""

    def __init__(self) -> None:
        self.cancelled = False
        self.procs: list[subprocess.Popen] = []

    def is_cancel_requested(self) -> bool:
        return self.cancelled

    def attach_subprocess(self, proc: subprocess.Popen) -> None:
        self.procs.append(proc)
        if self.cancelled:
            proc.terminate()
            raise JobCancelled()

    def detach_subprocess(self) -> None:
        pass


def test_a_cancel_mid_encode_stops_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_mod, "CANCEL_POLL_S", 0.05)
    handle = _HostedHandle()
    threading.Timer(0.3, lambda: setattr(handle, "cancelled", True)).start()
    started = time.monotonic()
    with pytest.raises(JobCancelled):
        server_mod._grid_runner(handle)(_SLEEP, capture_output=True)  # type: ignore[arg-type]
    assert time.monotonic() - started < 10
    (proc,) = handle.procs
    assert proc.poll() is not None


def test_the_grid_runner_reports_a_failed_encode_as_the_renderer_expects() -> None:
    """``subprocess.run``'s shape: a non-zero exit is a result, not a raise,
    so one bad stage is reported and the rest still render."""
    completed = server_mod._grid_runner(_HostedHandle())(  # type: ignore[arg-type]
        [sys.executable, "-c", "import sys; sys.exit(3)"], capture_output=True
    )
    assert completed.returncode == 3


def test_an_encode_that_ends_after_a_cancel_is_the_cancel() -> None:
    """Locally the registry terminates ffmpeg on cancel: the non-zero exit
    that follows is the cancel, not a failed stage."""
    handle = _HostedHandle()

    def _exit_after_cancel(*args: Any, **kwargs: Any) -> None:
        handle.cancelled = True

    handle.detach_subprocess = _exit_after_cancel  # type: ignore[method-assign]
    with pytest.raises(JobCancelled):
        server_mod._grid_runner(handle)(  # type: ignore[arg-type]
            [sys.executable, "-c", "import sys; sys.exit(1)"], capture_output=True
        )


def test_every_grid_job_encodes_through_the_cancelling_runner(
    two_stages, inline_deferrer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    client, _storage, match_id, slug, calls, _failing, _raising = two_stages
    inline_deferrer["hold_kinds"] = {"compare-grid-stage"}
    built: list[Any] = []
    real = server_mod._grid_runner

    def _spy(handle: Any) -> Any:
        runner = real(handle)
        built.append(runner)
        return runner

    monkeypatch.setattr(server_mod, "_grid_runner", _spy)
    _grid(client, match_id, slug, [1, 2])
    # The grid job rendered both stages itself, then stitched.
    assert [c.get("only_stage") for c in calls] == [2, 1, None]
    for call in calls:
        assert call["runner"] in built
        assert call["boundary_runner"] in built


def test_each_stage_job_encodes_through_its_own_cancelling_runner(
    two_stages, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """The stage jobs are what encode on the other workers (G10): each one's
    runner answers to that job's cancel, not the grid job's."""
    client, _storage, match_id, slug, calls, _failing, _raising = two_stages
    owner: dict[int, str] = {}
    real = server_mod._grid_runner

    def _spy(handle: Any) -> Any:
        runner = real(handle)
        owner[id(runner)] = handle._job_id
        return runner

    monkeypatch.setattr(server_mod, "_grid_runner", _spy)
    _grid(client, match_id, slug, [1, 2])
    stage_ids = {}
    for job in _stage_jobs(client, match_id):
        (part,) = client.get(f"/api/matches/{match_id}/me/jobs/{job['id']}").json()["parts"]
        stage_ids[int(part["label"].removeprefix("Stage "))] = job["id"]
    stage_calls = [c for c in calls if c.get("only_stage") is not None]
    assert sorted(c["only_stage"] for c in stage_calls) == [1, 2]
    for call in stage_calls:
        assert owner[id(call["runner"])] == stage_ids[call["only_stage"]]
        assert owner[id(call["boundary_runner"])] == stage_ids[call["only_stage"]]


def test_a_cancel_during_the_render_skips_the_upload(
    two_stages, inline_deferrer, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """A cancel that lands as the stitch ends is the export's end: no upload
    of a grid the user stopped, and the job ends cancelled, not succeeded."""
    client, storage, match_id, slug, calls, _failing, _raising = two_stages
    inline_deferrer["hold_kinds"] = {"compare-grid-stage"}
    handles: list[Any] = []
    real_runner = server_mod._grid_runner

    def _spy(handle: Any) -> Any:
        handles.append(handle)
        return real_runner(handle)

    monkeypatch.setattr(server_mod, "_grid_runner", _spy)
    real_render = mp4_grid_mod.render_grid_mp4

    def _cancel_at_the_stitch(*args: Any, **kwargs: Any) -> Any:
        result = real_render(*args, **kwargs)
        if kwargs.get("only_stage") is None:
            handle = handles[-1]
            server_mod.run_sync(handle._registry.cancel(handle._job_id))
        return result

    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _cancel_at_the_stitch)
    pushed: list[Any] = []
    monkeypatch.setattr(
        server_mod.export_storage, "push_match_export_file", lambda *args, **kwargs: pushed.append(args)
    )
    job = _grid(client, match_id, slug, [1, 2])
    assert job["status"] == "cancelled"
    assert pushed == []
