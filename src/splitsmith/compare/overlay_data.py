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
    """

    number: int
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
    except Exception as exc:  # noqa: BLE001 -- any unreadable project degrades
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
    """
    try:
        audit_data = read_audit_data(stage.audit_path)
    except Exception as exc:  # noqa: BLE001 -- one bad file must not fail the render
        logger.warning(
            "compare overlay: unreadable audit %s (%s); rendering this tile without shots",
            stage.audit_path,
            exc,
        )
        return ()
    if is_stub_audit(audit_data):
        # A beep-confirm placeholder means the same thing as no audit.
        # Belt and braces today: ``is_stub_audit`` requires an empty
        # ``shots``, so this branch can never change the result while that
        # definition holds. It is here so the intent survives a future
        # loosening of the sentinel rather than becoming a silent bug.
        return ()
    engine_shots = audit_shots_to_engine_shots(audit_data, beep_time_in_source=0.0)
    ordered = sorted(engine_shots, key=lambda s: s.time_from_beep)
    return tuple(
        TileShot(number=i + 1, time_from_beep=shot.time_from_beep, split=shot.split)
        for i, shot in enumerate(ordered)
    )


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
