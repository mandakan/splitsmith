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
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import StageData, VideoMatchConfig, VideoMatchResult, VideoStageMatch


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
    tolerance = timedelta(minutes=config.tolerance_minutes)

    # For each stage, which videos fall in its window?
    candidates_per_stage: dict[int, list[Path]] = {}
    for stage in stages:
        upper = stage.scorecard_updated_at
        lower = upper - tolerance
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
