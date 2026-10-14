"""What the Audit page flags when it reviews a corpus fixture (spec 2026-10-14).

Reviewing a fixture is an audit pass over its shots and its own audio; these
are the places worth stopping, shown as flags on the shot list and the band:
a shot whose leading edge a stricter onset puts elsewhere (the review
inventory's suggested moves), a snapped shot at the edge of its snap window,
and a sound the detector kept with no shot near it (a possible miss). A
snap's sign-off counts against the shots it was snapped with, never the
scorecard's rounds.

Pure: it reads the fixture's JSON, its promotion report and its inventory
entry, all already loaded.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

#: A snapped shot this close to its window's edge (share of the window) is
#: one the snap may have reached for rather than found.
EDGE_SHARE = 0.9
#: A kept candidate with no shot within this many seconds is a possible miss.
UNMARKED_GAP_S = 0.06


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


class Reference(BaseModel):
    """The shot list a sign-off counts against."""

    kind: Literal["snap"]
    times: list[float]


class ReviewSignals(BaseModel):
    moves: list[Move] = []
    edges: list[Edge] = []
    unmarked: list[Unmarked] = []
    reference: Reference | None = None
    #: The scorecard's rounds, shown beside the reference, never the gate.
    rounds: int | None = None


def review_signals(
    fixture: dict[str, Any], *, report: dict[str, Any] | None, inventory: dict[str, Any] | None
) -> ReviewSignals:
    shots = [s for s in fixture.get("shots") or [] if isinstance(s, dict) and s.get("time") is not None]
    times = sorted(float(s["time"]) for s in shots)
    anchor = fixture.get("anchor") if isinstance(fixture.get("anchor"), dict) else None

    moves = [
        Move(time=float(m["time"]), suggested=float(m["suggested"]), move_ms=float(m["move_ms"]))
        for m in (inventory or {}).get("moves") or []
        if isinstance(m, dict) and m.get("time") is not None and m.get("suggested") is not None
    ]

    edges: list[Edge] = []
    if anchor is not None:
        window = float(anchor.get("snap_window_ms") or 0)
        for s in shots:
            d = s.get("snap_displacement_ms")
            if window > 0 and d is not None and abs(float(d)) >= EDGE_SHARE * window:
                edges.append(Edge(time=float(s["time"]), displacement_ms=float(d)))

    unmarked: list[Unmarked] = []
    candidates = (fixture.get("_candidates_pending_audit") or {}).get("candidates") or []
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
        reference=reference,
        rounds=int(expected) if isinstance(expected, int) else None,
    )
