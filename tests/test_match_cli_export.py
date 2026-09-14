"""``splitsmith match export``: one shooter's stitched match video from
existing per-stage artefacts (issue #973).

The orchestrator (``ui/match_exports.export_match``) and the renderers are
tested in their own files; these cover the verb's job -- resolving the
shooter, refusing what it cannot render, forwarding the flags, and saying
what it wrote. ffprobe and the MP4 renderer are stubbed.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from splitsmith import match_cli
from splitsmith.cli import app
from splitsmith.config import VideoMetadata
from splitsmith.export_naming import stage_file_base
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui import match_exports as match_exports_mod
from tests.conftest import scaffold_match, strip_ansi

runner = CliRunner()


def _meta(_path: Path) -> VideoMetadata:
    return VideoMetadata(width=1920, height=1080, duration_seconds=20.0, frame_rate_num=30, frame_rate_den=1)


def _seed(tmp_path: Path, *, with_trim: bool = True, competitor_name: str | None = "M. Axell") -> Path:
    root, shooter_root = scaffold_match(
        tmp_path, name="Bromma Classifier", shooter_slug="me", shooter_name="Me"
    )
    project = MatchProject.load(shooter_root)
    project.competitor_name = competitor_name
    project.match_date = date(2026, 5, 1)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="Speed",
            time_seconds=8.0,
            videos=[StageVideo(path=Path("p.mp4"), role="primary", beep_time=12.0)],
        )
    ]
    project.save(shooter_root)
    exports = project.exports_path(shooter_root)
    exports.mkdir(parents=True, exist_ok=True)
    audit = project.audit_path(shooter_root)
    audit.mkdir(parents=True, exist_ok=True)
    base = stage_file_base(1, "Speed")
    if with_trim:
        (exports / f"{base}_trimmed.mp4").write_bytes(b"")
    (audit / "stage1.json").write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_name": "Speed",
                "stage_time_seconds": 8.0,
                "beep_time": 12.0,
                "shots": [{"shot_number": 1, "ms_after_beep": 500}],
                "_candidates_pending_audit": {"candidates": []},
            }
        ),
        encoding="utf-8",
    )
    return root


def _capture_mp4(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from splitsmith import mp4_render

    captured: dict[str, Any] = {}

    def fake_render_mp4(comp, *, output_path, **kwargs):  # type: ignore[no-untyped-def]
        captured["comp"] = comp
        captured["kwargs"] = kwargs
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")
        return mp4_render.Mp4RenderResult(output_path=output_path, duration_seconds=27.5)

    monkeypatch.setattr(match_exports_mod.mp4_render, "render_mp4", fake_render_mp4)
    monkeypatch.setattr(match_exports_mod.fcpxml_gen, "probe_video", _meta)
    return captured


def test_mp4_export_forwards_the_cards_and_reports_the_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _seed(tmp_path)
    captured = _capture_mp4(monkeypatch)
    out = tmp_path / "out" / "bromma.mp4"
    result = runner.invoke(
        app,
        [
            "match",
            "export",
            str(root),
            "--shooter",
            "me",
            "--format",
            "mp4",
            "--titles",
            "slate",
            "--title-duration",
            "2",
            "--title-page",
            "--title-info",
            "Production Optics",
            "--closing-card",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    comp = captured["comp"]
    assert comp.title_page.text == "Bromma Classifier"
    assert comp.title_page.info == ("2026-05-01", "M. Axell", "Production Optics")
    assert comp.closing is not None
    assert comp.stages[0].title.style == "slate"
    assert comp.stages[0].title.duration_seconds == 2.0
    assert out.exists()
    text = strip_ansi(result.output)
    assert str(out.resolve()) in text
    assert "27.5" in text


def test_default_shooter_when_the_match_has_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _seed(tmp_path)
    _capture_mp4(monkeypatch)
    result = runner.invoke(app, ["match", "export", str(root), "--format", "mp4"])
    assert result.exit_code == 0, result.output
    # Default output: the match file base under the shooter's exports dir.
    assert "bromma" in strip_ansi(result.output).lower()


def test_unknown_shooter_is_a_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _seed(tmp_path)
    _capture_mp4(monkeypatch)
    result = runner.invoke(app, ["match", "export", str(root), "--shooter", "nobody"])
    assert result.exit_code == 2
    assert "nobody" in strip_ansi(result.output)


def test_missing_trim_names_the_stage_and_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _seed(tmp_path, with_trim=False)
    _capture_mp4(monkeypatch)
    result = runner.invoke(app, ["match", "export", str(root), "--shooter", "me", "--format", "mp4"])
    assert result.exit_code == 1
    text = strip_ansi(result.output)
    assert "stage 1" in text
    assert "trim" in text


def test_bad_format_is_a_usage_error(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    result = runner.invoke(app, ["match", "export", str(root), "--shooter", "me", "--format", "avi"])
    assert result.exit_code == 2


def test_anomalies_are_printed_as_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A title page on FCPXML is ignored with an anomaly; the verb prints
    it under the result rather than swallowing it."""
    root = _seed(tmp_path)
    monkeypatch.setattr(match_exports_mod.fcpxml_gen, "probe_video", _meta)
    result = runner.invoke(app, ["match", "export", str(root), "--shooter", "me", "--title-page"])
    assert result.exit_code == 0, result.output
    assert "title page ignored" in strip_ansi(result.output)


