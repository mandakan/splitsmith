"""Tests for overlay_render (issue #45, ported in #684).

Pure unit tests on frame-state derivation and on the argv / rasterizer
calls the render loop makes. The ffmpeg pipe path is exercised via a
mocked Popen so CI doesn't need prores_ks support, and the browser via an
injected fake :class:`~splitsmith.overlay_raster.Rasterizer`; integration
tests cover the real binary and a real Chromium in development.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageFont
from playwright.sync_api import Error as PlaywrightError

from splitsmith import overlay_raster, overlay_render, runtime
from splitsmith.config import VideoMetadata
from splitsmith.overlay_clock import border_width
from splitsmith.overlay_layout import CellScale
from splitsmith.overlay_text import overlay_font_file, resolve_overlay_face
from tests.conftest import fake_ffmpeg_probe
from tests.synthetic_media import ffmpeg_available


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
        rasterizer=_FakeRasterizer(),
        probe_runner=fake_ffmpeg_probe(),
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
            rasterizer=_FakeRasterizer(),
            probe_runner=fake_ffmpeg_probe(),
        )


@pytest.mark.integration
def test_render_overlay_writes_real_prores_4444_alpha(tmp_path: Path) -> None:
    """End-to-end: real ffmpeg writes a parseable ProRes 4444 alpha MOV.

    Skipped when ffmpeg / ffprobe aren't on PATH. CI installs them and
    sets ``SPLITSMITH_REQUIRE_INTEGRATION``, which turns that skip into
    a failure (#670)."""
    if not ffmpeg_available():
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

    with overlay_raster.ChromiumRasterizer() as ras:
        overlay_render.render_overlay(
            audit_path=audit,
            trimmed_video_path=tmp_path / "ignored.mp4",
            output_path=output,
            beep_offset_seconds=0.0,
            probe=_meta_30fps(duration=0.5),  # 15 frames
            codec="prores-4444",  # this test asserts the prores path specifically
            rasterizer=ras,
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
    rasterizer: Any = None,
    beep_offset_seconds: float = 0.0,
    **kwargs: Any,
) -> tuple[list[str], int]:
    """Run :func:`render_overlay` with a stub Popen, returning the cmd
    that would have been invoked and the bytes piped to ffmpeg's stdin.

    ``rasterizer`` defaults to a fresh :class:`_FakeRasterizer`, so every
    caller that only cares about argv gets a browser-free render without
    saying so; pass one to inspect the documents it was handed.
    ``beep_offset_seconds`` defaults to the ``0.0`` this helper has always
    passed. The capability probe is faked for the reason
    ``render_overlay``'s ``probe_runner`` docstring gives -- stubbing
    ``Popen`` stubs it for ``subprocess.run`` too.
    """
    if rasterizer is None:
        rasterizer = _FakeRasterizer()
    kwargs.setdefault("probe_runner", fake_ffmpeg_probe())
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
        beep_offset_seconds=beep_offset_seconds,
        probe=probe,
        rasterizer=rasterizer,
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


def test_codec_hevc_alpha_emits_videotoolbox_cmd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_codec_auto_falls_back_to_prores_off_darwin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_codec_unknown_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub ``which`` so the ffmpeg-not-found check (which fires on CI
    # boxes without ffmpeg installed) doesn't shadow the codec error.
    monkeypatch.setattr(overlay_render.shutil, "which", lambda _b: "/bin/ffmpeg")
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
    probe = VideoMetadata(width=1920, height=1080, duration_seconds=0.1, frame_rate_num=30, frame_rate_den=1)
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
    probe = VideoMetadata(width=320, height=180, duration_seconds=1.0, frame_rate_num=60, frame_rate_den=1)
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


# --- the ported renderer (issue #684) ---------------------------------


class _FakeRasterizer:
    """Records what it was asked to draw and returns a real PNG.

    A real PNG, not a stub: ``render_overlay`` decodes what comes back
    and pipes its bytes, so a fake returning ``b""`` would test a code
    path that cannot exist.
    """

    def __init__(self) -> None:
        self.documents: list[str] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.documents.append(html)
        buffer = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buffer, format="PNG")
        return buffer.getvalue()


def test_the_renderer_rasterizes_once_per_run_not_once_per_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the port. 30 frames, two shots -> three runs
    (nothing fired, one fired, two fired), so three browser renders."""
    audit = tmp_path / "stage1.json"
    audit.write_text(
        json.dumps(
            {"shots": [{"shot_number": 1, "ms_after_beep": 200}, {"shot_number": 2, "ms_after_beep": 500}]}
        ),
        encoding="utf-8",
    )
    fake = _FakeRasterizer()
    _capture_render_cmd(
        monkeypatch,
        audit_path=audit,
        output=tmp_path / "overlay.mov",
        probe=_meta_30fps(duration=1.0),
        rasterizer=fake,
    )
    assert len(fake.documents) == 3


def test_the_counter_and_split_reach_the_rasterized_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = tmp_path / "stage1.json"
    audit.write_text(
        json.dumps(
            {"shots": [{"shot_number": 1, "ms_after_beep": 200}, {"shot_number": 2, "ms_after_beep": 500}]}
        ),
        encoding="utf-8",
    )
    fake = _FakeRasterizer()
    _capture_render_cmd(
        monkeypatch,
        audit_path=audit,
        output=tmp_path / "overlay.mov",
        probe=_meta_30fps(duration=1.0),
        rasterizer=fake,
    )
    assert "0/2" in fake.documents[0]
    assert "1/2" in fake.documents[1]
    assert "2/2" in fake.documents[2]
    # 500ms - 200ms = 0.30s
    assert "0.30s" in fake.documents[2]


def test_a_missing_browser_fails_the_render_with_the_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike the grid, which degrades to clock-only because its MP4 is
    still worth having, the overlay MOV *is* the deliverable here. A
    clock-only MOV looks like a success the user would only discover was
    empty after dropping it on V2 in Final Cut."""
    audit = tmp_path / "stage1.json"
    audit.write_text(json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 100}]}), encoding="utf-8")

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise overlay_raster.RasterizerUnavailableError("no chromium", "detail with a hint")

    monkeypatch.setattr(overlay_render.ChromiumRasterizer, "__enter__", boom)
    monkeypatch.setattr(overlay_render.shutil, "which", lambda _b: "/bin/ffmpeg")
    with pytest.raises(overlay_render.OverlayRenderError) as excinfo:
        overlay_render.render_overlay(
            audit_path=audit,
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=tmp_path / "overlay.mov",
            beep_offset_seconds=0.0,
            probe=_meta_30fps(duration=1.0),
            codec="prores-4444",
            probe_runner=fake_ffmpeg_probe(),
        )
    assert overlay_raster.INSTALL_HINT in str(excinfo.value)


