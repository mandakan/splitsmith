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


def test_build_frame_states_running_total_freezes_after_last_shot() -> None:
    # Two shots; clip continues for ~1s after the last shot. The timer
    # should hold at last_shot - beep, not keep ticking.
    states = overlay_render.build_frame_states(
        shot_times_in_clip=[1.0, 1.5],
        beep_time_in_clip=0.5,
        fps=30.0,
        duration_seconds=3.0,
    )
    final_total = 1.5 - 0.5
    # Frame at t=1.5 (last shot fires here): freeze begins.
    assert states[45].running_total == pytest.approx(final_total)
    # Mid-tail and end-of-clip frames hold at the same value.
    assert states[60].running_total == pytest.approx(final_total)
    assert states[-1].running_total == pytest.approx(final_total)


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


def test_default_template_renders_stroke_and_blurred_shadow() -> None:
    """Stroke + soft drop shadow widen the painted region beyond a bare
    text render (more non-transparent pixels) and leave dark pixels around
    the white glyphs (the stroke). Both checks fail under the old 1px
    shadow + no-stroke renderer."""
    state = overlay_render.FrameState(
        time_seconds=2.0,
        beep_time_in_clip=0.5,
        shot_count=5,
        shots_fired=2,
        last_split=0.21,
        last_shot_time_in_clip=1.5,
        running_total=1.5,
    )
    enhanced = overlay_render.DefaultTemplate(width=640, height=360)
    bare = overlay_render.DefaultTemplate(
        width=640,
        height=360,
        stroke_width_px=0,
        shadow_blur_px=0,
        shadow_offset_px=0,
    )
    canvas_enhanced = Image.new("RGBA", (640, 360), (0, 0, 0, 0))
    canvas_bare = Image.new("RGBA", (640, 360), (0, 0, 0, 0))
    enhanced.draw_frame(canvas_enhanced, state)
    bare.draw_frame(canvas_bare, state)

    def nonzero_alpha_count(img: Image.Image) -> int:
        return sum(1 for px in img.getchannel("A").tobytes() if px > 0)

    def dark_outline_count(img: Image.Image) -> int:
        # Opaque-ish pixel that is mostly black -> stroke around the glyph.
        alpha = img.getchannel("A").tobytes()
        red = img.getchannel("R").tobytes()
        return sum(1 for a, r in zip(alpha, red, strict=True) if a > 200 and r < 64)

    assert nonzero_alpha_count(canvas_enhanced) > nonzero_alpha_count(canvas_bare) * 1.5
    assert dark_outline_count(canvas_enhanced) > 100


def test_load_font_unknown_name_raises() -> None:
    with pytest.raises(overlay_render.OverlayRenderError):
        overlay_render._load_font(None, 24, font_name="not-a-real-font")


def test_load_font_known_name_falls_back_when_missing(tmp_path: Path) -> None:
    """A named preset whose files don't exist on this machine must still
    produce a usable font (generic fallback or PIL default), not crash."""
    font = overlay_render._load_font(None, 24, font_name="dejavu-mono")
    assert font is not None


def test_available_font_names_includes_known_presets() -> None:
    names = overlay_render.available_font_names()
    assert "menlo" in names
    assert "sf-mono" in names
    assert "dejavu-mono" in names


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
        codec="prores-4444",  # explicit so the test is host-independent
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
            codec="prores-4444",
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
        codec="prores-4444",  # this test asserts the prores path specifically
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


# --- format options ---------------------------------------------------------


def _capture_render_cmd(
    monkeypatch: pytest.MonkeyPatch,
    *,
    audit_path: Path,
    output: Path,
    probe: VideoMetadata,
    **kwargs: Any,
) -> tuple[list[str], int]:
    """Run :func:`render_overlay` with a stub Popen, returning the cmd
    that would have been invoked and the bytes piped to ffmpeg's stdin."""
    captured: dict[str, Any] = {"bytes": 0, "cmd": []}

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

    overlay_render.render_overlay(
        audit_path=audit_path,
        trimmed_video_path=audit_path.parent / "trim.mp4",
        output_path=output,
        beep_offset_seconds=0.0,
        probe=probe,
        **kwargs,
    )
    return captured["cmd"], captured["bytes"]


def _write_audit(tmp_path: Path) -> Path:
    audit = tmp_path / "stage1.json"
    audit.write_text(
        json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 100}]}),
        encoding="utf-8",
    )
    return audit


