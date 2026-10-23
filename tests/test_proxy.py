import hashlib
import json
import re
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from splitsmith.config import ProxyConfig
from splitsmith.proxy import (
    HTTP_INPUT_ARGS,
    ProxyError,
    ProxySourceReadError,
    proxy_key_for,
    transcode_proxy,
)

_URL = "https://r2.example/raw/clip.mov?X-Amz-Signature=secret"


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


def test_transcode_proxy_local_source_has_no_http_options():
    calls = {}

    def fake_runner(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    transcode_proxy(
        Path("/in.mp4"), Path("/out.mp4"), ProxyConfig(), ffmpeg_binary="ffmpeg", runner=fake_runner
    )
    assert "-reconnect" not in calls["cmd"]
    assert "-multiple_requests" not in calls["cmd"]


def test_transcode_proxy_url_source_gets_http_input_options():
    calls = {}

    def fake_runner(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    transcode_proxy(_URL, Path("/out.mp4"), ProxyConfig(), ffmpeg_binary="ffmpeg", runner=fake_runner)
    cmd = calls["cmd"]
    i = cmd.index("-i")
    assert cmd[i + 1] == _URL
    # Input options only bind when they come before ``-i``.
    assert cmd[i - len(HTTP_INPUT_ARGS) : i] == list(HTTP_INPUT_ARGS)
    assert cmd[cmd.index("-multiple_requests") + 1] == "1"
    assert cmd[cmd.index("-reconnect_on_network_error") + 1] == "1"
    assert cmd[cmd.index("-rw_timeout") + 1].isdigit()


def test_transcode_proxy_url_source_with_stderr_output_raises_although_ffmpeg_exited_0():
    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="",
            stderr=(
                f"[http @ 0x1] Stream ends prematurely at 30146560 reading {_URL}\n"
                "[http @ 0x1] redirected to https://other.example/x?X-Amz-Signature=secret2\n"
            ),
        )

    with pytest.raises(ProxySourceReadError) as info:
        transcode_proxy(_URL, Path("/out.mp4"), ProxyConfig(), ffmpeg_binary="ffmpeg", runner=fake_runner)
    assert "Stream ends prematurely" in str(info.value)
    assert "secret" not in str(info.value)  # a presigned URL is a credential


def test_transcode_proxy_url_source_failure_never_carries_the_url():
    def fake_runner(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr=f"Error opening input file {_URL}.")

    with pytest.raises(ProxySourceReadError) as info:
        transcode_proxy(_URL, Path("/out.mp4"), ProxyConfig(), ffmpeg_binary="ffmpeg", runner=fake_runner)
    assert "secret" not in str(info.value)
    assert info.value.__cause__ is None  # the CalledProcessError's argv holds the URL


def test_transcode_proxy_local_source_tolerates_stderr_output():
    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="[hevc @ 0x1] a decoder warning\n")

    transcode_proxy(
        Path("/in.mp4"), Path("/out.mp4"), ProxyConfig(), ffmpeg_binary="ffmpeg", runner=fake_runner
    )


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


# --- URL sources over real HTTP (#1674) -------------------------------------


