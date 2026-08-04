import json
import subprocess
from pathlib import Path

import pytest

from splitsmith.config import ProxyConfig
from splitsmith.proxy import ProxyError, proxy_key_for, transcode_proxy


def test_proxy_key_for_maps_prefix_and_forces_mp4():
    assert proxy_key_for("raw/GX010123.MP4") == "raw_proxy/GX010123.mp4"
    assert proxy_key_for("raw/clip.mov") == "raw_proxy/clip.mp4"


def test_proxy_key_for_rejects_non_raw_path():
    with pytest.raises(ValueError):
        proxy_key_for("exports/foo.mp4")


def test_transcode_proxy_builds_expected_argv():
    calls = {}

    def fake_runner(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    transcode_proxy(
        Path("/in.mp4"),
        Path("/out.mp4"),
        ProxyConfig(),
        ffmpeg_binary="ffmpeg",
        runner=fake_runner,
    )
    cmd = calls["cmd"]
    assert "scale=-2:480" in cmd
    assert cmd[cmd.index("-crf") + 1] == "30"
    assert cmd[cmd.index("-g") + 1] == "15"
    assert cmd[cmd.index("-keyint_min") + 1] == "15"
    assert cmd[cmd.index("-sc_threshold") + 1] == "0"
    assert "+faststart" in cmd
    assert cmd[-1] == "/out.mp4"
    assert cmd[cmd.index("-i") + 1] == "/in.mp4"


def test_transcode_proxy_raises_on_ffmpeg_error():
    def fake_runner(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="boom")

    with pytest.raises(ProxyError):
        transcode_proxy(
            Path("/in.mp4"),
            Path("/out.mp4"),
            ProxyConfig(),
            ffmpeg_binary="ffmpeg",
            runner=fake_runner,
        )


@pytest.mark.integration
def test_transcode_proxy_produces_smaller_valid_mp4(tmp_path, synthetic_source_video):
    """Real ffmpeg: the proxy is 480p, keyframe-dense and faststart.

    This test skipped unconditionally from the day it was written --
    there was no video fixture for it to use. It now runs against the
    clip ``tests.synthetic_media`` builds (#670).
    """
    out = tmp_path / "proxy.mp4"

    transcode_proxy(
        synthetic_source_video,
        out,
        ProxyConfig(),
        ffmpeg_binary="ffmpeg",
    )

    assert out.exists() and out.stat().st_size > 0
    assert out.stat().st_size < synthetic_source_video.stat().st_size

    streams = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,height",
                "-of",
                "json",
                str(out),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next(s for s in streams if s["codec_type"] == "audio")
    assert video["codec_name"] == "h264"
    assert video["height"] == ProxyConfig().height
    assert audio["codec_name"] == "aac"

    # The point of the proxy is scrubbing: keyframes every ``gop`` frames
    # so a seek lands on one. Asserting the argv carries ``-g 15`` (the
    # unit test above) does not prove ffmpeg honoured it.
    frames = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=key_frame",
            "-of",
            "csv=p=0",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    keyframe_indices = [i for i, flag in enumerate(frames) if flag.strip() == "1"]
    assert len(keyframe_indices) >= 2, f"expected a dense GOP, got {keyframe_indices}"
    gaps = [b - a for a, b in zip(keyframe_indices, keyframe_indices[1:], strict=False)]
    assert max(gaps) <= ProxyConfig().gop, f"keyframe gaps {gaps} exceed gop={ProxyConfig().gop}"
