"""One clock for an audit document: the clip's (issue #1497).

Audit (desktop and phone) draws a shot's ``time`` on the clip it plays, the
stage's trim when one exists, and anchors the beep at the clip's beep. An
audit made before its stage had a trim counts from the source's start, and a
save made while the clip's beep was unknown kept a source beep beside clip
times. Both drew every marker in the wrong place, and the second made every
split read as before the beep.

:func:`rebase_to_clip` moves a document onto the clip's clock:

- **source clock**: every shot sits at or after the stored beep, so the
  whole document counts from the source's start. Shots and pending
  candidates move by the difference between the beeps; ``ms_after_beep``
  and regions are beep-relative and stay.
- **mixed**: a shot sits before the stored beep, so the shots are already on
  the clip's clock and only the beep is wrong. The beep moves and
  ``ms_after_beep`` is computed again from it.

The rule cannot tell a mixed document from a source-clock one when the
stored beep is within a draw of the clip's beep; every mixed document found
was 10 s or more apart, and the save no longer writes them
(:func:`stamp_clip_beep`).

Pure: no file is read here.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

#: Beeps closer than this are the same beep (the docs round times to 1 ms).
SAME_BEEP_S = 0.0015

Rebase = Literal["ok", "shifted", "beep_fixed"]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _shot_times(doc: dict[str, Any]) -> list[float]:
    out: list[float] = []
    for shot in doc.get("shots") or []:
        if isinstance(shot, dict):
            t = _number(shot.get("time"))
            if t is not None:
                out.append(t)
    return out


def classify(doc: dict[str, Any] | None, clip_beep: float | None) -> Rebase:
    """What :func:`rebase_to_clip` would do to ``doc``: ``ok`` when it is on
    the clip's clock already (or there is nothing to compare)."""
    if not doc or clip_beep is None:
        return "ok"
    stored = _number(doc.get("beep_time"))
    if stored is None or abs(stored - clip_beep) < SAME_BEEP_S:
        return "ok"
    times = _shot_times(doc)
    if times and min(times) < stored - SAME_BEEP_S:
        return "beep_fixed"
    return "shifted"


def rebase_to_clip(
    doc: dict[str, Any] | None, clip_beep: float | None
) -> tuple[dict[str, Any] | None, Rebase]:
    """``doc`` on the clip's clock, and what was done (a copy; the input is
    never changed). See the module docstring for the two cases."""
    kind = classify(doc, clip_beep)
    if kind == "ok" or doc is None or clip_beep is None:
        return doc, "ok"
    stored = float(doc["beep_time"])
    out = copy.deepcopy(doc)
    if kind == "shifted":
        _shift_times(out, clip_beep - stored)
        out["beep_time"] = clip_beep
        return out, "shifted"
    stamp_clip_beep(out, clip_beep)
    return out, "beep_fixed"


def _shift_times(doc: dict[str, Any], delta: float) -> None:
    """Move every shot and pending candidate by ``delta`` seconds, in place."""
    for shot in doc.get("shots") or []:
        if isinstance(shot, dict) and _number(shot.get("time")) is not None:
            shot["time"] = round(float(shot["time"]) + delta, 3)
    pending = doc.get("_candidates_pending_audit")
    if isinstance(pending, dict):
        for cand in pending.get("candidates") or []:
            if isinstance(cand, dict) and _number(cand.get("time")) is not None:
                cand["time"] = round(float(cand["time"]) + delta, 3)


def stamp_clip_beep(doc: dict[str, Any], clip_beep: float | None) -> bool:
    """Write the clip's beep on a document whose shots are on the clip's
    clock (what Audit saves) and compute ``ms_after_beep`` from it, in
    place; whether anything changed. A save never mixes clocks, whatever
    beep the page fell back to."""
    if clip_beep is None:
        return False
    changed = _number(doc.get("beep_time")) is None or abs(float(doc["beep_time"]) - clip_beep) >= 1e-9
    doc["beep_time"] = clip_beep
    for shot in doc.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        t = _number(shot.get("time"))
        if t is None:
            continue
        ms = round((t - clip_beep) * 1000)
        if shot.get("ms_after_beep") != ms:
            shot["ms_after_beep"] = ms
            changed = True
    return changed


