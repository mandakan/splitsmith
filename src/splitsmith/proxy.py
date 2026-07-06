"""Preview/proxy video generation (hosted Ingest fast-scrub).

Pure ffmpeg wrapper mirroring ``trim.py``: paths + config in, shells ffmpeg
via an injected runner, raises on failure. No storage or project I/O here.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

from .config import ProxyConfig

RAW_PREFIX = "raw/"
PROXY_PREFIX = "raw_proxy/"


class ProxyError(RuntimeError):
    """ffmpeg failed to produce a proxy."""


def proxy_key_for(raw_path: str) -> str:
    """Map a raw upload key to its proxy key: raw/<name>.<ext> -> raw_proxy/<name>.mp4."""
    if not raw_path.startswith(RAW_PREFIX):
        raise ValueError(f"expected a {RAW_PREFIX!r} key, got {raw_path!r}")
    name = Path(raw_path[len(RAW_PREFIX) :]).with_suffix(".mp4").as_posix()
    return f"{PROXY_PREFIX}{name}"


def transcode_proxy(
    input_path: Path,
    output_path: Path,
    config: ProxyConfig,
    *,
    ffmpeg_binary: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    """Transcode ``input_path`` to a low-res, dense-GOP, faststart MP4 proxy."""
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
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
    try:
        runner(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ProxyError(f"ffmpeg binary not found: {ffmpeg_binary}") from exc
    except subprocess.CalledProcessError as exc:
        raise ProxyError(
            f"ffmpeg proxy transcode failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc
