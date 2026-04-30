"""Human-readable per-stage analysis reports + anomaly flagging.

Anomaly rules (from SPEC.md):
- Beep-to-last-shot window differs from official ``stage.time_seconds`` by >500 ms.
- Any split <80 ms (likely double-detection of a single shot).
- Any split >3 s within the stage window (likely a missed shot, or a long transition).
- Shot count outside a "typical IPSC stage" band (informational, not a hard error).

ASCII-only output (per CLAUDE.md): tags use ``[OK]``, ``[!]``, etc. instead of
Unicode glyphs so the report renders the same in any terminal / pager.
"""

from __future__ import annotations

from pathlib import Path

from .config import ReportFiles, Shot, SplitColorThresholds, StageAnalysis

# Anomaly thresholds.
_OFFICIAL_TIME_TOLERANCE_S = 0.500  # beep -> last shot vs stage.time_seconds
_DOUBLE_DETECTION_MAX_S = 0.080  # min legitimate split
_LONG_PAUSE_MAX_S = 3.000  # split above this is suspicious within the stage window
_SLOW_DRAW_S = 1.500  # shot 1 split greater than this gets a slow-draw note
_TYPICAL_ROUND_RANGE = (8, 32)  # informational shot-count band


def detect_anomalies(
    shots: list[Shot],
    beep_time: float,  # noqa: ARG001 -- kept for symmetry with caller; absolute beep time
    stage_time: float,
) -> list[str]:
    """Return human-readable anomaly strings; empty list means "all clean"."""
    anomalies: list[str] = []

    if not shots:
        anomalies.append("No shots detected in the stage window.")
        return anomalies

    last_after_beep = shots[-1].time_from_beep
    delta = last_after_beep - stage_time
    if abs(delta) > _OFFICIAL_TIME_TOLERANCE_S:
        anomalies.append(
            f"Last detected shot is {abs(delta) * 1000:.0f} ms "
            f"{'after' if delta > 0 else 'before'} official stage time "
            f"({last_after_beep:.3f} s vs {stage_time:.3f} s)."
        )

    for s in shots[1:]:  # shot 1's "split" is the draw, not a real split
        if s.split < _DOUBLE_DETECTION_MAX_S:
            anomalies.append(
                f"Shot {s.shot_number} split is {s.split * 1000:.0f} ms "
                f"(< {_DOUBLE_DETECTION_MAX_S * 1000:.0f} ms): possible double-detection."
            )
        elif s.split > _LONG_PAUSE_MAX_S:
            anomalies.append(
                f"Shot {s.shot_number} split is {s.split:.3f} s "
                f"(> {_LONG_PAUSE_MAX_S:.1f} s): missed shot or long transition?"
            )

    lo, hi = _TYPICAL_ROUND_RANGE
    if not (lo <= len(shots) <= hi):
        anomalies.append(
            f"Detected {len(shots)} shots; typical IPSC stages have {lo}-{hi}. Review for "
            f"{'missed shots' if len(shots) < lo else 'false positives (echoes / other bays)'}."
        )

    return anomalies


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
