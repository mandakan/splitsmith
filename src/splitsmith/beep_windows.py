"""Derive per-stage beep search windows inside a multi-stage single take.

Pure functions: datetimes + seconds in, windows out. No file I/O, no
project access - the job layer (ui/server.py) resolves the video's
wall-clock start, duration, and sibling beeps, then calls in here. Keep
it that way so window math stays unit-testable without audio or ffmpeg.
"""

from __future__ import annotations

from datetime import datetime
from itertools import combinations
from typing import Literal

from pydantic import BaseModel

from .config import BeepWindowConfig


class StagePrior(BaseModel):
    """What we know about one covered stage before detection runs."""

    stage_number: int
    scorecard_updated_at: datetime | None
    time_seconds: float


class StageBeepWindow(BaseModel):
    """A derived search window, seconds into the source file."""

    stage_number: int
    start_s: float
    end_s: float
    source: Literal["scoreboard", "sequential"]


def _clamp(start: float, end: float, duration_s: float, min_window_s: float) -> tuple[float, float]:
    start = max(0.0, min(start, duration_s))
    end = max(0.0, min(end, duration_s))
    if end - start < min_window_s:
        # Widen toward whichever bound has room; a too-short window is
        # worse than a generous one (the detector ranks by silence
        # preference, it does not mind extra quiet audio).
        end = min(duration_s, start + min_window_s)
        start = max(0.0, end - min_window_s)
    return start, end


def derive_scoreboard_windows(
    video_start: datetime,
    duration_s: float,
    priors: list[StagePrior],
    config: BeepWindowConfig,
) -> list[StageBeepWindow]:
    """One window per prior that has a scorecard timestamp.

    Expected beep offset = (scorecard - video_start) - stage_time -
    scorecard_lead_s; the window pads that by pre/post. Priors without a
    scorecard timestamp are skipped - the caller falls back to
    sequential_window for those stages.
    """
    windows: list[StageBeepWindow] = []
    for prior in priors:
        if prior.scorecard_updated_at is None:
            continue
        offset = (prior.scorecard_updated_at - video_start).total_seconds()
        expected = offset - prior.time_seconds - config.scorecard_lead_s
        start, end = _clamp(
            expected - config.pre_pad_s,
            expected + config.post_pad_s,
            duration_s,
            config.min_window_s,
        )
        windows.append(
            StageBeepWindow(stage_number=prior.stage_number, start_s=start, end_s=end, source="scoreboard")
        )
    return windows


def sequential_window(
    prior_anchor_s: float | None,
    duration_s: float,
    config: BeepWindowConfig,
) -> tuple[float, float]:
    """Fallback window when no scorecard timestamps exist.

    The caller computes prior_anchor_s = previous stage's beep + its
    stage_time + reset_margin_s (or None for the first covered stage).
    The window always runs to end of file - each found beep narrows the
    next stage's search, never the current one.
    """
    if prior_anchor_s is None:
        return 0.0, duration_s
    return min(prior_anchor_s, duration_s), duration_s


def find_beep_conflicts(beeps: dict[int, float], threshold_s: float) -> set[int]:
    """Stage numbers whose detected beeps sit closer than threshold_s.

    Two stages latching onto the same physical beep is a carve-up error;
    both are flagged (neither silently wins) so the take overview can
    surface the pair for the user to fix.
    """
    flagged: set[int] = set()
    for (stage_a, beep_a), (stage_b, beep_b) in combinations(sorted(beeps.items()), 2):
        if abs(beep_a - beep_b) < threshold_s:
            flagged.add(stage_a)
            flagged.add(stage_b)
    return flagged
