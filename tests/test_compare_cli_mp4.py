"""``splitsmith compare export --format mp4`` CLI wiring.

The renderer itself (``mp4_grid.render_grid_mp4``) is fully tested in
``test_compare_mp4_grid_render.py``. These tests only cover the CLI's job:
validating the flag, routing to the right emitter, owning + cleaning up
the scratch work dir, and reporting progress. Real ffmpeg is stubbed out
via ``subprocess.run`` the same way ``test_compare_cli.py`` stubs the
FCPXML path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import splitsmith.compare.cli as cli_mod
import splitsmith.compare.project_loader as pl_mod
from splitsmith.cli import app
from splitsmith.compare import mp4_grid
from splitsmith.fcpxml_gen import VideoMetadata
from splitsmith.match_model import Match, MatchStageDefinition, Shooter, ShooterStageData
from splitsmith.ui.match_exports import _slugify
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo
from tests.conftest import strip_ansi

runner = CliRunner()


def _ffmpeg_stub_factory() -> Any:
    def stub(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        Path(cmd[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    return stub


def _fake_probe(_p: Path) -> VideoMetadata:
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=30.0,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _seed_match_with_stages(root: Path, *, stage_count: int = 2) -> Path:
    """A merged match, one shooter, ``stage_count`` stages, all trimmed on disk."""
    stage_names = {n: f"Stage {n}" for n in range(1, stage_count + 1)}
    match = Match.init(root, name="Compare Match")
    match.stages = [MatchStageDefinition(stage_number=n, stage_name=name) for n, name in stage_names.items()]
    match.save(root)

    def _videos() -> list[StageVideo]:
        return [StageVideo(path=Path("raw/v.mov"), role="primary", beep_time=5.0)]

    match.add_shooter(
        root,
        Shooter(
            slug="mathias",
            name="Mathias",
            stages=[
                ShooterStageData(stage_number=n, time_seconds=10.0, videos=_videos()) for n in stage_names
            ],
        ),
    )

    shooter_root = Match.shooter_root(root, "mathias")
    project = MatchProject.init(shooter_root, name=match.name)
    project.stages = [
        StageEntry(stage_number=n, stage_name=name, time_seconds=10.0, videos=_videos())
        for n, name in stage_names.items()
    ]
    project.save(shooter_root)

    exports = project.exports_path(shooter_root)
    exports.mkdir(parents=True, exist_ok=True)
    for n, name in stage_names.items():
        (exports / f"stage{n}_{_slugify(name)}_trimmed.mp4").write_bytes(b"")
    return root


def _patch_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)


# --- flag plumbing ---------------------------------------------------------


def test_format_flag_is_documented() -> None:
    result = runner.invoke(app, ["compare", "export", "--help"])
    assert result.exit_code == 0
    output = strip_ansi(result.output)
    assert "--format" in output
    assert "mp4" in output


def test_audio_from_no_longer_claims_to_pick_the_track_that_plays() -> None:
    # It used to. Phase 1b made the mix the default track on every MP4,
    # so help text promising "the shooter whose audio plays" is now a
    # false statement about the file the user gets -- and help text is
    # the only place most people will ever read the rule.
    result = runner.invoke(app, ["compare", "export", "--help"])
    assert result.exit_code == 0
    # rich hard-wraps the option table and draws a box rule down both
    # sides of every wrapped line, so the border characters and the
    # padding have to come out before matching. Without this the
    # assertion passes for the wrong reason on any terminal width that
    # breaks a phrase.
    output = " ".join(strip_ansi(result.output).replace("│", " ").split())
    assert "whose audio plays" not in output
    assert "every shooter is mixed into the default track" in output
    assert "sets the render's frame rate" in output


def test_mp4_format_rejects_a_manifest_source(tmp_path: Path) -> None:
    manifest = tmp_path / "m.yaml"
    manifest.write_text("output: out.fcpxml\naudio_from: A\nshooters: []\n", encoding="utf-8")
    result = runner.invoke(
        app, ["compare", "export", str(manifest), "--format", "mp4", "-o", str(tmp_path / "o.mp4")]
    )
    assert result.exit_code == 2
    assert "match folder" in result.output


def test_unknown_format_rejected(tmp_path: Path) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "avi",
            "-o",
            str(tmp_path / "o.avi"),
        ],
    )
    assert result.exit_code == 2
    assert "--format" in result.output


# --- routing -----------------------------------------------------------


def test_fcpxml_format_still_routes_to_the_emitter_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (and explicit) fcpxml must never touch mp4_grid."""
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.fcpxml"
    _patch_probe(monkeypatch)
    monkeypatch.setattr(cli_mod.emitter_mod.subprocess, "run", _ffmpeg_stub_factory())

    render_calls: list[Any] = []
    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", lambda *a, **kw: render_calls.append((a, kw)))

    result = runner.invoke(
        app, ["compare", "export", str(match_root), "--audio-from", "mathias", "-o", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert output.exists()
    assert render_calls == []


def test_mp4_format_calls_render_grid_mp4_not_the_emitter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    emit_calls: list[Any] = []
    monkeypatch.setattr(
        cli_mod.emitter_mod, "emit_compare_fcpxml", lambda *a, **kw: emit_calls.append((a, kw))
    )
    monkeypatch.setattr(cli_mod.subprocess, "run", _ffmpeg_stub_factory())

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert emit_calls == []
    assert output.exists()


def test_mp4_render_receives_the_loaded_bundles_and_resolved_audio_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    seen: dict[str, Any] = {}
    real_render = cli_mod.mp4_grid.render_grid_mp4

    def spy(shooters, *, audio_label, output_path, **kwargs):
        seen["labels"] = sorted(s.label for s in shooters)
        seen["audio_label"] = audio_label
        seen["output_path"] = output_path
        return real_render(shooters, audio_label=audio_label, output_path=output_path, **kwargs)

    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", spy)
    monkeypatch.setattr(cli_mod.subprocess, "run", _ffmpeg_stub_factory())

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["labels"] == ["Mathias"]
    assert seen["audio_label"] == "Mathias"
    assert seen["output_path"] == output


# --- work dir ownership --------------------------------------------------


def test_work_dir_does_not_survive_a_successful_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)
    monkeypatch.setattr(cli_mod.subprocess, "run", _ffmpeg_stub_factory())

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    leftovers = [p for p in output.parent.iterdir() if p.name.startswith(".compare-grid-work")]
    assert leftovers == []


def test_work_dir_does_not_survive_a_failed_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    def _always_fails(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(cli_mod.subprocess, "run", _always_fails)

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 1, result.output
    assert not output.exists()
    leftovers = [p for p in output.parent.iterdir() if p.name.startswith(".compare-grid-work")]
    assert leftovers == []


# --- progress --------------------------------------------------------------


# --- overlay flag -----------------------------------------------------


def test_overlay_flag_is_documented() -> None:
    result = runner.invoke(app, ["compare", "export", "--help"])
    assert result.exit_code == 0
    text = strip_ansi(result.output)
    assert "--overlay" in text
    assert "--overlay-theme" in text


def test_overlay_defaults_to_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    captured: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> mp4_grid.GridRenderResult:
        captured.update(kwargs)
        return mp4_grid.GridRenderResult(output_path=kwargs["output_path"], stages=())

    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", fake_render)

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["overlay"] is False
    assert captured["overlay_theme"] == "splitsmith"


def test_overlay_flag_reaches_the_renderer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    captured: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> mp4_grid.GridRenderResult:
        captured.update(kwargs)
        return mp4_grid.GridRenderResult(output_path=kwargs["output_path"], stages=())

    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", fake_render)

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "--overlay",
            "--overlay-theme",
            "clean",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["overlay"] is True
    assert captured["overlay_theme"] == "clean"


def test_overlay_with_fcpxml_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.fcpxml"
    _patch_probe(monkeypatch)

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "fcpxml",
            "--overlay",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code != 0
    assert "fcpxml" in strip_ansi(result.output).lower()


def test_unknown_overlay_theme_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "--overlay",
            "--overlay-theme",
            "neon",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code != 0
    assert "overlay-theme" in strip_ansi(result.output).lower()


def test_unknown_overlay_theme_is_refused_without_the_overlay_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A theme name that is never valid is rejected on the spot.

    Accepting it in silence defers the error to the next run -- the one
    that adds --overlay and re-encodes the whole match.
    """
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "--overlay-theme",
            "neon",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code != 0
    assert "overlay-theme" in strip_ansi(result.output).lower()


def test_mp4_render_prints_per_stage_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=2)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)
    monkeypatch.setattr(cli_mod.subprocess, "run", _ffmpeg_stub_factory())

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    # One message per stage, in order, each naming its place out of the total --
    # a full 4K match render has no other sign of life for minutes at a time.
    stage1 = result.output.find("stage 1")
    stage2 = result.output.find("stage 2")
    assert stage1 != -1 and stage2 != -1, result.output
    assert stage1 < stage2
    assert "1 of 2" in result.output
    assert "2 of 2" in result.output


# --- reporting a degraded overlay -----------------------------------------


def _degraded_result(output_path: Path) -> mp4_grid.GridRenderResult:
    """What the engine returns on an ffmpeg with no ``drawtext``."""
    return mp4_grid.GridRenderResult(
        output_path=output_path,
        stages=(
            mp4_grid.StageOutcome(stage_number=1, stage_name="Stage 1", ok=True),
            mp4_grid.StageOutcome(stage_number=2, stage_name="Stage 2", ok=True),
        ),
        degradations=(
            mp4_grid.OverlayDegradation(
                summary=mp4_grid.OVERLAY_CLOCK_OMITTED_SUMMARY,
                detail="ffmpeg has no usable drawtext filter -- use --enable-libfreetype.",
            ),
        ),
    )


def test_a_dropped_clock_is_on_the_last_line_the_run_prints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A note at the top of a 40-minute render scrolls away; this does not."""
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=2)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    def fake_render(*_args: Any, **kwargs: Any) -> mp4_grid.GridRenderResult:
        return _degraded_result(kwargs["output_path"])

    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", fake_render)

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "--overlay",
            "-o",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    # Flattened, because rich hard-wraps the console to the terminal
    # width -- it wraps the clause rather than ellipsizing it, so nothing
    # is lost, but the clause spans two physical lines at 80 columns.
    flat = " ".join(strip_ansi(result.output).split())
    assert "Wrote" in flat
    assert flat.endswith(f"(2/2 stages, {mp4_grid.OVERLAY_CLOCK_OMITTED_SUMMARY})"), flat


def test_the_engines_notice_is_printed_before_the_encode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI does not decide anything; it renders what the engine says."""
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=2)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    def fake_render(*_args: Any, **kwargs: Any) -> mp4_grid.GridRenderResult:
        kwargs["on_notice"]("this ffmpeg cannot draw the clock; --enable-libfreetype")
        return _degraded_result(kwargs["output_path"])

    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", fake_render)

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "--overlay",
            "-o",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    text = strip_ansi(result.output)
    assert "--enable-libfreetype" in text
    assert text.index("--enable-libfreetype") < text.index("Wrote")


def test_an_undegraded_run_says_nothing_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=2)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    def fake_render(*_args: Any, **kwargs: Any) -> mp4_grid.GridRenderResult:
        return mp4_grid.GridRenderResult(
            output_path=kwargs["output_path"],
            stages=(mp4_grid.StageOutcome(stage_number=1, stage_name="Stage 1", ok=True),),
        )

    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", fake_render)

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "--overlay",
            "-o",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    flat = " ".join(strip_ansi(result.output).split())
    assert flat.endswith("(1/1 stages)"), flat


def test_a_refused_overlay_exits_non_zero_with_the_engines_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The concat-``option`` refusal, seen from the CLI.

    ``GridRenderError`` is already the CLI's fatal path; what matters is
    that the reason survives to the terminal, because "re-run without
    --overlay" is the whole point of refusing early.
    """
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    def fake_render(*_args: Any, **_kwargs: Any) -> mp4_grid.GridRenderResult:
        raise mp4_grid.GridRenderError(
            "--overlay needs the concat demuxer's 'option' keyword ... "
            "Re-run without --overlay for the plain grid."
        )

    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", fake_render)

    result = runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "--overlay",
            "-o",
            str(output),
        ],
    )

    assert result.exit_code == 1
    flat = " ".join(strip_ansi(result.output).split())
    assert "Re-run without --overlay for the plain grid." in flat, flat


