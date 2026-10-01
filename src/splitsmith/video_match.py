"""Match raw video files to stages using file timestamps vs scorecard_updated_at.

Strategy (per SPEC.md):
1. Read each video's timestamp: the recording-start time the camera embedded
   in the container (``com.apple.quicktime.creationdate``, else
   ``creation_time``), falling back to the file's st_birthtime (when
   ``prefer_ctime`` and available) or st_mtime. All timestamps are normalized
   to UTC. The embedded time comes first because footage shared by a club
   mate (Google Photos, a file server, AirDrop) carries the copy time on the
   filesystem, hours or months from the recording. The start, not the end, is
   the anchor because a head cam often keeps recording past the moment the
   score is typed in, while every device starts before it.
2. ``scorecard_updated_at`` is when the score was *typed in*; the actual
   recording finishes 1-10 minutes earlier. So the candidate window is
   ``[scorecard - tolerance, scorecard]`` -- never *after* the scorecard.
3. A stage is confidently matched when the videos in its window are one run
   (their starts lie within ``same_run_seconds`` of each other; one video is
   trivially one run) and none of them also falls in another stage's
   window. Several cameras on the same run match together. Everything else
   is surfaced (ambiguous, orphan, unmatched) for the CLI to resolve.

Pure function: takes paths + stages + config, does file stat() and one
ffprobe of the container tags per video but no other I/O.

The window math and per-video classification helpers live in this module so
the production UI can render its match-window timeline without duplicating
the heuristic. Keep them pure (no I/O) -- I/O happens in
:func:`video_timestamp` and :func:`match_videos_to_stages` only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Iterable, Mapping
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


def match_window(scorecard_updated_at: datetime, tolerance_minutes: int) -> tuple[datetime, datetime]:
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


# Container tags that carry the capture time, best first. ``creationdate`` is
# the QuickTime capture date with its UTC offset (iPhone, Meta glasses); on
# Meta glasses ``creation_time`` is when the phone app imported the clip, up
# to hours later, so it must lose. Insta360 and Android write only
# ``creation_time``.
_RECORDING_TIME_TAGS = ("com.apple.quicktime.creationdate", "creation_time")

# Anything earlier is a placeholder, not a capture time: exporters write the
# QuickTime epoch (1904) or the Unix epoch when they drop the real value.
_EARLIEST_PLAUSIBLE = datetime(2000, 1, 1, tzinfo=UTC)


def recording_start_from_tags(tags: Mapping[str, str]) -> datetime | None:
    """Return the capture start time from a container's format tags, in UTC.

    ``None`` when no tag holds a plausible timestamp; the caller then falls
    back to the filesystem. Tag lookup ignores case because muxers differ.
    """
    lowered = {k.lower(): v for k, v in tags.items()}
    for key in _RECORDING_TIME_TAGS:
        parsed = _parse_tag_time(lowered.get(key))
        if parsed is not None:
            return parsed
    return None


def read_format_tags(path: Path, *, ffprobe_binary: str = "ffprobe", timeout: float = 8.0) -> dict[str, str]:
    """Return ``path``'s container format tags, or ``{}`` when ffprobe is
    missing or fails. Never raises: a missing tag only means the filesystem
    fallback applies."""
    if not shutil.which(ffprobe_binary):
        return {}
    cmd = [
        ffprobe_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format_tags",
        str(path),
    ]
    try:
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
        payload = json.loads(completed.stdout)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return {}
    tags = (payload.get("format") or {}).get("tags") or {}
    return {str(k): str(v) for k, v in tags.items()}


def embedded_recording_start(path: Path) -> datetime | None:
    """The capture start the camera wrote into ``path``'s container, in UTC,
    or ``None`` when there is none (the caller decides the fallback)."""
    return recording_start_from_tags(read_format_tags(path))


def video_timestamp(path: Path, *, prefer_ctime: bool) -> datetime:
    """Public alias for :func:`_video_timestamp`. Returns the match timestamp
    for ``path``, normalized to UTC: the embedded recording-start time when
    the container has one, else ``st_birthtime`` (when available and
    requested) or ``st_mtime``.
    Use this everywhere a timestamp is captured so the SPA, the CLI, and the
    classifier all see the same value for the same file.
    """
    return _video_timestamp(path, prefer_ctime=prefer_ctime)


def match_videos_to_stages(
    videos: Iterable[Path],
    stages: list[StageData],
    config: VideoMatchConfig,
) -> VideoMatchResult:
    """Match videos to stages by timestamp; each stage takes one run's clips."""
    video_paths = list(videos)
    stamped = {p: _stamp(p, prefer_ctime=config.prefer_ctime) for p in video_paths}
    timestamps: dict[Path, datetime] = {p: ts for p, (ts, _) in stamped.items()}
    # Only a camera's own clock can say two clips are one run. Filesystem
    # times of a batch copy sit seconds apart whatever the clips show.
    embedded: set[Path] = {p for p, (_, is_embedded) in stamped.items() if is_embedded}

    # For each stage, which videos fall in its window?
    candidates_per_stage: dict[int, list[Path]] = {}
    for stage in stages:
        lower, upper = match_window(stage.scorecard_updated_at, config.tolerance_minutes)
        candidates_per_stage[stage.stage_number] = [p for p, ts in timestamps.items() if lower <= ts <= upper]

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
        # A stage is confidently matched only if its candidates are one run
        # AND no candidate is contested by another stage. Two runs in one
        # window (a squad mate filmed minutes later) stay ambiguous.
        exclusive = all(len(stages_per_video[p]) == 1 for p in cands)
        groupable = len(cands) == 1 or all(p in embedded for p in cands)
        if (
            cands
            and exclusive
            and groupable
            and is_one_run([timestamps[p] for p in cands], config.same_run_seconds)
        ):
            ordered = sorted(cands, key=lambda p: (timestamps[p], str(p)))
            matches.append(
                VideoStageMatch(
                    stage_number=stage.stage_number,
                    video_path=ordered[0],
                    video_timestamp=timestamps[ordered[0]],
                    additional_video_paths=ordered[1:],
                )
            )
        elif not cands:
            unmatched.append(stage.stage_number)
        else:
            ambiguous[stage.stage_number] = sorted(cands)

    matched_paths = {p for m in matches for p in (m.video_path, *m.additional_video_paths)}
    ambiguous_paths = {p for paths in ambiguous.values() for p in paths}
    orphans = sorted(p for p in video_paths if p not in matched_paths and p not in ambiguous_paths)

    return VideoMatchResult(
        matches=matches,
        ambiguous_stages=ambiguous,
        orphan_videos=orphans,
        unmatched_stages=unmatched,
    )


