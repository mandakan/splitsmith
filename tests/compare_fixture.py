"""The compare-grid example roster: real media, real geometry, real scoring.

One roster, two consumers. ``test_compare_grid_overlay_integration.py``
renders it and measures the pixels; ``scripts/render_grid_frames.py``
renders it and drops labelled frames for a design pass. They share this
module so a fixture that is good enough to catch a defect is also the
thing the user looks at, and neither can drift from the other.

**Why this module exists at all** (issue #682). Two defects reached a
release through a fixture that could not express them:

- The stage summary's freeze seek landed past the end of every trim, so
  the whole summary rendered as text on pure black. The fixture declared
  a 9-second stage against 24 seconds of media, and those 15 seconds of
  slack were the only thing keeping the seek inside a clip.
- The summary's cross-shooter placing had never appeared in a rendered
  frame, because no shooter in the fixture had a ``project.json`` at all.
  ``TileStageData.scorecard`` was ``None`` for everyone and the summary
  silently omitted hit factor, stage percentage, hit counts and placing.

Both are metadata defects, not pixel defects. Real match footage would
have caught neither -- which matters, because reaching for real footage
is the intuitive fix and it is the wrong one. What is here instead is
media with *known* geometry and scoring with *known* ordering, which is
what the assertions are actually about, and it runs in CI on every push.

**Geometry.** Every clip is cut to an exact frame count and the bundle is
handed that clip's *probed* duration, with the two asserted against each
other in :func:`build_clips`. In production the declared length is an
ffprobe of the trim (``compare/project_loader.py``), so it is exact and
anything reading "the end of this clip" has no slack to overrun into.

**Scoring.** Every scorecard here is internally consistent -- points are
the hits at that division's scoring, hit factor is points over time, and
stage percentage is that hit factor against the division's stage-winning
one. :func:`_card` derives all three from the hits so a hand-typed
inconsistency is not possible. The states the roster covers are listed on
:data:`ROSTER`.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle
from splitsmith.config import StageRounds
from splitsmith.ui.project import MatchProject, StageEntry, StageScorecard
from tests.synthetic_media import SYNTHETIC_FPS_DEN, SYNTHETIC_FPS_NUM

# --- clip lengths: declared == what the media actually is ---------------
#
# The frame counts are chosen to keep the segment arithmetic in whole
# 1/30s canvas frames: 270 source frames at 30000/1001 is 9.009s, and a
# beep 3.009s in leaves a post-beep span of exactly 6.0s.
STAGE_FRAMES = 270
STAGE_DURATION_SECONDS = STAGE_FRAMES * SYNTHETIC_FPS_DEN / SYNTHETIC_FPS_NUM  # 9.009

#: The short clip, deliberately shorter than the full one.
#:
#: A stage runs until the *longest* tile's post-beep span is done, so a
#: shooter on this clip runs out ~1.5s before the action does. That gap
#: is what separates "freeze on this tile's own last frame" from "freeze
#: on the action's last frame": the second reads black in that cell, and
#: with equal-length clips no assertion can tell them apart. It is the
#: condition that hid the blocker.
#:
#: What fills the gap depends on the render. Without a summary hold the
#: cell is the tile chain's black ``tpad``, as it always was. With one,
#: the per-tile early summary paints that shooter's own summary cell
#: over it from their footage end -- so a hold render has picture there,
#: and any assertion reading black in that window is reading a render
#: with no hold.
SHORT_STAGE_FRAMES = 225
SHORT_STAGE_DURATION_SECONDS = SHORT_STAGE_FRAMES * SYNTHETIC_FPS_DEN / SYNTHETIC_FPS_NUM  # 7.5075

#: 3.009s into the clip, leaving a post-beep span of exactly 6.0s.
BEEP_OFFSET_SECONDS = STAGE_DURATION_SECONDS - 6.0

HEAD_PAD_SECONDS = 1.0
TAIL_PAD_SECONDS = 0.5

#: Longest post-beep span across the roster, in seconds. Every tile's beep
#: lands at the segment's head pad, so a stage's action is this plus both
#: pads regardless of which shooters are in it.
POST_BEEP_SECONDS = STAGE_DURATION_SECONDS - BEEP_OFFSET_SECONDS  # 6.0
SEGMENT_SECONDS = HEAD_PAD_SECONDS + POST_BEEP_SECONDS + TAIL_PAD_SECONDS  # 7.5

#: When the short clip's footage stops, in segment time.
SHORT_FOOTAGE_ENDS = HEAD_PAD_SECONDS + (SHORT_STAGE_DURATION_SECONDS - BEEP_OFFSET_SECONDS)

# Shots as milliseconds after the beep. Anders (row0,col0) fires at 0.5s
# and 3.0s; Mathias (row1,col0) at 1.2s and 2.0s. A sample at absolute
# 2.0s therefore sits after Anders' first shot and before Mathias's --
# one shooter with overlay content and no other tile changing at the same
# instant.
ANDERS_SHOTS_MS = (500, 3000)
MATHIAS_SHOTS_MS = (1200, 2000)
#: Every shooter past the first three fires the same pair, offset so no
#: two tiles change state on the same frame.
EXTRA_SHOTS_MS = (700, 2600)

#: Stage names, taken from ``examples/blacksmith-handgun-open-2026.json``
#: rather than invented -- that file is the shape of the scoring data this
#: fixture stands in for.
STAGE_NAMES = ("K-vallen", "100m", "H1", "H2", "H3", "H4", "H5", "H6")

MATCH_NAME = "Blacksmith Handgun Open 2026"

#: Round counts per stage, in stage order. ``expected`` is what makes the
#: live counter render ``2/8`` rather than a bare ``2``; its absence once
#: caused a reviewer to misread the layout. Paper targets carry two rounds
#: each, so the counts agree with the hit totals on the cards below.
STAGE_ROUNDS = (
    StageRounds(expected=8, paper_targets=4, steel_targets=0),
    StageRounds(expected=12, paper_targets=6, steel_targets=0),
)

Scoring = Literal["minor", "major"]

#: Points per hit zone, by scoring. Production Optics is minor; Open is
#: major. This is why two shooters can post the same stage percentage on
#: very different raw points -- see :data:`ROSTER`.
_HIT_VALUES: dict[Scoring, dict[str, int]] = {
    "minor": {"alpha": 5, "charlie": 3, "delta": 1},
    "major": {"alpha": 5, "charlie": 4, "delta": 2},
}
_PENALTY = 10


def _card(
    *,
    scoring: Scoring,
    stage_number: int,
    time_seconds: float,
    alphas: int | None,
    charlies: int | None,
    deltas: int | None,
    misses: int | None = 0,
    no_shoots: int | None = 0,
    procedurals: int | None = 0,
    dq: bool = False,
) -> StageScorecard:
    """A scorecard whose points, hit factor and percentage agree.

    ``stage_points`` is the raw points the hits are worth at ``scoring``
    -- the same quantity ``Scorecard.tsx``'s ``matchTotals`` divides by
    time to get a hit factor -- so it is a *division-dependent* number.
    ``stage_pct`` is this shooter's hit factor against the winning hit
    factor **in their own division**, which is the only number comparable
    across the grid.

    That distinction is the point of the fixture. Within one division the
    two move together, which is exactly why a summary that ranked by
    ``stage_points`` would look correct on any real match data and needs a
    deliberately mixed-division roster to catch. Nothing here is fudged to
    manufacture the divergence: minor and major simply pay differently for
    the same hits.

    A ``None`` hit count means "the scoreboard did not carry this field",
    and it is worth nothing rather than being counted as zero -- the
    summary must render it as absent, never as ``0``.
    """
    values = _HIT_VALUES[scoring]
    points = (
        (alphas or 0) * values["alpha"]
        + (charlies or 0) * values["charlie"]
        + (deltas or 0) * values["delta"]
        - ((misses or 0) + (no_shoots or 0) + (procedurals or 0)) * _PENALTY
    )
    hit_factor = max(0.0, points) / time_seconds
    winner = _WINNER_HIT_FACTOR[((stage_number - 1) % len(STAGE_ROUNDS) + 1, scoring)]
    if hit_factor > winner:
        raise ValueError(
            f"stage {stage_number}: a {scoring} hit factor of {hit_factor:.3f} beats that "
            f"division's stage winner ({winner:.3f}), which would render as more than 100%. "
            "Nobody outscores the winner -- 100.0% is the winner."
        )
    return StageScorecard(
        hit_factor=hit_factor,
        stage_points=float(points),
        stage_pct=hit_factor / winner * 100.0,
        alphas=alphas,
        charlies=charlies,
        deltas=deltas,
        misses=misses,
        no_shoots=no_shoots,
        procedurals=procedurals,
        dq=dq,
    )


#: The stage-winning hit factor, per division and per stage. A percentage
#: is a hit factor against one of these, so they are held here rather than
#: inside the cards: every shooter on a stage has to be measured against
#: the same winner or the percentages do not mean anything together.
#:
#: No shooter here beats their division's winner, because nobody can --
#: 100.0% *is* the winner. A fixture that hands out 127% would be
#: describing a match that cannot occur.
_WINNER_HIT_FACTOR: dict[tuple[int, Scoring], float] = {
    (1, "minor"): 8.0,
    (1, "major"): 10.0,
    (2, "minor"): 12.0,
    (2, "major"): 15.5,
}


@dataclass(frozen=True)
class StageScoring:
    """One shooter's ``StageEntry`` payload for one stage.

    ``time_seconds`` of ``0.0`` is the model's "unset" -- an untouched
    placeholder stage carries it and a zero-second stage time is never
    real -- so a shooter with neither a scorecard nor a stage time renders
    as their label alone.
    """

    time_seconds: float = 0.0
    time_seconds_manual: bool = False
    scorecard: StageScorecard | None = None


@dataclass(frozen=True)
class ShooterSpec:
    """One shooter across every stage.

    ``shots_ms`` of ``None`` is the no-audit shooter: the audit path names
    a file that is never written, so ``audit_data.read_audit_data`` hits
    its missing-file branch and the overlay degrades to no shots for that
    tile -- on the rendered path, not just in a unit test.
    """

    label: str
    clip: Literal["full", "short"]
    shots_ms: tuple[int, ...] | None
    scoring: tuple[StageScoring, ...]

    def stage_scoring(self, stage_number: int) -> StageScoring:
        """This shooter's scoring for ``stage_number``.

        Stages past the last defined one cycle back through the list, so a
        caller can render any number of stages without the roster growing
        a row per stage. Stages 1 and 2 are the ones that carry the states
        listed on :data:`ROSTER`.
        """
        return self.scoring[(stage_number - 1) % len(self.scoring)]


#: ``(alphas, charlies, deltas, misses, procedurals, time)`` per filler
#: shooter, per stage. Hit-zone rounds (alphas+charlies+deltas+misses)
#: total the stage's round count (12 on stage 1, 8 on stage 2, counting a
#: miss as a round fired) and every time sits inside the post-beep span
#: the trims give the stage, so nothing here describes a run the media
#: could not hold. Procedurals are a rule-violation penalty, not a round
#: fired at a target, so they sit outside that budget -- Sanna's stage-2
#: procedural below is the one nonzero value in that column, and it costs
#: her nothing in round count.
_FILLER_SCORES: dict[int, tuple[tuple[int, int, int, int, int, float], ...]] = {
    1: (
        (5, 2, 1, 0, 0, 4.20),  # Nils
        (4, 3, 1, 0, 0, 4.50),  # Olof
        (3, 4, 1, 0, 0, 4.40),  # Petra
        (7, 1, 0, 0, 0, 4.75),  # Rikard -- winner
        (2, 4, 1, 1, 0, 4.30),  # Sanna
        (6, 2, 0, 0, 0, 4.50),  # Tove -- ties Rikard
    ),
    2: (
        (7, 4, 1, 0, 0, 4.20),  # Nils
        (6, 5, 1, 0, 0, 4.50),  # Olof
        (5, 5, 2, 0, 0, 4.40),  # Petra
        (11, 1, 0, 0, 0, 4.90),  # Rikard
        # Sanna carries the roster's one nonzero procedural. She is a
        # filler shooter that nothing else depends on -- unlike Anders
        # (the tie), Bea (the no-penalty-columns case) or Mathias (the
        # points-vs-percentage divergence), none of whom can absorb a
        # penalty without disturbing a load-bearing property. A
        # procedural costs no round, so her hit-zone total stays at 12
        # even though she already carries misses=2.
        (4, 4, 2, 2, 1, 4.30),  # Sanna
        (9, 3, 0, 0, 0, 4.50),  # Tove -- lands on the winning hit factor
    ),
}


def _filler(index: int, stage_number: int) -> StageScoring:
    """A scored, un-DQ'd, audited stage for one of the filler shooters.

    Only the first three shooters carry the states the assertions are
    about; everyone past them exists so a larger render has a ranked field
    under it. Their times fan out so their placings do, with one landing
    on the winning hit factor exactly so a tie stays visible at 3x3 too.
    All of them shoot Production Optics, so nothing about the
    points-versus-percentage divergence depends on them.
    """
    alphas, charlies, deltas, misses, procedurals, time_seconds = _FILLER_SCORES[
        (stage_number - 1) % len(_FILLER_SCORES) + 1
    ][index % 6]
    return StageScoring(
        time_seconds=time_seconds,
        scorecard=_card(
            scoring="minor",
            stage_number=stage_number,
            time_seconds=time_seconds,
            alphas=alphas,
            charlies=charlies,
            deltas=deltas,
            misses=misses,
            procedurals=procedurals,
        ),
    )


#: The roster, in alphabetical order -- which is the order the compare
#: grid assigns slots in, so ``ROSTER[:n]`` is exactly what an ``n``-tile
#: grid shows, left to right and top to bottom.
#:
#: The first three carry every state the summary has to handle, split
#: across two stages.
#:
#: **Stage 1 -- the degradations.** Anders is DQ'd: his cell shows ``DQ``
#: and no placing, and his card deliberately carries a *winning*
#: ``stage_pct`` of 100.0 so the exclusion has to come from the DQ flag
#: and cannot come from a missing number. Bea has no scorecard, no audit
#: and no stage time, so her cell is her label alone. Mathias has a
#: manually entered stage time and no scorecard.
#:
#: Nobody is ranked on stage 1, and that is the point: it leaves Bea's
#: cell with a single line of text at the top, which is what the rendered
#: pixel checks use as their control. Her cell reads the same clip at the
#: same seek as Anders', so their blurred freezes are identical by
#: construction and the picture underneath subtracts out; the bottom of
#: her cell is picture and nothing else, which is the only place "is the
#: held frame actually blurred" can be measured without glyphs in the way.
#:
#: **Stage 2 -- the ranked stage.**
#:
#: ==========  =====  =========  ==========  ============  =======
#: Shooter     Audit  Scorecard  ``pct``     ``points``    Placing
#: ==========  =====  =========  ==========  ============  =======
#: Anders      yes    yes        100.0       54            #1
#: Bea         no     yes        100.0       48            #1
#: Mathias     yes    yes        78.5        56            #3
#: ==========  =====  =========  ==========  ============  =======
#:
#: Two things are load-bearing there. The **tie** exercises competition
#: ranking -- two ``#1``s and then ``#3``, never ``#2``. And the points
#: column is ordered *differently* from the percentage column, because
#: Mathias shoots Open (major) while the other two shoot Production
#: Optics (minor): a summary that ranked by ``stage_points`` would put him
#: first, and that shows up in a rendered frame rather than only in a unit
#: test. Bea also carries the scorecard-but-no-audit state here.
#:
#: Bea has no audit in **either** stage, so she never gets a running
#: clock. That is what makes her clock corner the control for "did a clock
#: survive into the hold".
ROSTER: tuple[ShooterSpec, ...] = (
    ShooterSpec(
        label="Anders",
        clip="full",
        shots_ms=ANDERS_SHOTS_MS,
        scoring=(
            StageScoring(
                time_seconds=4.00,
                scorecard=_card(
                    scoring="minor",
                    stage_number=1,
                    time_seconds=4.00,
                    alphas=5,
                    charlies=2,
                    deltas=1,
                    # A stage-winning card, and DQ'd anyway. The summary
                    # has to drop him from the ranked pool on the flag --
                    # if it dropped him for want of a number instead, this
                    # card would still come out ``#1``.
                    dq=True,
                ),
            ),
            StageScoring(
                time_seconds=4.50,
                scorecard=_card(
                    scoring="minor",
                    stage_number=2,
                    time_seconds=4.50,
                    alphas=10,
                    charlies=1,
                    deltas=1,
                ),
            ),
        ),
    ),
    ShooterSpec(
        label="Bea",
        clip="full",
        shots_ms=None,
        scoring=(
            StageScoring(),
            StageScoring(
                time_seconds=4.00,
                scorecard=_card(
                    scoring="minor",
                    stage_number=2,
                    time_seconds=4.00,
                    alphas=7,
                    charlies=4,
                    deltas=1,
                    # The scoreboard carried no penalty columns for this
                    # row. Absent, not zero: the summary must draw
                    # "A7 C4 D1" and stop there.
                    misses=None,
                    no_shoots=None,
                    procedurals=None,
                ),
            ),
        ),
    ),
    ShooterSpec(
        label="Mathias",
        clip="short",
        shots_ms=MATHIAS_SHOTS_MS,
        scoring=(
            StageScoring(time_seconds=3.10, time_seconds_manual=True),
            StageScoring(
                time_seconds=4.60,
                scorecard=_card(
                    scoring="major",
                    stage_number=2,
                    time_seconds=4.60,
                    alphas=10,
                    charlies=1,
                    deltas=1,
                ),
            ),
        ),
    ),
    *(
        ShooterSpec(
            label=label,
            clip="full",
            shots_ms=EXTRA_SHOTS_MS,
            scoring=(_filler(index, 1), _filler(index, 2)),
        )
        for index, label in enumerate(("Nils", "Olof", "Petra", "Rikard", "Sanna", "Tove"))
    ),
)

MAX_SHOOTERS = len(ROSTER)


def cut_clip(source: Path, destination: Path, frames: int, *, ffmpeg: str) -> Path:
    """Re-encode the first ``frames`` frames of ``source`` to ``destination``.

    ``-frames:v`` rather than ``-t`` so the clip's length is an exact
    frame count and its container duration is exactly
    ``frames * 1001/30000`` -- ``-shortest`` carries the same bound to the
    audio, so ``format=duration`` (which is what ``project_loader``
    probes) agrees with the video stream instead of overhanging it.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k", "-shortest", str(destination),
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return destination


