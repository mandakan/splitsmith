# Rendered video first: Looks, motion templates, identity, MP4 transitions

Status: approved direction 2026-10-06 (brainstorm). Detection, splits and
coaching are unchanged by this spec and keep their own roadmap. Everything
here is the render and export side.

## Problem

The finished product a shooter shares is a cut, overlayed match video on
YouTube. FCPXML was the route to two things the MP4 renderer lacked: a
transition catalog and a music library. It costs a Final Cut licence, a
desktop round trip and a manual upload, and the overlay, cards and summary
splitsmith draws so carefully arrive in FCP as a flat connected clip. The
MP4 path already has segments, cards, a stage summary, chapters, presets, a
preview and a YouTube upload. What it lacks is motion, transitions,
theming, logos and identity.

Decision: the rendered MP4 uploaded to YouTube is the primary export. The
FCPXML and FCP7 XML exports stay as they are, frozen: no new Look feature
reaches them, unknown values map to their nearest built-in with an anomaly.

## What was measured (2026-10-06)

- The shipped static ffmpeg (9.0.2, `desktop/build-ffmpeg.sh`) has `xfade`
  with 57 built-in transitions and a custom expression, `zoompan`,
  `overlay` with alpha, `lut3d`, `drawtext` (freetype + harfbuzz),
  `acrossfade`, `sidechaincompress` and `loudnorm`. No libass.
- Headless Chromium (`overlay_raster.ChromiumRasterizer`, already shipped
  for cards and overlays) renders 1920x1080 alpha PNG frames at 28 fps and
  JPEG at 50 fps when a page's Web Animations are seeked per frame. A 5 s
  animated card costs about 5 s of render time.
- Music libraries with an API are not a fit for a product (YouTube's Audio
  Library has no API, Jamendo's free tier is non-commercial, Pixabay has no
  music endpoint). Music is parked, see "Parked".
- Remotion is free up to three employees and $100 per month minimum after,
  and like Motion Canvas needs Node in the desktop bundle. Not taken.

## Scope

In: a Look system (theme tokens + HTML motion templates) that every
generated card, the lower third, the stage summary, the live overlay and
the clock read from; per-shooter identity (accent, logo, club line);
transitions in both MP4 renderers; the Export page gallery fed from the
Looks; identity editing on the Footage page.

Parked, each as its own issue under the epic: Shorts (9:16 per-stage
cuts), a music bed with ducking, moving previews in the Export rail,
accents on the Compare roster and share views, per-clip transitions inside
a stage, user-authored Looks beyond dropping a directory in place.

## 1. Looks and motion templates

### A Look is a directory

```
looks/<name>/
  look.json          tokens: palette, accent series, font roles, motion
  title_page.html    one template per slot (any may be absent)
  slate.html
  lower_third.html
  summary.html
  closing.html
  sting_*.html       transitions offered by this Look
  preview/           authoring-time previews the gallery shows
```

Shipped Looks live under `src/splitsmith/data/looks/` (the first two:
`splitsmith`, the brand palette, and `clean`, the neutral one, both
migrated from `overlay_theme.json`). User Looks live under
`~/.splitsmith/looks/`. `splitsmith.looks` (new module, pure) loads,
validates (Pydantic `Look`, `LookManifest`) and lists them; a manifest
names each slot's variants, their parameters and preview assets. The
existing `data/templates/` (YAML export templates) is unrelated and keeps
its name.

### The template contract

One HTML document per slot. The renderer injects, before any script runs:

```
window.splitsmith = { theme, data, size: {width, height}, fps }
```

and the document exposes on `window`:

- `duration()` seconds; a still template returns 0.
- `seek(seconds)` sets `currentTime` on every animation
  (`document.getAnimations()`, and a vendored `lottie-web` instance's
  frame for a Lottie-driven template) and returns when the DOM is settled.
- `poster()` seconds, optional, the hero frame for the preview pane;
  default the midpoint.

`data` is the slot's declaration in the shape the renderers already build
(`overlay_card.Card`, `stage_summary_data.TileStageData`, the match roster)
plus `shooters[i].identity` (section 2). Fonts are bundled `@font-face`
files, no network, so a frame is a pure function of (template, theme, data,
size, t). Templates are plain HTML, CSS and JavaScript; no build step, no
framework.

### Rasterization

`overlay_raster.Rasterizer` grows `frames(html, *, width, height, fps,
duration) -> Iterator[bytes]` beside `png`. A still (`duration() == 0`)
renders as today, one PNG held with `-loop 1`. An animated template streams
RGBA frames into ffmpeg as rawvideo over a pipe, the path
`overlay_render` already uses for the live overlay. Cards keep encoding as
their own segment; a lower third and a sting become an alpha overlay input
on the stage's or boundary segment's filter graph instead of a PNG with a
fade.

### IR

No new node kinds. `MatchTitle`, `TitleCard`, `SummaryHold` and
`Transition` gain `variant: str` (a template name in the Look),
`Composition` gains `look: str`. `TransitionKind` becomes an xfade name
from the curated list or `sting:<template>`. The FCPXML emitter maps every
value it does not know to its cross dissolve and records an anomaly.

### One theme source

`overlay_theme.py` reads tokens from the Look instead of
`overlay_theme.json`; the live overlay sprites, the stage summary, the
clock's `drawtext` colours and every card take their colours from the same
file. `scripts/build_overlay_theme.py` writes the `splitsmith` Look's
tokens from the SPA's CSS, as it does today.

