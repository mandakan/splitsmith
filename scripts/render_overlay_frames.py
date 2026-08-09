"""Render a single-shooter overlay and drop labelled frames at named moments.

The counterpart to ``scripts/render_grid_frames.py``, which covers the
compare grid. ``splitsmith export overlay`` produces a *transparent* MOV
meant to sit on V2 in Final Cut over the trimmed clip on V1, so looking
at the MOV on its own tells you almost nothing -- this composites it the
way Final Cut would and then extracts frames.

Run::

    uv run python scripts/render_overlay_frames.py

    # against a different output directory, to diff two revisions
    uv run python scripts/render_overlay_frames.py --out build/overlay-frames-main

It builds its own media (``tests/synthetic_media.py``) and its own audit
(``tests/compare_fixture.write_audit``) -- no real match, nothing that
only exists on one laptop.

Frames come out at **named** moments rather than frame indices the caller
has to work out: ``pre-beep``, ``first-shot``, ``mid-action``,
``last-shot``, ``after-last-shot`` and ``tail-end``.

**Moments are converted to frame indices once, in Python, and extracted
with ``select=eq(n,N)``** -- never by seeking to a timestamp. The
synthetic clip runs at 30000/1001, so a seek that keeps the first frame
at or after a requested time is deciding a tie that sub-tick rounding
breaks in either direction. A frame index is exact at any rate.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# ``tests`` is a package on the repo root, not under ``src``. The fixture
# lives there because it is a fixture -- this tool is a consumer, not its
# owner.
sys.path.insert(0, str(REPO_ROOT))

from splitsmith import overlay_render  # noqa: E402
from tests.compare_fixture import cut_clip, write_audit  # noqa: E402
from tests.synthetic_media import (  # noqa: E402
    SYNTHETIC_FPS_DEN,
    SYNTHETIC_FPS_NUM,
    build_synthetic_video,
    ffmpeg_available,
)

DEFAULT_OUT = REPO_ROOT / "build" / "overlay-frames"

FPS = SYNTHETIC_FPS_NUM / SYNTHETIC_FPS_DEN
CLIP_FRAMES = 300
BEEP_OFFSET_SECONDS = 1.0
# A 12-shot stage at roughly IPSC Production Optics pace: a 1.1s draw
# then splits in the 0.18-0.34s band. Real enough that the counter and
# the split label both change at a plausible rate.
SHOTS_MS = (1100, 1320, 1560, 1740, 1980, 2310, 2530, 2790, 3040, 3280, 3600, 3850)


@dataclass(frozen=True)
class Moment:
    name: str
    index: int
    why: str


def _moments() -> tuple[Moment, ...]:
    def at(seconds: float) -> int:
        return round(seconds * FPS)

    first = BEEP_OFFSET_SECONDS + SHOTS_MS[0] / 1000.0
    last = BEEP_OFFSET_SECONDS + SHOTS_MS[-1] / 1000.0
    mid = BEEP_OFFSET_SECONDS + SHOTS_MS[len(SHOTS_MS) // 2] / 1000.0
    return (
        Moment("pre-beep", at(BEEP_OFFSET_SECONDS / 2), "counter reads 0/M, clock reads 0.00"),
        Moment("first-shot", at(first), "counter goes 1/M, no split yet -- nothing to measure against"),
        Moment("mid-action", at(mid), "counter and split both live, clock ticking"),
        Moment("last-shot", at(last), "counter reads M/M"),
        Moment("after-last-shot", at(last + 0.75), "clock frozen, split still up"),
        Moment("tail-end", CLIP_FRAMES - 2, "the post-buffer -- what the viewer is left looking at"),
    )


def _run(cmd: list[str]) -> None:
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd[:3])}...\n{done.stderr[-2000:]}")


def _composite(trim: Path, overlay: Path, destination: Path, *, ffmpeg: str) -> None:
    """Burn the alpha overlay onto the trim, the way FCP composites V2
    over V1. The filter shape is ``mp4_render._build_stage_filter_graph``'s
    (see ``src/splitsmith/mp4_render.py:454-457``), not a new one."""
    _run(
        [
            ffmpeg, "-hide_banner", "-y", "-v", "error",
            "-i", str(trim), "-i", str(overlay),
            "-filter_complex",
            "[1:v]setpts=PTS-STARTPTS[overlay_v];[0:v][overlay_v]overlay=0:0[out]",
            "-map", "[out]", "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p",
            str(destination),
        ]  # fmt: skip
    )


def _extract(video: Path, index: int, destination: Path, *, ffmpeg: str) -> bool:
    """Write frame ``index`` of ``video`` to ``destination``.

    Returns ``False`` when the index is past the end rather than raising:
    ffmpeg exits 0 and writes nothing in that case, and a caller asking
    for a moment a shorter render does not contain should hear about it
    once, not lose the whole run.
    """
    destination.unlink(missing_ok=True)
    _run(
        [
            ffmpeg, "-hide_banner", "-y", "-v", "error", "-i", str(video),
            "-vf", f"select=eq(n\\,{index})", "-fps_mode", "passthrough",
            "-frames:v", "1", str(destination),
        ]  # fmt: skip
    )
    return destination.exists() and destination.stat().st_size > 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--theme", choices=("splitsmith", "clean"), default="splitsmith")
    parser.add_argument("--keep-video", action="store_true")
    args = parser.parse_args()

    if not ffmpeg_available():
        parser.error("ffmpeg and ffprobe must be on PATH")
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None  # ffmpeg_available() just said so

    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = out / "work"
    work.mkdir()

    source = work / "source.mp4"
    build_synthetic_video(source)
    trim = work / "trim.mp4"
    cut_clip(source, trim, CLIP_FRAMES, ffmpeg=ffmpeg)

    audit = work / "stage1.json"
    write_audit(audit, SHOTS_MS)

    overlay = work / "overlay.mov"
    overlay_render.render_overlay(
        audit_path=audit,
        trimmed_video_path=trim,
        output_path=overlay,
        beep_offset_seconds=BEEP_OFFSET_SECONDS,
        codec="prores-4444",
        theme=args.theme,
        ffmpeg_binary=ffmpeg,
    )

    composed = work / "composed.mp4"
    _composite(trim, overlay, composed, ffmpeg=ffmpeg)

    for moment in _moments():
        target = out / f"{moment.name}.png"
        if _extract(composed, moment.index, target, ffmpeg=ffmpeg):
            print(f"{moment.name:18s} frame {moment.index:4d}  {moment.why}")
        else:
            print(f"{moment.name:18s} frame {moment.index:4d}  SKIPPED (past end of render)")

    if not args.keep_video:
        shutil.rmtree(work)
    print(f"\nframes in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
