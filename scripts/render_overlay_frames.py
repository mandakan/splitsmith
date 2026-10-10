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

With ``--stage-events`` (implied by ``--reload-chip`` / ``--stage-bar``)
the stage is a different twelve shots with a confirmed movement spanning
three of them, a confirmed reload overlapping the movement's end (so
part of it is exposed) and a confirmed activation later on; five more
moments come out: ``mid-movement``, ``mid-reload``, ``reload-fade``
(0.2 s after the reload ends), ``activation`` and ``landed``. Render
it with both toggles off as well: that is the frame to compare with the
template before a change, the regions are in the data and must draw
nothing.

``--size WxH`` scales the trim first, so a frame at 1920x1080 or a
phone-ish size shows the HUD at that page size.

**Moments are converted to frame indices once, in Python, and extracted
with ``select=eq(n,N)``** -- never by seeking to a timestamp. The
synthetic clip runs at 30000/1001, so a seek that keeps the first frame
at or after a requested time is deciding a tie that sub-tick rounding
breaks in either direction. A frame index is exact at any rate.
"""

from __future__ import annotations

import argparse
import json
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
from splitsmith.overlay_hud import HUD_POSITIONS, HudOptions  # noqa: E402
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

# The stage with regions: three standing shots, three fired on the move,
# a reload that starts on the move and ends 1.0 s after the movement
# does (1.0 s of it exposed), then six standing shots with an activation
# among them. Milliseconds from the beep, like the shots; every region
# confirmed (``manual``).
EVENT_SHOTS_MS = (1100, 1320, 1540, 1900, 2150, 2400, 3850, 4070, 4290, 4520, 4740, 4960)
MOVEMENT_MS = (1650, 2700)
RELOAD_MS = (2550, 3700)
ACTIVATION_MS = (4150, 4450)
#: The templates' fade after a reload ends; ``reload-fade`` sits part-way.
RELOAD_FADE_SECONDS = 0.4


@dataclass(frozen=True)
class Moment:
    name: str
    index: int
    why: str


def _moments(shots_ms: tuple[int, ...], *, events: bool) -> tuple[Moment, ...]:
    def at(seconds: float) -> int:
        return round(seconds * FPS)

    first = BEEP_OFFSET_SECONDS + shots_ms[0] / 1000.0
    last = BEEP_OFFSET_SECONDS + shots_ms[-1] / 1000.0
    mid = BEEP_OFFSET_SECONDS + shots_ms[len(shots_ms) // 2] / 1000.0
    base = (
        Moment("pre-beep", at(BEEP_OFFSET_SECONDS / 2), "counter reads 0/M, clock reads 0.00"),
        Moment("first-shot", at(first), "counter goes 1/M, no split yet -- nothing to measure against"),
        Moment("mid-action", at(mid), "counter and split both live, clock ticking"),
        # round()-to-nearest lands this one frame before the last shot's
        # exact timestamp (4.8382s vs 4.85s) and build_frame_states fires
        # at-or-after, so the counter reads (M-1)/M here, not M/M -- e.g.
        # 11/12 for the default SHOTS_MS. The frame is correct and
        # deterministic; only this description used to overpromise.
        Moment("last-shot", at(last), "counter reads (M-1)/M -- one frame short of the last shot"),
        Moment("after-last-shot", at(last + 0.75), "clock frozen, split still up"),
        Moment("tail-end", CLIP_FRAMES - 2, "the post-buffer -- what the viewer is left looking at"),
    )
    if not events:
        return base

    def clip(ms: int) -> float:
        return BEEP_OFFSET_SECONDS + ms / 1000.0

    reload_start, reload_end = clip(RELOAD_MS[0]), clip(RELOAD_MS[1])
    return (
        *base,
        Moment("mid-movement", at(clip(EVENT_SHOTS_MS[4]) + 0.1), "movement band growing, no chip"),
        Moment("mid-reload", at(reload_start + 1.0), "chip up, counting ~1.00; reload band growing"),
        Moment(
            "reload-fade",
            at(reload_end + RELOAD_FADE_SECONDS / 2),
            f"chip half faded, holding {(RELOAD_MS[1] - RELOAD_MS[0]) / 1000:.2f}",
        ),
        Moment("activation", at(clip(ACTIVATION_MS[1]) + 0.15), "activation band drawn in full"),
        Moment("landed", at(last + 1.0), "stage done; bands frozen, chip gone"),
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


def _events() -> list[dict[str, object]]:
    return [
        {"id": "evt-1", "kind": "movement", "start": MOVEMENT_MS[0] / 1000, "end": MOVEMENT_MS[1] / 1000,
         "source": "manual"},
        {"id": "evt-2", "kind": "reload", "start": RELOAD_MS[0] / 1000, "end": RELOAD_MS[1] / 1000,
         "source": "manual"},
        {"id": "evt-3", "kind": "activation", "start": ACTIVATION_MS[0] / 1000,
         "end": ACTIVATION_MS[1] / 1000, "source": "manual"},
    ]  # fmt: skip


def _size(text: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in text.lower().split("x"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected WxH, got {text!r}") from exc
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise argparse.ArgumentTypeError("both sides must be positive and even")
    return width, height


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--theme", choices=("splitsmith", "clean"), default="splitsmith")
    parser.add_argument("--keep-video", action="store_true")
    parser.add_argument(
        "--overlay-variant", default="default", help="overlay style: default (Classic) or a template"
    )
    parser.add_argument("--overlay-position", default=None, choices=HUD_POSITIONS)
    parser.add_argument("--speed-colors", action="store_true")
    parser.add_argument("--no-class-labels", action="store_true")
    parser.add_argument("--no-landing", action="store_true")
    parser.add_argument("--reload-chip", action="store_true", help="draw the reload chip (HUD styles)")
    parser.add_argument("--stage-bar", action="store_true", help="draw the stage bar (HUD styles)")
    parser.add_argument(
        "--stage-events",
        action="store_true",
        help="use the stage with a confirmed movement and reload (implied by the two toggles)",
    )
    parser.add_argument("--size", type=_size, default=None, help="scale the trim to WxH first")
    args = parser.parse_args()
    with_events = args.stage_events or args.reload_chip or args.stage_bar
    shots_ms = EVENT_SHOTS_MS if with_events else SHOTS_MS

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
    if args.size is not None:
        scaled = work / "trim-scaled.mp4"
        width, height = args.size
        _run(
            [
                ffmpeg, "-hide_banner", "-y", "-v", "error", "-i", str(trim),
                "-vf", f"scale={width}:{height}", "-c:v", "libx264", "-crf", "14",
                "-pix_fmt", "yuv420p", "-c:a", "copy", str(scaled),
            ]  # fmt: skip
        )
        trim = scaled

    audit = work / "stage1.json"
    write_audit(audit, shots_ms)
    if with_events:
        doc = json.loads(audit.read_text(encoding="utf-8"))
        doc["events"] = _events()
        audit.write_text(json.dumps(doc), encoding="utf-8")

    overlay = work / "overlay.mov"
    degraded: list[str] = []
    overlay_render.render_overlay(
        audit_path=audit,
        trimmed_video_path=trim,
        output_path=overlay,
        beep_offset_seconds=BEEP_OFFSET_SECONDS,
        codec="prores-4444",
        theme=args.theme,
        ffmpeg_binary=ffmpeg,
        variant=args.overlay_variant,
        hud_options=HudOptions(
            speed_colors=args.speed_colors,
            class_labels=not args.no_class_labels,
            landing=not args.no_landing,
            position=args.overlay_position,
            reload_chip=args.reload_chip,
            stage_bar=args.stage_bar,
        ),
        degraded=degraded,
    )
    for note in degraded:
        print(f"FELL BACK: {note}")

    composed = work / "composed.mp4"
    _composite(trim, overlay, composed, ffmpeg=ffmpeg)

    for moment in _moments(shots_ms, events=with_events):
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
