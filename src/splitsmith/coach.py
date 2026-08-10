"""Coaching annotations on audit-JSON shot dicts (issue #158).

The Coach page persists per-shot coaching data into the existing per-stage
audit JSON. The wire format is a flat extension of each ``shots[i]`` dict:

- ``interval_class``: one of the values in :data:`COACH_INTERVAL_CLASSES`
  or absent.
- ``interval_class_source``: ``"auto"`` or ``"manual"`` -- absent iff
  ``interval_class`` is absent. ``manual`` survives re-classification.
- ``improvement_flag``: bool, defaults False.
- ``coaching_note``: free text, absent when not set.

This module is the single source of truth for those field names and the
shape of a coach-annotated shot dict; the auto-classifier (#160), the
HTTP layer (#161), and the histogram path (#163) all go through it.

Pure functions only -- no I/O. Callers own the audit JSON read/write.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final, Protocol, get_args

from .config import (
    CoachAutoClassifyConfig,
    IntervalClass,
    IntervalClassSource,
    Shot,
)

COACH_INTERVAL_CLASSES: Final[tuple[str, ...]] = get_args(IntervalClass)
COACH_INTERVAL_CLASS_SOURCES: Final[tuple[str, ...]] = get_args(IntervalClassSource)

# Public field names. Importers should reference these constants rather
# than hard-coding strings so a future rename has one place to land.
FIELD_INTERVAL_CLASS: Final = "interval_class"
FIELD_INTERVAL_CLASS_SOURCE: Final = "interval_class_source"
FIELD_IMPROVEMENT_FLAG: Final = "improvement_flag"
FIELD_COACHING_NOTE: Final = "coaching_note"

COACH_FIELDS: Final[tuple[str, ...]] = (
    FIELD_INTERVAL_CLASS,
    FIELD_INTERVAL_CLASS_SOURCE,
    FIELD_IMPROVEMENT_FLAG,
    FIELD_COACHING_NOTE,
)


def read_coach_fields(shot: dict[str, Any]) -> dict[str, Any]:
    """Extract coach annotations from an audit-JSON shot dict.

    Returns a dict containing only the coach fields that are set, with
    canonical types. Fields that are absent or carry a placeholder value
    (False ``improvement_flag``, ``None`` note) are omitted from the
    return so callers can ``len()`` it to ask "is this shot annotated?".
    """
    out: dict[str, Any] = {}
    cls = shot.get(FIELD_INTERVAL_CLASS)
    src = shot.get(FIELD_INTERVAL_CLASS_SOURCE)
    if cls is not None:
        if cls not in COACH_INTERVAL_CLASSES:
            raise ValueError(f"unknown interval_class: {cls!r}")
        if src not in COACH_INTERVAL_CLASS_SOURCES:
            raise ValueError(
                f"interval_class_source must be one of {COACH_INTERVAL_CLASS_SOURCES} "
                f"when interval_class is set; got {src!r}"
            )
        out[FIELD_INTERVAL_CLASS] = cls
        out[FIELD_INTERVAL_CLASS_SOURCE] = src
    elif src is not None:
        raise ValueError("interval_class_source set without interval_class -- inconsistent state")
    flag = shot.get(FIELD_IMPROVEMENT_FLAG, False)
    if flag:
        out[FIELD_IMPROVEMENT_FLAG] = bool(flag)
    note = shot.get(FIELD_COACHING_NOTE)
    if isinstance(note, str) and note != "":
        out[FIELD_COACHING_NOTE] = note
    return out


def write_coach_fields(
    shot: dict[str, Any],
    *,
    interval_class: str | None = None,
    interval_class_source: str | None = None,
    improvement_flag: bool | None = None,
    coaching_note: str | None = None,
    clear_class: bool = False,
    clear_note: bool = False,
) -> dict[str, Any]:
    """Patch coach annotations on a shot dict, returning the same dict.

    Each kwarg defaults to ``None`` meaning "leave alone". Use
    ``clear_class=True`` to drop both class fields (e.g. when reverting a
    manual override back to auto-classified). Use ``clear_note=True`` to
    drop the note explicitly. ``improvement_flag`` is set with a literal
    bool; pass ``False`` to clear it.

    Validates the same invariant as :class:`Shot`: ``interval_class`` and
    ``interval_class_source`` must be set or unset together.
    """
    if clear_class:
        shot.pop(FIELD_INTERVAL_CLASS, None)
        shot.pop(FIELD_INTERVAL_CLASS_SOURCE, None)
    elif interval_class is not None or interval_class_source is not None:
        if interval_class is None or interval_class_source is None:
            raise ValueError("interval_class and interval_class_source must be set together")
        if interval_class not in COACH_INTERVAL_CLASSES:
            raise ValueError(f"unknown interval_class: {interval_class!r}")
        if interval_class_source not in COACH_INTERVAL_CLASS_SOURCES:
            raise ValueError(f"unknown interval_class_source: {interval_class_source!r}")
        shot[FIELD_INTERVAL_CLASS] = interval_class
        shot[FIELD_INTERVAL_CLASS_SOURCE] = interval_class_source

    if improvement_flag is not None:
        if improvement_flag:
            shot[FIELD_IMPROVEMENT_FLAG] = True
        else:
            shot.pop(FIELD_IMPROVEMENT_FLAG, None)

    if clear_note:
        shot.pop(FIELD_COACHING_NOTE, None)
    elif coaching_note is not None:
        if coaching_note == "":
            shot.pop(FIELD_COACHING_NOTE, None)
        else:
            shot[FIELD_COACHING_NOTE] = coaching_note

    return shot


# ---------------------------------------------------------------------------
# Auto-classifier (#160). The rule is purely a function of the gap to the
# previous shot (or "first shot" for index 0). Manual classifications are
# always preserved; auto-classifications are recomputed every call so a
# timing edit reflows automatically.
# ---------------------------------------------------------------------------


def _classify_gap(gap_s: float | None, config: CoachAutoClassifyConfig) -> IntervalClass:
    """Map a gap to the auto-class. ``None`` means "this is shot 1"."""
    if gap_s is None:
        return "first_shot"
    if gap_s <= config.split_max_s:
        return "split"
    if gap_s <= config.transition_max_s:
        return "transition"
    return "movement"


def reload_hinted(gap_s: float | None, config: CoachAutoClassifyConfig) -> bool:
    """True when the auto-class is movement *and* the gap exceeds the
    reload-hint threshold. UI surfaces a "could be reload?" badge.
    """
    if gap_s is None:
        return False
    return gap_s > config.reload_hint_min_s


# ---------------------------------------------------------------------------
# Split statistics (#772). Best/avg/worst figures describe shooting, not
# the run's dead time, so only ``"split"``-classed intervals feed them.
# ---------------------------------------------------------------------------

# The unclassified fallback mirrors the auto-classifier's split rule
# (``CoachAutoClassifyConfig.split_max_s``) rather than the FCPXML band's
# ``transition_min`` (1.0s): 35% of corpus intervals sit between the two,
# so any other cutoff would move the figures the moment a stage gets
# classified (issue #773).
SPLIT_STAT_SPLIT_MAX: Final[float] = CoachAutoClassifyConfig().split_max_s


class SplitStatInterval(Protocol):
    """What :func:`statistic_splits` reads off a shot record."""

    @property
    def split(self) -> float: ...

    @property
    def interval_class(self) -> IntervalClass | None: ...


def statistic_splits(
    shots: Sequence[SplitStatInterval],
    *,
    split_max: float = SPLIT_STAT_SPLIT_MAX,
) -> list[float]:
    """The splits eligible for split statistics (best/avg/worst), in order.

    ``shots`` is one stage's full time-ordered shot sequence, draw first.
    On a stage with any classified interval, exactly the ``"split"``-classed
    intervals count - transitions, movement and reloads are the run's dead
    time, not its shooting. A stage with no classification at all falls
    back to the auto-classifier's split rule (:func:`_classify_gap`):
    index 0 is the draw, anything above ``split_max`` is not a split (the
    boundary itself is inclusive, as in the rule).

    An empty return is meaningful - a stage of transitions and reloads has
    no splits to average, and callers render nothing rather than a zero.

    Mirrored by ``statisticSplits`` in ``ui_static/src/lib/splits.ts``; if
    the rule changes, update both.

    Partial classification (#775): the save endpoint and the coach GET
    both run the auto-classifier, so an audited stage is fully classified
    for every shot that has ``ms_after_beep``. The ``any`` branch below is
    therefore all-or-nothing in practice; shots without ``ms_after_beep``
    never reach this function (audit_shots_to_engine_shots drops them).
    """
    if any(s.interval_class is not None for s in shots):
        return [s.split for s in shots if s.interval_class == "split"]
    return [s.split for i, s in enumerate(shots) if i > 0 and s.split <= split_max]


def classify_intervals_in_dicts(
    shots: list[dict[str, Any]],
    config: CoachAutoClassifyConfig,
) -> list[dict[str, Any]]:
    """Apply the auto-classifier to a list of audit-JSON shot dicts.

    Mutates ``shots`` in place and returns it. Walks in time order (by
    ``ms_after_beep``, falling back to ``shot_number`` then list index).
    Shots whose ``interval_class_source`` is ``"manual"`` are left
    untouched. Shots with ``"auto"`` or no source are (re)written to the
    rule's verdict with ``source="auto"``.

    Required per-shot fields: ``ms_after_beep`` (number, milliseconds
    from the beep). Shots without it are skipped (no class is written).
    """
    indexed = list(enumerate(shots))
    indexed.sort(key=_sort_key)
    prev_ms: float | None = None
    for _orig_idx, shot in indexed:
        ms = shot.get("ms_after_beep")
        if ms is None:
            prev_ms = None
            continue
        gap_s: float | None
        if prev_ms is None:
            gap_s = None  # first shot in the stage
        else:
            gap_s = (float(ms) - prev_ms) / 1000.0
        prev_ms = float(ms)

        if shot.get(FIELD_INTERVAL_CLASS_SOURCE) == "manual":
            continue

        new_class = _classify_gap(gap_s, config)
        write_coach_fields(
            shot,
            interval_class=new_class,
            interval_class_source="auto",
        )
    return shots


def heal_unclassified(
    shots: Any,
    config: CoachAutoClassifyConfig | None = None,
) -> bool:
    """Backfill ``interval_class`` on a legacy audit doc's shot list (#780).

    Every consumer of :func:`statistic_splits` that reads raw audit shots
    needs this: a doc written before #775 carries no classes at all, and
    without a heal it silently falls back to the threshold rule and
    reports different figures than its neighbours. This is the one
    definition of "needs a heal" -- four surfaces used to carry a copy and
    one had already drifted.

    ``shots`` is the raw ``audit["shots"]`` value, since that is what
    every caller has; anything that is not a list is "nothing to heal",
    and non-dict entries inside the list are skipped.

    Returns True iff the classifier ran, so a caller on a writable surface
    can decide whether to persist. Read-only surfaces (the share card, the
    overlay renderer, the compare payload) ignore the result: they must
    reach the same in-memory verdict without writing back.

    A shot with no ``interval_class`` but ``interval_class_source ==
    "manual"`` is an explicitly-cleared "do not reclassify" marker and
    does not call for a heal. :func:`classify_intervals_in_dicts` skips
    such a shot per-shot anyway, so this clause only decides whether the
    pass runs at all -- which is exactly what decides whether the other
    shots' stale auto classes get rewritten, and whether a persisting
    caller has anything to write.

    Mutates the shot dicts in place.
    """
    if not isinstance(shots, list):
        return False
    dicts = [s for s in shots if isinstance(s, dict)]
    needs_heal = any(
        s.get("ms_after_beep") is not None
        and s.get(FIELD_INTERVAL_CLASS) is None
        and s.get(FIELD_INTERVAL_CLASS_SOURCE) != "manual"
        for s in dicts
    )
    if not needs_heal:
        return False
    classify_intervals_in_dicts(dicts, config or CoachAutoClassifyConfig())
    return True


def classify_intervals_in_models(
    shots: list[Shot],
    config: CoachAutoClassifyConfig,
) -> list[Shot]:
    """Pydantic equivalent of :func:`classify_intervals_in_dicts`.

    Returns a new list of Shot instances; the inputs are not mutated.
    Walks in ``time_from_beep`` order (matching the dict path's behaviour).
    """
    indexed = sorted(enumerate(shots), key=lambda p: (p[1].time_from_beep, p[1].shot_number))
    new_classes: dict[int, tuple[IntervalClass | None, IntervalClassSource | None]] = {}
    prev_t: float | None = None
    for orig_idx, shot in indexed:
        t = shot.time_from_beep
        gap_s = None if prev_t is None else (t - prev_t)
        prev_t = t
        if shot.interval_class_source == "manual":
            new_classes[orig_idx] = (shot.interval_class, "manual")
        else:
            new_classes[orig_idx] = (_classify_gap(gap_s, config), "auto")
    out: list[Shot] = []
    for i, shot in enumerate(shots):
        cls, src = new_classes[i]
        out.append(shot.model_copy(update={"interval_class": cls, "interval_class_source": src}))
    return out


def is_classification_stale(
    shot: dict[str, Any] | Shot,
    *,
    gap_s: float | None,
    config: CoachAutoClassifyConfig,
) -> bool:
    """Return True iff the stored auto-classification disagrees with what
    the rule would assign now. Computed on read; never persisted.

    For ``manual`` shots the stale flag is also surfaced (the rule's
    verdict differs from the user's pick) so the UI can show a hint, but
    the caller decides whether to act on it. For shots with no class
    set, returns False.
    """
    if isinstance(shot, Shot):
        cls = shot.interval_class
    else:
        cls = shot.get(FIELD_INTERVAL_CLASS)
    if cls is None:
        return False
    return _classify_gap(gap_s, config) != cls


def _sort_key(pair: tuple[int, dict[str, Any]]) -> tuple[float, int, int]:
    orig_idx, shot = pair
    ms = shot.get("ms_after_beep")
    ms_key = float(ms) if isinstance(ms, (int, float)) else float("inf")
    sn = shot.get("shot_number")
    sn_key = int(sn) if isinstance(sn, (int, float)) else orig_idx
    return (ms_key, sn_key, orig_idx)
