"""Render a single-shooter match MP4 with generated cards and drop labelled
frames at named moments (issue #973).

The sibling of ``scripts/render_overlay_frames.py`` (the per-stage alpha
overlay) and ``scripts/render_grid_frames.py`` (the compare grid). The
cards are a visual feature; this is the supported way to look at one
before and after a change.

Run::

    uv run python scripts/render_match_frames.py

    # lower-thirds instead of slates
    uv run python scripts/render_match_frames.py --titles lower-third

    # against a different output directory, to diff two revisions
    uv run python scripts/render_match_frames.py --out build/match-frames-main

It builds its own media (``tests/synthetic_media.py``) and its own audits
(``tests/compare_fixture.write_audit``): two short stages from the
synthetic clip, a title page, a card per stage and a closing card. It
needs ffmpeg on PATH and a Chromium the overlay rasterizer can launch;
without the browser the render still succeeds, the cards are skipped and
the degradation is printed -- which is itself worth seeing once.

Frames come out at **named** moments: ``title-page``, ``card-1``,
``stage-1-head``, ``stage-1-mid``, ``card-2``, ``closing``. Moments are
converted to frame indices once, in Python, from the same timeline plan
the renderer walks, and extracted with ``select=eq(n,N)`` -- never by
seeking to a timestamp.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from splitsmith import composition, mp4_render  # noqa: E402
from splitsmith.audit_data import audit_shots_to_engine_shots, read_audit_data  # noqa: E402
from splitsmith.fcpxml_gen import StageComposition, probe_video  # noqa: E402
from tests.compare_fixture import cut_clip, write_audit  # noqa: E402
from tests.synthetic_media import (  # noqa: E402
    SYNTHETIC_FPS_DEN,
    SYNTHETIC_FPS_NUM,
    build_synthetic_video,
    ffmpeg_available,
)

DEFAULT_OUT = REPO_ROOT / "build" / "match-frames"
FPS = SYNTHETIC_FPS_NUM / SYNTHETIC_FPS_DEN
CLIP_FRAMES = 240
BEEP_OFFSET_SECONDS = 1.0
SHOTS_MS = (1100, 1320, 1560, 1740, 1980, 2310, 2530, 2790)
TITLE_PAGE_SECONDS = 3.0
CARD_SECONDS = 1.5
CLOSING_SECONDS = 2.0


@dataclass(frozen=True)
class Moment:
    name: str
    seconds: float
    why: str


def _moments(plan: mp4_render.TimelinePlan, *, titles: str) -> tuple[Moment, ...]:
    """Named moments from the timeline plan, so a change to a duration
    moves the frames with it."""
    starts: dict[str, float] = {}
    t = 0.0
    for item in plan.items:
        key = (
            item.kind
            if item.kind in ("title_page", "closing")
            else f"{item.kind}_{len([k for k in starts if k.startswith(item.kind)])}"
        )
        starts[key] = t
        t += item.duration_seconds
    moments = [
        Moment(
            "title-page",
            starts["title_page"] + TITLE_PAGE_SECONDS / 2,
            "match name over stage 1's blurred first frame",
        )
    ]
    if titles == "slate":
        moments.append(
            Moment("card-1", starts["slate_0"] + CARD_SECONDS / 2, "stage 1 slate with its round count")
        )
        moments.append(
            Moment("card-2", starts["slate_1"] + CARD_SECONDS / 2, "stage 2 slate, no round count")
        )
    stage_1 = starts["stage_0"]
    moments.append(Moment("stage-1-head", stage_1 + 0.5, "lower-third fully up (with --titles lower-third)"))
    moments.append(
        Moment("stage-1-mid", stage_1 + BEEP_OFFSET_SECONDS + 2.0, "action; a lower-third has faded out")
    )
    moments.append(
        Moment("closing", starts["closing"] + CLOSING_SECONDS / 2, "closing card over stage 2's last frame")
    )
    return tuple(moments)


def _run(cmd: list[str]) -> None:
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd[:3])}...\n{done.stderr[-2000:]}")


def _extract(video: Path, index: int, destination: Path, *, ffmpeg: str) -> bool:
    destination.unlink(missing_ok=True)
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            f"select=eq(n\\,{index})",
            "-fps_mode",
            "passthrough",
            "-frames:v",
            "1",
            str(destination),
        ]  # fmt: skip
    )
    return destination.exists() and destination.stat().st_size > 0


def _stage(trim: Path, audit: Path, *, name: str) -> StageComposition:
    shots = audit_shots_to_engine_shots(read_audit_data(audit), beep_time_in_source=0.0)
    return StageComposition(
        stage_name=name,
        video_path=trim,
        video=probe_video(trim),
        shots=shots,
        beep_offset_seconds=BEEP_OFFSET_SECONDS,
        head_pad_seconds=BEEP_OFFSET_SECONDS,
        tail_pad_seconds=1.0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--theme", choices=("splitsmith", "clean"), default="splitsmith")
    parser.add_argument("--titles", choices=("slate", "lower-third"), default="slate")
    parser.add_argument("--keep-video", action="store_true")
    args = parser.parse_args()

    if not ffmpeg_available():
        parser.error("ffmpeg and ffprobe must be on PATH")
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None

    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = out / "work"
    work.mkdir()

    source = work / "source.mp4"
    build_synthetic_video(source)
    stages: list[StageComposition] = []
    for number in (1, 2):
        trim = work / f"stage{number}.mp4"
        cut_clip(source, trim, CLIP_FRAMES, ffmpeg=ffmpeg)
        audit = work / f"stage{number}.json"
        write_audit(audit, SHOTS_MS)
        stages.append(_stage(trim, audit, name=f"Stage {number}"))

    titles = {
        0: composition.TitleCard(
            text="Stage 1: Speed", duration_seconds=CARD_SECONDS, style=args.titles, info=("24 rounds",)
        ),
        1: composition.TitleCard(text="Stage 2: Accuracy", duration_seconds=CARD_SECONDS, style=args.titles),
    }
    comp = composition.from_stage_compositions(
        stages,
        project_name="Bromma Classifier",
        titles=titles,
        title_page=composition.MatchTitle(
            text="Bromma Classifier",
            info=("2026-05-01", "M. Axell", "Production Optics"),
            duration_seconds=TITLE_PAGE_SECONDS,
        ),
        closing=composition.MatchTitle(
            text="Bromma Classifier", info=("2026-05-01",), duration_seconds=CLOSING_SECONDS
        ),
    )
    plan = mp4_render.plan_timeline(comp)
    rendered = work / "match.mp4"
    result = mp4_render.render_mp4(
        comp, output_path=rendered, work_dir=work / "render", ffmpeg_binary=ffmpeg, overlay_theme=args.theme
    )
    for note in result.degradations:
        print(f"DEGRADED: {note}")
    print(f"planned {plan.duration_seconds:.2f}s, wrote {result.duration_seconds:.2f}s")

    for moment in _moments(plan, titles=args.titles):
        index = round(moment.seconds * FPS)
        target = out / f"{moment.name}.png"
        if _extract(rendered, index, target, ffmpeg=ffmpeg):
            print(f"{moment.name:14s} frame {index:4d}  {moment.why}")
        else:
            print(f"{moment.name:14s} frame {index:4d}  SKIPPED (past end of render)")

    if not args.keep_video:
        shutil.rmtree(work)
    print(f"\nframes in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
