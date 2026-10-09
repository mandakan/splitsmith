"""Every curated xfade kind exists in the ffmpeg on PATH (issue #1259).

A kind the ffmpeg lacks would fail its boundary and silently become a cut
in the render; this pins the list against the ffmpeg CI installs (FFmpeg
6.1, the oldest the project meets: hosted and the desktop ship newer).
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from splitsmith import composition
from tests.synthetic_media import ffmpeg_available

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_every_curated_kind_is_an_xfade_transition_of_the_ffmpeg_on_path() -> None:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    done = subprocess.run([ffmpeg, "-hide_banner", "-h", "filter=xfade"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    offered = set(re.findall(r"^\s+([a-z]+)\s+-?\d+\s", done.stdout, re.M))
    assert "fade" in offered, done.stdout[:2000]
    missing = [kind for kind in composition.XFADE_KINDS if kind not in offered]
    assert missing == [], f"not in this ffmpeg's xfade: {missing}"
