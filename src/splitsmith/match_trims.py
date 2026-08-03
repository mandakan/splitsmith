"""Trim-only export across every shooter in a match.

Produces the lossless per-stage trims a multi-shooter compare grid needs,
from a beep and a stage time alone. Shot detection is not involved: the
grid's emitter never reads shot data, and ``exports.export_stage`` treats a
missing audit document as zero shots.

``plan_trims`` is pure -- it reads project files and classifies, touching no
media -- so ``--dry-run`` shows exactly what a real run would do.
``run_trims`` drives ``exports.export_stage`` with trim-only flags, one
stage at a time, and never lets one failure end the run.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from . import camera_select
from .compare.project_loader import audit_path_for_stage, trim_path_for_video
from .config import Config, StageData
from .match_model import Match
from .ui import exports
from .ui.project import MatchProject, StageEntry, StageVideo, TrimBlocker, trim_blocker

# The engine's ``StageData`` requires a non-None ``scorecard_updated_at``
# (the video-matching heuristic keys off it), but manually-timed stages on
# scoreboard-less matches have none. A trim export never reads the field, so
# feed this sentinel rather than inventing a real-looking time -- same
# approach as ``ui.server`` and ``mcp.export_tools``.
_PLACEHOLDER_SCORECARD_TIME = datetime(2000, 1, 1, tzinfo=UTC)


SkipReason = TrimBlocker | Literal["source_unreachable", "already_exported", "camera_ambiguous"]
"""Every reason a planned stage can be turned down.

The permanent ones come straight from :data:`ui.project.TrimBlocker` -- the
rule the SPA and the per-stage export endpoint read too -- so a stage cannot
be ineligible here for a reason those surfaces have never heard of. The three
added here are what only a *planner* can see: the source is not on disk right
now, the trim already exists, or this shooter's camera selector matched two
cams on one stage.

