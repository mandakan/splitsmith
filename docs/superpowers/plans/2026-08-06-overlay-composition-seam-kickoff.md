# Issue #683 kickoff -- a composition seam for overlay elements

Entry point for a session starting fresh on #683. Assumes no memory of
the sessions that built the compare-grid overlay or the fixture under it.

**The issue:** https://github.com/mandakan/splitsmith/issues/683 -- read
it, but read the correction below first: one of its two motivating
symptoms no longer reproduces.

## Why this exists

The compare grid renders N shooters' beep-aligned trims into one MP4.
Two optional layers sit on top:

- a **live overlay** -- per-tile shot counter, last split, and a running
  clock -- and
- a **post-stage summary** -- each tile freezes on its own last frame,
  blurred and dimmed, with that shooter's figures and cross-shooter
  placing drawn over their own cell.

Both are on `main`. The overlay is about to grow more optional elements,
and adding one today means editing a shared drawing function whose layout
assumptions all shift around it.

## Correct the premise before you start

The issue names two symptoms. **Verify both before designing against
them.** One is real and one is not, as of `d9f5518`:

**Stale -- the counter and the clock are already pinned together.**
The issue says the shot counter and the running clock "sit in the same
corner of the same cell at visibly different weights, because nothing
owns what size is a per-tile element". They are in fact pinned to the
same formula, deliberately and with a comment saying so:
`mp4_grid._stage_overlay_plan` sets `font_size=max(48, cell_h // 14)` to
match `overlay_sprites.render_state`'s `big`, and `mp4_grid._clock_pad`
mirrors the sprite's `pad`.

Measured on a rendered frame (640x360 cells, `2/12` against `3.00`):

| | drawn ink height |
|---|---|
| counter, leading digit only (PIL sprite) | 35 px |
| clock digits (`drawtext`) | 35 px |
| counter, whole `2/12` string | 45 px |

The 45 is the **slash**, which ascends and descends past any digit. It is
a glyph-repertoire difference, not a type-scale one, and it is identical
at 480x270 cells too. So do not go hunting for a divergence to fix; if
the two are to share a resolver it is for the next element's sake, not
because they disagree today.

**Real -- the summary's line sequence is hardcoded.**
`overlay_summary._cell_lines` builds an ordered `list` of
`(text, size, colour)` in a fixed order: label, placing, shot count,
stage time, then `DQ` *or* hit factor / stage percentage / hit counts,
then split statistics, then the draw. Adding the placing meant inserting
into that sequence, and anything else means the same. `_lay_out_block`
then scales the whole stack to fit and drops lines from the bottom when
it cannot.

## What a seam has to preserve

These are not negotiable and each has a test behind it.

1. **The live overlay is a step function**, not a per-frame loop. It
   renders roughly 30 sprite PNGs per stage at shot-event boundaries and
   composites them with `overlay`, keyed by a content-addressed cache.
   An element needing a per-frame value cannot live in a sprite.
2. **The clock is `drawtext`** precisely because it is the one genuinely
   per-frame element. The seam therefore spans two rendering mechanisms
   with different text metrics. An ffmpeg built without
   `--enable-libfreetype` loses the clock and keeps everything else;
   there is a capability preflight that degrades rather than failing, and
   an integration test that renders both ways and measures the pixels.
3. **Default-off stays byte-identical.** Two fingerprint tests pin it:
   - `test_the_default_off_argv_is_unchanged_since_the_preflight_landed`
     (`tests/test_compare_mp4_grid_commands.py`) -- 42 commands.
   - `test_zero_hold_produces_the_command_main_produces_today`
     (`tests/test_compare_mp4_grid_hold.py`) -- 18 commands.
   If either moves, the stitch's `concat -c copy` can refuse a segment
   hours into a match render.
4. **Absence stays first class.** A missing value renders *less* -- never
   a zero, never a guess. Four absences are distinct today and must stay
   distinguishable: a DQ, a missing scorecard, a filler tile
   (`present=False`), and a missing audit. The example roster exercises
   all four; see below.

## You can see a frame in seconds now

This is new as of #682 (`d9f5518`) and it changes how to work here.

```bash
uv run python scripts/render_grid_frames.py --overlay --summary-hold 2
uv run python scripts/render_grid_frames.py --shooters 9 --canvas 1440x810 \
    --overlay --summary-hold 3
uv run python scripts/render_grid_frames.py --shooters 2 --overlay
```

It builds its own media and roster -- no real match, nothing gitignored --
and drops labelled PNGs at named moments (`pre-beep`, `first-shot`,
`mid-action`, `short-tile-black`, `last-picture`, `last-action`,
`hold-start`, `hold-mid`, `hold-end`, `next-stage`) into a stable
directory that two runs can diff. It also copies out the composed summary
stills, so a hold frame that looks wrong can be compared against what the
renderer thought it was drawing.