#: The video whose clip an audit's times are on (#1496): the stage's primary
#: when it was saved. A change of primary carries the audit to the new
#: camera's clip (:func:`carry_to_clip`) and restamps it.
CLIP_VIDEO_FIELD = "clip_video_id"


def stamp_clip_video(doc: dict[str, Any], video_id: str | None) -> bool:
    """Record ``video_id`` as the camera ``doc``'s times are on, in place;
    whether it changed. ``None`` (a stage with no primary) writes nothing."""
    if not video_id or doc.get(CLIP_VIDEO_FIELD) == video_id:
        return False
    doc[CLIP_VIDEO_FIELD] = video_id
    return True


def carry_to_clip(
    doc: dict[str, Any],
    *,
    from_clip_beep: float | None,
    to_clip_beep: float,
    to_video_id: str,
    event_id: str,
    ts: str,
) -> tuple[dict[str, Any], float]:
    """``doc`` moved from one camera's clip onto another's, and the shift in
    seconds (a copy; the input is never changed).

    The cameras share the beep, so a shot keeps its time from the beep:
    shots and pending candidates move by the difference of the two clip
    beeps, and ``ms_after_beep``, splits and regions (all beep-relative)
    stay. The document is first put on the old clip's clock
    (:func:`rebase_to_clip` with ``from_clip_beep``), so a source-clock or
    mixed document carries from where Audit drew it. The shift is the beep
    sync's: a camera farther from the gun hears each shot a few ms later
    than its beep says, which a later re-detection on the new clip does not
    correct, since a carried audit keeps its shots. An ``audit_events``
    entry records both cameras and the shift, so the move can be undone.
    """
    out, _kind = rebase_to_clip(doc, from_clip_beep)
    out = copy.deepcopy(out if out is not None else doc)
    stored = _number(out.get("beep_time"))
    shift = to_clip_beep - stored if stored is not None else 0.0
    if shift:
        _shift_times(out, shift)
    stamp_clip_beep(out, to_clip_beep)
    from_video_id = out.get(CLIP_VIDEO_FIELD)
    out[CLIP_VIDEO_FIELD] = to_video_id
    events = list(out.get("audit_events") or [])
    events.append(
        {
            "id": event_id,
            "ts": ts,
            "kind": "clip_camera_changed",
            "payload": {
                "from_video_id": from_video_id,
                "to_video_id": to_video_id,
                "shift_ms": round(shift * 1000, 1),
            },
        }
    )
    out["audit_events"] = events
    return out, shift


def clip_camera_problem(
    doc: dict[str, Any] | None,
    *,
    primary_video_id: str | None,
    primary_beep_confirmed: bool,
    confirmed_others: list[str],
) -> tuple[Literal["other_camera", "suspect"], str | None] | None:
    """Why ``doc`` may not be on its stage's primary's clip (#1496), or ``None``.

    ``other_camera``: it is stamped with another camera (named). ``suspect``:
    it predates the stamp, the primary's beep was never confirmed, and
    another camera's was (``confirmed_others``, video ids): the shape the
    stranded Blacksmith stages had, where the audit was made on the camera
    with the confirmed beep. A doc without shots has nothing to strand."""
    if not doc or not doc.get("shots"):
        return None
    stamped = doc.get(CLIP_VIDEO_FIELD)
    if stamped:
        return None if stamped == primary_video_id else ("other_camera", str(stamped))
    if not primary_beep_confirmed and confirmed_others:
        return "suspect", confirmed_others[0] if len(confirmed_others) == 1 else None
    return None


__all__ = [
    "CLIP_VIDEO_FIELD",
    "SAME_BEEP_S",
    "Rebase",
    "carry_to_clip",
    "classify",
    "clip_camera_problem",
    "rebase_to_clip",
    "stamp_clip_beep",
    "stamp_clip_video",
]
