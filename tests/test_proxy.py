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
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    with pytest.raises(ProxyError):
        transcode_proxy(
            Path("/in.mp4"),
            Path("/out.mp4"),
            ProxyConfig(),
            ffmpeg_binary="ffmpeg",
            runner=fake_runner,
        )


@pytest.mark.integration
def test_transcode_proxy_produces_smaller_valid_mp4(tmp_path):
    pytest.skip(
        "No short video fixture available in tests/fixtures/ (only .wav files exist); "
        "skipping real-ffmpeg integration test until a video fixture is added."
    )