def is_one_run(timestamps: Iterable[datetime], same_run_seconds: float) -> bool:
    """True when every timestamp lies within ``same_run_seconds`` of the
    earliest: several cameras that started on the same run. Empty is not a
    run."""
    ordered = sorted(timestamps)
    if not ordered:
        return False
    return (ordered[-1] - ordered[0]).total_seconds() <= same_run_seconds


def stages_in_span(
    start: datetime,
    end: datetime,
    stages: Iterable[StageData],
    tolerance_minutes: int,
) -> list[int]:
    """Stage numbers whose match window overlaps [start, end], ordered by
    scorecard_updated_at (shooting order). The coverage suggestion for a
    single take spanning several runs; a one-stage clip returns one hit,
    so the single-file case needs no special path."""
    hits: list[StageData] = []
    for stage in stages:
        lower, upper = match_window(stage.scorecard_updated_at, tolerance_minutes)
        if start <= upper and end >= lower:
            hits.append(stage)
    hits.sort(key=lambda s: s.scorecard_updated_at)
    return [s.stage_number for s in hits]


def _video_timestamp(path: Path, *, prefer_ctime: bool) -> datetime:
    """Return the match timestamp for ``path``, normalized to UTC.

    The embedded recording-start time wins. Without one, prefers HFS+/APFS
    birthtime when available and ``prefer_ctime`` is set; otherwise mtime.
    Raises ``OSError`` when the file cannot be stat'd, as before.
    """
    return _stamp(path, prefer_ctime=prefer_ctime)[0]


def _stamp(path: Path, *, prefer_ctime: bool) -> tuple[datetime, bool]:
    """:func:`_video_timestamp` plus whether the time came from the
    container (``True``) or the filesystem (``False``)."""
    st = path.stat()
    embedded = embedded_recording_start(path)
    if embedded is not None:
        return embedded, True
    if prefer_ctime and getattr(st, "st_birthtime", None) is not None:
        ts = st.st_birthtime
    else:
        ts = st.st_mtime
    return datetime.fromtimestamp(ts, tz=UTC), False


def _parse_tag_time(raw: str | None) -> datetime | None:
    """Parse an ISO-8601 tag value (``2026-04-12T13:41:03+0200``,
    ``2026-04-12T11:41:03.000000Z``) to UTC. A value without an offset is
    taken as UTC, which is what ``creation_time`` means by spec."""
    if not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    # ``+0200`` -> ``+02:00``: fromisoformat on 3.11 wants the colon.
    if len(text) > 5 and text[-5] in "+-" and text[-4:].isdigit():
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    return parsed if parsed >= _EARLIEST_PLAUSIBLE else None
