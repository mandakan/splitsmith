"""Shot times and scoring for the grid overlay, read off disk.

Kept out of ``project_loader`` on purpose: that module also feeds
``compare/emitter.py``, and the FCPXML grid ships clean tiles by
decision, so it should not pay to read every shooter's audit.

Everything here is offline. The renderer is batch and must never reach a
network service mid-render, so scoring comes from the ``MatchProject``
already on disk rather than from the scoreboard.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from ..config import StageRounds
from ..ui.exports import audit_shots_to_engine_shots, read_audit_data
from ..ui.project import MatchProject, StageScorecard, is_stub_audit
from .project_loader import CompareShooterBundle, CompareStageBundle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TileShot:
    """One accepted shot, measured from the beep.

    ``time_from_beep`` is seconds after the start signal. It is
    independent of the trim, the head pad and
    :attr:`CompareStageBundle.beep_offset_in_clip` -- that field converts
    to *clip-local* time, which is a different origin, and the grid's own
    head pad is applied later by the sprite builder.

    There is deliberately no shot number here. The sequence is ordered by
    time and the overlay counts what has been fired, so an index over
    this tuple is all any caller needs. A stored number would have been
    that index plus one, which disagrees with the audit's own
    ``shot_number`` on exactly the input that motivated re-deriving the
    splits -- an audit whose row order is not its time order.
    """

    time_from_beep: float
    split: float


@dataclass(frozen=True)
class TileStageData:
    """Everything the overlay knows about one shooter on one stage.

    Every field is optional in practice: a shooter can have a trim with no
    audit, an audit with no scorecard, or a manually entered stage time
    with neither. Absent data stays absent -- nothing here substitutes a
    zero for a number that was never read.
    """

    label: str
    stage_number: int
    shots: tuple[TileShot, ...] = ()
    stage_time_seconds: float | None = None
    stage_time_is_manual: bool = False
    scorecard: StageScorecard | None = None
    stage_rounds: StageRounds | None = None

    @property
    def shot_count(self) -> int:
        return len(self.shots)

    @property
    def has_shots(self) -> bool:
        return bool(self.shots)

    @property
    def last_shot_time(self) -> float | None:
        return self.shots[-1].time_from_beep if self.shots else None


def load_overlay_data(
    shooters: Sequence[CompareShooterBundle],
) -> dict[tuple[str, int], TileStageData]:
    """Read shots + scoring for every (label, stage) the roster covers.

    Every pair present in ``stages_by_number`` gets an entry, even when
    nothing could be read for it -- the overlay draws less, it does not
    skip a tile, and a caller should never have to distinguish "absent
    from the mapping" from "present but empty".
    """
    out: dict[tuple[str, int], TileStageData] = {}
    for bundle in shooters:
        project = _load_project(bundle)
        for stage_number, stage in sorted(bundle.stages_by_number.items()):
            out[(bundle.label, stage_number)] = _load_tile(bundle, stage, stage_number, project)
    return out


def _load_project(bundle: CompareShooterBundle) -> MatchProject | None:
    """Return the shooter's project, or ``None`` when it cannot be read.

    Warned about once per shooter rather than once per stage: a shooter
    exported from a merged Match carries ``project=None`` on the bundle
    and a 12-stage render would otherwise log the same line 12 times.
    """
    if bundle.project is not None:
        return bundle.project
    try:
        return MatchProject.load(bundle.project_root)
    except OSError as exc:
        # Deliberately narrow. The requirement is that a shooter with no
        # ``project.json`` degrades, which is ``FileNotFoundError``. A
        # validation failure, a hosted state conflict or a broken schema
        # migration are bugs, not missing data, and must stay loud rather
        # than turn into a shooter that silently renders without scoring.
        logger.warning(
            "compare overlay: no readable project.json for %s at %s (%s); "
            "scoring and stage times will be omitted for this shooter",
            bundle.label,
            bundle.project_root,
            exc,
        )
        return None


def _load_shots(stage: CompareStageBundle) -> tuple[TileShot, ...]:
    """Read this stage's audited shots, measured from the beep.

    ``beep_time_in_source=0.0`` makes the engine's ``time_absolute``
    degenerate to ``time_from_beep`` so nothing downstream can mistake it
    for a source-absolute value. A corrupt audit degrades to no shots:
    one bad file must not fail a 12-stage render.

    Splits are re-derived over the time-sorted sequence rather than taken
    from ``audit_shots_to_engine_shots``, which is the one audit consumer
    in this codebase that orders by ``shot_number`` instead of by time
    (``ui/server.py`` and ``coach_distributions`` both sort by time). The
    two orderings agree on every audit a detector writes, but
    ``audit.py``'s CSV apply preserves row order as ``shot_number``, so a
    hand-sorted prep sheet can land shots out of time order -- and the
    helper's splits would then be differences between non-adjacent shots,
    including negative ones. The overlay draws the split on screen, so a
    wrong number is worse than no number. Parsing and the helper's
    rejection filtering are still its job; only ``split`` is recomputed.
    """
    try:
        audit_data = read_audit_data(stage.audit_path)
        if not isinstance(audit_data, dict):
            # Valid JSON, wrong shape. ``read_audit_data`` returns whatever
            # ``json.loads`` produced, so a list/null/string audit would
            # otherwise reach ``.get`` and raise past this handler.
            raise TypeError(f"audit JSON is {type(audit_data).__name__}, expected object")
        if is_stub_audit(audit_data):
            # A beep-confirm placeholder means the same thing as no audit.
            # Belt and braces today: ``is_stub_audit`` requires an empty
            # ``shots``, so this branch can never change the result while
            # that definition holds. It is here so the intent survives a
            # future loosening of the sentinel rather than becoming a
            # silent bug.
            return ()
        engine_shots = audit_shots_to_engine_shots(audit_data, beep_time_in_source=0.0)
    except Exception as exc:  # noqa: BLE001 -- one bad file must not fail the render
        logger.warning(
            "compare overlay: unreadable audit %s (%s); rendering this tile without shots",
            stage.audit_path,
            exc,
        )
        return ()
    ordered = sorted(engine_shots, key=lambda s: s.time_from_beep)
    shots: list[TileShot] = []
    previous: float | None = None
    for shot in ordered:
        # Shot 1's split is the draw; every later split is the gap from
        # the shot before it in time order.
        split = shot.time_from_beep if previous is None else shot.time_from_beep - previous
        previous = shot.time_from_beep
        shots.append(TileShot(time_from_beep=shot.time_from_beep, split=split))
    return tuple(shots)


def _load_tile(
    bundle: CompareShooterBundle,
    stage: CompareStageBundle,
    stage_number: int,
    project: MatchProject | None,
) -> TileStageData:
    """Build one tile's overlay data, degrading rather than raising."""
    shots = _load_shots(stage)
    entry = None
    if project is not None:
        try:
            entry = project.stage(stage_number)
        except KeyError:
            logger.warning(
                "compare overlay: %s has no stage %d in project.json; no scoring for this tile",
                bundle.label,
                stage_number,
            )
    if entry is None:
        return TileStageData(label=bundle.label, stage_number=stage_number, shots=shots)
    return TileStageData(
        label=bundle.label,
        stage_number=stage_number,
        shots=shots,
        # The model treats <=0 as unset: an untouched placeholder stage
        # carries 0.0, and a zero-second stage time is never real.
        stage_time_seconds=entry.time_seconds if entry.time_seconds > 0 else None,
        stage_time_is_manual=entry.time_seconds_manual,
        scorecard=entry.scorecard,
        stage_rounds=entry.stage_rounds,
    )
