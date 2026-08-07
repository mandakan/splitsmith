# Amendment: the summary rasterizes through a box engine (#683)

Amends `2026-08-06-overlay-composition-seam.md` and its spec
`docs/superpowers/specs/2026-08-06-overlay-composition-seam-design.md`.
Decision taken 2026-08-06, mid-execution, after three fix rounds on
Task 6. Tasks 1-5 stand unchanged and are merged into the branch.

## Why the pivot

Task 6 hand-rolled a text fitter. Three review rounds found, in order:

1. `_draw_group` accepted a `height_budget` and never used it.
2. A bottom-anchored group could spill **upward** into the cell above.
3. ROW flow had no cumulative width bound: elements were each fitted
   against the *full* cell width and could sum past it, and the accent
   plate added padding the fitter never measured. With an ordinary
   23-character competitor name this drew one shooter's placing and
   stage percentage inside the next shooter's cell -- 3/13 boxes
   crossing at 3x3/1280x720, 4/13 at 2x2/640x360, against **zero** for
   the pre-refactor code.
4. After fixing (3), a ROW's **first** element still drew unconditionally,
   so a floor-fitted identity label of 166px drew across a 112px budget
   at 4x4/640x360.

Measured on the branch: ~176 lines answer "what does this cell say" and
~685 lines are fitting machinery. **Every Critical finding was in the
machinery.** None of these is a novel problem; all four are what a box
model exists to prevent.

The three failure classes map onto engines unevenly:

| failure | nature | CSS | Pango | in-house |
|---|---|---|---|---|
| cumulative advance unbounded | box solver | solved | no | hand-written |
| fitter and drawer measured different quantities | two-pass discipline | solved | helps | hand-written |
| first element not truncated at the floor | overflow policy | solved | solved | hand-written |

Only CSS closes all three, so the summary moves to **headless Chromium
via Playwright**.

## What is kept

Nothing below is re-litigated; all of it is merged and reviewed.

- **`src/splitsmith/overlay_layout.py`** -- `Anchor`, `Flow`, `Role`,
  `Emphasis`, `Element`, `Group`, `CellScale`, `anchor_origin`,
  `anchor_ffmpeg_expr`. This is the declaration layer and it is exactly
  what makes the swap cheap. `Group`/`Element` map onto CSS nodes
  directly.
- **`CellScale`** stays. `live_primary` and `pad` are pinned by test and
  by byte-identical sprite output (Task 3) and must not move. The
  summary stops using it for *fitting* -- CSS does that -- but keeps it
  as the type scale.
- **The Task 5 defect fixes**: procedurals reaching the screen, and
  accuracy (`A/C/D`) split from faults (`M/NS/P`).
- **The Task 4 fixture** and its nonzero procedural on Sanna.
- **`_cell_groups`** -- the composition declaration. Unchanged.
- **The repaired hold integration check** (compares the in-hold frame to
  its own composed still, both clock corners).
- The live sprite and the `drawtext` clock: **untouched**. They stay PIL
  and ffmpeg respectively. See "Explicitly out of scope".

## What is deleted

From `src/splitsmith/compare/overlay_summary.py`: `_text_width`,
`_fit_font`, `_plate_size`, `_plate`, `_element_footprint`,
`_fit_group_scale`, `_draw_group`, and the layout half of `_draw_cell`.
With them go the six lever tests and the boundary harness that exist
only to prove the fitter does not leak -- **except** the boundary
assertions, which carry over as-is against the new renderer. If they
still pass unchanged, that is the strongest evidence the seam held.

## Architecture

Two new modules, split so that only one of them needs a browser.

### `src/splitsmith/overlay_html.py` -- pure, no browser

```python
def cell_html(groups: Sequence[Group], *, scale: CellScale, theme: OverlayTheme) -> str
def summary_html(
    cells: Sequence[tuple[TilePlacement, Sequence[Group]]],
    *,
    geometry: SpriteGeometry,
    scale: CellScale,
    theme: OverlayTheme,
) -> str
```

A pure function from declaration to an HTML document. Unit-testable
without launching anything. Design rules:

- **One CSS grid** at canvas size, `rows x cols`, each cell exactly
  `cell_width x cell_height` (floor division, matching `_cell_size`).
- **`overflow: hidden` on every cell.** This is the invariant becoming
  structural rather than tested: no descendant can paint outside its
  cell, which is the bug class that consumed three fix rounds.
- Anchors are grid areas within the cell (`align-items` / `justify-items`
  off `Anchor.is_bottom` / `is_right` / `is_center`); `Flow` is
  `flex-direction: row | column`; a shared anchor is two stacked flex
  children in declaration order.
- `Role` maps to a CSS class carrying `font-size` from `CellScale`.
  `Emphasis.PLATE` is a filled `background` with padding; `MUTED` is an
  `opacity`/colour token. No sizes computed in Python.
- Overflow policy is declared, not implemented: `text-overflow: ellipsis`
  on `IDENTITY` (a shortened name still identifies), and the shrink-to-fit
  the fitter emulated becomes `min-width: 0` plus a container query or a
  clamped `font-size`. **Truncating to zero visible characters is not
  acceptable** -- #617 shipped a bug where rich ellipsized a note away and
  the assertion passed while the user saw nothing.
