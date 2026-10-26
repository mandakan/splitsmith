"""Preview/proxy video generation (hosted Ingest fast-scrub).

Pure ffmpeg wrapper mirroring ``trim.py``: paths + config in, shells ffmpeg
via an injected runner, raises on failure. No storage or project I/O here.

The source is a local file or, in hosted mode, a presigned GET URL that
ffmpeg reads with HTTP range requests (#1674), so decoding starts with the
first bytes instead of after a whole-file download. A moov atom at the end
of the file (iPhone clips) costs one small range read of the tail, not a
full read.
"""

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from .config import ProxyConfig

RAW_PREFIX = "raw/"
PROXY_PREFIX = "raw_proxy/"

#: ffmpeg input options for a URL source. ``multiple_requests`` keeps one
#: connection across the demuxer's seeks: without it ffmpeg sends
#: ``Connection: close`` yet may still reuse the socket for the next range
#: request when the server's reply did not say ``close``, and that request
#: reads EOF. The reconnect options retry a dropped connection or a 5xx at
#: the same offset; ``rw_timeout`` (microseconds) bounds a read that stalls,
#: which would otherwise hang the job.
HTTP_INPUT_ARGS: tuple[str, ...] = (
    "-multiple_requests",
    "1",
    "-reconnect",
    "1",
    "-reconnect_on_network_error",
    "1",
    "-reconnect_on_http_error",
    "5xx",
    "-reconnect_delay_max",
    "10",
    "-rw_timeout",
    "60000000",
)

#: What a URL stands for in an error message: a presigned URL is a bearer
#: credential for the object until it expires.
_URL_PLACEHOLDER = "<source-url>"
_URL_RE = re.compile(r"[a-z][a-z0-9+.-]*://[^\s'\"]+", re.IGNORECASE)


def redact_urls(text: str | None) -> str:
    """``text`` with every URL replaced: a presigned URL is a credential, and
    ffmpeg may print a redirect target or a reformatted copy of the input."""
    return _URL_RE.sub(_URL_PLACEHOLDER, text or "")


class ProxyError(RuntimeError):
    """ffmpeg failed to produce a proxy."""


class ProxySourceReadError(ProxyError):
    """ffmpeg reported a problem reading a URL source, so the proxy may be
    missing frames even though ffmpeg exited 0.

    Measured with ffmpeg 8: a connection that drops and cannot be resumed
    (the presigned URL expired, the retries ran out) ends the input early,
    and a range request that reads EOF skips the samples it covered; both
    exit 0 with a short or damaged output and an error line on stderr. The
    caller discards the output and transcodes from a downloaded copy.
    """


def proxy_key_for(raw_path: str) -> str:
    """Map a raw upload key to its proxy key: raw/<name>.<ext> -> raw_proxy/<name>.mp4."""
    if not raw_path.startswith(RAW_PREFIX):
        raise ValueError(f"expected a {RAW_PREFIX!r} key, got {raw_path!r}")
    name = Path(raw_path[len(RAW_PREFIX) :]).with_suffix(".mp4").as_posix()
    return f"{PROXY_PREFIX}{name}"


def transcode_proxy(
    source: Path | str,
    output_path: Path,
    config: ProxyConfig,
    *,
    ffmpeg_binary: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    """Transcode ``source`` to a low-res, dense-GOP, faststart MP4 proxy.

    ``source`` is a local path, or a ``str`` URL that ffmpeg reads over
    HTTP. For a URL any output on stderr (ffmpeg runs at ``-loglevel
    error``) raises :class:`ProxySourceReadError`, since a failed read does
    not change the exit code; the URL never appears in an error message.
    """
    is_url = isinstance(source, str)
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        *(HTTP_INPUT_ARGS if is_url else ()),
        "-i",
        str(source),
        "-vf",
        f"scale=-2:{config.height}",
        "-c:v",
        config.video_codec,
        "-preset",
        config.preset,
        "-crf",
        str(config.crf),
        "-g",
        str(config.gop),
        "-keyint_min",
        str(config.gop),
        "-sc_threshold",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        config.audio_bitrate,
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    def _redact(text: str | None) -> str:
        return redact_urls(text) if is_url else (text or "")

    try:
        result = runner(cmd, check=True, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProxyError(f"ffmpeg binary not found: {ffmpeg_binary}") from exc
    except subprocess.CalledProcessError as exc:
        detail = _redact(exc.stderr or exc.stdout)
        if is_url:
            raise ProxySourceReadError(
                f"ffmpeg proxy transcode failed (exit {exc.returncode}): {detail!r}"
            ) from None
        raise ProxyError(f"ffmpeg proxy transcode failed (exit {exc.returncode}): {detail!r}") from exc
    if is_url and (result.stderr or "").strip():
        raise ProxySourceReadError(
            f"ffmpeg reported errors reading the source: {_redact(result.stderr).strip()!r}"
        )
