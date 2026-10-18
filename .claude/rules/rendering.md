---
paths:
  - "src/splitsmith/mp4_render.py"
  - "src/splitsmith/composition.py"
  - "src/splitsmith/segment_cache.py"
  - "src/splitsmith/overlay_*.py"
  - "src/splitsmith/summary_*.py"
  - "src/splitsmith/stage_summary_data.py"
  - "src/splitsmith/match_summary.py"
  - "src/splitsmith/logo_spots.py"
  - "src/splitsmith/thumbnail_card.py"
  - "src/splitsmith/look_motion.py"
  - "src/splitsmith/look_sting.py"
  - "src/splitsmith/export_preview.py"
  - "src/splitsmith/youtube_sidecar.py"
  - "src/splitsmith/compare/**"
  - "src/splitsmith/ui/match_exports.py"
  - "src/splitsmith/ui/exports*.py"
  - "src/splitsmith/ui/export_preview_api.py"
  - "scripts/render_*.py"
---

# MP4 rendering: segments, cache, cards, transitions

How the single-shooter (`mp4_render`) and grid (`compare/mp4_grid`) MP4
renderers build a video. Look templates are in `looks.md`, upright layouts in
`upright.md`, shooter identity and logos in `identity.md`.

**Check pixels, not argv.** `scripts/render_match_frames.py`,
`render_grid_frames.py` and `render_overlay_frames.py` (with their card,
`--transition` and `--identity-demo` flags) are the visual checks. Run them
against main for any change that claims "default frames unchanged". A green
argv test proves nothing about pixels.

## Spine and segments

- Every generated card (title page, stage slate, closing card, summary hold,
  match summary) is **its own segment** on the spine, encoded exactly like a
  stage so the final stitch stays a video stream copy. The grid's card segment
  carries the grid's full N+1 audio layout (`build_card_segment_command`).
- The single-shooter stitch re-encodes audio only when a generated segment is
  present, so a zero-card render's argv is unchanged.
- A lower third is the one card that is not a segment: it rides the stage's own
  filter graph (`overlay_card.lower_third_filters`), upstream of the grid's
  hold `concat`, so it never reaches a summary frame.
- Backdrop grabs differ by end, deliberately: a *head* grab takes the first
  frame (`-frames:v 1`); a *tail* grab reads a 0.5 s window and keeps the last
  decoded frame, because a seek to the last timestamp can come back empty. The
  tail technique used for a head lands 0.5 s into the stage.
- The stage card's round count comes from `project.json` alone
  (`overlay_data.load_expected_rounds`), never the overlay's data, so
  `--titles slate` works without `--overlay`.
- The summary's declaration lives in core (`overlay_summary_cell`,
  `stage_summary_data`); `compare/overlay_summary` rebinds the old private
  names because its tests monkeypatch them.
- **Runner hooks.** Card, summary, edge and boundary encodes go through their
  own hooks (`card_runner`, `still_runner`, `boundary_runner`), never the
  progress `runner` both CLIs count for "stage N of M". The grid's progress is
  `GridRenderStep` through `progress=` (one per stage, encoded or reused, then
  the stitch); never count `runner` calls, a reused stage makes none.
  Single-shooter progress is `RenderStep`.

## Segment cache

- `segment_cache` (`<cache_dir>/render-segments`, LRU past
  `OutputConfig.render_cache_gb`) keys each segment on its ffmpeg argv with the
  output path factored out, inputs outside `work_dir` by path/size/mtime,
  per-render card PNGs by content, and the ffmpeg binary's identity.
- **Anything that changes a segment must reach its argv or one of its input
  files.** State reaching ffmpeg another way (an env var, a file named inside a
  filter string) is served stale. Bump `segment_cache.KEY_VERSION` when the
  recipe changes.
- Template-drawn clips (animated cards, stings) are keyed by
  `look_template.template_digest` through `SegmentCache.key(virtual_inputs=...)`
  and their frames are rendered in the encode's `prepare` step, so a cache hit
  renders no frame.
- The grid shares the cache (`render_grid_mp4(segment_cache=)`; `.mov` segments
  keep their suffix). It renders in a fresh temp dir, so the key writes the work
  dir as `<work>` in tokens and in small work files (the sprite concat list).
  Files ffmpeg reads with no token of their own (sprites the list names, the
  clock's `drawtext` font) go in as `extra_inputs` through `_overlay_inputs`;
  a new indirect input goes there.
- Job and CLI get it through `match_exports.render_segment_cache`. Tests run
  with `SPLITSMITH_RENDER_CACHE=0` (conftest); a cache test passes its own.

## Live overlay

- A Look's `overlay` slot names HUD templates; `default` is always Classic (the
  engine path: run-length PNGs and the `drawtext` clock) and a manifest cannot
  name a file for it. Classic's argv and pixels never change for template work.
- `render_overlay(variant=...)` sends a template through `overlay_hud_render`.
  The template draws clock, counter and split per frame via `seek(t)` and
  declares `settle()`; `overlay_hud.hud_frame_plan` renders only beep to
  `last shot + settle` and holds a frame either side.
