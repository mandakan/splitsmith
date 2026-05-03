"""Tests for report.detect_anomalies and report.render_report."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from splitsmith.config import (
    ReportFiles,
    Shot,
    SplitColorThresholds,
    StageAnalysis,
    StageData,
)
from splitsmith.report import (
    detect_anomalies,
    detect_anomalies_structured,
    render_report,
    write_report,
)


def _stage(
    *, stage_number: int = 3, name: str = "Per told me to do it!", time_seconds: float = 14.74
) -> StageData:
    return StageData(
        stage_number=stage_number,
        stage_name=name,
        time_seconds=time_seconds,
        scorecard_updated_at=datetime(2026, 4, 26, 15, 0, tzinfo=UTC),
    )


def _shot(
    n: int, time_from_beep: float, split: float, *, beep_time: float = 19.873, peak: float = 0.5
) -> Shot:
    return Shot(
        shot_number=n,
        time_absolute=beep_time + time_from_beep,
        time_from_beep=time_from_beep,
        split=split,
        peak_amplitude=peak,
        confidence=0.8,
    )


# --- detect_anomalies -----------------------------------------------------


def test_anomalies_clean_run_returns_empty() -> None:
    shots = [
        _shot(1, 1.42, 1.42),
        _shot(2, 1.65, 0.23),
        _shot(3, 1.85, 0.20),
    ]
    # Stage time = 1.85s -> last shot lands exactly on stage time -> clean
    # Shot count 3 is below typical band (8-32) -> expect informational anomaly,
    # so use 16 synthetic shots instead to stay in band.
    shots = [_shot(1, 1.42, 1.42)]
    for i in range(2, 17):
        shots.append(_shot(i, shots[-1].time_from_beep + 0.20, 0.20))
    anomalies = detect_anomalies(shots, beep_time=20.0, stage_time=shots[-1].time_from_beep)
    assert anomalies == []


def test_anomalies_no_shots_flag() -> None:
    anomalies = detect_anomalies([], beep_time=10.0, stage_time=14.74)
    assert anomalies == ["No shots detected in the stage window."]


def test_anomalies_official_time_mismatch() -> None:
    # Last shot is 1s after stage_time -> > 500ms tolerance -> flag
    shots = [_shot(1, 1.0, 1.0), _shot(2, 1.5, 0.5)]
    anomalies = detect_anomalies(shots, beep_time=10.0, stage_time=0.5)
    assert any("Last detected shot" in a for a in anomalies)


def test_anomalies_double_detection() -> None:
    shots = [_shot(1, 1.0, 1.0), _shot(2, 1.05, 0.05)]  # 50ms split
    anomalies = detect_anomalies(shots, beep_time=10.0, stage_time=1.05)
    assert any("possible double-detection" in a for a in anomalies)


def test_anomalies_long_pause() -> None:
    shots = [_shot(1, 1.0, 1.0), _shot(2, 4.5, 3.5)]  # 3.5s split
    anomalies = detect_anomalies(shots, beep_time=10.0, stage_time=4.5)
    assert any("missed shot or long transition" in a for a in anomalies)


def test_anomalies_shot_count_low() -> None:
    shots = [_shot(1, 1.0, 1.0), _shot(2, 1.5, 0.5)]
    anomalies = detect_anomalies(shots, beep_time=10.0, stage_time=1.5)
    assert any("missed shots" in a for a in anomalies)


def test_anomalies_shot_count_high() -> None:
    shots = [_shot(1, 1.0, 1.0)]
    for i in range(2, 50):
        shots.append(_shot(i, shots[-1].time_from_beep + 0.30, 0.30))
    anomalies = detect_anomalies(shots, beep_time=10.0, stage_time=shots[-1].time_from_beep)
    assert any("false positives" in a for a in anomalies)


# --- detect_anomalies_structured -----------------------------------------


def test_anomalies_structured_no_shots_emits_warn() -> None:
    """``no_shots`` is the discriminator for "stage hasn't been audited yet"."""
    out = detect_anomalies_structured([], beep_time=10.0, stage_time=14.74)
    assert len(out) == 1
    assert out[0].kind == "no_shots"
    assert out[0].severity == "warn"
    assert out[0].shot_number is None
    assert out[0].time is None


def test_anomalies_structured_double_detection_carries_shot_number() -> None:
    """Click-to-jump works because each shot anomaly carries the offending
    shot's 1-based number + audit-timeline time."""
    shots = [_shot(1, 1.0, 1.0), _shot(2, 1.05, 0.05)]  # 50ms split
    out = detect_anomalies_structured(shots, beep_time=10.0, stage_time=1.05)
    doubles = [a for a in out if a.kind == "double_detection"]
    assert len(doubles) == 1
    assert doubles[0].shot_number == 2
    assert doubles[0].time == shots[1].time_from_beep
    assert doubles[0].severity == "warn"


def test_anomalies_structured_long_pause() -> None:
    shots = [_shot(1, 1.0, 1.0), _shot(2, 4.5, 3.5)]  # 3.5s split
    out = detect_anomalies_structured(shots, beep_time=10.0, stage_time=4.5)
    longs = [a for a in out if a.kind == "long_pause"]
    assert len(longs) == 1
    assert longs[0].shot_number == 2