def test_codec_hevc_alpha_emits_videotoolbox_cmd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking for ``hevc-alpha`` produces a ``hevc_videotoolbox`` cmd
    with ``hvc1`` tagging and yuva420p (the only alpha pix-fmt the
    encoder accepts)."""
    cmd, _ = _capture_render_cmd(
        monkeypatch,
        audit_path=_write_audit(tmp_path),
        output=tmp_path / "overlay.mov",
        probe=_meta_30fps(duration=0.1),
        codec="hevc-alpha",
    )
    assert "hevc_videotoolbox" in cmd
    assert "yuva420p" in cmd
    # ``hvc1`` tag matters for FCP / QuickTime import.
    assert "hvc1" in cmd
    assert "-alpha_quality" in cmd
    assert "prores_ks" not in cmd


def test_codec_auto_falls_back_to_prores_off_darwin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``auto`` resolves to ``prores-4444`` when the host isn't macOS,
    so non-Mac CI doesn't try to call ``hevc_videotoolbox``."""
    monkeypatch.setattr(overlay_render.platform, "system", lambda: "Linux")
    cmd, _ = _capture_render_cmd(
        monkeypatch,
        audit_path=_write_audit(tmp_path),
        output=tmp_path / "overlay.mov",
        probe=_meta_30fps(duration=0.1),
        codec="auto",
    )
    assert "prores_ks" in cmd
    assert "hevc_videotoolbox" not in cmd


def test_codec_auto_picks_hevc_on_darwin_with_videotoolbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On macOS with VideoToolbox advertised, ``auto`` switches to
    ``hevc-alpha`` -- the size win that motivated the option."""
    monkeypatch.setattr(overlay_render.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        overlay_render, "_ffmpeg_supports_encoder", lambda _bin, enc: enc == "hevc_videotoolbox"
    )
    cmd, _ = _capture_render_cmd(
        monkeypatch,
        audit_path=_write_audit(tmp_path),
        output=tmp_path / "overlay.mov",
        probe=_meta_30fps(duration=0.1),
        codec="auto",
    )
    assert "hevc_videotoolbox" in cmd
    assert "prores_ks" not in cmd


def test_codec_unknown_raises(tmp_path: Path) -> None:
    audit = _write_audit(tmp_path)
    with pytest.raises(overlay_render.OverlayRenderError, match="unknown overlay codec"):
        overlay_render.render_overlay(
            audit_path=audit,
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=tmp_path / "overlay.mov",
            beep_offset_seconds=0.0,
            probe=_meta_30fps(duration=0.1),
            codec="bogus",  # type: ignore[arg-type]
        )


def test_max_height_downscales_canvas_aspect_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capping height shrinks the canvas (and therefore the bytes piped
    to ffmpeg) while keeping the aspect ratio."""
    # 1920x1080 source -> cap at 720 -> 1280x720.
    probe = VideoMetadata(
        width=1920, height=1080, duration_seconds=0.1, frame_rate_num=30, frame_rate_den=1
    )
    cmd, byte_count = _capture_render_cmd(
        monkeypatch,
        audit_path=_write_audit(tmp_path),
        output=tmp_path / "overlay.mov",
        probe=probe,
        codec="prores-4444",
        max_height=720,
    )
    # ``-s WxH`` argument follows ``-s`` in the cmd.
    s_idx = cmd.index("-s")
    assert cmd[s_idx + 1] == "1280x720"
    # 3 frames @ 30fps for 0.1s -> 3 * 1280 * 720 * 4 bytes.
    assert byte_count == 3 * 1280 * 720 * 4


def test_max_height_above_source_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cap larger than the source height never upscales."""
    cmd, _ = _capture_render_cmd(
        monkeypatch,
        audit_path=_write_audit(tmp_path),
        output=tmp_path / "overlay.mov",
        probe=_meta_30fps(duration=0.1),  # 320x180
        codec="prores-4444",
        max_height=4000,
    )
    s_idx = cmd.index("-s")
    assert cmd[s_idx + 1] == "320x180"


def test_max_fps_caps_frame_count_and_rate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Capping fps drops the frame count and quotes the rate as a rational."""
    probe = VideoMetadata(
        width=320, height=180, duration_seconds=1.0, frame_rate_num=60, frame_rate_den=1
    )
    cmd, byte_count = _capture_render_cmd(
        monkeypatch,
        audit_path=_write_audit(tmp_path),
        output=tmp_path / "overlay.mov",
        probe=probe,
        codec="prores-4444",
        max_fps=30,
    )
    # 60fps capped at 30 -> 30/1 (clean integer divisor preserved).
    assert "30/1" in cmd
    # 1.0s @ 30 fps = 30 frames.
    assert byte_count == 30 * 320 * 180 * 4


def test_max_fps_above_source_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap above the source fps preserves the source rational unchanged."""
    cmd, _ = _capture_render_cmd(
        monkeypatch,
        audit_path=_write_audit(tmp_path),
        output=tmp_path / "overlay.mov",
        probe=_meta_30fps(duration=0.1),  # 30/1
        codec="prores-4444",
        max_fps=120,
    )
    assert "30/1" in cmd


def test_capped_frame_rate_keeps_rational_for_29_97() -> None:
    """``60000/1001`` capped at 30 -> ``30000/1001`` (integer divisor)."""
    num, den = overlay_render._capped_frame_rate(60000, 1001, 30)
    assert (num, den) == (30000, 1001)


def test_scaled_dimensions_forces_even() -> None:
    """Even output dims keep yuv420 / yuv444 chroma alignment happy."""
    w, h = overlay_render._scaled_dimensions(1921, 1081, 720)
    assert w % 2 == 0 and h % 2 == 0