- **Data is computed in `overlay_hud`, never in a template** (splits, coach
  classes, speed tiers). Shot 1's split is the Draw on every overlay.
- The MOV is cached by template digest and left untouched on a hit (the MP4
  keys on its mtime). A template failure or unknown variant draws Classic and
  lands in `degraded`; no browser is `OverlayRenderError`.
- **Overlay reuse has one rule**, `ui/exports.overlay_record_matches`, used by
  the match export job, the MCP tool and `splitsmith match export`. The
  `<base>_overlay.json` record carries: the audit revision
  (`overlay_audit_revision`); for template styles `template`
  (`overlay_hud.overlay_template_identity`: template bytes + `shared_digest`);
  for every style `theme` (`overlay_theme_identity`: theme tokens plus both face
  ids, an own face by file name only); `stage_data` (`STAGE_DATA_VERSION`) and
  each provider's `hud_data` version; `layout: upright-safe-1` on upright;
  `breakdown: false` when drawn without Breakdown. All are read *before* the
  render; a null `template` or `theme` never matches; a legacy record never
  matches. A failed redraw drops that stage's overlay with an anomaly, never
  reuses the stale one. The CLI cannot redraw, so it leaves a stale overlay out
  with a note.
- `tests/test_hud_data_pins.py` pins `data.stage` and provider outputs: a
  changed output needs a bumped version, or exports keep stale data.

## Summaries

- Stage summary hold and match summary are Look slots (`summary`,
  `match_summary`), but `default` is the engine card and never names a file
  (`looks.summary_template_for` answers `None`), so default renders are
  unchanged. A named variant goes through `summary_template` with
  `data.figures` (`stage_summary_data.stage_summary_figures`, pinned to the
  engine text by `tests/test_stage_summary_figures.py`), `data.stage`
  (`hud_stage_data`, beep at 0), declared HUD providers
  (`SummaryHold.extras`) or `data.match`. A failing template draws the engine
  card plus a degradation. The stage export's summary file and the previews hold
  it at `poster()`. `summary_variant` / `match_summary_variant` default to
  `default`, never to `card_variant`. Grid holds are engine only.
- Match summary (`match_summary.build_match_summary`) reads the same
  `TileStageData` and rules as the stage hold (`statistic_splits`; Draw from
  `events.draw_seconds`). **It never sums stage times and never shows a
  match %.** It sits after the last stage's summary, before the closing card,
  drawn by `build_match_summary_still` over the last stage's blurred tail frame.
  **It adds no chapter**: YouTube drops every chapter when one is under 10 s.
  Preview: `export_preview.match_summary_for`, keyed by `summary_digest`.
- Grid match summary: `GridCardItem` kind `match_summary`
  (`render_grid_mp4(match_summary_seconds=)`, 0 is off), tiles from
  `match_summary_groups`, each over that shooter's tail frame on the last stage
  with their footage (`extract_match_summary_freezes`). Reads
  `load_overlay_data` itself; no ranking between shooters. The rail preview
  cannot draw it (the route previews one shooter), so the pane shows its gallery
  thumbnail.
- `fit_columns` is set only by the summary stills (`build_summary_still`,
  `build_hold_still`, `build_match_summary_grid_still`) on `single_html` /
  `grid_html`. fit.js `fitColumns` shrinks the band until no grid column's text
  overflows its column or comes within 0.6 em (the caption size) of the next
  column overlapping it vertically, measured on the row's last baseline. It
  floors at the caption size; past that, and only when a *figure* still
  collides (`fitColumnsPastFloor`), rows wrap to half their columns, then go one
  line without captions, then both, never below the floor. A caption that merely
  wrapped does not trigger it. The live race never opts in (its rows change
  text per frame).
- Static / Moving split rows need a single-shooter landscape/square cell
  >= `_SPLIT_ROWS_MIN_CELL_HEIGHT` or an upright card; grid holds pass
  `split_rows=False` but keep the reload row.
- The single-shooter match summary (`match_summary_html`, no fit.js) sizes by
  canvas height on canvases at least `SIZE_ASPECT` (6:5) wide, else as a 6:5
  card of its width.
- A `fit.js` change reaches the summary PNG by content and the preview through
  `shared_digest`; any other change to what a preview draws needs
  `PREVIEW_REVISION` bumped.

## Logo spots, thumbnail, watermark, credit

- `logo_spots` (Export page Logos row `lib/logoPlan`, `--logos`, every request
  body, preset, preview). `polished` (`wipe` + `summaries` + `thumbnail`) is the
  request default; **the renderers' default is empty**, so a caller that says
  nothing draws what it always drew. A stored preset drops an unknown spot; a
  request body refuses one.
- `wipe` hands the sting `data.brand` (`look_brand.brand_mark_json`: the Look's
  brand, else `Composition.brand` / the grid's `brand`, the account's when
  `account_brand` is on); the shipped wipe carries it over the shooter's logo.