def probe_seconds(path: Path, *, ffprobe: str) -> float:
    """``format=duration``, the field ``project_loader`` reads."""
    done = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return float(done.stdout.strip())


def build_clips(source: Path, root: Path, *, ffmpeg: str, ffprobe: str) -> dict[str, tuple[Path, float]]:
    """The full and short clips, each with its probed duration beside it.

    The probe is asserted against the frame count the clip was cut to. A
    fixture whose declared length drifts from its media is the exact
    condition that hid the freeze-seek blocker, so it fails here rather
    than quietly handing the renderer slack production never has.
    """
    frame_seconds = SYNTHETIC_FPS_DEN / SYNTHETIC_FPS_NUM
    clips: dict[str, tuple[Path, float]] = {}
    for name, frames, nominal in (
        ("full", STAGE_FRAMES, STAGE_DURATION_SECONDS),
        ("short", SHORT_STAGE_FRAMES, SHORT_STAGE_DURATION_SECONDS),
    ):
        path = cut_clip(source, root / f"{name}.mp4", frames, ffmpeg=ffmpeg)
        probed = probe_seconds(path, ffprobe=ffprobe)
        assert abs(probed - nominal) <= frame_seconds, (
            f"the {name} clip declares {nominal:.4f}s but its media probes at {probed:.4f}s -- "
            "the fixture's declared duration has to match its media or the freeze-frame seek "
            "gets slack production never has"
        )
        clips[name] = (path, probed)
    return clips


