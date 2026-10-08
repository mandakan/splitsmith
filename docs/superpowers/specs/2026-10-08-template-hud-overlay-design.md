# Template HUD overlay

Status: approved 2026-10-08 (conversation). Mockups of the five variants:
https://art.urdr.dev/splitsmith-hud-styles. Render cost follow-up: #1305.

## Goal

Polish and motion for videos that get shared. Looks already restyle every card;
the live overlay (clock, shot counter, last split) is the one surface they do
not reach. This makes the overlay a Look slot whose variants are templates that
draw the whole HUD per frame, clock included, with shot effects and a landing
moment on the last shot.

Today's overlay stays as **Classic**: the default, the fast path, byte for byte
what it renders now (run-length PNGs for the counter and split, an ffmpeg
`drawtext` clock). A template HUD is opt-in and slower, and says so.

## Scope

- In: the single-shooter MP4 and the FCPXML overlay MOV. Both consume the one
  per-stage MOV that `overlay_render.render_overlay` writes, so one renderer
  change serves both.
- Out: the compare grid (its per-tile sprites, `compare/overlay_live`, are a
  separate path and a tile-sized HUD is its own design; a follow-up). The grid
  request carries none of the new fields and keeps drawing its own overlay.
- Out: parallel frame rendering (#1305 measures a real match first).

## Variants

Five shipped templates beside Classic, in the shipped `splitsmith` Look:

| Variant | Layout | Shot effect | Landing |
|---|---|---|---|
| `plate` | One block: clock on a dark plate with a progress line, counter, split chip with its class | Counter punch, chip slides in, plate edge lights on the beep | Plate flashes accent, label turns to "Stage time" |
| `pips` | One pip per round filling in a row, clock under it; split large, centred | Pip fills with a ring burst; split pops and drains over about a second | Row flashes, stage time stamps in centre |
| `ticker` | Rail: clock, "Shot N of M", the last four splits with classes | Stack slides, the new row lands with an accent edge | Stack folds away, stamp |
| `timeline` | Lower-third band: clock left, count right, a track that fills with time and drops a tick per shot | Tick drops in, split tag rises above it | Track completes, stamp |
| `minimal` | A small clock at the bottom | The split flashes large in the centre and shrinks away | The clock grows into the stamp |

The mockup page is the visual reference; final type sizes and plates are tuned
against real footage with the frame scripts.

## User controls

Shared by every template variant:

- `speed_colors` (on): colour a split by its speed tier.
- `class_labels` (on): show draw / split / transition / reload.
- `landing` (on): the landing moment and stamp.
- `position`: a corner or edge, offered only for a variant that declares
  positions (`plate`, `pips`, `ticker`). `None` is the variant's own default.

Classic ignores all four.

## Engine

### The Look slot

`look.json` gains an `overlay` slot, variants as for the cards:

```json
"overlay": {"plate": "hud-plate.html", "pips": "hud-pips.html", "ticker": "hud-ticker.html",
            "timeline": "hud-timeline.html", "minimal": "hud-minimal.html"}
```

`default` always means Classic, the engine path with no template; a manifest
cannot name a file for it. A variant the Look lacks falls back to Classic with
a warning (the cards' rule, `looks.py`). A template declares the positions it
supports in its own markup:

```html
<meta name="splitsmith-positions" content="top-left,top-right,bottom-left,bottom-right">
```

The first is its default. The catalog (`looks.look_catalog`) reads the tag as
text, without a browser, and serves it per variant so the gallery knows when to
offer the control. The manifest grammar does not change.

### The data (`overlay_hud`, pure)

A new module builds the template's `data` from the audit and the trim's probe.
Templates never compute a split, a class or a tier; all five read the same
numbers and the tests pin them in Python.

`data.stage`:

- `beep`: the beep in clip seconds.
- `shots`: per kept shot, in time order: `t` (clip seconds), `split` (shot 1's
  is the draw, as `build_frame_states` has it), `cls` (the coach class through
  `coach.heal_unclassified`, `None` when the audit has none), `tier`.
- `stage_time`: last shot minus beep. `rounds`: the shot count.

`tier` is `good`, `normal` or `slow`: the split against this stage's median for
its class (below 0.93x is `good`, above 1.12x is `slow`, the mockup's cutoffs,
named constants). The draw, a reload and an unclassified shot have no tier
(`None`), so a reload is never "slow" beside a split. A class with fewer than
three shots on the stage has no median and its shots no tier (a median of two
says nothing).

`data.options`: `speed_colors`, `class_labels`, `landing`, `position` (resolved:
never `None`).

The theme, fonts, size and fps reach the template as for the cards
(`TemplateContext`); the shared `_shared/` scripts are mounted.

### Template contract

- `seek(t)`: draw the HUD at clip time `t`. Required.
- `settle()`: seconds after the last shot until the HUD stops moving (the
  landing's length). Required; `0` is allowed.
- `fonts()` and `fit()` as for the cards.

The template must be static before the beep and static after `last shot +
settle()`; that is what makes the frame plan below valid. `looks check` words a
template that moves outside that span (it compares two frames on either side).

### Frame plan

`render_overlay` with a template variant:

- one frame at `seek(beep)`, held for every frame before the beep;
- every frame from the beep to `last shot + settle()`, at the trim's fps (or
  `overlay_max_fps`);
- the last of those held to the end.

The page renders at the output size capped at 1080 lines; a larger output is
scaled up by ffmpeg before encode. The frames pipe into the same encoder the
MOV uses today (ProRes 4444 or HEVC with alpha, `_resolve_codec`), so the MP4's
compositing and the FCPXML are unchanged.

Budgets: the HUD does not use the card's `MAX_ANIMATION_SECONDS` (a long field
course would be refused). It has its own in `look_sandbox`: a per-frame budget
and a total that grows with the live span (frames times the per-frame budget,
plus the load). Every call into the page goes through `_TemplatePage.call` and
the watchdog, as for the cards.

### Cache

The HUD MOV is cached under `cache_dir/overlay-hud`, keyed by content: the
audit revision, the trim's probe (size, fps, duration), the variant, the
options, `template_digest`, the codec settings and the ffmpeg identity. A hit is
copied to `<base>_overlay.mov` only when that file's bytes differ, so a
re-export leaves the MOV's mtime alone and the MP4 segment cache key with it.
Bump a `HUD_KEY_VERSION` when the recipe changes. Classic has no cache and no
new code on its path: same argv, same pixels.

### Failure

No browser, a script error, a timeout or a crashed renderer falls back to
Classic for that stage, with a line in the export report naming the variant and
the reason. An export never fails because of the HUD.

### Hosted

The shipped variants run in the sandbox like the shipped cards. A stored Look
on hosted carries no template, so it can only pick a shipped variant (through
`styles`). Custom HUD templates are desktop only, through the existing guards;
nothing here relaxes them.

## Surfaces

### Request fields

On every body that carries `overlay_theme` for the single-shooter path (the
stage export, the match export, the export preview), `ExportPresetBody`,
`MatchExportRequestData` and both CLIs:

- `overlay_variant: str | None` (`None` is the Look's default: Classic).
- `overlay_speed_colors`, `overlay_class_labels`, `overlay_landing`: `bool`,
  default `True`.
- `overlay_position: str | None`, one of `top-left`, `top-right`,
  `bottom-left`, `bottom-right`; a position the variant does not declare is
  ignored with a warning.

They join a cache key only when the variant is a template, so an untouched form
sends the body it always sent and Classic's keys do not move. CLI flags:
`--overlay-variant`, `--overlay-position`, `--no-speed-colors`,
`--no-class-labels`, `--no-landing` on `match export` and on the `cli.py`
verb that renders an overlay today (`render_overlay`'s caller there).

### SPA

- `lib/lookGallery.ts`: an Overlay slot. Classic and the five variants are
  tiles with looping WebP thumbnails; the template tiles carry "Slower render".
  Hidden on the grid mode through the registry's visibility table.
- Under the chosen template tile: the three toggles, and a position
  `Segmented` when the catalog lists positions for that variant.
- `lib/exportPresets` maps the new fields (`settingsToBody` / `applyBody`), and
  they live on `ExportSettings`. `api.exportBodies.test.ts` types them
  `Required<...>`.
- The rail names the chosen variant.

### Preview

The export preview with `motion` renders a short loop for a template overlay:
from the beep through the first three shots, over the trim's head frame, and a
second window for the landing over the tail frame,
held on the stamp; the request picks the window with
`overlay_window: "shots" | "landing"` (default `shots`). It reuses the animated WebP path and is cached under
`export-preview` by the HUD key plus the window. Without a trim on this disk
(hosted) it draws over the backdrop the cards use. A still request answers the
frame at the third shot.

### Thumbnails, frame scripts, check

- `render_look_thumbnails.py --look-previews` writes `overlay-<variant>.webp`
  from a sample stage; the gallery never rasterizes.
- `render_match_frames.py --overlay-variant <name>` (and the position and
  toggle flags) writes frames at the beep, mid-stage, a reload and the landing.
- `looks check` probes overlay templates with three sample stages (12 rounds;
  32 rounds; no class data) and words a missing `seek` or `settle`, motion
  outside the live span, clipped text at the probed times and fonts other than
  the bundled faces.
- The authoring guide (`docs/looks/authoring.md`) gains an overlay section with
  the contract and the data shape; a starter `hud` template joins
  `data/looks/_starters/`.

### What's new

One entry for the new overlay styles (the `whats-new` skill), in the slice that
makes them choosable in the app.

## Testing

- `overlay_hud`: splits, classes and tiers against a fixture audit, including a
  reload, a class with too few shots for a median, and an unclassified audit
  healed.
- Frame plan with a fake rasterizer: the seek times requested, the pre-beep
  hold, the settle span, frame count equal to the trim's, and a 90 s live span
  not refused.
- Cache: a second render calls no rasterizer and leaves the MOV's bytes and
  mtime alone; each option, the variant and the template bytes move the key.
- Fallback: a template that throws yields a Classic MOV and the report line.
- Classic unchanged: the existing overlay tests pass untouched, plus one that
  asserts no template is loaded when `overlay_variant` is `None`.
- Integration (`@pytest.mark.integration`, real Chromium and ffmpeg, input from
  `tests/synthetic_media.py`): each shipped variant renders a short stage into a
  MOV with the trim's frame count and non-transparent pixels at the counter
  after a shot.
- Each new test is checked to fail against the code without its fix.
- SPA: the gallery visibility table, the mappers and the preset round trip.
- Visual: every variant's frames from the frame script on an Urdr page,
  reviewed before merge.

## Delivery

1. Engine: `overlay_hud`, the frame plan, budgets, the cache, the fallback,
   `plate` as the first template, the CLI flags, the frame script flag.
2. The other four templates and the thumbnails.
3. Request fields, presets, the gallery, the preview, the What's new entry.
4. `looks check`, the authoring guide section and the starter.
