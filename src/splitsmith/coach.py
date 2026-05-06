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

from .config import IntervalClass, IntervalClassSource

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
