"""Match raw video files to stages using file timestamps vs scorecard_updated_at.

Strategy (per SPEC.md):
1. Read each video's timestamp (st_birthtime when ``prefer_ctime`` and available,
   otherwise st_mtime). All timestamps are normalized to UTC.
2. ``scorecard_updated_at`` is when the score was *typed in*; the actual
   recording finishes 1-10 minutes earlier. So the candidate window is
   ``[scorecard - tolerance, scorecard]`` -- never *after* the scorecard.
3. A stage is confidently matched when exactly one video falls in its window
   and that video does not also fall in any other stage's window. Everything
   else is surfaced (ambiguous, orphan, unmatched) for the CLI to resolve.

Pure function: takes paths + stages + config, does file stat() but no other I/O.

The window math and per-video classification helpers live in this module so
the production UI can render its match-window timeline without duplicating
the heuristic. Keep them pure (no I/O) -- I/O happens in
:func:`video_timestamp` and :func:`match_videos_to_stages` only.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from .config import StageData, VideoMatchConfig, VideoMatchResult, VideoStageMatch

# Per-video classification produced by :func:`classify_video_against_stages`.
# - ``in_window``: timestamp falls within exactly one stage's window
# - ``contested``: timestamp falls within multiple stages' windows
# - ``orphan``: timestamp falls within no stage's window (warm-up clip,
#   neighbour-bay grab, etc.)
# - ``no_timestamp``: video has no recorded timestamp; the SPA should still
#   render the row but skip the timeline tick
VideoClassification = Literal["in_window", "contested", "orphan", "no_timestamp"]


def match_window(
    scorecard_updated_at: datetime, tolerance_minutes: int
) -> tuple[datetime, datetime]:
    """Return ``(lower, upper)`` for a stage's match window.

    Asymmetric: the upper bound is ``scorecard_updated_at`` itself because the
    scorecard is typed *after* the run finishes. The lower bound subtracts the
    full tolerance. Centralised here so the CLI heuristic, the production UI
    timeline, and any future scorers all agree on the shape of the window.
    """
    tolerance = timedelta(minutes=tolerance_minutes)
    return scorecard_updated_at - tolerance, scorecard_updated_at


def classify_video_against_stages(
    timestamp: datetime | None,
    stages: Iterable[StageData],
    tolerance_minutes: int,
) -> tuple[VideoClassification, list[int]]:
    """Classify one video against a list of stages.

    Returns ``(classification, stage_numbers)``. ``stage_numbers`` is the list
    of stages whose windows contain ``timestamp`` -- one element when
    ``in_window``, two or more when ``contested``, empty when ``orphan`` /
    ``no_timestamp``. The list lets the UI render "this clip lands in stages
    3 and 4" hints when contested.
    """
    if timestamp is None:
        return "no_timestamp", []
    hits: list[int] = []
    for stage in stages:
        lower, upper = match_window(stage.scorecard_updated_at, tolerance_minutes)
        if lower <= timestamp <= upper:
            hits.append(stage.stage_number)
    if not hits:
        return "orphan", []
    if len(hits) == 1:
        return "in_window", hits
    return "contested", hits


def video_timestamp(path: Path, *, prefer_ctime: bool) -> datetime:
    """Public alias for :func:`_video_timestamp`. Returns the recording-finished
    time for ``path``, normalized to UTC, using the heuristic's preferred
    source (``st_birthtime`` when available and requested, else ``st_mtime``).
    Use this everywhere a timestamp is captured so the SPA, the CLI, and the
    classifier all see the same value for the same file.
    """
    return _video_timestamp(path, prefer_ctime=prefer_ctime)


def match_videos_to_stages(
    videos: Iterable[Path],
    stages: list[StageData],
    config: VideoMatchConfig,
) -> VideoMatchResult:
    """Greedy 1:1 matching of videos to stages by timestamp."""
    video_paths = list(videos)
    timestamps: dict[Path, datetime] = {
        p: _video_timestamp(p, prefer_ctime=config.prefer_ctime) for p in video_paths
    }

    # For each stage, which videos fall in its window?
    candidates_per_stage: dict[int, list[Path]] = {}
    for stage in stages:
        lower, upper = match_window(stage.scorecard_updated_at, config.tolerance_minutes)
        candidates_per_stage[stage.stage_number] = [
            p for p, ts in timestamps.items() if lower <= ts <= upper
        ]

    # And inversely, which stages does each video belong to?
    stages_per_video: dict[Path, list[int]] = defaultdict(list)
    for stage_num, paths in candidates_per_stage.items():
        for p in paths:
            stages_per_video[p].append(stage_num)

    matches: list[VideoStageMatch] = []
    ambiguous: dict[int, list[Path]] = {}
    unmatched: list[int] = []

    for stage in stages:
        cands = candidates_per_stage[stage.stage_number]
        # A stage is confidently matched only if exactly one candidate AND that
        # candidate is not contested by another stage.
        unique_cands = [p for p in cands if len(stages_per_video[p]) == 1]
        if len(cands) == 1 and len(unique_cands) == 1:
            p = cands[0]
            matches.append(
                VideoStageMatch(
                    stage_number=stage.stage_number,
                    video_path=p,
                    video_timestamp=timestamps[p],
                )
            )
        elif not cands:
            unmatched.append(stage.stage_number)
        else:
            ambiguous[stage.stage_number] = sorted(cands)

    matched_paths = {m.video_path for m in matches}
    ambiguous_paths = {p for paths in ambiguous.values() for p in paths}
    orphans = sorted(p for p in video_paths if p not in matched_paths and p not in ambiguous_paths)

    return VideoMatchResult(
        matches=matches,
        ambiguous_stages=ambiguous,
        orphan_videos=orphans,
        unmatched_stages=unmatched,
    )


def _video_timestamp(path: Path, *, prefer_ctime: bool) -> datetime:
    """Return the recording-finished time for ``path``, normalized to UTC.

    Prefers HFS+/APFS birthtime (true creation time on macOS) when available
    and ``prefer_ctime`` is set; otherwise uses mtime.
    """
    st = path.stat()
    if prefer_ctime and getattr(st, "st_birthtime", None) is not None:
        ts = st.st_birthtime
    else:
        ts = st.st_mtime
    return datetime.fromtimestamp(ts, tz=UTC)