def write_audit(path: Path, ms_after_beep: Sequence[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "shots": [
                    {"shot_number": i + 1, "candidate_number": i + 1, "ms_after_beep": ms}
                    for i, ms in enumerate(ms_after_beep)
                ]
            }
        ),
        encoding="utf-8",
    )


def stage_shots_ms(spec: ShooterSpec, stage_number: int) -> tuple[int, ...] | None:
    """This shooter's shot times on ``stage_number``, or ``None`` for no audit.

    Each later stage adds three more shots than the one before, so a
    per-stage summary sliced on the wrong stage draws a different shot
    count *and* different split figures -- visibly wrong rather than
    plausibly wrong. Three rather than one because a few extra glyphs
    average away over a small canvas, and a discriminator that lands
    within the encode's own residue discriminates nothing. Every added
    shot stays inside the post-beep span the trims give the stage.

    These counts are deliberately far below the stage's round count, so
    the live counter reads ``2/12`` rather than ``12/12``. The sample
    instants the pixel assertions use have to fall unambiguously between
    two shots, and a full 12-shot run leaves no gap wide enough to sample
    in. The scorecard still describes the whole stage; the audit is a
    handful of shots at known times, which is what the frames are read
    against.
    """
    if spec.shots_ms is None:
        return None
    extra = tuple(3500 + 300 * index for index in range(3 * (stage_number - 1)))
    return (*spec.shots_ms, *extra)