Typed onto :attr:`TrimPlanEntry.reason` so pydantic rejects a typo at
construction (#614). The exit code of ``splitsmith match trims`` is decided by
matching on these strings, and a silent typo silently changes it.
"""


class TrimPlanEntry(BaseModel):
    """One shooter-stage, classified as trim-exportable or not."""

    shooter_slug: str
    stage_number: int
    stage_name: str
    camera: str | None = None
    eligible: bool = False
    reason: SkipReason | None = None
    substituted_from: str | None = None


class TrimResult(BaseModel):
    """What actually happened for one planned entry."""

    entry: TrimPlanEntry
    trim_path: Path | None = None
    # Why no trim was written. Empty on a successful export.
    skip_reasons: list[str] = []
    # Things worth telling the user about a trim that *was* written --
    # currently only "the camera changed between plan and run". Separate
    # from ``skip_reasons`` because a note on a successful export is not a
    # skip, and a reader (or a renderer) that treats the two alike either
    # hides the note or reports a success as a failure (#617).
    notes: list[str] = []
    # The camera this run actually stood in for, which is not necessarily
    # ``entry.substituted_from``: that is what the *plan* expected, and a cam
    # that appeared or vanished in between makes them disagree. ``None`` when
    # nothing was substituted or the entry never ran.
    substituted_from: str | None = None


def _choose_video(stage: StageEntry, camera: str | None) -> tuple[StageVideo | None, str | None]:
    """Return the video for this stage plus the camera it stood in for.

    The second element is the requested camera when the primary had to
    substitute, else ``None``. A camera that resolves nowhere in the project
    is caught earlier by ``validate_camera``; here a miss is just this
    stage's gap. Ambiguity (two secondaries, selector ``"secondary"``)
    propagates as :class:`camera_select.CameraResolutionError` -- callers
    downgrade it to a per-stage skip.
    """
    primary = stage.primary()
    if camera is None:
        return primary, None
    chosen = camera_select.resolve_camera(stage.videos, camera)
    if chosen is not None and chosen.beep_time is not None:
        return chosen, None
    return primary, camera


def plan_trims(
    match_root: Path,
    *,
    shooters: list[str] | None = None,
    stages: list[int] | None = None,
    cameras: dict[str, str] | None = None,
    force: bool = False,
) -> list[TrimPlanEntry]:
    """Classify every shooter-stage in the match. Touches no media.

    Reads ``project.json`` per shooter -- authoritative for beeps, roles and
    stage *names*; ``shooter.json`` is a merge-time snapshot nothing keeps in
    sync. The name matters because it becomes the trim's filename, and every
    writer (the SPA's per-stage export, ``splitsmith single``, this module)
    derives that basename from the shooter's own project. A per-shooter
    scoreboard import rewrites ``project.stages`` without touching
    ``match.json``, so planning against the match's names would miss trims
    that already exist and re-cut them under a second filename (#615).

    Raises ``camera_select.CameraResolutionError`` when a requested camera
    matches nothing anywhere in a shooter's project. That is a config error
    for the whole run (a typo, a stale ``compare_camera``) and must stop it;
    per-*stage* ambiguity is recorded as an ineligible entry instead, so one
    over-populated stage never costs the user the rest of the match.
    """
    match = Match.load(match_root)
    wanted_shooters = set(shooters) if shooters else None
    wanted_stages = set(stages) if stages else None
    overrides = cameras or {}

    plan: list[TrimPlanEntry] = []
    for slug in match.shooters:
        if wanted_shooters is not None and slug not in wanted_shooters:
            continue
        shooter_root = Match.shooter_root(match_root, slug)
        project = MatchProject.load(shooter_root)
        camera = overrides.get(slug) or project.compare_camera
        camera_select.validate_camera([s.videos for s in project.stages if not s.skipped], camera)

        for stage in project.stages:
            if wanted_stages is not None and stage.stage_number not in wanted_stages:
                continue
            entry = TrimPlanEntry(
                shooter_slug=slug,
                stage_number=stage.stage_number,
                stage_name=stage.stage_name,
                camera=camera,
            )
            plan.append(_classify(entry, stage, project, shooter_root, camera, force=force))
    return plan


def _classify(
    entry: TrimPlanEntry,
    stage: StageEntry,
    project: MatchProject,
    shooter_root: Path,
    camera: str | None,
    *,
    force: bool,
) -> TrimPlanEntry:
    """Fill in ``eligible`` / ``reason`` / ``substituted_from``. First match wins.

    Camera resolution has to happen mid-way -- it decides *which* video the
    beep check reads -- so the permanent blockers are asked of
    :func:`ui.project.trim_blocker` rather than re-implemented here (#613).
    A deliberately skipped stage answers before camera selection, so it is
    never reported as an ambiguous-camera config problem instead.
    """
    if trim_blocker(stage, stage.primary()) == "skipped":
        return entry.model_copy(update={"reason": "skipped"})

    try:
        chosen, substituted_from = _choose_video(stage, camera)
    except camera_select.CameraResolutionError:
        return entry.model_copy(update={"reason": "camera_ambiguous"})
    entry = entry.model_copy(update={"substituted_from": substituted_from})
    blocker: TrimBlocker | None = trim_blocker(stage, chosen)
    if blocker is not None:
        # Every ``TrimBlocker`` is a ``SkipReason``; the annotation is what
        # holds those two Literals together as the sets evolve (#614).
        reason: SkipReason = blocker
        return entry.model_copy(update={"reason": reason})
    assert chosen is not None  # a missing video is the "no_beep" blocker

    # ``source_present``, not ``resolve_video_path``: the latter mirrors a
    # hosted object into the local cache on first access, so using it as an
    # existence check would download every video in the match during a pass
    # that promises to touch no media (#617).
    try:
        if not project.source_present(shooter_root, chosen.path):
            return entry.model_copy(update={"reason": "source_unreachable"})
    except Exception:  # noqa: BLE001 -- any resolution failure is unreachable
        return entry.model_copy(update={"reason": "source_unreachable"})

    target = trim_path_for_video(project, shooter_root, stage.stage_number, entry.stage_name, chosen)
    if target.exists() and not force:
        return entry.model_copy(update={"reason": "already_exported"})

    return entry.model_copy(update={"eligible": True})


def run_trims(
    match_root: Path,
    plan: list[TrimPlanEntry],
    *,
    progress: Callable[[TrimPlanEntry], None] | None = None,
) -> list[TrimResult]:
    """Write the trim for every eligible entry.

    One stage's failure never ends the run: ffmpeg blowing up on stage 7
    must not cost the user the other twenty-three. Failures come back as a
    ``TrimResult`` with no path and the reason recorded.
    """
    results: list[TrimResult] = []
    for entry in plan:
        if not entry.eligible:
            results.append(TrimResult(entry=entry, skip_reasons=[entry.reason or "ineligible"]))
            continue
        if progress is not None:
            progress(entry)
        results.append(_run_one(match_root, entry))
    return results


def _run_one(match_root: Path, entry: TrimPlanEntry) -> TrimResult:
    """Export one stage's trim. Never raises -- every failure is a ``TrimResult``.

    Everything that re-reads the shooter happens inside the guarded region.
    The user may have edited the project (or unplugged the drive) between
    ``--dry-run`` and the real run, so loading it, finding the stage and
    resolving the source can all fail -- and any of those escaping would
    end ``run_trims``'s loop, costing the user every stage after this one.
    """
    shooter_root = Match.shooter_root(match_root, entry.shooter_slug)
    try:
        project = MatchProject.load(shooter_root)
        stage = project.stage(entry.stage_number)
        chosen, substituted_from = _choose_video(stage, entry.camera)
        if chosen is None or chosen.beep_time is None:
            return TrimResult(entry=entry, skip_reasons=["beep disappeared between plan and run"])
        source = project.resolve_video_path(shooter_root, chosen.path)
    except camera_select.CameraResolutionError as exc:
        return TrimResult(entry=entry, skip_reasons=[f"camera became ambiguous after planning: {exc}"])
    except (ValueError, KeyError, OSError) as exc:
        # ValueError covers pydantic's ValidationError and json's
        # JSONDecodeError (a rewritten or truncated project.json); KeyError
        # is a stage deleted since the plan; OSError is the file or its
        # media gone.
        return TrimResult(entry=entry, skip_reasons=[f"stage unavailable at run time: {exc}"])

    # A substitution that disagrees with the plan means the run is exporting
    # a different camera than --dry-run showed (the shooter gained -- or
    # lost -- the requested cam in between). Export it, but say so.
    notes: list[str] = []
    if substituted_from != entry.substituted_from:
        notes.append(
            "camera substitution changed since planning: planned "
            f"{entry.substituted_from or 'none'}, ran {substituted_from or 'none'}"
        )

    secondaries: list[exports.SecondaryExport] = []
    if chosen.role != "primary":
        secondaries.append(
            exports.SecondaryExport(
                video_id=chosen.video_id,
                source_path=source,
                beep_time_in_source=chosen.beep_time,
                label=chosen.camera_mount or "Selected cam",
            )
        )

    try:
        result = exports.export_stage(
            request=exports.StageExportRequest(
                stage_number=entry.stage_number,
                write_trim=True,
                write_csv=False,
                write_fcpxml=False,
                write_report=False,
                write_overlay=False,
            ),
            audit_path=audit_path_for_stage(project, shooter_root, entry.stage_number),
            exports_dir=project.exports_path(shooter_root),
            # A secondary-cam run wants only the per-cam trim; passing no
            # primary source skips export_stage's primary-trim branch.
            source_video_path=source if chosen.role == "primary" else None,
            stage_data=StageData(
                stage_number=stage.stage_number,
                stage_name=entry.stage_name,
                time_seconds=stage.time_seconds,
                scorecard_updated_at=stage.scorecard_updated_at or _PLACEHOLDER_SCORECARD_TIME,
            ),
            beep_time_in_source=chosen.beep_time,
            pre_buffer_seconds=project.trim_pre_buffer_seconds,
            post_buffer_seconds=project.trim_post_buffer_seconds,
            config=Config(),
            secondaries=secondaries,
        )
    except (exports.StageExportError, OSError, RuntimeError) as exc:
        return TrimResult(
            entry=entry, skip_reasons=[str(exc)], notes=notes, substituted_from=substituted_from
        )

    # ``result.anomalies`` mixes shot-audit findings with export failures.
    # In a trim-only run "No shots detected in the stage window" is the
    # designed state, not a problem, and on a secondary-cam run the absent
    # primary trim is deliberate. Keep only what actually explains this
    # trim, so a clean export reports nothing and a real failure stands out.
    if chosen.role == "primary":
        path = result.trimmed_video_path
        prefix = "trim not written"
    else:
        path = result.secondary_trimmed_paths.get(chosen.video_id)
        prefix = f"secondary cam {chosen.video_id} trim not written"
    reasons = [a for a in result.anomalies if a.startswith(prefix)]
    if path is None and not reasons:
        reasons = [f"{prefix}: export produced no file"]
    return TrimResult(
        entry=entry,
        trim_path=path,
        skip_reasons=reasons,
        notes=notes,
        substituted_from=substituted_from,
    )


__all__ = [
    "TrimPlanEntry",
    "TrimResult",
    "plan_trims",
    "run_trims",
]
