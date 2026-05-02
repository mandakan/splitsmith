"""Tests for overlay_render (issue #45).

Pure unit tests on frame-state derivation and template rendering. The
ffmpeg pipe path is exercised via a mocked Popen so CI doesn't need
prores_ks support; an integration test skipped without ffmpeg covers the
real binary in development.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from splitsmith import overlay_render
from splitsmith.config import VideoMetadata


def _meta_30fps(duration: float = 2.0) -> VideoMetadata:
    return VideoMetadata(
        width=320,
        height=180,
        duration_seconds=duration,
        frame_rate_num=30,
        frame_rate_den=1,
    )


# --- build_frame_states -----------------------------------------------------


def test_build_frame_states_count_matches_duration_times_fps() -> None:
    states = overlay_render.build_frame_states(
        shot_times_in_clip=[],
        beep_time_in_clip=0.5,
        fps=30.0,
        duration_seconds=2.0,
    )
    assert len(states) == 60
    assert states[0].time_seconds == pytest.approx(0.0)
    assert states[-1].time_seconds == pytest.approx(59 / 30.0)


def test_build_frame_states_running_total_clamps_pre_beep() -> None:
    states = overlay_render.build_frame_states(
        shot_times_in_clip=[],
        beep_time_in_clip=1.0,
        fps=30.0,
        duration_seconds=2.0,
    )
    # Frames before t=1.0 must hold at 0.
    assert states[0].running_total == pytest.approx(0.0)
    assert states[15].running_total == pytest.approx(0.0)
    # Frame at t=1.0 starts ticking.
    assert states[30].running_total == pytest.approx(0.0)
    # Frame at t > 1.0 is positive.
    assert states[45].running_total == pytest.approx(45 / 30.0 - 1.0)


def test_build_frame_states_advances_shots_fired() -> None:
    # Two shots at clip-local 1.0 and 1.5 (with beep at 0.5).
    states = overlay_render.build_frame_states(
        shot_times_in_clip=[1.0, 1.5],
        beep_time_in_clip=0.5,
        fps=30.0,
        duration_seconds=2.0,
    )
    # Pre-shot frame (t=0.5): no shots fired.
    assert states[15].shots_fired == 0
    assert states[15].last_split is None
    # Frame at t=1.0: 1 shot fired; split is the draw == 1.0 - 0.5 = 0.5.
    assert states[30].shots_fired == 1
    assert states[30].last_split == pytest.approx(0.5)
    # Frame at t=1.5: 2 shots fired; split is 0.5.
    assert states[45].shots_fired == 2
    assert states[45].last_split == pytest.approx(0.5)


def test_build_frame_states_sorts_unsorted_input() -> None:
    states = overlay_render.build_frame_states(
        shot_times_in_clip=[1.5, 1.0],
        beep_time_in_clip=0.5,
        fps=30.0,
        duration_seconds=2.0,
    )
    # Same as the sorted case above -- the renderer mustn't bleed shots
    # into the wrong frames just because the JSON wrote them out of order.
    assert states[30].shots_fired == 1
    assert states[30].last_split == pytest.approx(0.5)


# --- _split_alpha -----------------------------------------------------------


def test_split_alpha_holds_then_fades_then_zero() -> None:
    # 1.0s hold, 0.5s fade.
    assert overlay_render._split_alpha(0.0, 1.0, 0.5) == 255
    assert overlay_render._split_alpha(0.5, 1.0, 0.5) == 255
    assert overlay_render._split_alpha(1.0, 1.0, 0.5) == 255
    # Mid-fade.
    mid = overlay_render._split_alpha(1.25, 1.0, 0.5)
    assert 100 < mid < 200
    # End of fade.
    assert overlay_render._split_alpha(1.5, 1.0, 0.5) == 0
    assert overlay_render._split_alpha(2.0, 1.0, 0.5) == 0
    # Pre-shot (negative since_shot can happen at frame boundary).
    assert overlay_render._split_alpha(-0.1, 1.0, 0.5) == 0


# --- DefaultTemplate --------------------------------------------------------


def test_default_template_draws_n_over_m_and_running_total() -> None:
    tmpl = overlay_render.DefaultTemplate(width=320, height=180)
    canvas = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
    state = overlay_render.FrameState(
        time_seconds=2.0,
        beep_time_in_clip=0.5,
        shot_count=5,
        shots_fired=2,
        last_split=0.21,
        last_shot_time_in_clip=1.5,
        running_total=1.5,
    )
    tmpl.draw_frame(canvas, state)
    # The template wrote SOMETHING -- at least one pixel is non-transparent.
    assert canvas.getextrema()[3][1] > 0  # alpha channel max > 0


def test_default_template_skips_split_after_fade() -> None:
    """Long after the last shot, the split label fades to zero. The N/M
    and total are still drawn, but the split region is empty."""
    tmpl = overlay_render.DefaultTemplate(width=320, height=180)
    canvas_after = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
    state_after = overlay_render.FrameState(
        time_seconds=10.0,
        beep_time_in_clip=0.5,
        shot_count=2,
        shots_fired=2,
        last_split=0.21,
        last_shot_time_in_clip=1.5,  # 8.5s ago -> well past fade
        running_total=9.5,
    )
    tmpl.draw_frame(canvas_after, state_after)
    # Bottom strip (where the split label lives) must be fully transparent.
    bottom = canvas_after.crop((0, 140, 320, 180))
    assert bottom.getextrema()[3][1] == 0


# --- format helpers ---------------------------------------------------------


def test_format_running_total_under_minute() -> None:
    assert overlay_render._format_running_total(0.0).strip() == "0.00"
    assert overlay_render._format_running_total(7.42).strip() == "7.42"


def test_format_running_total_over_minute() -> None:
    assert overlay_render._format_running_total(75.5) == "1:15.50"


# --- render_overlay error paths ---------------------------------------------


def test_render_overlay_raises_when_audit_missing(tmp_path: Path) -> None:
    with pytest.raises(overlay_render.OverlayRenderError):
        overlay_render.render_overlay(
            audit_path=tmp_path / "missing.json",
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=tmp_path / "overlay.mov",
            beep_offset_seconds=5.0,
        )


def test_render_overlay_raises_when_no_shots(tmp_path: Path) -> None:
    audit = tmp_path / "stage1.json"
    audit.write_text(json.dumps({"shots": []}), encoding="utf-8")
    with pytest.raises(overlay_render.OverlayRenderError):
        overlay_render.render_overlay(
            audit_path=audit,
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=tmp_path / "overlay.mov",
            beep_offset_seconds=5.0,
        )


def test_render_overlay_pipes_rgba_frames_and_writes_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke: with a stub Popen we capture how many bytes get piped and
    confirm the output path comes back. Frame count = round(fps * dur)."""
    audit = tmp_path / "stage1.json"
    audit.write_text(
        json.dumps(
            {
                "shots": [
                    {"shot_number": 1, "ms_after_beep": 200},
                    {"shot_number": 2, "ms_after_beep": 500},
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "overlay.mov"

    captured: dict[str, Any] = {"bytes": 0, "cmd": None}

    class StubStdin:
        def write(self, data: bytes) -> int:
            captured["bytes"] += len(data)
            return len(data)

        def close(self) -> None:
            return None

    class StubStderr:
        def read(self) -> bytes:
            return b""

    class StubProc:
        def __init__(self, *, stdin: Any, stderr: Any) -> None:
            self.stdin = stdin
            self.stderr = stderr

        def wait(self) -> int:
            output.write_bytes(b"")
            return 0

        def kill(self) -> None:
            return None

    def fake_popen(cmd: list[str], **_: Any) -> StubProc:
        captured["cmd"] = cmd
        return StubProc(stdin=StubStdin(), stderr=StubStderr())

    monkeypatch.setattr(overlay_render.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(overlay_render.shutil, "which", lambda _b: "/bin/ffmpeg")

    result = overlay_render.render_overlay(
        audit_path=audit,
        trimmed_video_path=tmp_path / "trim.mp4",
        output_path=output,
        beep_offset_seconds=0.0,
        probe=_meta_30fps(duration=1.0),  # 30 frames
    )

    assert result == output
    # 30 frames * 320 * 180 * 4 bytes = 6_912_000.
    assert captured["bytes"] == 30 * 320 * 180 * 4
    cmd = captured["cmd"]
    assert "rawvideo" in cmd
    assert "rgba" in cmd
    assert "prores_ks" in cmd
    assert "yuva444p10le" in cmd
    # Frame rate is mirrored as a rational so 29.97 doesn't drift.
    assert "30/1" in cmd


def test_render_overlay_raises_when_ffmpeg_returns_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = tmp_path / "stage1.json"
    audit.write_text(
        json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 100}]}),
        encoding="utf-8",
    )

    class StubStdin:
        def write(self, data: bytes) -> int:
            return len(data)

        def close(self) -> None:
            return None

    class StubStderr:
        def read(self) -> bytes:
            return b"prores_ks: encoder not found"

    class StubProc:
        def __init__(self, **_: Any) -> None:
            self.stdin = StubStdin()
            self.stderr = StubStderr()

        def wait(self) -> int:
            return 1

        def kill(self) -> None:
            return None

    monkeypatch.setattr(overlay_render.subprocess, "Popen", lambda cmd, **_: StubProc())
    monkeypatch.setattr(overlay_render.shutil, "which", lambda _b: "/bin/ffmpeg")

    with pytest.raises(overlay_render.OverlayRenderError, match="prores_ks"):
        overlay_render.render_overlay(
            audit_path=audit,
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=tmp_path / "overlay.mov",
            beep_offset_seconds=0.0,
            probe=_meta_30fps(duration=0.1),
        )


@pytest.mark.integration
def test_render_overlay_writes_real_prores_4444_alpha(tmp_path: Path) -> None:
    """End-to-end: real ffmpeg writes a parseable ProRes 4444 alpha MOV.
    Skipped when ffmpeg / ffprobe aren't on PATH (e.g. CI without them)."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")
    audit = tmp_path / "stage1.json"
    audit.write_text(
        json.dumps(
            {
                "shots": [
                    {"shot_number": 1, "ms_after_beep": 100},
                    {"shot_number": 2, "ms_after_beep": 350},
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "overlay.mov"

    overlay_render.render_overlay(
        audit_path=audit,
        trimmed_video_path=tmp_path / "ignored.mp4",
        output_path=output,
        beep_offset_seconds=0.0,
        probe=_meta_30fps(duration=0.5),  # 15 frames
    )

    assert output.exists() and output.stat().st_size > 0
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt,width,height",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    info = json.loads(proc.stdout)["streams"][0]
    assert info["codec_name"] == "prores"
    assert "yuva" in info["pix_fmt"]  # alpha channel present
    assert info["width"] == 320 and info["height"] == 180