# --- the summary hold -----------------------------------------------------


def _invoke_mp4(match_root: Path, output: Path, *flags: str):
    return runner.invoke(
        app,
        [
            "compare",
            "export",
            str(match_root),
            "--audio-from",
            "mathias",
            "--format",
            "mp4",
            "-o",
            str(output),
            *flags,
        ],
    )


def test_summary_hold_flag_is_documented(monkeypatch: pytest.MonkeyPatch) -> None:
    # Run under CI's own environment too: rich interleaves ANSI escapes
    # into --help when it detects GITHUB_ACTIONS, so a literal substring
    # check can pass locally and fail on CI.
    for ci in ("", "true"):
        monkeypatch.setenv("GITHUB_ACTIONS", ci)
        result = runner.invoke(app, ["compare", "export", "--help"])
        assert result.exit_code == 0
        text = strip_ansi(result.output)
        assert "--summary-hold" in text, ci
        assert "--overlay" in text, ci


def test_summary_hold_defaults_to_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    captured: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> mp4_grid.GridRenderResult:
        captured.update(kwargs)
        return mp4_grid.GridRenderResult(output_path=kwargs["output_path"], stages=())

    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", fake_render)

    result = _invoke_mp4(match_root, output)
    assert result.exit_code == 0, result.output
    assert captured["summary_hold_seconds"] == 0.0