### Cache and fallback

The segment cache keys a card today by PNG content. An animated segment or
overlay keys on the digest of template source, theme, `data`, size, fps
and the Chromium channel version; `segment_cache.KEY_VERSION` bumps once. No browser means every
template skipped and the degradation recorded on the result, exactly the
current behaviour.

## 2. Per-shooter identity

A Look is match-level. Identity is per shooter:

```
Identity:
  accent: str | None      CSS colour
  logo: str | None        file under <shooter>/identity/
  club: str | None
```

It lives on the shooter's `project.json` (a synced state doc already) and
the logo under `shooters/<slug>/identity/`, pushed and pulled over the
media channel like a trim (`sync/push.py`'s subdir pattern gains
`identity`; the hosted delete route accepts it). Templates read
`data.shooters[i].identity`; the grid tints each tile's chrome with its
shooter's accent, a lower third and the summary carry that shooter's logo,
the closing roster puts each logo beside its row. The single-shooter render
is the one-shooter case. A shooter without an identity gets the Look's
accent series by slot index and the match logo, so rendering never waits
on identity. A match-level logo is an export Details field stored under
the match, never in a preset.

## 3. Transitions in the MP4 renderers

One mechanism in both `mp4_render` and `compare/mp4_grid`: the boundary
segment. For a transition of d seconds between spine items N and N+1 the
renderer encodes one segment from the last d/2 of N and the first d/2 of
N+1, trims both neighbours by d/2, and the stitch stays a video stream
copy. Cards are spine items, so a transition into a slate or out of the
closing card is the same code. The grid's boundary segment carries the
N+1 audio layout the way `build_card_segment_command` does.

Two families on that segment: an `xfade` from a curated list of about ten
(fade, fadeblack, dissolve, slideleft, slideright, circleopen, zoomin,
hblur, smoothleft, wipeleft) and a sting, a Look template rendered as an
alpha sequence over a fade or cut. Audio crossfades (`acrossfade`) over
the same window in both. The FCPXML path's d/2 check against head and
tail pads is reused and reports, never clamps. The boundary segment's
cache key is both neighbours' argv plus the transition.

## 4. Export page, Footage page, API

- `GET /api/looks` lists Looks with slots, variants, parameters and
  preview URLs; `GET /api/looks/{name}/preview/{file}` serves them.
  `lib/lookGallery.ts` keeps the slot definitions and the per-format
  visibility table (the pinned tests) and takes variants from the API.
- A Look slot heads the Look group. `transition` turns on for `mp4`
  (`TRANSITION_FORMATS`), with looping previews rendered at authoring
  time by `scripts/render_look_thumbnails.py`.
- `ExportPresetBody` gains `look: str = "splitsmith"` and per-slot
  `*_variant` fields, all defaulted, `extra="ignore"` as today; the match
  logo is match-specific and not a preset field.
- The preview endpoint renders an animated template at `poster()`.
- Footage page, shooter panel: accent (palette of eight plus custom), logo
  upload, club line. `PATCH` on the shooter's project; the logo through the
  existing per-shooter upload path.
- Nothing changes on the phone surfaces.

## 5. Testing

- `splitsmith.looks`: manifest validation, listing precedence (user over
  shipped), a template missing a slot.
- Template contract: a fixture template with a known animation; assert
  `frames()` yields `fps * duration` frames and that frame k is
  pixel-stable across two runs (determinism).
- Renderers: argv tests for the boundary segment in both renderers
  (neighbour trims, cache key covering both neighbours), plus
  `scripts/render_match_frames.py` and `render_grid_frames.py` across a
  boundary. Look at the frames.
- Identity: defaults derive when absent; the grid tints by accent; the
  push plan includes `identity/` files; the delete route accepts them.
- Gallery: the per-format table still pins `mp4` transitions on; the
  mappers never send a field the registry hides; `api.exportBodies.test.ts`
  covers the new preset fields.
- Every new test must fail against the pre-change code (review practice
  in CLAUDE.md).

## 6. Epic breakdown

Epic: "Rendered video first". Issues, in build order:

1. `splitsmith.looks` + the two migrated Looks + one theme source
   (section 1 without animation: still templates replace the current
   card builders, output pixel-identical for the default Look).
2. Template animation: `frames()`, the `seek` contract, animated title
   page and slate variants, cache key, determinism test.
3. Per-shooter identity: model, sync, editor, templates reading it.
4. MP4 transitions: boundary segment in both renderers, curated xfade
   list, audio crossfade (#1156 closes into this).
5. Stings: Look transition templates on the boundary segment.
6. Gallery from the API: `/api/looks`, Look slot, previews, preset fields,
   poster preview.
7. Parked: Shorts.
8. Parked: music bed with timeline ducking and -14 LUFS normalisation
   (also resolves #676).
9. Parked: moving previews, accents on Compare and share views.

## 7. Risks

- Chromium render time on long slates and summaries: a 30 s summary hold
  is 900 frames, about 30 s. Run-length rendering (static holds emit one
  frame and `-loop`) keeps it bounded; the template reports its animated
  span.
- Determinism across Chromium versions: previews are authoring-time
  assets, renders are not compared across machines, the cache key includes
  the Chromium channel version.
- The hosted container has no trims on disk by design; the preview there
  composes over the surface as today.
