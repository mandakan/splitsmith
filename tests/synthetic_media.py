"""ffmpeg-generated media for the integration tests.

The renderer/trim/proxy integration tests need a real video file to push
through ffmpeg and then measure. The only video fixture this repo ever
had was ``tests/fixtures/stage_sample.mp4`` -- 4K head-cam footage that
is gitignored for size, so it exists on exactly one laptop and never in
CI. Every test bound to it skipped, which is how six defects reached a
green suite on ``feat/compare-grid-mp4-phase-0`` (#670).

So the media is synthesized instead. This is not a fabricated fixture in
the sense CLAUDE.md forbids: there is no invented ground truth here. The
clip carries no shots and no beep, and nothing in the detection pipeline
reads it. It is a container with *known* geometry, frame rate, duration
and stream layout, which is precisely what the ffmpeg-output tests
assert against -- pad lengths, stream counts, keyframe spacing, decoded
sample counts. Real footage would make those assertions no stronger.

The clip is built once per pytest session and reused, so the ~2s encode
is paid once.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# Known properties of the synthesized clip. Tests assert against these
# rather than magic numbers so a change here can't silently weaken them.
#
# The frame rate is deliberately the NTSC fraction rather than a round
# 30: fractional rates are where frame-count and duration maths goes
# wrong, and the head-cam footage this tool actually processes is
# 30000/1001.
SYNTHETIC_WIDTH = 1280
SYNTHETIC_HEIGHT = 720
SYNTHETIC_FPS_NUM = 30000
SYNTHETIC_FPS_DEN = 1001
SYNTHETIC_DURATION_S = 24.0
SYNTHETIC_SAMPLE_RATE = 48000
# Keyframe every ~1s, so a stream-copy trim (which snaps ``-ss`` to the
# preceding keyframe) lands within a second of the requested window.
SYNTHETIC_GOP_FRAMES = 30


def ffmpeg_available() -> bool:
    """True when both ffmpeg and ffprobe are on ``PATH``."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def build_synthetic_video(destination: Path) -> Path:
    """Encode the canonical synthetic source clip at ``destination``.

    H.264 video + AAC audio in MP4 -- the same shape as the head-cam
    footage the pipeline consumes, so stream-copy and re-encode paths
    both behave the way they do in production.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        (
            f"testsrc2=size={SYNTHETIC_WIDTH}x{SYNTHETIC_HEIGHT}"
            f":rate={SYNTHETIC_FPS_NUM}/{SYNTHETIC_FPS_DEN}"
            f":duration={SYNTHETIC_DURATION_S}"
        ),
        "-f",
        "lavfi",
        "-i",
        (f"sine=frequency=440:sample_rate={SYNTHETIC_SAMPLE_RATE}" f":duration={SYNTHETIC_DURATION_S}"),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-g",
        str(SYNTHETIC_GOP_FRAMES),
        "-keyint_min",
        str(SYNTHETIC_GOP_FRAMES),
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        str(destination),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return destination
