"""What a highlight reel is made from, read off a shooter's project.

The I/O side of the reel (spec 2026-10-18-highlight-reel-design): each
stage's audit doc, its tile (read the way the match summary reads it),
its footage and where the beep sits in it, and whether it is ready. The
footage is each camera's audit trim under ``trimmed/``, which every audited
stage has, else its 720p web rendition (the same window, so the same beep).
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .coach import statistic_splits
from .config import HighlightConfig, MomentKind, VideoMetadata
from .export_naming import stage_display_name
from .fcpxml_gen import probe_video
from .highlights import (
    OUT_OF_PLAY,
    StageReadiness,
    find_moments,
    pick_reel,
    reel_unlocked,
    stage_readiness,
)
from .match_model import is_standard_start
from .match_project import MatchProject, StageVideo
from .reel_plan import FootageClip, StageFootage, plan_reel
from .reel_render import ReelRenderResult, reel_format, render_reel
from .segment_cache import SegmentCache
from .stage_summary_data import TileStageData, load_stage_events, load_stage_reloads, load_stage_shots
from .trim import beep_in_trim, web_trim_path
from .ui.audio import resolve_trim_for_read, trim_params_path, trim_pre_buffer_seconds_for

logger = logging.getLogger(__name__)

Probe = Callable[[Path], VideoMetadata]

#: The Shorts / Reels cut picks fewer beats and cuts a shorter montage.
SHORT_BEATS = 3
SHORT_MONTAGE_CUTS = 4


@dataclass(frozen=True)
class ReelInputs:
    #: Ready stages only, in stage order.
    stages: tuple[TileStageData, ...]
    footage: dict[int, StageFootage]
    #: Every stage.
    readiness: tuple[StageReadiness, ...]
    #: The ready stages' primaries, for the canvas.
    primaries: tuple[VideoMetadata, ...]


#: How far a trim's recorded beep may sit from the video's before the trim
#: counts as cut for another beep (the sidecar rounds to 4 decimals).
STALE_BEEP_S = 0.001


def _clip_for(project: MatchProject, root: Path, stage_number: int, video: StageVideo) -> Path | None:
    """The trim the players read (``resolve_trim_for_read``: new-keyed, else
    a legacy-keyed one), else its web rendition."""
    trim = resolve_trim_for_read(root, stage_number, video, project=project)
    if trim is not None:
        return trim
    keyed = project.trimmed_path(root) / f"stage{stage_number}_cam_{video.video_id}_trimmed.mp4"
    web = web_trim_path(keyed)
    return web if web.is_file() else None


def _cut_for_another_beep(trim: Path, beep_time: float) -> bool:
    """The trim's params sidecar records the beep it was cut around; a beep
    moved since (a re-trim pending or failed) would shift every window."""
    try:
        recorded = float(json.loads(trim_params_path(trim).read_text(encoding="utf-8"))["beep_time"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return abs(recorded - beep_time) > STALE_BEEP_S


def _footage(
    project: MatchProject, root: Path, stage_number: int, video: StageVideo, probe: Probe
) -> tuple[FootageClip, VideoMetadata] | None:
    if video.beep_time is None:
        return None
    path = _clip_for(project, root, stage_number, video)
    if path is None:
        return None
    # The web rendition is cut from the trim, so the trim's params name its window too.
    trim = (
        path
        if path.name.endswith("_trimmed.mp4")
        else path.with_name(path.name[: -len("_web.mp4")] + "_trimmed.mp4")
    )
    if _cut_for_another_beep(trim, video.beep_time):
        logger.warning("highlight reel: %s was cut for another beep; it needs a re-trim", path)
        return None
    pre_buffer = trim_pre_buffer_seconds_for(trim, default=project.trim_pre_buffer_seconds)
    try:
        meta = probe(path)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("highlight reel: cannot probe %s (%s); the stage has no footage", path, exc)
        return None
    return FootageClip(path, beep_in_trim(video.beep_time, pre_buffer), meta.duration_seconds), meta


def _audit_doc(path: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def load_reel_inputs(
    project: MatchProject, root: Path, *, label: str, probe: Probe = probe_video
) -> ReelInputs:
    statuses = project.stage_statuses(root)
    audit_dir = project.audit_path(root)
    stages: list[TileStageData] = []
    footage: dict[int, StageFootage] = {}
    readiness: list[StageReadiness] = []
    primaries: list[VideoMetadata] = []
    for stage in project.stages:
        n = stage.stage_number
        primary = stage.primary()
        found = _footage(project, root, n, primary, probe) if primary is not None else None
        audit_path = audit_dir / f"stage{n}.json"
        row = stage_readiness(
            n,
            statuses[n],
            _audit_doc(audit_path),
            has_video=primary is not None,
            has_footage=found is not None,
        )
        readiness.append(row)
        if row.state != "ready" or found is None:
            continue
        clip, meta = found
        secondary: FootageClip | None = None
        for video in stage.videos:
            # Only a beep someone confirmed: an auto-detected one can sit
            # off the primary's, and the replay would centre on the wrong shot.
            if video.role == "secondary" and video.beep_reviewed:
                other = _footage(project, root, n, video, probe)
                if other is not None:
                    secondary = other[0]
                    break
        name = stage_display_name(n, stage.stage_name)
        footage[n] = StageFootage(n, name, clip, secondary)
        primaries.append(meta)
        stages.append(
            TileStageData(
                label=label,
                stage_number=n,
                shots=load_stage_shots(audit_path),
                stage_time_seconds=stage.time_seconds if stage.time_seconds > 0 else None,
                stage_time_is_manual=stage.time_seconds_manual,
                scorecard=stage.scorecard,
                stage_rounds=stage.stage_rounds,
                reloads=load_stage_reloads(audit_path),
                events=load_stage_events(audit_path),
                standard_start=is_standard_start(stage.start),
            )
        )
    return ReelInputs(tuple(stages), footage, tuple(readiness), tuple(primaries))


def end_lines(stages: Sequence[TileStageData]) -> tuple[str, ...]:
    """The end card's figures: the best Draw over standard starts, the
    average split, the rounds. A figure the data lacks is left out."""
    lines: list[str] = []
    draws = [s.draw for s in stages if s.standard_start and s.draw is not None]
    if draws:
        lines.append(f"Best draw {min(draws):.2f}")
    splits = [x for s in stages for x in statistic_splits(s.shots)]
    if splits:
        lines.append(f"Avg split {sum(splits) / len(splits):.2f}")
    rounds = sum(s.shot_count for s in stages)
    if rounds:
        lines.append(f"{rounds} rounds")
    return tuple(lines)


@dataclass(frozen=True)
class ReelOutcome:
    #: Stages keeping the reel locked; on a forced reel, the stages it left out.
    blockers: tuple[StageReadiness, ...]
    result: ReelRenderResult | None
    #: The stage numbers the reel was drawn from (the ready ones).
    stages: tuple[int, ...] = ()
    #: The end card's figures (best draw, average split, rounds).
    end_lines: tuple[str, ...] = ()


def make_reel(
    project: MatchProject,
    root: Path,
    *,
    label: str,
    output: Path,
    upright: bool = True,
    force: bool = False,
    moments: Sequence[MomentKind] | None = None,
    short: bool = False,
    config: HighlightConfig | None = None,
    probe: Probe = probe_video,
    segment_cache: SegmentCache | None = None,
    ffmpeg_binary: str = "ffmpeg",
) -> ReelOutcome:
    """Find, pick, plan and render the shooter's reel. Locked (a stage not
    yet audited or broken down) renders nothing and names the blockers,
    unless ``force``, which renders from the ready stages alone.
    ``moments`` is the menu's choice (else the config's); ``short`` the
    Shorts / Reels cut, with fewer beats and a shorter montage."""
    cfg = config or HighlightConfig()
    inputs = load_reel_inputs(project, root, label=label, probe=probe)
    blockers = tuple(r for r in inputs.readiness if r.state != "ready" and r.state not in OUT_OF_PLAY)
    if not inputs.stages or (not reel_unlocked(inputs.readiness) and not force):
        return ReelOutcome(blockers, None)
    picks = pick_reel(
        find_moments(inputs.stages, cfg),
        inputs.stages,
        kinds=moments,
        max_beats=SHORT_BEATS if short else None,
        montage_cuts=SHORT_MONTAGE_CUTS if short else 8,
        config=cfg,
    )
    figures = end_lines(inputs.stages)
    items = plan_reel(
        picks,
        inputs.footage,
        title=project.name,
        shooter=label,
        end_lines=figures,
        short=short,
    )
    with tempfile.TemporaryDirectory(prefix="splitsmith-reel-") as tmp:
        result = render_reel(
            items,
            sequence=reel_format(inputs.primaries, upright=upright),
            output_path=output,
            work_dir=Path(tmp),
            ffmpeg_binary=ffmpeg_binary,
            segment_cache=segment_cache,
        )
    return ReelOutcome(
        blockers, result, stages=tuple(s.stage_number for s in inputs.stages), end_lines=figures
    )