def test_summary_hold_reaches_the_renderer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    captured: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> mp4_grid.GridRenderResult:
        captured.update(kwargs)
        return mp4_grid.GridRenderResult(output_path=kwargs["output_path"], stages=())

    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", fake_render)

    result = _invoke_mp4(match_root, output, "--overlay", "--summary-hold", "2.5")
    assert result.exit_code == 0, result.output
    assert captured["summary_hold_seconds"] == 2.5
    assert captured["overlay"] is True


def test_summary_hold_without_the_overlay_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A design contradiction, refused by name rather than accepted.

    The summary is the overlay's own shot data in the overlay's own
    typography, so a hold on a clean grid would freeze on a blurred still
    with nothing written on it. The message has to name --overlay: that
    is the one-word fix.
    """
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    # The stand-in returns a real result rather than None on purpose: a
    # guard removed from the CLI must fail this test on its own
    # ``exit_code`` assertion, not on an incidental AttributeError from a
    # fake that cannot stand in for the thing it replaces.
    called: list[int] = []

    def fake_render(*args: Any, **kwargs: Any) -> mp4_grid.GridRenderResult:
        called.append(1)
        return mp4_grid.GridRenderResult(output_path=kwargs["output_path"], stages=())

    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", fake_render)

    result = _invoke_mp4(match_root, output, "--summary-hold", "2.0")
    assert result.exit_code == 2, result.output
    flat = strip_ansi(result.output)
    assert "--overlay" in flat, flat
    assert "--summary-hold" in flat, flat
    # Refused before anything was rendered, not after.
    assert called == []


def test_a_negative_summary_hold_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    result = _invoke_mp4(match_root, output, "--overlay", "--summary-hold", "-1")
    assert result.exit_code == 2
    assert "summary-hold" in strip_ansi(result.output).lower()


def test_an_implausibly_long_hold_warns_but_still_renders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accept and say so. Refusing a legal value because it is unusual is
    worse than a warning, but a hold is charged per stage and the bill
    only arrives when a 40-minute render finishes."""
    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    output = tmp_path / "out.mp4"
    _patch_probe(monkeypatch)

    captured: dict[str, Any] = {}

    def fake_render(*args: Any, **kwargs: Any) -> mp4_grid.GridRenderResult:
        captured.update(kwargs)
        return mp4_grid.GridRenderResult(output_path=kwargs["output_path"], stages=())

    monkeypatch.setattr(cli_mod.mp4_grid, "render_grid_mp4", fake_render)

    over = mp4_grid.SUMMARY_HOLD_WARN_SECONDS + 1
    result = _invoke_mp4(match_root, output, "--overlay", "--summary-hold", str(over))
    assert result.exit_code == 0, result.output
    assert captured["summary_hold_seconds"] == over
    flat = strip_ansi(result.output)
    assert "unusually long" in flat, flat

    # ...and the threshold itself does not warn.
    at = mp4_grid.SUMMARY_HOLD_WARN_SECONDS
    result = _invoke_mp4(match_root, output, "--overlay", "--summary-hold", str(at))
    assert result.exit_code == 0, result.output
    assert "unusually long" not in strip_ansi(result.output)