- `summaries` pastes the shooter's logo top right with Pillow
  (`logo_spots.paste_logo`): no browser, reaching the cache through the still's
  bytes; on the grid in each shooter's own tile. At any aspect the summaries lay
  text out around it: `build_summary_still`, `build_match_summary_still(logo=)`
  and the grid stills compute `logo_spots.logo_box` first and hand it to the
  document (`--logo-room`, `overlay_html.logo_room_rule`), so a name ends before
  the logo. No logo, no change to the document. `paste_logo` and
  `watermark_image` cap at 1x; templates use `object-fit: scale-down`.
- `thumbnail` (`thumbnail_card`): the Look's `thumbnail` slot over the first
  stage's first-shot frame from the stage's own clip
  (`youtube_sidecar.action_frame_source`, so no HUD burnt in). No browser or a
  failed template is the plain frame plus an anomaly; **a thumbnail never fails
  an export**.
- `watermark` (only in `everything`, single shooter only, since the grid's tiles
  have a clock and counter in both top corners): your brand
  (`look_brand.brand_logo_path`, the same rule as the wipe's) over every stage
  segment **and its transition edges**, through one more looped input on
  `_build_stage_command`; `_prepare_watermark` writes it once into the work dir
  so the cache keys it by content. `Composition.watermark_corner` is top right
  when the request's HUD is top left. A new edge path must carry it too.
- `end-screen` closing style keeps the right clear for YouTube elements and
  holds at least `composition.END_SCREEN_SECONDS`.
- "Made with splitsmith": `MatchTitle.credit`, `data.credit` on the `closing`
  slot only; `made_with` on every request body, preset, `CardOptions`,
  `MatchExportRequestData`, preview and both CLIs (`--no-made-with`). It joins
  the preview key only on a closing card that draws it.

## Transitions

- `plan_timeline` turns `Composition.transitions` into
  `TimelinePlan.boundaries`. A transition of d seconds is a crossfade of length
  d **centred on the cut**, so timeline, chapters and duration keep their
  length. Each neighbour gives up d/2 (`head_cut_seconds` /
  `tail_cut_seconds`); the boundary is `xfade` + `acrossfade` over two *edge*
  renders from the item's own builder (`_edge_plan`), holding the edge's
  last/first frame where the trim has no handle (so default pads over 5 s trim
  buffers still get a transition). A card's handle is its own frame; an animated
  card's head edge delays its clip and its tail edge offsets it, cloning the
  last frame before the skip so an offset past the animation still shows the
  card.
- **The fit check reports, never clamps**: d/2 must fit the pad (beep and last
  shot stay out of the fade) and a card must be at least d long; a miss or a
  failed edge is a cut plus a degradation.
- The driver prepares each item once, decides a boundary *before* encoding the
  item that opens it, and keys the boundary by its edges' cache keys, not their
  files (the LRU touch re-dates files).
- A lower third the head edge showed in full is dropped from the trimmed stage
  (`_trimmed_lower_third`; a looped PNG with `-t 0` runs forever). One that
  spans a boundary continues (`delay_seconds=` / `skip_seconds=`), never
  restarts.
- `composition.XFADE_FAMILIES` is the one list of kinds: `XFADE_KINDS`, request
  validation, CLI help and `GET /api/looks`'s `transitions` derive from it. A
  new transition is one family entry plus
  `render_look_thumbnails.py --look-previews`; the SPA has no list of its own.
  `tests/test_xfade_kinds_integration.py` pins every kind against the ffmpeg on
  PATH (CI's FFmpeg 6.1 is the oldest supported). The two FCP effects map
  through `xfade_name` in the MP4; FCPXML substitutes `zoom` plus an anomaly.
- Grid: `plan_grid_spine` (a stage's segment is action plus hold),
  `narrow_grid_plan` (rebuilds every tile's seek and lead pad from the cut head
  pad, the beep staying on it; the overlay plan is built from the narrowed plan
  so clocks and sprites move with it; a tail cut comes from the hold first),
  `grid_edge_plan` (a tile with no footage in the window becomes filler; a tail
  edge inside the hold is a still of it), `build_boundary_segment_command`
  (crossfades video and each of the N+1 tracks by stream index).
- **Stings** (`sting:<name>`, a variant of the Look's `transition` slot,
  resolved by `looks.sting_template_for` with no fallback to another variant).
  `TransitionKind` is an open `str`, so `composition.validate_transition_kind`
  is the one grammar check: request bodies, `ExportPresetBody`, both CLIs and
  `Transition.__post_init__` run it, so no unchecked text reaches
  `xfade=transition=`. Each driver decides a sting while deciding the boundary
  (`sting_for_boundary`); `look_sting.sting_motion` renders the template with
  `data.transition` and `data.shooters`, and the clip is laid over the
  boundary's fade (`sting_overlay_filters`) for the whole segment from its first
  frame, whatever handle the edges had. The template's `window.duration()`
  returns the transition's length, so the renderer samples exactly the
  boundary. A missing sting or no browser is a fade plus a degradation; frames
  that fail are a cut like any failed boundary. Without a sting every argv is
  unchanged. The shipped wipe carries a logo only when the shooters have
  exactly one distinct logo between them (each shooter's own; nothing stands in
  for one), else the next item's name.