def test_the_clock_is_three_drawtext_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-beep 0.00, a ticking window, and a held final value. The grid
    needs only two because it never draws a pre-beep zero; this path has
    always shown one and keeps doing so."""
    audit = tmp_path / "stage1.json"
    audit.write_text(json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 200}]}), encoding="utf-8")
    cmd, _ = _capture_render_cmd(
        monkeypatch,
        audit_path=audit,
        output=tmp_path / "overlay.mov",
        probe=_meta_30fps(duration=2.0),
        rasterizer=_FakeRasterizer(),
        beep_offset_seconds=1.0,
    )
    graph = cmd[cmd.index("-vf") + 1]
    assert graph.count("drawtext") == 3
    assert r"enable='lt(t\,1)'" in graph
    assert r"enable='gte(t\,1)*lt(t\,1.2)'" in graph
    assert r"enable='gte(t\,1.2)'" in graph


def test_an_ffmpeg_without_drawtext_still_renders_the_counter_and_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other degradation, and it goes the other way: losing the clock
    leaves a file worth having, so it warns rather than failing."""
    audit = tmp_path / "stage1.json"
    audit.write_text(json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 200}]}), encoding="utf-8")
    monkeypatch.setattr(
        overlay_render,
        "ffmpeg_capabilities",
        lambda *_a, **_k: runtime.FFmpegCapabilities(
            binary="ffmpeg", version="6.1.1", drawtext=False, concat_option_keyword=True
        ),
    )
    fake = _FakeRasterizer()
    cmd, _ = _capture_render_cmd(
        monkeypatch,
        audit_path=audit,
        output=tmp_path / "overlay.mov",
        probe=_meta_30fps(duration=1.0),
        rasterizer=fake,
    )
    assert "-vf" not in cmd
    assert len(fake.documents) == 2