def test_output_naming_a_directory_is_a_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _seed(tmp_path)
    _capture_mp4(monkeypatch)
    result = runner.invoke(app, ["match", "export", str(root), "--shooter", "me", "--output", str(tmp_path)])
    assert result.exit_code == 2
    assert "directory" in strip_ansi(result.output)


def test_verb_is_registered() -> None:
    assert "export" in {c.name for c in match_cli.match_app.registered_commands}


def test_summary_hold_reaches_the_composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _seed(tmp_path)
    captured = _capture_mp4(monkeypatch)
    result = runner.invoke(
        app, ["match", "export", str(root), "--shooter", "me", "--format", "mp4", "--summary-hold", "2.5"]
    )
    assert result.exit_code == 0, result.output
    hold = captured["comp"].stages[0].summary
    assert hold is not None and hold.duration_seconds == 2.5
    assert hold.label == "M. Axell"


def test_negative_summary_hold_is_a_usage_error(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    result = runner.invoke(app, ["match", "export", str(root), "--shooter", "me", "--summary-hold", "-1"])
    assert result.exit_code == 2


def test_summary_label_falls_back_to_the_roster_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _seed(tmp_path, competitor_name=None)
    captured = _capture_mp4(monkeypatch)
    result = runner.invoke(
        app, ["match", "export", str(root), "--shooter", "me", "--format", "mp4", "--summary-hold", "2"]
    )
    assert result.exit_code == 0, result.output
    assert captured["comp"].stages[0].summary.label == "Me"


def test_plain_export_never_reads_the_roster_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a summary the shooter's roster entry is not consulted, so a
    missing shooter.json cannot fail an ordinary export (review finding)."""
    root = _seed(tmp_path, competitor_name=None)
    captured = _capture_mp4(monkeypatch)
    (root / "shooters" / "me" / "shooter.json").unlink()
    result = runner.invoke(app, ["match", "export", str(root), "--shooter", "me", "--format", "mp4"])
    assert result.exit_code == 0, result.output
    assert captured["comp"].stages[0].summary is None


def test_youtube_sidecar_and_captions_move_with_a_renamed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _seed(tmp_path)
    _capture_mp4(monkeypatch)
    out = tmp_path / "out" / "lt.mp4"
    result = runner.invoke(
        app,
        [
            "match",
            "export",
            str(root),
            "--shooter",
            "me",
            "--format",
            "mp4",
            "--youtube-sidecar",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert out.with_suffix(".srt").exists()
    assert out.with_name("lt-youtube.json").exists()
    # Nothing left behind under the default name.
    assert not list((root / "shooters" / "me" / "exports").glob("*-youtube.json"))


def test_reclassify_rejudges_auto_intervals_and_keeps_manual_ones(tmp_path: Path) -> None:
    """The verb re-runs the classifier with the current thresholds over a
    stored audit: an interval auto-classed under an old rule moves, a
    manual override does not, a reclassify event is appended, and
    --dry-run writes nothing."""
    root = _seed(tmp_path)
    audit = root / "shooters" / "me" / "audit" / "stage1.json"
    doc = json.loads(audit.read_text())
    doc["shots"] = [
        {
            "shot_number": 1,
            "ms_after_beep": 1500,
            "interval_class": "first_shot",
            "interval_class_source": "auto",
        },
        # 0.80 s: 'transition' under the old 0.5 s rule, a split now.
        {
            "shot_number": 2,
            "ms_after_beep": 2300,
            "interval_class": "transition",
            "interval_class_source": "auto",
        },
        # 1.50 s, manually called a reload: stays.
        {
            "shot_number": 3,
            "ms_after_beep": 3800,
            "interval_class": "reload",
            "interval_class_source": "manual",
        },
    ]
    audit.write_text(json.dumps(doc))

    dry = runner.invoke(app, ["match", "reclassify", str(root), "--shooter", "me", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "1 intervals moved (nothing written)" in strip_ansi(dry.output)
    assert json.loads(audit.read_text())["shots"][1]["interval_class"] == "transition"

    result = runner.invoke(app, ["match", "reclassify", str(root), "--shooter", "me"])
    assert result.exit_code == 0, result.output
    text = strip_ansi(result.output)
    assert "split <= 1s, transition <= 2s" in text
    assert (
        "stage 1 Speed: first_shot=1 transition=1 reload=1 -> first_shot=1 split=1 reload=1  (1 moved)"
        in text
    )
    written = json.loads(audit.read_text())
    assert [s["interval_class"] for s in written["shots"]] == ["first_shot", "split", "reload"]
    assert written["shots"][2]["interval_class_source"] == "manual"
    assert written["audit_events"][-1]["kind"] == "coach_reclassify"
