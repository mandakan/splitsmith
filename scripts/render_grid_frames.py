"""Render the compare grid and drop labelled frames at named moments.

The overlay and the stage summary are visual features, and for several
days the only way to look at one was to hand-assemble a throwaway script
that imported private helpers out of an integration test. That is how a
live delta strip got built, reviewed and fixed before anyone saw it and
said it was in the way, and how a summary that rendered on pure black
shipped past three reviews. This is the supported way to look.

Run::

    uv run python scripts/render_grid_frames.py --overlay --summary-hold 2

    # a 3x3 with a ranked field under it, at the shipped canvas size
    uv run python scripts/render_grid_frames.py --shooters 9 --canvas 3840x2160 \\
        --overlay --summary-hold 3

    # the two-shooter head-to-head, which is a different layout entirely
    uv run python scripts/render_grid_frames.py --shooters 2 --overlay

    # tiles from the local real-footage corpus (#686) instead of synthetic
    uv run python scripts/render_grid_frames.py --corpus tests/fixtures/corpus \\
        --shooters 4 --overlay --summary-hold 3

It builds its own media (``tests/synthetic_media.py``) and its own roster
(``tests/compare_fixture.py``) -- no real match, no gitignored
``stage_sample.mp4``, nothing that only exists on one laptop. The roster
is the same one the integration test measures, so a frame here and a
failing assertion there are talking about the same render.

``--corpus`` swaps only the pixels: each clip in the directory is
normalized to the fixture's exact geometry and dealt to roster slots in
sorted filename order, while scoring, audits and shot times stay the
synthetic roster's. That is the shape #686 asks for -- design judgement
calls (dim, blur, ink over busy footage) need real picture, and nothing
else about the fixture may drift when they are made. The corpus is local
and gitignored; see ``tests/fixtures/corpus/README.md`` for what it must
never be used for.

Frames come out at **named** moments rather than at frame indices the
caller has to work out: ``pre-beep``, ``first-shot``, ``mid-action``,
``last-picture``, ``short-tile-ends``, ``last-action``, ``hold-start``,
``hold-mid``, ``hold-end`` and ``next-stage``. Output goes to a stable
directory (``build/grid-frames`` by default) so two runs diff.

**The canvas rate is pinned to a whole number** (30/1 by default) so a
moment is an exact frame index. Frames are selected with
``select=eq(n,N)`` and never by seeking to a timestamp: on a 1/30s grid
the boundaries land exactly on frame edges, and a seek that keeps the
first frame at or after the requested time is then deciding a tie, which
sub-tick rounding anywhere in the seek path breaks in either direction.
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
# lives there because it is a fixture -- this tool is its second consumer,
# not its owner.
sys.path.insert(0, str(REPO_ROOT))

from splitsmith.compare import mp4_grid  # noqa: E402
from splitsmith.compare.layout import choose_grid, grid_shape  # noqa: E402
from tests.compare_fixture import (  # noqa: E402
    HEAD_PAD_SECONDS,
    MAX_SHOOTERS,
    POST_BEEP_SECONDS,
    ROSTER,
    SEGMENT_SECONDS,
    SHORT_FOOTAGE_ENDS,
    SHORT_STAGE_DURATION_SECONDS,
    SHORT_STAGE_FRAMES,
    STAGE_DURATION_SECONDS,
    STAGE_FRAMES,
    TAIL_PAD_SECONDS,
    build_clips,
    build_roster,
    probe_seconds,
)
from tests.synthetic_media import (  # noqa: E402
    SYNTHETIC_FPS_DEN,
    SYNTHETIC_FPS_NUM,
    build_synthetic_video,
    ffmpeg_available,
)

DEFAULT_OUT = REPO_ROOT / "build" / "grid-frames"


@dataclass(frozen=True)
class Moment:
    """One labelled frame: a name, a frame index, and why it is there."""

    name: str
    index: int
    why: str


def _first_shot_seconds(shooters: int) -> float:
    """When the first shot of the stage lands, in seconds after the beep.

    Read off the roster rather than hard-coded, so a change to the shot
    times moves the sample point with it instead of quietly aiming it at
    an instant where nothing happens.
    """
    firsts = [spec.shots_ms[0] / 1000.0 for spec in ROSTER[:shooters] if spec.shots_ms]
    return min(firsts) if firsts else 0.0


def _moments(*, fps: int, hold_seconds: float, stages: int, shooters: int) -> list[tuple[int, Moment]]:
    """Every ``(stage number, moment)`` the render should be sampled at.

    Indices are derived from the segment geometry the fixture pins, not
    measured off the file: a stage is ``head pad + longest post-beep span
    + tail pad`` of action followed by the hold, and every tile's beep
    lands at the head pad.
    """
    action_frames = round(SEGMENT_SECONDS * fps)
    hold_frames = round(hold_seconds * fps)
    segment_frames = action_frames + hold_frames

    def at(seconds: float) -> int:
        return round(seconds * fps)

    per_stage: list[Moment] = [
        Moment(
            "pre-beep",
            at(HEAD_PAD_SECONDS * 0.9),
            "inside the head pad -- nothing has fired, so nothing should be drawn anywhere",
        ),
        Moment(
            "first-shot",
            at(HEAD_PAD_SECONDS + _first_shot_seconds(shooters) + 0.2),
            "just after the stage's first shot: one counter, one split, one running clock",
        ),
        Moment(
            "mid-action",
            at(HEAD_PAD_SECONDS + POST_BEEP_SECONDS / 2),
            "mid-stage, every tile still running",
        ),
        Moment(
            "short-tile-ends",
            at((SHORT_FOOTAGE_ENDS + HEAD_PAD_SECONDS + POST_BEEP_SECONDS) / 2),
            "the short clip has run out while the others still have picture"
            + (
                " -- with a hold, that tile is already showing its own summary"
                if hold_frames > 0
                else " -- with no hold, that tile is black"
            ),
        ),
        # ``- 2``, not ``- 1``. The full clips' nominal footage end is
        # ``HEAD_PAD + POST_BEEP`` (frame 210 at 30fps), but two things
        # cost a frame there and neither is visible from the arithmetic.
        # The decoded stream is a frame shorter than the probed duration
        # implies, so the tile chain's black ``tpad`` starts at 209; and
        # the early summary's arm is emitted at six significant digits
        # (``6.96667`` for a 7.000s end), which is above frame 209's own
        # 6.966666...s, so the summary arms at 210 rather than covering
        # 209. Measured on this fixture at 30fps: live picture through
        # 208, black at 209 on the full tiles, summary from 210. So 208
        # is the frame this moment is named for.
        Moment(
            "last-picture",
            at(HEAD_PAD_SECONDS + POST_BEEP_SECONDS) - 2,
            "the last frame with any live picture in it -- the full tiles go black at the next "
            "one and pick their summary up at the one after that",
        ),
        Moment(
            "last-action",
            action_frames - 1,
            f"the action's final frame -- inside the {TAIL_PAD_SECONDS:g}s tail pad"
            + (
                ", so every tile is already showing its summary"
                if hold_frames > 0
                else ", so black on every tile"
            ),
        ),
    ]
    if hold_frames > 0:
        per_stage += [
            Moment("hold-start", action_frames, "the hold's first frame"),
            Moment(
                "hold-mid",
                action_frames + hold_frames // 2,
                "the middle of the hold: the frozen, blurred, dimmed summary",
            ),
            Moment("hold-end", segment_frames - 1, "the hold's last frame"),
        ]

    out: list[tuple[int, Moment]] = []
    for stage in range(1, stages + 1):
        base = (stage - 1) * segment_frames
        for moment in per_stage:
            out.append((stage, Moment(moment.name, base + moment.index, moment.why)))
        if stage < stages:
            out.append(
                (
                    stage,
                    Moment(
                        "next-stage",
                        base + segment_frames,
                        "the first frame of the following stage, across the concat join",
                    ),
                )
            )
    return out


def _extract(video: Path, index: int, destination: Path, *, ffmpeg: str) -> bool:
    """Write frame ``index`` of ``video`` to ``destination``.

    Returns ``False`` when the index is past the end of the file rather
    than raising: ffmpeg exits 0 and writes nothing in that case, and a
    caller asking for a moment a shorter render does not contain should
    hear about it once, not lose the whole run.
    """
    destination.unlink(missing_ok=True)
    done = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-y", "-v", "error", "-i", str(video),
            "-vf", f"select=eq(n\\,{index})", "-fps_mode", "passthrough",
            "-frames:v", "1", str(destination),
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise SystemExit(f"ffmpeg failed extracting frame {index}:\n{done.stderr[-2000:]}")
    return destination.exists() and destination.stat().st_size > 0


def _corpus_clips(
    corpus: Path, root: Path, *, count: int, ffmpeg: str, ffprobe: str
) -> dict[str, tuple[Path, float]]:
    """Per-label clips cut from the real-footage corpus, in fixture geometry.

    Corpus clips arrive at whatever rate and length they were cut at
    (#686 says 10-15s of real match video), so each is normalized here to
    exactly what the synthetic clip it replaces would have been: the
    fixture's frame rate and an exact frame count -- ``STAGE_FRAMES`` for
    a full-clip shooter, ``SHORT_STAGE_FRAMES`` for a short-clip one. The
    probed duration is asserted the same way ``build_clips`` asserts the
    synthetic clips, because the freeze-seek slack it guards against does
    not care where the pixels came from.

    Files are dealt to roster slots in sorted filename order, cycling
    when the roster is longer than the corpus, so a 4-clip corpus still
    fills a 3x3 and every background appears at least once.
    """
    if not corpus.is_dir():
        raise SystemExit(f"--corpus {corpus} is not a directory")
    sources = sorted(corpus.glob("*.mp4"))
    if not sources:
        raise SystemExit(
            f"--corpus {corpus} holds no .mp4 files. See tests/fixtures/corpus/README.md "
            "for the slots to cut, or drop the flag for synthetic media."
        )
    frame_seconds = SYNTHETIC_FPS_DEN / SYNTHETIC_FPS_NUM
    clips: dict[str, tuple[Path, float]] = {}
    for index, spec in enumerate(ROSTER[:count]):
        source = sources[index % len(sources)]
        frames, nominal = (
            (SHORT_STAGE_FRAMES, SHORT_STAGE_DURATION_SECONDS)
            if spec.clip == "short"
            else (STAGE_FRAMES, STAGE_DURATION_SECONDS)
        )
        destination = root / f"{spec.label}-{source.stem}.mp4"
        destination.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                "-vf", f"fps={SYNTHETIC_FPS_NUM}/{SYNTHETIC_FPS_DEN}",
                "-frames:v", str(frames),
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
                "-c:a", "aac", "-b:a", "128k", "-shortest", str(destination),
            ],  # fmt: skip
            capture_output=True,
            text=True,
        )
        if done.returncode != 0:
            raise SystemExit(f"ffmpeg failed normalizing {source.name}:\n{done.stderr[-2000:]}")
        probed = probe_seconds(destination, ffprobe=ffprobe)
        if abs(probed - nominal) > frame_seconds:
            raise SystemExit(
                f"{source.name} normalized to {probed:.4f}s where the {spec.clip} clip needs "
                f"{nominal:.4f}s -- the source is shorter than the fixture's stage. Corpus clips "
                "must run at least 10s (see tests/fixtures/corpus/README.md)."
            )
        clips[spec.label] = (destination, probed)
        print(f"  tile {spec.label:8} <- {source.name}")
    return clips


def _parse_canvas(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x", 1))
    except ValueError:
        raise argparse.ArgumentTypeError(f"canvas must look like 1280x720, got {value!r}") from None
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError(f"canvas dimensions must be positive, got {value!r}")
    return width, height


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--shooters",
        type=int,
        default=3,
        help=f"how many of the roster to render, 1..{MAX_SHOOTERS} (default 3, a 2x2 with one "
        "cell unreached). 2 gives the head-to-head layout, 5-9 a 3x3.",
    )
    parser.add_argument("--stages", type=int, default=2, help="stages to render (default 2)")
    parser.add_argument(
        "--canvas",
        type=_parse_canvas,
        default=(1280, 720),
        help="output size, WIDTHxHEIGHT (default 1280x720). The shipped default is 3840x2160; "
        "type sizes are driven by the cell, so legibility has to be judged at the real size.",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="canvas frame rate, a whole number so moments land on exact frames (default 30)",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="directory of real-footage clips to source tiles from instead of synthetic media "
        "(#686). Local-only; see tests/fixtures/corpus/README.md.",
    )
    parser.add_argument("--overlay", action="store_true", help="burn the live overlay in")
    parser.add_argument(
        "--overlay-theme", default="splitsmith", choices=("splitsmith", "clean"), help="overlay theme"
    )
    parser.add_argument(
        "--summary-hold",
        type=float,
        default=0.0,
        help="seconds to hold the frozen stage summary at the end of each stage (needs --overlay)",
    )
    parser.add_argument(
        "--audio-from",
        default=None,
        help="which shooter's audio is unmuted (default: the last one rendered)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"output directory, wiped on each run so successive runs diff (default {DEFAULT_OUT})",
    )
    parser.add_argument("--keep-video", action="store_true", help="keep the rendered MP4 beside the frames")
    args = parser.parse_args(argv)

    if not 1 <= args.shooters <= MAX_SHOOTERS:
        parser.error(f"--shooters must be 1..{MAX_SHOOTERS}")
    if args.stages < 1:
        parser.error("--stages must be at least 1")
    if args.summary_hold > 0 and not args.overlay:
        parser.error("--summary-hold needs --overlay: the hold draws the overlay's own data")
    if not ffmpeg_available():
        parser.error("ffmpeg and ffprobe must be on PATH")

    # ``mp4_grid._cell_size`` floors, so a canvas that does not divide by
    # the grid composes narrower or shorter than the canvas -- 1280x720 at
    # 3x3 is 3 x 426 = 1278 wide. The live overlay survives that (it is
    # composited with ``overlay``, which just draws at 0,0), but the
    # summary hold does not: the still is canvas-sized and goes into the
    # video stream through ``concat``, which refuses mismatched
    # dimensions and fails the whole stage with a wall of filter-graph
    # errors. The shipped 3840x2160 divides by 1, 2 and 3 so it never
    # bites in production, and no CLI flag exposes the canvas today.
    # Caught here rather than left to ffmpeg, because the message it
    # gives is unreadable.
    rows, cols = grid_shape(choose_grid(args.shooters))
    width, height = args.canvas
    if args.summary_hold > 0 and (width % cols or height % rows):
        parser.error(
            f"a {args.shooters}-shooter render is a {rows}x{cols} grid, and {width}x{height} does "
            f"not divide by it ({cols} x {width // cols} = {cols * (width // cols)} wide, "
            f"{rows} x {height // rows} = {rows * (height // rows)} tall). The summary hold "
            f"concatenates a canvas-sized still into the video stream and concat refuses a size "
            f"mismatch. Use a canvas divisible by {cols}x{rows} (e.g. "
            f"{width - width % cols}x{height - height % rows}), or drop --summary-hold."
        )

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg is not None and ffprobe is not None  # ffmpeg_available() just said so

    out = args.out
    # Wiped rather than merged: a stale frame from a previous run under a
    # name this run did not write is indistinguishable from a fresh one,
    # and that is exactly the mistake a diff-two-runs workflow makes.
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    work = out / "work"
    if args.corpus is not None:
        # Every rendered shooter gets a corpus tile, so the synthetic
        # source is never built and ``clips`` is never consulted.
        overrides = _corpus_clips(
            args.corpus, work / "clips", count=args.shooters, ffmpeg=ffmpeg, ffprobe=ffprobe
        )
        clips = {}
    else:
        source = build_synthetic_video(work / "source.mp4")
        clips = build_clips(source, work / "clips", ffmpeg=ffmpeg, ffprobe=ffprobe)
        overrides = None
    shooters = build_roster(
        work / "projects", clips, count=args.shooters, stages=args.stages, clip_overrides=overrides
    )
    audio_label = args.audio_from or shooters[-1].label
    if audio_label not in {bundle.label for bundle in shooters}:
        parser.error(
            f"--audio-from {audio_label!r} is not in this roster: "
            f"{', '.join(bundle.label for bundle in shooters)}"
        )

    video = out / "grid.mp4"
    print(
        f"rendering {args.shooters} shooter(s) x {args.stages} stage(s) at {width}x{height}@"
        f"{args.fps}, overlay={args.overlay}, hold={args.summary_hold:g}s ..."
    )
    result = mp4_grid.render_grid_mp4(
        shooters,
        audio_label=audio_label,
        output_path=video,
        canvas=mp4_grid.GridCanvas(width=width, height=height, frame_rate_num=args.fps, frame_rate_den=1),
        head_pad_seconds=HEAD_PAD_SECONDS,
        tail_pad_seconds=TAIL_PAD_SECONDS,
        overlay=args.overlay,
        overlay_theme=args.overlay_theme,
        summary_hold_seconds=args.summary_hold,
        ffmpeg_binary=ffmpeg,
        work_dir=work / "render",
        on_notice=lambda text: print(f"  notice: {text}"),
    )
    if result.failed:
        print(f"  {len(result.failed)} stage(s) failed: {result.failed}", file=sys.stderr)

    moments = _moments(
        fps=args.fps,
        hold_seconds=args.summary_hold,
        stages=args.stages,
        shooters=args.shooters,
    )
    written = 0
    for stage, moment in moments:
        destination = out / f"stage{stage}-{moment.name}.png"
        if _extract(video, moment.index, destination, ffmpeg=ffmpeg):
            written += 1
            print(f"  {destination.name:34} frame {moment.index:5}  {moment.why}")
        else:
            print(f"  {destination.name:34} frame {moment.index:5}  SKIPPED (past the end)")

    # The composed stills are what the hold is supposed to be showing.
    # Copying them out means a hold frame that looks wrong can be compared
    # against what the renderer thought it was drawing, without digging
    # through the work directory.
    for still in sorted((work / "render").glob("summary-stage*.png")):
        shutil.copy2(still, out / f"composed-{still.name}")
        written += 1

    if not args.keep_video:
        video.unlink(missing_ok=True)
    shutil.rmtree(work, ignore_errors=True)

    print(f"\n{written} image(s) in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