@pytest.mark.integration
def test_the_clock_holds_before_the_beep_ticks_during_and_freezes_after(
    tmp_path: Path,
) -> None:
    """Three windows, asserted on rendered frames rather than on argv.

    No OCR: what matters is behavioural and reads straight off the
    pixels. Two frames inside the same window must be identical in the
    clock's corner (it is holding a value); two frames inside the ticking
    window must differ (it is counting). And the corner must not be
    blank before the beep -- drawing nothing there is exactly the
    regression copying the grid's two-filter clock would have caused.
    """
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")

    audit = tmp_path / "stage1.json"
    audit.write_text(json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 1000}]}), encoding="utf-8")
    output = tmp_path / "overlay.mov"
    with overlay_raster.ChromiumRasterizer() as ras:
        overlay_render.render_overlay(
            audit_path=audit,
            trimmed_video_path=tmp_path / "unused.mp4",
            output_path=output,
            beep_offset_seconds=1.0,
            probe=_meta_30fps(duration=3.0),
            codec="prores-4444",
            rasterizer=ras,
        )

    def corner(frame_index: int) -> Image.Image:
        """The top-right quadrant, where the clock lives."""
        png = tmp_path / f"f{frame_index}.png"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-y", "-v", "error", "-i", str(output),
                "-vf", f"select=eq(n\\,{frame_index})", "-fps_mode", "passthrough",
                "-frames:v", "1", str(png),
            ],  # fmt: skip
            check=True,
        )
        image = Image.open(png).convert("RGBA")
        width, height = image.size
        return image.crop((width // 2, 0, width, height // 3))

    # Pre-beep: two frames, both reading 0.00.
    pre_early, pre_late = corner(6), corner(24)
    assert pre_early.getbbox() is not None, "the clock corner is blank before the beep"
    assert list(pre_early.getdata()) == list(pre_late.getdata())

    # Ticking (beep at 1.0s = frame 30, freeze at 2.0s = frame 60).
    assert list(corner(36).getdata()) != list(corner(50).getdata())

    # Frozen after the last shot: the running total is the stage time,
    # not the clip duration.
    assert list(corner(66).getdata()) == list(corner(86).getdata())


# --- fix round 1: the failure path, the clamp, and the shared baseline ---


def test_a_rasterizer_that_dies_mid_render_kills_ffmpeg_and_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A browser crash must not orphan ffmpeg or leave a truncated MOV.

    ``active.png`` is a Playwright call and its failures are
    ``playwright.sync_api.Error``, which is neither ``BrokenPipeError``
    nor ``OSError``. Escaping the loop uncaught, it leaves ffmpeg blocked
    on an open stdin until the GC drops the Popen and flushes a truncated
    file -- and it is not an ``OverlayRenderError``, so ``ui/exports.py``
    turns one bad stage into a failed export request instead of a skip
    reason, then wires the truncation into the next FCPXML as a "stale
    render from a prior run".
    """
    audit = tmp_path / "stage1.json"
    audit.write_text(json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 100}]}), encoding="utf-8")
    output = tmp_path / "overlay.mov"
    # ffmpeg opens its output with -y before the first frame, so there is
    # always something on disk by the time a run fails.
    output.write_bytes(b"truncated")
    lifecycle: list[str] = []

    class StubStdin:
        def write(self, data: bytes) -> int:
            return len(data)

        def close(self) -> None:
            lifecycle.append("close")

    class StubStderr:
        def read(self) -> bytes:
            return b""

    class StubProc:
        def __init__(self, **_: Any) -> None:
            self.stdin = StubStdin()
            self.stderr = StubStderr()

        def wait(self) -> int:
            lifecycle.append("wait")
            return 0

        def kill(self) -> None:
            lifecycle.append("kill")

    class DyingRasterizer:
        def png(self, html: str, *, width: int, height: int) -> bytes:
            raise PlaywrightError("Timeout 30000ms exceeded.")

    monkeypatch.setattr(overlay_render.subprocess, "Popen", lambda cmd, **_: StubProc())
    monkeypatch.setattr(overlay_render.shutil, "which", lambda _b: "/bin/ffmpeg")

    with pytest.raises(overlay_render.OverlayRenderError, match="Timeout 30000ms"):
        overlay_render.render_overlay(
            audit_path=audit,
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=output,
            beep_offset_seconds=0.0,
            probe=_meta_30fps(duration=0.1),
            codec="prores-4444",
            rasterizer=DyingRasterizer(),
            probe_runner=fake_ffmpeg_probe(),
        )
    assert lifecycle == ["kill", "wait"], "the encoder was not killed and reaped"
    assert not output.exists(), "a truncated MOV was left where a caller would find it"


def test_a_shot_before_the_beep_cannot_stack_two_clock_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A negative ``ms_after_beep`` used to put ``freeze`` below ``start``,
    which enables the pre-beep filter and the held filter over the same
    ``[freeze, start)`` window and draws two numbers on top of each other
    -- the exact collision the lt/gte windows exist to prevent."""
    audit = tmp_path / "stage1.json"
    audit.write_text(json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": -200}]}), encoding="utf-8")
    cmd, _ = _capture_render_cmd(
        monkeypatch,
        audit_path=audit,
        output=tmp_path / "overlay.mov",
        probe=_meta_30fps(duration=2.0),
        rasterizer=_FakeRasterizer(),
        beep_offset_seconds=1.0,
    )
    graph = cmd[cmd.index("-vf") + 1]
    # freeze is clamped up to the beep, so the ticking window is empty and
    # the two remaining windows meet at 1 without overlapping.
    assert r"enable='lt(t\,1)'" in graph
    assert r"enable='gte(t\,1)*lt(t\,1)'" in graph
    assert r"enable='gte(t\,1)'" in graph
    assert "lt(t\\,0.8)" not in graph


@pytest.mark.integration
def test_the_counter_and_the_clock_sit_on_one_baseline(tmp_path: Path) -> None:
    """Two rasterizers, one frame, one baseline -- asserted on pixels.

    ``drawtext`` puts the top of the drawn *ink* at ``y``; CSS (and PIL's
    default ``"la"`` anchor, which the pre-port template used) put the
    *ascender* there. Same face, same size, ~22px apart at 1080 unless
    something reconciles them -- which is what shipped, and what this
    pins.

    The two corners draw different glyph sets (``0/1`` has a slash that
    rises above digits), so comparing raw ink tops would compare the
    fonts, not the layout. Instead each corner's measured ink top is
    walked back to the ascender line it implies -- minus its own outside
    stroke, minus the face's ascender-to-ink gap for the string it
    actually drew -- and both must land on ``pad``. That also catches a
    stroke regression: the two corners are only equal here because their
    visible outside stroke is the same number.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    width, height = 1920, 1080
    audit = tmp_path / "stage1.json"
    audit.write_text(json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 200}]}), encoding="utf-8")
    output = tmp_path / "overlay.mov"
    with overlay_raster.ChromiumRasterizer() as ras:
        overlay_render.render_overlay(
            audit_path=audit,
            trimmed_video_path=tmp_path / "unused.mp4",
            output_path=output,
            # Beep at 1.0s with a 0.1s clip: every rendered frame is
            # pre-beep, so the corners read "0/1" and "0.00".
            beep_offset_seconds=1.0,
            probe=VideoMetadata(
                width=width, height=height, duration_seconds=0.1, frame_rate_num=30, frame_rate_den=1
            ),
            codec="prores-4444",
            rasterizer=ras,
        )

    frame = tmp_path / "f0.png"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-y", "-v", "error", "-i", str(output),
            "-vf", "select=eq(n\\,0)", "-fps_mode", "passthrough",
            "-frames:v", "1", "-pix_fmt", "rgba", str(frame),
        ],  # fmt: skip
        check=True,
    )
    # Threshold away the soft text-shadow so the measurement is the glyph
    # and its stroke, which is what "on one baseline" is about.
    alpha = Image.open(frame).convert("RGBA").getchannel("A")
    core = alpha.point(lambda p: 255 if p >= 200 else 0)

    def ink_top(left: int, right: int) -> int:
        box = core.crop((left, 0, right, height // 3)).getbbox()
        assert box is not None, f"nothing drawn in x={left}..{right}"
        return box[1]

    scale = CellScale.for_cell(height)
    face = ImageFont.truetype(
        str(overlay_font_file(resolve_overlay_face("splitsmith-mono"), tmp_path)),
        size=scale.live_primary,
    )
    outside = border_width(scale.live_primary)
    counter_ascender = ink_top(0, width // 2) + outside - face.getbbox("0/1")[1]
    clock_ascender = ink_top(width // 2, width) + outside - face.getbbox("0.00")[1]

    assert (
        abs(counter_ascender - clock_ascender) <= 1
    ), f"counter puts the ascender at y={counter_ascender}, clock at y={clock_ascender}"
    assert (
        abs(counter_ascender - scale.pad) <= 1
    ), f"both corners agree at y={counter_ascender} but pad is {scale.pad}"