class _RangeServer:
    """A local HTTP server with byte-range support, standing in for R2.

    ``drop_after`` closes the connection once that many bytes have been
    sent (once), and ``expired`` then answers every later request 403, the
    way the store answers a reconnect with a presigned URL past its expiry.
    """

    def __init__(self, path: Path, *, drop_after: int | None = None) -> None:
        self.path = path
        self.drop_after = drop_after
        self.dropped = False
        self.expired = False
        self.bytes_sent = 0
        self.requests: list[str | None] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                pass

            def handle(self) -> None:
                # ffmpeg abandons a connection when it seeks; that is not an error here.
                try:
                    super().handle()
                except ConnectionError:
                    pass

            def do_GET(self) -> None:  # noqa: N802 -- http.server's hook name
                server.requests.append(self.headers.get("Range"))
                if server.expired:
                    self.send_response(403)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                size = server.path.stat().st_size
                start, end = 0, size - 1
                rng = self.headers.get("Range")
                if rng:
                    m = re.fullmatch(r"bytes=(\d+)-(\d*)", rng)
                    assert m is not None, rng
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                self.send_response(206 if rng else 200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if rng:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with server.path.open("rb") as fh:
                    fh.seek(start)
                    remaining = end - start + 1
                    try:
                        while remaining > 0:
                            chunk = fh.read(min(64 * 1024, remaining))
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                            server.bytes_sent += len(chunk)
                            if (
                                server.drop_after is not None
                                and not server.dropped
                                and server.bytes_sent >= server.drop_after
                            ):
                                server.dropped = server.expired = True
                                self.close_connection = True
                                return
                    except (BrokenPipeError, ConnectionResetError):
                        return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}/{path.name}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self) -> "_RangeServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _moov_is_at_the_end(path: Path) -> bool:
    """Walk the top-level MP4 boxes: True when ``moov`` follows ``mdat``."""
    order: list[str] = []
    with path.open("rb") as fh:
        offset, size_total = 0, path.stat().st_size
        while offset < size_total:
            fh.seek(offset)
            header = fh.read(8)
            size, kind = int.from_bytes(header[:4], "big"), header[4:].decode("latin-1")
            if size == 1:
                size = int.from_bytes(fh.read(8), "big")
            order.append(kind)
            offset += size
    return order.index("moov") > order.index("mdat")


def _frame_digest(path: Path) -> str:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0", "-f", "framemd5", "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return hashlib.sha256(
        "\n".join(ln for ln in out.splitlines() if not ln.startswith("#")).encode()
    ).hexdigest()


@pytest.fixture
def moov_at_end_source(synthetic_source_video: Path) -> Iterator[Path]:
    # The synthetic clip is written without ``+faststart``, so its moov
    # trails the media like an iPhone clip's. Guard it: a faststart source
    # would make the streamed test prove less than it claims.
    assert _moov_is_at_the_end(synthetic_source_video)
    yield synthetic_source_video


@pytest.mark.integration
def test_transcode_proxy_from_url_matches_the_local_file(tmp_path, moov_at_end_source):
    """Real ffmpeg over real HTTP: the streamed proxy decodes to the same
    frames as the one cut from a local copy, and the source is read once
    (the trailing moov costs a small range read, not a second pass)."""
    from_file = tmp_path / "file.mp4"
    from_url = tmp_path / "url.mp4"
    transcode_proxy(moov_at_end_source, from_file, ProxyConfig(), ffmpeg_binary="ffmpeg")
    with _RangeServer(moov_at_end_source) as server:
        transcode_proxy(server.url, from_url, ProxyConfig(), ffmpeg_binary="ffmpeg")

    assert _frame_digest(from_url) == _frame_digest(from_file)
    # The count is what the server wrote, including what the loopback socket
    # buffered on the connection ffmpeg abandons after the header (a few MB),
    # so it runs over the size; a second pass over the source would double it.
    size = moov_at_end_source.stat().st_size
    assert server.bytes_sent < size * 1.5, f"sent {server.bytes_sent} bytes of a {size}-byte source"


@pytest.mark.integration
def test_transcode_proxy_from_url_raises_when_the_stream_cannot_resume(tmp_path, moov_at_end_source):
    """The connection drops a third of the way in and the reconnect is
    refused (an expired presigned URL). ffmpeg exits 0 with a truncated
    proxy; the stderr check is what turns that into an error."""
    size = moov_at_end_source.stat().st_size
    with _RangeServer(moov_at_end_source, drop_after=size // 3) as server:
        with pytest.raises(ProxySourceReadError):
            transcode_proxy(server.url, tmp_path / "url.mp4", ProxyConfig(), ffmpeg_binary="ffmpeg")
    assert server.dropped
    assert any(r is not None and r != "bytes=0-" for r in server.requests[1:])  # it did try to resume
