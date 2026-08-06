# Issue #682 kickoff -- an honest example fixture, and a way to render frames from it

Entry point for a session starting fresh on #682. Assumes no memory of
the sessions that built the compare-grid overlay.

**The issue:** https://github.com/mandakan/splitsmith/issues/682 -- read
it and its one comment; the comment carries the requirements that matter.

## Why this exists

The compare grid renders N shooters' beep-aligned trims into one MP4,
with an opt-in overlay (per-tile shot counter, last split, running clock)
and an opt-in post-stage summary (each tile freezes on its own last
frame, blurred and dimmed, with that shooter's figures and placing over
their own cell). All of that is on `main` as of `3566c54`.

Two things went wrong while building it, and both trace to the same
cause.

**A Critical shipped through three scoped reviews.** The summary's
freeze-frame seek landed past the end of every trim, so under production
geometry the summary rendered as text on **pure black** -- no frozen
picture, no blur, no dim. A 5-shooter render at shipped defaults wrote
zero freeze frames and came out 84% black. It survived because the
integration fixture declared a 9-second stage against 24 seconds of
media, and that slack was the only thing keeping an out-of-range seek
inside a clip.

**The user saw one frame of the whole feature in several days**, and
rendered it themselves on a different machine. Producing a frame meant
hand-assembling a throwaway script that imported private helpers out of
the integration test.

So: a fixture that cannot express production geometry, and no supported
way to look at output. #682 closes both.

## What is already done -- do not redo it

Milestone B's fix wave (merged in #687) delivered the **geometry** half:

- `shooter_clips` (`tests/test_compare_grid_overlay_integration.py:463`)
  cuts each clip, probes it, and **asserts the probed duration against
  the frame count it was cut to**, then hands the bundle the *probed*
  value -- so declared and actual cannot diverge.
- One shooter (`Mathias`) gets a **deliberately shorter clip**, so "the
  action's end" and "this tile's own footage end" are distinguishable.
  With equal-length clips they are not, and that is precisely what hid
  the blocker.
- Pixel assertions on the rendered result (pure-black fraction,
  luma, high-frequency energy).

Verified: run against the *pre-fix* seek, that fixture goes red -- the
integration test at 81.9% black plus five unit tests.

## What is left

### 1. The scoring half of the fixture

There is currently **no** `project.json`, `StageScorecard`,
`stage_rounds` or `MatchProject` anywhere in
`tests/test_compare_grid_overlay_integration.py` (grep returns zero
hits). `compare/overlay_data.load_overlay_data` therefore logs "no
readable project.json" for every shooter and leaves
`TileStageData.scorecard` as `None`.

The consequence: **the summary silently omits hit factor, stage
percentage, hit counts and the placing.** The ranking is implemented and
tested at unit level, and it has never appeared in a rendered frame.

Write a `project.json` per shooter (`MatchProject` /
`StageEntry` in `src/splitsmith/ui/project.py`) carrying
`StageScorecard` values that exercise the real states:

- A **tie** on `stage_pct`, to exercise shared-place-then-skip ranking
  (two `#1`s followed by `#3`).
- **`stage_points` ordered differently from `stage_pct`**, so a
  regression that sorts on the wrong field shows up in a rendered frame
  and not only in a unit test. This is the single most important rule in
  this domain: raw points are meaningless across stages and divisions.
- A **DQ** (shows `DQ`, no placing).
- One shooter with a **scorecard but no audit**, one with **neither**.
- One **manually timed** stage (`time_seconds_manual=True`, no
  scorecard).
- Some `None` hit counts -- a missing count renders as absent, never
  `0`.
- **`stage_rounds.expected`**, so the counter draws `7/12` rather than a
  bare `7`. Its absence already caused a false design finding.

Derive the numbers from `examples/blacksmith-handgun-open-2026.json` or
`examples/tallmilan-2026.json` rather than inventing plausible-looking
values -- those are the shape of the real scoring data.

### 2. The render-frames tool

A script or CLI verb that renders a grid with whatever is being tuned and
drops labelled frames at named moments.

- Builds its own inputs (`tests/synthetic_media.py`). No dependency on
  the gitignored `stage_sample.mp4`, no real match required.
- Takes the knobs: shooter count (to reach 2x2, 1x2, 3x3), canvas size,
  `--overlay`, `--summary-hold`, theme.
