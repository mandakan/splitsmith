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

from typing import Any, Final, get_args

from .config import CoachAutoClassifyConfig, IntervalClass, IntervalClassSource, Shot

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