def test_anomalies_structured_stage_time_mismatch_points_at_last_shot() -> None:
    """The mismatch anomaly anchors on the last shot so click-to-jump puts
    the user on the marker that decides the calculation."""
    shots = [_shot(1, 1.0, 1.0), _shot(2, 1.5, 0.5)]
    out = detect_anomalies_structured(shots, beep_time=10.0, stage_time=0.5)
    mismatch = [a for a in out if a.kind == "stage_time_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0].shot_number == 2
    assert mismatch[0].time == 1.5


def test_anomalies_structured_count_band_is_info_severity() -> None:
    """Shot-count anomalies are informational, not warnings -- the report
    explicitly calls them out as "informational, not a hard error"."""
    shots = [_shot(1, 1.0, 1.0), _shot(2, 1.5, 0.5)]
    out = detect_anomalies_structured(shots, beep_time=10.0, stage_time=1.5)
    count = [a for a in out if a.kind == "shot_count_low"]
    assert len(count) == 1
    assert count[0].severity == "info"
    assert count[0].shot_number is None
    assert count[0].time is None


def test_anomalies_structured_high_count_uses_distinct_kind() -> None:
    shots = [_shot(1, 1.0, 1.0)]
    for i in range(2, 50):
        shots.append(_shot(i, shots[-1].time_from_beep + 0.30, 0.30))
    out = detect_anomalies_structured(shots, beep_time=10.0, stage_time=shots[-1].time_from_beep)
    assert any(a.kind == "shot_count_high" for a in out)
    assert not any(a.kind == "shot_count_low" for a in out)


def test_detect_anomalies_string_messages_match_structured() -> None:
    """The legacy string list is a stringification of the structured form;
    report.txt rendering depends on this byte-for-byte equivalence."""
    shots = [_shot(1, 1.0, 1.0), _shot(2, 1.05, 0.05)]
    structured = detect_anomalies_structured(shots, beep_time=10.0, stage_time=0.5)
    legacy = detect_anomalies(shots, beep_time=10.0, stage_time=0.5)
    assert legacy == [a.message for a in structured]


# --- render_report --------------------------------------------------------


def _make_analysis(*, anomalies: list[str] | None = None) -> StageAnalysis:
    shots = [
        _shot(1, 1.420, 1.420),
        _shot(2, 1.630, 0.210),
        _shot(3, 1.820, 0.190),
        _shot(4, 3.160, 1.340),  # transition (split > 1.0s)
        _shot(5, 14.700, 11.540),  # very long, would be flagged separately
    ]
    return StageAnalysis(
        stage=_stage(),
        video_path=Path("/tmp/stage3.mp4"),
        beep_time=19.873,
        shots=shots,
        anomalies=anomalies or [],
    )


def test_render_report_includes_header_and_marker() -> None:
    analysis = _make_analysis()
    files = ReportFiles(
        video=Path("analysis/stage3_trimmed.mp4"),
        csv=Path("analysis/stage3_splits.csv"),
        fcpxml=Path("analysis/stage3.fcpxml"),
    )
    text = render_report(analysis, files)
    assert 'Stage 3 -- "Per told me to do it!"' in text
    assert "Official time:        14.740s" in text
    assert "Detected beep at:     19.873s" in text
    # Last shot time_from_beep = 14.700, official 14.74 -> within 500ms -> [OK]
    assert "[OK]" in text
    # File footer
    assert "Files:" in text
    assert "analysis/stage3_trimmed.mp4" in text
    assert "analysis/stage3_splits.csv" in text
    assert "analysis/stage3.fcpxml" in text


def test_render_report_marks_draw_and_transition() -> None:
    analysis = _make_analysis()
    text = render_report(analysis, None)
    # Shot 1 labeled (draw)
    lines = [line for line in text.splitlines() if "Shot  1" in line]
    assert lines and "(draw)" in lines[0]
    # Shot 4 labeled (transition) -- split 1.34s > default transition_min 1.0s
    lines = [line for line in text.splitlines() if "Shot  4" in line]
    assert lines and "(transition)" in lines[0]


def test_render_report_color_band_flags() -> None:
    """Per default thresholds: split <= 0.25 -> [OK], <= 0.35 -> [~] yellow, > 0.35 -> [!] red."""
    shots = [
        _shot(1, 1.0, 1.0),
        _shot(2, 1.20, 0.20),  # GREEN -> [OK]
        _shot(3, 1.50, 0.30),  # YELLOW -> [~]
        _shot(4, 1.95, 0.45),  # RED -> [!]
    ]
    analysis = StageAnalysis(
        stage=_stage(),
        video_path=Path("/tmp/x.mp4"),
        beep_time=10.0,
        shots=shots,
    )
    text = render_report(analysis, None, color_thresholds=SplitColorThresholds())
    line2 = next(line for line in text.splitlines() if "Shot  2" in line)
    line3 = next(line for line in text.splitlines() if "Shot  3" in line)
    line4 = next(line for line in text.splitlines() if "Shot  4" in line)
    assert "[OK]" in line2 and "[~]" not in line2 and "[!]" not in line2
    assert "[~] yellow" in line3
    assert "[!] red" in line4


def test_render_report_renders_anomalies() -> None:
    analysis = _make_analysis(anomalies=["foo bar baz", "another anomaly"])
    text = render_report(analysis, None)
    assert "Anomalies:" in text
    assert "  - foo bar baz" in text
    assert "  - another anomaly" in text


def test_render_report_no_anomalies_says_none() -> None:
    analysis = _make_analysis()
    text = render_report(analysis, None)
    assert "Anomalies:" in text
    assert "  None." in text


def test_write_report_writes_to_path(tmp_path: Path) -> None:
    analysis = _make_analysis()
    out = tmp_path / "report.txt"
    write_report(analysis, None, out)
    assert out.exists()
    assert "Stage 3" in out.read_text()


def test_render_report_no_shots() -> None:
    analysis = StageAnalysis(
        stage=_stage(),
        video_path=Path("/tmp/x.mp4"),
        beep_time=10.0,
        shots=[],
        anomalies=["No shots detected in the stage window."],
    )
    text = render_report(analysis, None)
    assert "Detected 0 shots." in text
    assert "(none)" in text