- Emits frames at **named** moments -- pre-beep, mid-action, last action
  frame, inside the hold, first frame of the next stage -- rather than
  making the caller work out frame indices.
- Stable output directory so successive runs diff.
- Reuses the fixture from part 1, so one roster serves both correctness
  and design.

`scripts/` is where this project keeps such tools (see
`build_overlay_theme.py`, `build_ensemble_fixture.py`).

## The acceptance test

Not "the fixture has more fields". It is:

1. **The blocker reproduces.** Point the honest fixture at the pre-fix
   seek and the tests must go red. If they do not, the fixture still
   cannot express the failure.
2. **A rendered summary shows a placing.** Not a unit assertion -- a
   frame, with `#1`, `#2` and a tie visible on the tiles.
3. **`stage_points` cannot masquerade as `stage_pct`.** Swap the sort key
   and a *rendered frame* changes.

## Baselines on `main` at `3566c54`

| | |
|---|---|
| Unit + integration, one run | 2709 passed / 20 skipped, ~1m34s |
| Integration within it | 28 ran / **0 skipped** |

```bash
uv run pytest -q --ignore=tests/test_hosted_docker_smoke.py
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest -m integration --ignore=tests/test_hosted_docker_smoke.py -q
uv run ruff check src tests && uv run black --check src tests
```

The suite runs in parallel by default (`addopts` carries
`-n auto --dist load`); `-n0` restores serial, and both must report
**identical counts** -- a parallel run that passes while collecting fewer
tests is a silent regression. **Use `-n 4` if more than one agent is
running**: `-n auto` takes 12 workers each, and concurrent sessions
oversubscribe the box and produce contention failures in
`test_shot_detect` / `test_tta_agreement` that are not defects. Three
agents filed those as "pre-existing flaky" when the suite was clean.

**Integration must never skip** -- CI fails the build on one.

## How to work on this

**Review depth: this is tooling.** Per the agreed tiering, mechanical
work gets implement-plus-full-suite and no review agent. But the
fixture's acceptance test above is strong and mechanical -- lean on it
rather than on a reviewer's eye.

**Show the user pixels.** The user works on **gaspode**, a headless
remote host: they cannot open local files, and `SendUserFile` does not
reach them. Publish an **Artifact** with frames embedded as base64 data
URIs (the CSP blocks every external host). Downscale first -- a 4K PNG at
~1.4 MB becomes ~115 KB as a 1600px JPEG.

**Verify by rendering and measuring.** Every defect that mattered on this
project was found that way; none by reading. Four ways a result lies
here:

1. CPython invalidates `.pyc` on mtime-in-**seconds** plus size, so a
   same-length edit reverted within one second is silently never applied.
   Purge `__pycache__` on both sides of every mutation.
2. `git checkout <file>` reverts your own uncommitted fix along with the
   mutation.
3. The content-addressed sprite cache serves pre-mutation PNGs into a
   reused work dir. Fresh work dir per render.
4. A fixture that cannot express the failure proves nothing -- which is
   the entire reason this issue exists.

`ffprobe` lies about container durations (it once reported a 21ms A/V
difference on a file 372ms out, and reports a uniform packet duration
across a concat join). Measure decoded frames and decoded PCM.

## Useful context

- Overlay data: `src/splitsmith/compare/overlay_data.py`
- Live overlay states + sprites: `src/splitsmith/compare/overlay_sprites.py`
- Summary still: `src/splitsmith/compare/overlay_summary.py`
- Graph and driver: `src/splitsmith/compare/mp4_grid.py`
  (`render_grid_mp4` at :1632)
- Scoring models: `src/splitsmith/ui/project.py`
- Synthetic media: `tests/synthetic_media.py`
- The overlay's clock needs an ffmpeg built with `--enable-libfreetype`;
  there is a capability preflight that degrades rather than failing. On
  macOS the default Homebrew formula lacks it -- `brew install
  ffmpeg-full` and point **both** `SPLITSMITH_FFMPEG` and
  `SPLITSMITH_FFPROBE` at it.

## What comes after

#683 (a composition seam for overlay elements) and #684 (porting
single-shooter exports onto the sprite engine and summary) both depend on
being able to see a frame quickly. #686 adds a local, gitignored
real-footage corpus for the design calls this fixture cannot answer --
`dim=0.45`, blur radius, legibility over real video. **The repo is
public; match footage must never enter it.**
