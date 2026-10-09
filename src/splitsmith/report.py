"""Human-readable per-stage analysis reports + anomaly flagging.

Anomaly rules (from SPEC.md):
- Beep-to-last-shot window differs from the timer's ``stage.time_seconds`` by >100 ms.
- Any split <80 ms (likely double-detection of a single shot).
- Any split >3 s within the stage window (likely a missed shot, or a long transition).
- Shot count outside a "typical IPSC stage" band (informational, not a hard error).

ASCII-only output (per CLAUDE.md): tags use ``[OK]``, ``[!]``, etc. instead of
Unicode glyphs so the report renders the same in any terminal / pager.

Two flavours of the anomaly check live here:

- :func:`detect_anomalies_structured` returns :class:`Anomaly` records
  carrying ``kind`` + ``shot_number`` + ``time`` so the audit screen can
  render clickable entries that jump to the offending marker (issue #42).
- :func:`detect_anomalies` is the legacy string-list shape used by the
  CLI / report.txt; it stringifies the structured output so the rendered
  report bytes are unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .config import ReportFiles, Shot, SplitColorThresholds, StageAnalysis

# Anomaly thresholds.
_OFFICIAL_TIME_TOLERANCE_S = 0.100  # beep -> last shot vs stage.time_seconds
_TIMER_MATCH_S = 0.050  # a kept shot this close to the timer's stop is the one it caught
_DOUBLE_DETECTION_MAX_S = 0.080  # min legitimate split
_LONG_PAUSE_MAX_S = 3.000  # split above this is suspicious within the stage window
_SLOW_DRAW_S = 1.500  # shot 1 split greater than this gets a slow-draw note
_TYPICAL_ROUND_RANGE = (8, 32)  # informational shot-count band


AnomalyKind = Literal[
    "no_shots",
    "stage_time_mismatch",
    "double_detection",
    "long_pause",
    "shot_count_low",
    "shot_count_high",
]
AnomalySeverity = Literal["info", "warn"]


class Anomaly(BaseModel):
    """Structured anomaly emitted by :func:`detect_anomalies_structured`.

    ``kind`` tags the rule so the SPA can group / filter without parsing
    ``message``. ``shot_number`` (1-based, matches :class:`Shot.shot_number`)
    and ``time`` (seconds from beep) are populated for shot-bound anomalies
    so the audit screen can scroll to the offending marker on click.
    Stage-level anomalies (count band, no shots) leave both null.
    """

    kind: AnomalyKind
    severity: AnomalySeverity
    message: str
    shot_number: int | None = None
    time: float | None = None


def _shot_range(first: int, last: int) -> str:
    return f"shot {first}" if first == last else f"shots {first} to {last}"


def _stage_time_mismatch(shots: list[Shot], stage_time: float) -> Anomaly | None:
    """The timer (the scorecard's stage time) against beep -> last kept
    shot. Past the tolerance one end of the audit is wrong, and the flag
    says which to look at: shots after the one the timer caught, a final
    shot the audit missed, or the beep. A flag on extra shots anchors on
    the first of them; a timer past the last shot anchors where the timer
    stopped, on no shot."""
    last = shots[-1]
    delta = last.time_from_beep - stage_time
    if abs(delta) <= _OFFICIAL_TIME_TOLERANCE_S + 1e-9:
        return None
    if delta < 0:
        return Anomaly(
            kind="stage_time_mismatch",
            severity="warn",
            message=(
                f"Timer stopped {-delta:.2f} s after the last shot ({stage_time:.2f} s): "
                f"look for a missed final shot, or a beep placed too late."
            ),
            time=stage_time,
        )
    head = f"Last shot is {delta:.2f} s after the timer stopped ({stage_time:.2f} s)"
    caught = min(shots[:-1], key=lambda s: abs(s.time_from_beep - stage_time), default=None)
    if caught is None or abs(caught.time_from_beep - stage_time) > _TIMER_MATCH_S:
        return Anomaly(
            kind="stage_time_mismatch",
            severity="warn",
            message=f"{head}: the last shot may be extra, or the beep placed too early.",
            shot_number=last.shot_number,
            time=last.time_from_beep,
        )
    extra = [s for s in shots if s.time_from_beep > caught.time_from_beep]
    plural = len(extra) > 1
    return Anomaly(
        kind="stage_time_mismatch",
        severity="warn",
        message=(
            f"{head}. Shot {caught.shot_number} matches the timer: "
            f"{'are' if plural else 'is'} {_shot_range(extra[0].shot_number, extra[-1].shot_number)} "
            f"extra (an echo or steel)?"
        ),
        shot_number=extra[0].shot_number,
        time=extra[0].time_from_beep,
    )


def detect_anomalies_structured(
    shots: list[Shot],
    beep_time: float,  # noqa: ARG001 -- kept for symmetry with caller; absolute beep time
    stage_time: float,
) -> list[Anomaly]:
    """Return structured :class:`Anomaly` records; empty list means "all clean"."""
    anomalies: list[Anomaly] = []

    if not shots:
        anomalies.append(
            Anomaly(
                kind="no_shots",
                severity="warn",
                message="No shots detected in the stage window.",
            )
        )
        return anomalies

    mismatch = _stage_time_mismatch(shots, stage_time)
    if mismatch is not None:
        anomalies.append(mismatch)

    for s in shots[1:]:  # shot 1's "split" is the draw, not a real split
        if s.split < _DOUBLE_DETECTION_MAX_S:
            anomalies.append(
                Anomaly(
                    kind="double_detection",
                    severity="warn",
                    message=(
                        f"Shot {s.shot_number} split is {s.split * 1000:.0f} ms "
                        f"(< {_DOUBLE_DETECTION_MAX_S * 1000:.0f} ms): "
                        f"possible double-detection."
                    ),
                    shot_number=s.shot_number,
                    time=s.time_from_beep,
                )
            )
        elif s.split > _LONG_PAUSE_MAX_S:
            anomalies.append(
                Anomaly(
                    kind="long_pause",
                    severity="warn",
                    message=(
                        f"Shot {s.shot_number} split is {s.split:.3f} s "
                        f"(> {_LONG_PAUSE_MAX_S:.1f} s): missed shot or long transition?"
                    ),
                    shot_number=s.shot_number,
                    time=s.time_from_beep,
                )
            )

    lo, hi = _TYPICAL_ROUND_RANGE
    if not (lo <= len(shots) <= hi):
        is_low = len(shots) < lo
        anomalies.append(
            Anomaly(
                kind="shot_count_low" if is_low else "shot_count_high",
                severity="info",
                message=(
                    f"Detected {len(shots)} shots; typical IPSC stages have {lo}-{hi}. "
                    f"Review for "
                    f"{'missed shots' if is_low else 'false positives (echoes / other bays)'}."
                ),
            )
        )

    return anomalies


def detect_anomalies(
    shots: list[Shot],
    beep_time: float,
    stage_time: float,
) -> list[str]:
    """Return human-readable anomaly strings; empty list means "all clean".

    Thin wrapper over :func:`detect_anomalies_structured` so the report.txt
    bullet rendering stays byte-identical to its pre-#42 output.
    """
    return [a.message for a in detect_anomalies_structured(shots, beep_time, stage_time)]


def render_report(
    analysis: StageAnalysis,
    files: ReportFiles | None = None,
    *,
    color_thresholds: SplitColorThresholds | None = None,
) -> str:
    """Render the SPEC.md-shaped per-stage report as a single ASCII string."""
    files = files or ReportFiles()
    thresholds = color_thresholds or SplitColorThresholds()

    stage = analysis.stage
    shots = analysis.shots

    lines: list[str] = []
    lines.append(f'Stage {stage.stage_number} -- "{stage.stage_name}"')
    lines.append(f"Official time:        {stage.time_seconds:.3f}s")
    lines.append(f"Detected beep at:     {analysis.beep_time:.3f}s")

    if shots:
        last = shots[-1]
        delta_ms = (last.time_from_beep - stage.time_seconds) * 1000.0
        match_marker = "[OK]" if abs(delta_ms) <= _OFFICIAL_TIME_TOLERANCE_S * 1000 else "[!]"
        lines.append(
            f"Detected last shot:   {last.time_absolute:.3f}s "
            f"({last.time_from_beep:.3f}s after beep) -- "
            f"{'matches' if match_marker == '[OK]' else 'differs from'} "
            f"official by {abs(delta_ms):.0f}ms {match_marker}"
        )
    lines.append(f"Detected {len(shots)} shot{'s' if len(shots) != 1 else ''}.")
    lines.append("")

    lines.append("Splits:")
    if not shots:
        lines.append("  (none)")
    else:
        for s in shots:
            lines.append(_render_shot_line(s, thresholds))
    lines.append("")

    lines.append("Anomalies:")
    if analysis.anomalies:
        for a in analysis.anomalies:
            lines.append(f"  - {a}")
    else:
        lines.append("  None.")
    lines.append("")

    if files.video or files.csv or files.fcpxml:
        lines.append("Files:")
        if files.video:
            lines.append(f"  Video:  {files.video}")
        if files.csv:
            lines.append(f"  CSV:    {files.csv}")
        if files.fcpxml:
            lines.append(f"  FCPXML: {files.fcpxml}")

    return "\n".join(lines).rstrip() + "\n"


def _render_shot_line(s: Shot, thresholds: SplitColorThresholds) -> str:
    label_parts: list[str] = []
    if s.shot_number == 1:
        label_parts.append("draw")
    elif s.split > thresholds.transition_min:
        label_parts.append("transition")
    label = f" ({', '.join(label_parts)})" if label_parts else ""
    flag = _shot_flag(s, thresholds)
    return f"  Shot {s.shot_number:>2}{label:<14}: {s.split:.3f}s  {flag}".rstrip()


def _shot_flag(s: Shot, thresholds: SplitColorThresholds) -> str:
    if s.shot_number == 1:
        return "[!] slow draw" if s.split > _SLOW_DRAW_S else "[OK]"
    if s.split < _DOUBLE_DETECTION_MAX_S:
        return "[!] possible double"
    if s.split > _LONG_PAUSE_MAX_S:
        return "[!] long pause"
    if s.split > thresholds.transition_min:
        return ""  # transitions speak for themselves; no good/bad call
    if s.split <= thresholds.green_max:
        return "[OK]"
    if s.split <= thresholds.yellow_max:
        return "[~] yellow"
    return "[!] red"


def write_report(
    analysis: StageAnalysis,
    files: ReportFiles | None,
    output_path: Path,
    *,
    color_thresholds: SplitColorThresholds | None = None,
) -> None:
    """Write the rendered report to ``output_path``."""
    output_path.write_text(
        render_report(analysis, files, color_thresholds=color_thresholds), encoding="utf-8"
    )