- Fonts via `@font-face` pointing at the already-bundled
  `src/splitsmith/data/fonts/*.ttf`. No system font may be reachable, or
  output stops being deterministic across machines.

### `src/splitsmith/overlay_raster.py` -- the only browser-aware module

```python
class Rasterizer(Protocol):
    def png(self, html: str, *, width: int, height: int) -> bytes: ...

class ChromiumRasterizer:
    """Playwright-backed. Reuses one browser across a whole render."""
```

Injected the way `mp4_grid.Runner` already is, so unit tests never launch
a browser. One browser instance per render, not per stage: a 12-stage
match is 12 rasterizations, and process startup dominates otherwise.
Pin `device_scale_factor=1` and an explicit viewport; never let the
browser pick.

`overlay_summary.build_hold_still` composes as it does today -- freeze,
blur, dim, paste -- then alpha-composites ONE canvas-sized PNG from the
rasterizer over the result, instead of drawing text per cell.

## Dependency change, stated plainly

`playwright>=1.60.0` moves from `[dev]` to project dependencies.

**Playwright brings its own Chromium, and that is a feature rather than a
cost.** `uv run playwright install chromium` fetches a build Playwright
pins to its own package version into `~/.cache/ms-playwright`. There is
no system package, no apt or brew, and no distro drift -- so the
rasterizer is byte-stable across the dev host, CI, the hosted deployment
and the workers, bound by `uv.lock` like everything else. A *system*
browser would be the risky choice: its version moves under you and pixel
output moves with it. Confirmed working headless on the dev host at
Chromium 148.0.7778.96.

What remains a real cost, and must be handled rather than assumed:

- The browser is **not vendored in the wheel**. Every environment that
  renders an overlay needs the one-time install step: the hosted image's
  Dockerfile, the self-hosted worker's provisioning, the Railway
  fallback, and CI.
- Disk, measured on the dev host at build 1223: the full browser is
  **377M**, the headless shell **260M** -- a 31% saving. Use
  `playwright install chromium --only-shell` and launch with
  `channel="chromium-headless-shell"`. Verified: the shell channel
  renders the same screenshot **byte for byte** (4865 bytes either way),
  so nothing is given up. Note that `p.chromium.executable_path` still
  reports the full-browser path even when the shell channel is used --
  it reports the default, not what was launched, so do not use it to
  assert which binary is in play.
- Pinning the browser to the package version means a `playwright` bump
  can move rendered pixels. Treat a version bump the way this repo treats
  an ffmpeg change: re-render the fixture frames and look at them.

**Degradation is required, not optional.** The codebase already has the
pattern: `mp4_grid` preflights ffmpeg for `drawtext` and degrades with a
readable notice rather than failing. Do the same here -- no usable
browser means the summary hold is skipped with a clear message and the
rest of the render proceeds. It must NOT mean falling back to a second
renderer; maintaining two is what this amendment exists to stop.

## Explicitly out of scope

- **The live sprite** (`overlay_sprites.render_state`) stays PIL. It
  draws two elements, has no reported overflow defect, and Task 3 proved
  it byte-identical. Moving it is #693's job, with the single-shooter
  port (#684).
- **The `drawtext` clock** stays in the ffmpeg graph. It is the one
  genuinely per-frame element and no rasterizer change touches it. The
  two-mechanism split survives regardless -- which is why
  `anchor_ffmpeg_expr` exists.
- Both argv fingerprint tests must still pass unmodified.

## Tasks

### Task 6R-1: `overlay_html.py`, pure HTML generation
Declaration to HTML, no browser. Tests assert structure: every cell has
`overflow: hidden`; each `Role` carries its `CellScale` size; `PLATE`
renders a filled background; a filler tile emits an EMPTY cell; a tile
with no audit and no scorecard emits its label and nothing else. Golden
HTML for the fixture roster.

### Task 6R-2: `overlay_raster.py` + the `Rasterizer` protocol
Injected rasterizer, one browser per render, `device_scale_factor=1`.
A fake rasterizer for unit tests. An `@pytest.mark.integration` test that
really launches Chromium and asserts the PNG is canvas-sized and
non-blank. Bundled `@font-face` resolves -- assert the rendered text is
NOT the browser's fallback face.

### Task 6R-3: `build_hold_still` composes through the rasterizer
Delete the fitter. Keep freeze/blur/dim/paste and `_cell_groups`. Carry
the boundary assertions over UNCHANGED and confirm they pass against the
new renderer. Preflight + degradation for a missing browser. Both
fingerprint tests unmodified.

### Task 7 (unchanged in intent)
Render frames and READ them. Now also: the same roster at 1920x1080,
2704x1520 and 3840x2160 (#692), and 9 shooters at 3840x2160 so Sanna's
penalty plate is on screen -- Sanna is roster index 7, so a 3-shooter
render will not show one.

## Baseline to beat

Branch at `6ec56ed`: `tests/ -k compare -n 4` 431 passed / 1 skipped
(environmental DTD). `tests/test_compare_overlay_summary.py` 46 passed.
Both fingerprint tests green.