The roster is `tests/compare_fixture.py`, shared with the integration
test, so a frame here and a failing assertion there describe the same
render. Stage 1 is the degradations (a DQ carrying a deliberately
*winning* card, a shooter with no scorecard / audit / stage time, and a
manually timed stage). Stage 2 is the ranked stage: a tie at 100% drawn
`#1 / #1 / #3`, with raw points ordered differently because one shooter
is Open/major and the others Production Optics/minor.

**Known limit:** `--summary-hold` needs a canvas that divides by the
grid. `_cell_size` floors, so 1280 at three columns composes 1278 and the
canvas-sized still fails `concat`. That is issue **#691**, filed and not
fixed; the tool refuses the combination with a readable message. Use
1440x810 or 3840x2160 for 3x3 work.

## How to work on this

**Show a frame before building.** This is the top row of the agreed
review tiering and it matters most here. The single biggest waste on this
feature was a live delta strip built across two tasks, reviewed, and a
Critical fixed in it -- then the user watched one frame and said it was
in the way, and it was deleted. Nothing visual had been shown until it
was finished. Render the proposed composition, publish it, and get a
read before writing the seam.

**Publish an Artifact, not a file.** The user works on **gaspode**, a
headless remote host: local files do not reach them and `SendUserFile`
does not either. Embed frames as base64 data URIs -- the CSP blocks every
external host. Downscale first; a 1400px JPEG at quality 82 is 40-90 KB
where the source PNG is over a megabyte.

**Review depth: this is a refactor with visual consequences**, so it sits
between two tiers. The mechanical half (moving draw calls behind a
declaration) is implement-plus-full-suite. The composition half -- what
lands where, at what size, when a neighbour is absent -- is design and
needs the frame loop above. Filter-graph changes, if any are needed at
all, are full depth: they fail silently.

**Verify by rendering and measuring.** Every defect that mattered on this
project was found that way; none by reading. Four ways a result lies:

1. CPython invalidates `.pyc` on mtime-in-**seconds** plus size, so a
   same-length edit reverted within one second is silently never applied.
   Purge `__pycache__` on both sides of every mutation.
2. `git checkout <file>` reverts your own uncommitted fix along with the
   mutation. Revert with an edit.
3. The content-addressed sprite cache serves pre-mutation PNGs into a
   reused work dir. Fresh work dir per render.
4. A fixture that cannot express the failure proves nothing. `#682` was
   filed for exactly that and its acceptance drill is worth repeating:
   re-introduce the bug, confirm red, revert.

## Baselines on `main` at `d9f5518`

| | |
|---|---|
| Unit + integration, one run | 2712 passed / 20 skipped, ~2m14s |
| Integration within it | 28 ran / **0 skipped** |

```bash
uv run pytest -q --ignore=tests/test_hosted_docker_smoke.py
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest -m integration --ignore=tests/test_hosted_docker_smoke.py -q
uv run ruff check src tests scripts && uv run black --check src tests scripts
```

The suite runs in parallel by default (`addopts` carries
`-n auto --dist load`); `-n0` restores serial and is right when debugging
one test. **Use `-n 4` if more than one agent is running** -- `-n auto`
takes 12 workers each, and concurrent sessions oversubscribe the box and
produce contention failures in `test_shot_detect` / `test_tta_agreement`
that are not defects.

**Integration must never skip** -- CI fails the build on one.

## Useful context

| | |
|---|---|
| Live overlay states + sprites | `src/splitsmith/compare/overlay_sprites.py` |
| Summary still | `src/splitsmith/compare/overlay_summary.py` |
| Shared text drawing | `src/splitsmith/overlay_text.py` |
| Theme tokens | `src/splitsmith/overlay_theme.py` |
| Graph, clock filters, driver | `src/splitsmith/compare/mp4_grid.py` |
| Shot + scoring data per tile | `src/splitsmith/compare/overlay_data.py` |
| Single-shooter overlay (not yet ported) | `src/splitsmith/overlay_render.py` |
| The example roster | `tests/compare_fixture.py` |
| The frame tool | `scripts/render_grid_frames.py` |

The overlay's clock needs an ffmpeg built with `--enable-libfreetype`. On
macOS the default Homebrew formula lacks it -- `brew install ffmpeg-full`
and point **both** `SPLITSMITH_FFMPEG` and `SPLITSMITH_FFPROBE` at it.

## What comes after

**#684** -- port single-shooter exports onto the sprite engine and the
summary. Today only `overlay_text.py` is shared; `overlay_render.py`
still draws every frame with PIL and has no summary and no scoring. This
is the main reason #683 is worth doing first: #684 is where a second
consumer arrives, and a seam is much cheaper to design with one consumer
than to retrofit around two.

**#686** -- a local, gitignored real-footage corpus for the design calls
the synthetic fixture cannot answer: `dim=0.45`, blur radius, legibility
over bright head-cam video with motion blur. **The repo is public; match
footage must never enter it.**

**#689** -- two summary-hold tests weaker than they read.

**#691** -- the canvas-divisibility bug above.

**Still undecided, deliberately:** whether the summary shows a time delta
alongside the placing. Deferred until it can be seen on real footage.
