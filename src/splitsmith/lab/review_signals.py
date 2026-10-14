"""What the Audit page flags when it reviews a corpus fixture (spec 2026-10-14).

Reviewing a fixture is an audit pass over its shots and its own audio; these
are the places worth stopping, shown as flags on the shot list and the band:
a snapped shot at the edge of its snap window, a shot sitting on a louder
echo with a real blast just before it (Seat's known trap on some head cams:
Earlier rise is the fix), and for a hand-audited fixture the review
inventory's suggested moves and a sound the detector kept with no shot near
it (a possible miss). A snap copies a reviewed primary's shots, so every
shot exists and such a sound on the other camera is another bay or a steel
ring; and a snap is seated on the rise foot when it is written, so the
inventory's stricter onset, a different point, is not shown for one either.
A snap's sign-off counts against the shots it was snapped with, never the
scorecard's rounds.

Pure: it reads the fixture's JSON, its promotion report and its inventory
entry, all already loaded.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from ..rise_foot import TOLERANCE_S, rise_foot

#: A snapped shot this close to its window's edge (share of the window) is
#: one the snap may have reached for rather than found.
EDGE_SHARE = 0.9
#: A kept candidate with no shot within this many seconds is a possible miss.
UNMARKED_GAP_S = 0.06
#: An earlier burst this close before the shot, at least this loud against
#: the shot's own burst, may be the blast the shot's echo followed.
ECHO_WINDOW_S = 0.040
ECHO_MIN_SHARE = 0.5


class Move(BaseModel):
    time: float
    suggested: float
    move_ms: float


class Edge(BaseModel):
    time: float
    displacement_ms: float


class Unmarked(BaseModel):
    time: float
    confidence: float | None = None


class Echo(BaseModel):
    time: float
    #: The foot of the louder-than-half burst before it.
    earlier: float


class Reference(BaseModel):
    """The shot list a sign-off counts against."""

    kind: Literal["snap"]
    times: list[float]


class ReviewSignals(BaseModel):
    moves: list[Move] = []
    edges: list[Edge] = []
    unmarked: list[Unmarked] = []
    echoes: list[Echo] = []
    reference: Reference | None = None
    #: The scorecard's rounds, shown beside the reference, never the gate.
    rounds: int | None = None


def _echoes(times: list[float], peaks: list[float], duration: float) -> list[Echo]:
    """Shots whose burst has a real one just before it: the foot of the
    burst before the shot (the rule's window ending just short of it, as the
    app's Earlier rise searches) within ``ECHO_WINDOW_S``, peaking at least
    ``ECHO_MIN_SHARE`` of the shot's own burst."""
    n = len(peaks)
    if n == 0 or duration <= 0:
        return []
    bin_s = duration / n
    out: list[Echo] = []
    for t in times:
        before = rise_foot(peaks, duration, t - TOLERANCE_S - 0.001)
        if before is None or not (0.002 < t - before <= ECHO_WINDOW_S):
            continue
        a, b = int(before / bin_s), int(t / bin_s)
        shot_peak = max(peaks[b : min(n, b + int(0.03 / bin_s) + 1)] or [0.0])
        earlier_peak = max(peaks[a:b] or [0.0])
        if shot_peak > 0 and earlier_peak >= ECHO_MIN_SHARE * shot_peak:
            out.append(Echo(time=t, earlier=round(before, 4)))
    return out


def review_signals(
    fixture: dict[str, Any],
    *,
    report: dict[str, Any] | None,
    inventory: dict[str, Any] | None,
    envelope: tuple[list[float], float] | None = None,
) -> ReviewSignals:
    """``envelope``: the fixture WAV's 1 ms peak envelope and duration
    (``rise_foot.peak_envelope``), for the echo check; without it none."""
    shots = [s for s in fixture.get("shots") or [] if isinstance(s, dict) and s.get("time") is not None]
    times = sorted(float(s["time"]) for s in shots)
    anchor = fixture.get("anchor") if isinstance(fixture.get("anchor"), dict) else None

    moves = (
        []
        if anchor is not None
        else [
            Move(time=float(m["time"]), suggested=float(m["suggested"]), move_ms=float(m["move_ms"]))
            for m in (inventory or {}).get("moves") or []
            if isinstance(m, dict) and m.get("time") is not None and m.get("suggested") is not None
        ]
    )
    echoes = _echoes(times, envelope[0], envelope[1]) if envelope is not None else []

    edges: list[Edge] = []
    if anchor is not None:
        window = float(anchor.get("snap_window_ms") or 0)
        for s in shots:
            d = s.get("snap_displacement_ms")
            if window > 0 and d is not None and abs(float(d)) >= EDGE_SHARE * window:
                edges.append(Edge(time=float(s["time"]), displacement_ms=float(d)))

    unmarked: list[Unmarked] = []
    candidates = (
        [] if anchor is not None else (fixture.get("_candidates_pending_audit") or {}).get("candidates") or []
    )
    for c in candidates:
        if not isinstance(c, dict) or not c.get("kept") or c.get("time") is None:
            continue
        t = float(c["time"])
        if all(abs(t - x) > UNMARKED_GAP_S for x in times):
            unmarked.append(Unmarked(time=t, confidence=c.get("confidence")))

    reference = None
    if anchor is not None and report is not None:
        window_start = float((fixture.get("fixture_window_in_source") or [0.0])[0])
        snapped = [
            round(float(p["snapped_time"]) - window_start, 4)
            for p in report.get("per_shot") or []
            if isinstance(p, dict) and p.get("snapped_time") is not None
        ]
        reference = Reference(kind="snap", times=snapped)

    expected = (fixture.get("stage_rounds") or {}).get("expected")
    return ReviewSignals(
        moves=moves,
        edges=edges,
        unmarked=unmarked,
        echoes=echoes,
        reference=reference,
        rounds=int(expected) if isinstance(expected, int) else None,
    )