def write_project(root: Path, spec: ShooterSpec, *, stages: int) -> MatchProject:
    """Write this shooter's ``project.json``, carrying every stage's scoring.

    Written through the model rather than as hand-rolled JSON so the
    fixture cannot describe a project the application would reject, and so
    a schema change breaks here instead of silently degrading every tile
    to "no readable project.json".
    """
    entries = [
        StageEntry(
            stage_number=number,
            stage_name=STAGE_NAMES[(number - 1) % len(STAGE_NAMES)],
            time_seconds=spec.stage_scoring(number).time_seconds,
            time_seconds_manual=spec.stage_scoring(number).time_seconds_manual,
            scorecard=spec.stage_scoring(number).scorecard,
            stage_rounds=STAGE_ROUNDS[(number - 1) % len(STAGE_ROUNDS)],
        )
        for number in range(1, stages + 1)
    ]
    project = MatchProject(name=MATCH_NAME, competitor_name=spec.label, stages=entries)
    root.mkdir(parents=True, exist_ok=True)
    project.save(root)
    return project


def build_roster(
    root: Path,
    clips: Mapping[str, tuple[Path, float]],
    *,
    count: int = 3,
    stages: int = 1,
    clip_overrides: Mapping[str, tuple[Path, float]] | None = None,
) -> list[CompareShooterBundle]:
    """The first ``count`` shooters of :data:`ROSTER`, over ``stages`` stages.

    Each gets a ``project.json`` on disk and, unless they are the no-audit
    shooter, one audit JSON per stage. Bundles carry the clip's *probed*
    duration, never a nominal one.

    ``clip_overrides`` maps a shooter's *label* to a ``(path, probed
    duration)`` pair that replaces the ``clips`` lookup for that shooter.
    It exists for the local real-footage corpus (#686): the frame tool
    substitutes real tiles while scoring, audits and geometry stay this
    fixture's. The caller owns the geometry contract -- an override must
    be cut to the same frame count and rate as the clip it replaces, or
    the declared beep offset and every sampled moment drift off the media.
    """
    if not 1 <= count <= MAX_SHOOTERS:
        raise ValueError(f"count must be 1..{MAX_SHOOTERS}, got {count}")
    if stages < 1:
        raise ValueError(f"stages must be at least 1, got {stages}")

    bundles: list[CompareShooterBundle] = []
    for spec in ROSTER[:count]:
        trim, duration_seconds = (clip_overrides or {}).get(spec.label) or clips[spec.clip]
        project_root = root / spec.label
        write_project(project_root, spec, stages=stages)
        by_number: dict[int, CompareStageBundle] = {}
        for number in range(1, stages + 1):
            audit_path = project_root / "audit" / f"stage{number}.json"
            shots = stage_shots_ms(spec, number)
            if shots is not None:
                write_audit(audit_path, shots)
            by_number[number] = CompareStageBundle(
                stage_number=number,
                stage_name=STAGE_NAMES[(number - 1) % len(STAGE_NAMES)],
                trim_path=trim,
                audit_path=audit_path,
                beep_offset_in_clip=BEEP_OFFSET_SECONDS,
                duration_seconds=duration_seconds,
                width=1280,
                height=720,
                frame_rate_num=SYNTHETIC_FPS_NUM,
                frame_rate_den=SYNTHETIC_FPS_DEN,
            )
        bundles.append(
            CompareShooterBundle(label=spec.label, project_root=project_root, stages_by_number=by_number)
        )
    return bundles
