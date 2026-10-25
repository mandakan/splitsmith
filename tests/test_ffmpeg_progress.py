"""ffmpeg's ``-progress`` file read while an encode runs (spec 2026-10-25)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from splitsmith import ffmpeg_progress as fp


def _wait_for(predicate, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        time.sleep(0.005)


def test_fractions_follow_out_time(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []
    with fp.ffmpeg_progress(10.0, seen.append, interval_s=0.01) as extra:
        assert extra[0] == "-progress" and extra[-1] == "-nostats"
        path = Path(extra[1])
        with path.open("a") as out:
            out.write("frame=1\nout_time_us=2500000\nprogress=continue\n")
            out.flush()
            _wait_for(lambda: 0.25 in seen)
            out.write("out_time_us=30000000\nprogress=end\n")
            out.flush()
            _wait_for(lambda: 1.0 in seen)
    assert 0.25 in seen
    assert max(seen) == 1.0  # past the duration is capped
    assert not path.exists()


def test_no_final_one_on_error() -> None:
    seen: list[float] = []
    with pytest.raises(RuntimeError):
        with fp.ffmpeg_progress(10.0, seen.append, interval_s=0.01):
            raise RuntimeError("encode failed")
    assert 1.0 not in seen
