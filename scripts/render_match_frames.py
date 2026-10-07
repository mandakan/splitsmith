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
import dataclasses
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
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
    moves the frames with it. A boundary (a transition, #1244) gets three:
    two frames in, the middle, two frames before its end."""
    starts: dict[str, float] = {}
    shown: dict[str, float] = {}
    boundaries: list[tuple[float, float]] = []
    t = 0.0
    for index, item in enumerate(plan.items):
        key = (
            item.kind
            if item.kind in ("title_page", "closing")
            else f"{item.kind}_{len([k for k in starts if k.startswith(item.kind)])}"
        )
        starts[key] = t
        shown[key] = item.duration_seconds  # the item's length on the spine, cuts applied
        t += item.duration_seconds
        boundary = plan.boundary_after(index)
        if boundary is not None:
            boundaries.append((t, boundary.duration_seconds))
            t += boundary.duration_seconds
    moments = [
        Moment(
            "title-page-in",
            starts["title_page"] + 0.25,
            "title page a quarter second in (the rise is mid-way with --card-variant rise)",
        ),
        Moment(
            "title-page",
            starts["title_page"] + TITLE_PAGE_SECONDS / 2,
            "match name over stage 1's blurred first frame",
        ),
    ]
    if titles == "slate":
        moments.append(
            Moment("card-1", starts["slate_0"] + shown["slate_0"] / 2, "stage 1 slate with its round count")
        )
        moments.append(
            Moment("card-2", starts["slate_1"] + shown["slate_1"] / 2, "stage 2 slate, no round count")
        )
    stage_1 = starts["stage_0"]
    if "summary_0" in starts:
        moments.append(
            Moment("summary-1", starts["summary_0"] + 0.5, "stage 1's summary held after its action")
        )
    moments.append(Moment("stage-1-head", stage_1 + 0.5, "lower-third fully up (with --titles lower-third)"))
    moments.append(
        Moment("stage-1-mid", stage_1 + BEEP_OFFSET_SECONDS + 2.0, "action; a lower-third has faded out")
    )
    moments.append(
        Moment("closing", starts["closing"] + CLOSING_SECONDS / 2, "closing card over stage 2's last frame")
    )
    two_frames = 2 / FPS
    for n, (start, seconds) in enumerate(boundaries, start=1):
        moments.append(Moment(f"boundary-{n}-in", start + two_frames, "the crossfade has just begun"))
        moments.append(Moment(f"boundary-{n}-mid", start + seconds / 2, "halfway through the crossfade"))
        moments.append(
            Moment(f"boundary-{n}-out", start + seconds - two_frames, "the crossfade about to end")
        )
    return tuple(moments)


def demo_logo(path: Path) -> Path:
    """A club logo for the identity demo: a filled disc with two letters,
    the kind of thing a shooter would upload."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 248, 248), fill=(20, 23, 28, 255), outline=(255, 45, 45, 255), width=12)
    draw.text((128, 128), "PK", fill=(244, 244, 245, 255), anchor="mm", font_size=120)
    image.save(path)
    return path


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


def _stage(trim: Path, audit: Path, *, name: str, head_pad: float = BEEP_OFFSET_SECONDS) -> StageComposition:
    shots = audit_shots_to_engine_shots(read_audit_data(audit), beep_time_in_source=0.0)
    return StageComposition(
        stage_name=name,
        video_path=trim,
        video=probe_video(trim),
        shots=shots,
        beep_offset_seconds=BEEP_OFFSET_SECONDS,
        head_pad_seconds=head_pad,
        tail_pad_seconds=1.0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--theme", default="splitsmith", help="an installed Look name")
    parser.add_argument("--titles", choices=("slate", "lower-third"), default="slate")
    parser.add_argument("--card-variant", default="default", help="Look template variant for every card")
    parser.add_argument(
        "--transition",
        default="none",
        help="a transition between the two stages: an xfade kind (fade, dissolve, ...), zoom / static, "
        "or a Look sting (sting:wipe)",
    )
    parser.add_argument(
        "--transition-seconds", type=float, default=1.0, help="its length, centred on the cut"
    )
    parser.add_argument(
        "--identity-demo",
        action="store_true",
        help="give the shooter an identity (the Look's first accent, a generated club logo, a club line)",
    )
    parser.add_argument("--keep-video", action="store_true")
    parser.add_argument(
        "--summary-hold",
        type=float,
        default=0.0,
        help="seconds to hold each stage's summary after its action (#972); 0 is off",
    )
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
        # A transition needs handle footage before the head pad; the
        # synthetic beep sits 1 s in, so the demo keeps half of it as pad
        # and the other half as handle. The default geometry is untouched.
        head_pad = BEEP_OFFSET_SECONDS / 2 if args.transition != "none" else BEEP_OFFSET_SECONDS
        stages.append(_stage(trim, audit, name=f"Stage {number}", head_pad=head_pad))

    variant = args.card_variant
    titles = {
        0: composition.TitleCard(
            text="Stage 1: Speed",
            duration_seconds=CARD_SECONDS,
            style=args.titles,
            info=("24 rounds",),
            variant=variant,
        ),
        1: composition.TitleCard(
            text="Stage 2: Accuracy", duration_seconds=CARD_SECONDS, style=args.titles, variant=variant
        ),
    }
    # The shooter's identity goes through the production resolver in both
    # cases, so the default frames are the frames an export produces for a
    # shooter who set nothing (the pixel gate against main runs this path).
    from splitsmith.identity import ShooterIdentity, resolve_identity
    from splitsmith.looks import load_look

    resolved = resolve_identity(
        label="M. Axell",
        identity=ShooterIdentity(club="Bromma PK") if args.identity_demo else ShooterIdentity(),
        index=0,
        look=load_look(args.theme),
        shooter_root=None,
        series_default=args.identity_demo,
    )
    if args.identity_demo:
        # The demo shooter has a logo of their own (nothing falls back).
        resolved = replace(resolved, logo_path=demo_logo(work / "logo.png"))
    shooters = (
        composition.CompositionShooter(
            label=resolved.label, accent=resolved.accent, logo_path=resolved.logo_path, club=resolved.club
        ),
    )
    transitions = (
        (
            composition.Transition(
                from_stage_index=0,
                to_stage_index=1,
                kind=args.transition,
                duration_seconds=args.transition_seconds,
            ),
        )
        if args.transition != "none"
        else ()
    )
    comp = composition.from_stage_compositions(
        stages,
        project_name="Bromma Classifier",
        titles=titles,
        shooters=shooters,
        transitions=transitions,
        title_page=composition.MatchTitle(
            text="Bromma Classifier",
            info=(
                "2026-05-01",
                "M. Axell",
                *(("Bromma PK",) if args.identity_demo else ()),
                "Production Optics",
            ),
            duration_seconds=TITLE_PAGE_SECONDS,
            variant=variant,
        ),
        closing=composition.MatchTitle(
            text="Bromma Classifier", info=("2026-05-01",), duration_seconds=CLOSING_SECONDS, variant=variant
        ),
    )
    if args.summary_hold > 0:
        from splitsmith.match_project import StageScorecard
        from splitsmith.stage_summary_data import TileStageData, load_stage_shots

        summaries = {}
        for index in range(len(stages)):
            summaries[index] = composition.SummaryHold(
                data=TileStageData(
                    label="M. Axell",
                    stage_number=index + 1,
                    shots=load_stage_shots(work / f"stage{index + 1}.json"),
                    stage_time_seconds=4.5,
                    scorecard=StageScorecard(hit_factor=12.0, alphas=6, charlies=1, deltas=1, misses=0),
                ),
                label="M. Axell",
                duration_seconds=args.summary_hold,
            )
        comp = dataclasses.replace(
            comp,
            stages=tuple(dataclasses.replace(s, summary=summaries[i]) for i, s in enumerate(comp.stages)),
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
