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

from ..match_project import MatchProject
from ..stage_summary_data import TileShot, TileStageData, load_stage_shots
from .project_loader import CompareShooterBundle, CompareStageBundle

logger = logging.getLogger(__name__)


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


def load_expected_rounds(shooters: Sequence[CompareShooterBundle]) -> dict[int, int]:
    """The expected round count per stage number, from whichever shooter's
    project knows it (issue #973). A stage card is match-level and every
    shooter's project describes the same stage, so the first project that
    carries a count wins. Reads ``project.json`` only -- never an audit --
    so a card can ask for it without paying for the overlay's data."""
    out: dict[int, int] = {}
    for bundle in shooters:
        project = _load_project(bundle)
        if project is None:
            continue
        for stage_number in bundle.stages_by_number:
            if stage_number in out:
                continue
            try:
                entry = project.stage(stage_number)
            except KeyError:
                continue
            if entry.stage_rounds is not None and entry.stage_rounds.expected:
                out[stage_number] = entry.stage_rounds.expected
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
    """This stage's audited shots, measured from the beep -- see
    :func:`splitsmith.stage_summary_data.load_stage_shots`, where the
    reader lives since issue #972."""
    return load_stage_shots(stage.audit_path)


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


__all__ = [
    "TileShot",
    "TileStageData",
    "load_expected_rounds",
    "load_overlay_data",
]
