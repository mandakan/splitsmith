# Single-shooter overlay port -- design (issue #684)

`splitsmith export overlay` renders a standalone transparent MOV per
stage -- ProRes 4444 or hevc-alpha -- that drops onto the trimmed clip in
Final Cut as a connected clip on V2. Its renderer,
`src/splitsmith/overlay_render.py`, predates everything the compare grid
learned: it draws **every frame** with PIL and pipes raw RGBA to ffmpeg,
where the grid pre-renders roughly 30 states per stage and composites
them once.

This spec ports the single-shooter renderer onto the shared overlay
engine. It is not a mechanical port -- it is a deliberate, reviewable
change to what a single-shooter overlay looks like, and section 4 lists
every pixel that moves.

Read `docs/superpowers/plans/2026-08-07-single-shooter-overlay-port-kickoff.md`
for the background. Section 2 below corrects two of its claims.

## 1. Scope

**In:** the render port. The overlay says the same things it says today,
drawn by the shared engine, with the deltas in section 4.

**Out, deferred to follow-up issues:**

- The **stage summary**. It fights the delivery format: a transparent MOV
  never sees the footage, so it cannot blur it, and it must match the
  trim frame-for-frame, so it cannot extend the clip. A summary here
  would be a scrim over the trim's existing post-buffer tail -- related
  to the grid's, not the same thing, and an unsettled design question.
- **Scorecard plumbing.** `compare/overlay_data.py` reads
  `StageEntry.scorecard` for the grid; this path takes an `--audit` file
  and has no project handle. Data plumbing, separable from rendering,
  and only worth doing once something draws scoring.

## 2. Two corrections to the kickoff

### The "1x1 trap" is not one

The kickoff warns that resolving `CellScale` off the full frame gives
`live_primary = max(48, 1080 // 14) = 77px` against the grid's 48px at
2x2, and calls that "a real output change".

It is a change against a *grid tile*. Against today's single-shooter
overlay it is a no-op:

| | formula |
|---|---|
| `DefaultTemplate.__init__` | `big = max(48, height // 14)`, `self.pad = max(24, height // 36)` |
| `CellScale.for_cell` | `live_primary = max(48, cell_height // 14)`, `pad = max(24, cell_height // 36)` |

At 1x1 the cell is the frame, so both resolve identically. Type size and
inset do not move.

### The composition mechanism has a third option

The kickoff frames the choice as the grid's concat-demuxer-of-PNGs
(option a) versus keeping the rawvideo pipe and writing each buffer N
times (option b), and recommends (b).

Take (b), but build the runs by **run-length encoding
`build_frame_states`** rather than porting the grid's event-stepped state
machine. See section 3.

## 3. Architecture

### 3.1 Run-length encoding, not the grid's state machine

`build_frame_states` already computes, per frame, everything a sprite
depends on. Collapsing consecutive frames whose `(shots_fired,
last_split)` match yields exactly the same ~31 runs per 30-shot stage
that the grid's event-stepped machine would, with three properties the
grid's approach does not have on this path:

- **Boundaries are frame indices by construction.**
  `overlay_sprites.quantize_durations` exists to snap millisecond shot
  times onto frame edges. An RLE over frames has no sub-frame boundary to
  snap, so that class of bug is absent rather than solved.
- **`build_frame_states` and its five tests stay alive and meaningful**
  instead of becoming dead code.
- **No grid vocabulary is imported.** Reusing
  `build_overlay_states`/`write_concat_list` would mean synthesising a
  `TilePlacement(label="", row=0, col=0, present=True)` and a
  `head_pad_seconds` for a path that has neither.

Rejected: importing the grid's state machine and concat list. It shares
more lines, shoehorns tile-label keying into a single-shooter path, and
changes the ffmpeg invocation shape for no gain.

### 3.2 Units

| unit | responsibility |
|---|---|
| `overlay_render.build_frame_states` | unchanged |
| **`src/splitsmith/overlay_single.py`** (new) | run-building (RLE) and what a single-shooter frame *says*, as `Group`/`Element` declarations. Sibling of `compare/overlay_live.py`; same split of concerns, different content. |
| **`overlay_html.single_html`** (new, ~15 lines) | one canvas-sized cell as a whole document. `cell_html` already exists for this -- its docstring names "a future single-shooter port" -- but returns a fragment, so this adds the `html`/`body` sizing and transparent background the rasterizer needs. Signature takes plain ints: no `SpriteGeometry`, no `TilePlacement`. |

`.cell` is `width: 100%; height: 100%`, which today only resolves because
`grid_html` supplies a pixel-sized ancestor. `single_html` has no `.grid`
wrapper, so it must size `html, body` itself -- the same block `grid_html`
builds inline, minus the grid tracks. Prototyped and rasterized at
1920x1080 before this was written into the spec: the counter's ink lands
at x=31, y=41 against `CellScale.pad` of 30, and the split centres at
x 846-1075 on a 1920 canvas. `.cell` in a sized `<body>` resolves
correctly with no wrapper.

`single_html` carries `_fit_script()` like both siblings. The rasterizer
calls `window.__splitsmithFit && window.__splitsmithFit()` unconditionally
and the `&&` guard makes omitting it safe -- but omitting it would trade
the shrink-before-clip policy for bare `overflow: hidden` clipping, for
no gain.
| **`src/splitsmith/overlay_clock.py`** (new) | `clock_text()`, `ffmpeg_color()` and the `%{eif:...}` elapsed expression, lifted out of `compare/mp4_grid.py` verbatim. Both renderers import it. |

`overlay_render.py` keeps orchestration only: probe, codec resolution,
dimension and rate capping, ffmpeg argv, the pipe. It loses `Template`,
`DefaultTemplate`, `_split_alpha`, `_format_running_total`, `_draw` and
the `overlay_text` re-exports -- roughly 150 lines out, ~60 in.

The `Template` ABC goes with them. It was kept for a hypothetical second
template; declared `Group`s are that extensibility now, and shipping an
abstract base with no second implementation alongside a declaration
vocabulary that has one is two answers to one question.

### 3.3 The clock stays an ffmpeg filter

The running clock changes every frame and cannot be a sprite. It becomes
a `drawtext` filter, as in the grid, so no PIL text remains anywhere in
the overlay pipeline -- which is what #693 was sequenced first to achieve.

Verified before designing around it: `drawtext` paints opaque text onto a
fully transparent RGBA stream and ProRes 4444 preserves the alpha.
Measured on a 640x360 test frame -- 2293 fully opaque pixels (the text),
227575 fully transparent, the remainder antialiasing.

Extracting `overlay_clock.py` is provable rather than hopeful, but **not
by the argv fingerprint tests** -- checked rather than assumed. Both
`DEFAULT_OFF_ARGV_SHA256` (`tests/test_compare_mp4_grid_commands.py:574`)
and `ZERO_HOLD_ARGV_SHA256` (`tests/test_compare_mp4_grid_hold.py:429`)
hash commands built without an `overlay=` argument, so `_clock_filters`
is never invoked and no `drawtext` text is inside either hash.
`test_the_clock_expression_is_character_for_character_what_it_is_today`
(`tests/test_overlay_layout.py:107`) asserts only `anchor_ffmpeg_expr`'s
output, not the filter string -- its own docstring's claim that a drift
"fails them" does not hold against the current fixtures.

What actually pins the strings is the substring suite in
`tests/test_compare_mp4_grid_overlay.py`: `enable='gte(t\,1)*lt(t\,6)'`
and `enable='gte(t\,6)'` (lines 398-415), `trunc(t-1.5)` (418-440),
`:x=960+960-tw-24:y=540+24:` (443-457), `fontsize=`/`fontcolor=`/
`bordercolor=` (460-498), plus `_clock_text`'s own literal-return tests
(523-545) and an integration test that feeds the graph through a real
`ffmpeg -f null` (556-592). Those are the extraction's proof. Leave every
one of them untouched; if the extraction is correct they stay green.

**Both ends of the clock have to be built explicitly**, because
`build_frame_states` handles them in Python today and a filter graph
cannot inherit that:

- **Before the beep** `running_total` clamps to zero, so today's overlay
  draws `" 0.00"` from frame zero. The grid's `gte(t,start)` guard draws
  nothing at all before its beep. Preserve today's behaviour with a third
  filter, `enable='lt(t,start)'` drawing a literal `0.00` -- it is one
  more `drawtext` instance, and it keeps the clock consistent with the
  counter, which also reads `0/M` from frame zero.
- **After the last shot** `running_total` freezes at the stage time
  rather than running on to the end of the clip. This is the grid's
  `freeze_seconds` / `final_text` pair: a ticking filter bounded by
  `gte(t,start)*lt(t,freeze)` and a held one at `gte(t,freeze)` drawing
  `clock_text(last_shot - beep)`. `clock_text` truncates on integer
  milliseconds precisely so the held value can never read above the last
  ticked one.

Four `drawtext` instances per stage in total, against 600 per-frame PIL
draws of the same string.

### 3.4 Data flow

```
audit JSON --> shot_times_in_clip
                    |
              build_frame_states(fps, duration)      [N frames, unchanged]
                    |
              build_overlay_runs()                   [RLE -> ~31 runs]
                    |
              run_groups(run)                        [Group/Element]
                    |
              single_html(groups, w, h, scale, theme)
                    |
              Rasterizer.png()                       [~31 browser renders]
                    |
              Image.open(png).tobytes()  --repeat run.frame_count times--+
                                                                         v
   ffmpeg -f rawvideo -pix_fmt rgba -i -  -vf drawtext=<clock>  -c:v prores_ks ...
```

Total bytes piped is unchanged at `W*H*4*N`.

No PNG files, no cache directory, no concat list. The grid writes files
because the concat demuxer reads paths; this path holds bytes in memory.
Content-addressed dedup is also unnecessary here: `shots_fired`
increments at every run boundary, so two runs can never carry identical
content.

### 3.5 Layout

Three elements in the same three positions they occupy today:

| element | anchor | mechanism |
|---|---|---|
| shot counter `N/M` | `TOP_LEFT` | sprite, `Role.LIVE_PRIMARY` |
| last split `0.21s` | `BOTTOM_CENTER` | sprite, `Role.LIVE_PRIMARY`, `ColorToken.SPLIT` |
| running clock | `TOP_RIGHT` | `drawtext` positioned by `anchor_ffmpeg_expr` |

## 4. What moves on screen

Every one of these is deliberate. This is the section a reviewer checks
rendered frames against.

- **Text rendering.** CSS `text-shadow` replaces PIL's Gaussian-blurred
  shadow, and the stroke narrows from `max(2, 77 // 18)` = 4px to
  `CellScale.stroke_width` = `max(1, 1080 // 540)` = 2px at 1080p.
- **Split position.** Rises by one `pad`: today
  `y = height - th - pad * 2`, where `Anchor.BOTTOM_CENTER` insets by
  `pad`.
- **Split persistence.** The split holds until the next shot and stays
  through the tail after the last one, instead of holding 1.0s and fading
  over 0.3s. A step function has no frames between events, and the
  alternatives are worse: quantising the ramp into six alpha steps costs
  ~210 states, which at the measured 124 ms per sprite is ~26 s/stage
  against today's measured 12.78 s -- the fade is the one thing that
  would make the port a net loss.
- **Clock format.** `5.20` rather than `" 5.20"`. Both are right-anchored
  so the right edge does not move; the width-stable padding was
  compensating for a jitter that right-alignment already prevents. Past a
  minute it reads `65.20` rather than `1:05.20`.
- **Clock precision.** Inherits the grid's known, measured behaviour: the
  hundredths half of the `%{eif:...}` expression reads one hundredth low
  on ~4.6% of frames, with zero backward steps and a held final value
  that never reads below the last ticked one.

**Unchanged, and deliberately so:** the pre-beep `0.00` and the freeze at
the last shot both survive, built explicitly out of `drawtext` filters --
see section 3.3. Neither is free the way it was in Python, and both would
have been lost by copying the grid's two-filter clock as-is.

**Unchanged:** the counter still reads `0/M` from frame zero. The grid
draws nothing until a shot fires, but that rule was written for a case
this is not -- four tiles all reading `0/32` over people standing still
is noise, where on a single-shooter frame it is the only thing on screen
and it tells the viewer the stage's round count. This is why
`overlay_single` declares its own groups rather than calling
`overlay_live.panel_groups`.

## 5. Removed surface

`--font` becomes inert and is removed, along with `font_name` /
`font_path` on `render_overlay` and the `overlay_text` re-exports from
`overlay_render`. The blast radius is one file: `cli.py:1154-1156` and
`cli.py:1184`. `ui/exports.py`'s `StageExportRequest` has no font field,
the MCP `export_stage` / `export_match` tools have none, the SPA has no
font control, and neither README.md nor SPEC.md documents the flag.

The sprite's typeface comes from `overlay_html`'s unconditional
`@font-face` rules and the clock's from `theme_font_face`; both are the
bundled JetBrains Mono. The grid already settled this -- one bundled
face, every theme, chosen for cross-machine determinism -- and a flag
that changes nothing is worse than no flag.

`--max-height` and `--max-fps` **stay**, with their justification
restated rather than removed. `--max-height` still cuts both raster and
encode cost. `--max-fps` now cuts only piped bytes and encode time, not
draw count, because runs are event-shaped rather than frame-shaped. The
help text says so instead of implying they trade quality for render time.

## 6. Error handling

| condition | behaviour |
|---|---|
| audit missing / no shots with `ms_after_beep` | unchanged -- `OverlayRenderError` |
| ffmpeg binary missing / nonzero exit | unchanged |
| **no usable Chromium** | **hard fail**: `OverlayRenderError` carrying `RasterizerUnavailableError.detail` and `overlay_raster.INSTALL_HINT` |
| **ffmpeg built without `drawtext`** | clock omitted, notice logged, counter and split still render |

The two degradations differ on purpose. The grid degrades a missing
browser to clock-only because `--overlay` is one option on a render still
worth having; here the overlay MOV *is* the deliverable, and a clock-only
MOV looks like a success the user would only discover was empty after
dropping it on V2 in Final Cut. `ui/exports.py` already turns
`OverlayRenderError` into a visible skip reason. A missing `drawtext`
loses only the clock and leaves a file worth having, so it degrades --
reusing `runtime.ffmpeg_capabilities` and mirroring
`mp4_grid._drawtext_degradation`.

Falling back to PIL when no browser is available was considered and
rejected. `overlay_raster`'s own rule: "It must never fall back to a
second rendering engine -- maintaining two is what this amendment exists
to stop."

`render_overlay` gains `rasterizer: Rasterizer | None = None`, launching
and closing one `ChromiumRasterizer` per call when not supplied. Both
existing callers (`cli.py:1176`, `ui/exports.py:406`) render one stage at
a time, so per-call launch costs one browser start per stage; the
parameter exists so a future batching caller, and every unit test, can
supply their own.

## 7. Testing

### 7.1 The 31 pinned tests

The issue's rule is that an edit to any of `tests/test_overlay_render.py`'s
31 tests is a finding to explain, not a chore. Every one is accounted for:

**6 deleted with the mechanism they describe.**
`test_split_alpha_holds_then_fades_then_zero` (117-129), the three
`test_default_template_*` tests (135-149, 152-189, 241-258), and both
`test_format_running_total_*` tests (264-266, 269-270). These exercise
PIL drawing, the fade, and a formatter that no longer exists.

**5 moved verbatim to `tests/test_overlay_text.py`**, not deleted. An
earlier draft of this spec claimed they only exercise re-exports and that
`test_overlay_text.py` already covers the same ground. That was checked
and is **false**: only `test_load_font_unknown_name_raises` has a true
equivalent there. The other four each cover something that file does not
-- the known-preset-with-missing-files fallback
(`test_load_font_known_name_falls_back_when_missing`), the contents of
`available_font_names()`, the full PIL-bitmap fallback and its warning
log (`test_load_font_pil_default_fallback_warns`), and the absence of any
WARNING on the happy path (`test_load_font_bundled_emits_debug_only`).
They test `overlay_text` functions and belong in `overlay_text`'s file;
moving them there loses nothing and is the only change they need, since
`overlay_render` will no longer re-export the names they reach through.

**10 gain one `rasterizer=` argument** and nothing else -- every test
that drives `render_overlay` far enough to reach the render loop: codec
(3), `--max-height` (2), `--max-fps` (2), the pipe test, the
ffmpeg-nonzero test, and the real-ProRes integration test, which gets a
real rasterizer rather than a fake. Seven of these go through the
`_capture_render_cmd` helper (475-526), so the argument is added once
there and in three direct callers. No assertion weakens. In particular
`assert captured["bytes"] == 30 * 320 * 180 * 4` survives untouched,
which is the frame-count contract this port must not break.

**10 pass completely untouched**: the five `build_frame_states` tests,
`test_capped_frame_rate_keeps_rational_for_29_97`,
`test_scaled_dimensions_forces_even`, both early-raise paths
(`test_render_overlay_raises_when_audit_missing`,
`test_render_overlay_raises_when_no_shots`), and
`test_codec_unknown_raises` -- which raises in `_resolve_codec` before
any rasterizer is needed.

6 + 5 + 10 + 10 = 31.

### 7.1b A sixth file the earlier draft missed

`tests/test_overlay_theme.py` drives `overlay_render.DefaultTemplate`
directly at five call sites (94, 141, 171, 179, 193) to assert that a
theme's palette reaches the drawn pixels. Deleting `DefaultTemplate`
breaks them, and they are not covered by the accounting above because
they live in a different file.

They must be **re-expressed against the new renderer, not deleted** --
they assert a property that still matters and that nothing else checks:
that `--theme clean` and `--theme splitsmith` actually produce different
ink. Re-express each as an assertion over `single_html`'s emitted CSS
(the established style in `tests/test_overlay_html.py` -- the `_rule(html,
selector)` helper plus a substring check, e.g. `f"color: rgb({r},{g},{b})"`),
which is where the theme now reaches the picture. `test_overlay_theme.py`
also has `test_available_font_names_includes_bundled_presets` (198-202)
reaching through the `overlay_render` re-export; repoint it at
`overlay_text`.

### 7.2 New tests

- **Runs:** run count equals distinct shot events + 1; run lengths sum to
  the frame count; every boundary is an exact frame index; the split
  survives to the final frame; the counter reads `0/M` before the first
  shot.
- **Declaration:** `run_groups` emits the counter at `TOP_LEFT` and the
  split at `BOTTOM_CENTER` in `ColorToken.SPLIT`, and emits no split
  before the second shot.
- **`single_html`:** canvas-sized, transparent background, carries the
  fit script, contains the counter text.
- **Clock ends:** a frame before the beep reads `0.00`; a frame after the
  last shot reads the same value as the frame at the last shot. Both
  assert on rendered pixels, not on the filter string -- the argv can be
  right while the `enable` windows overlap and draw two numbers over each
  other, which is a failure mode this project has already met on ffmpeg
  6.1.1.
- **Integration:** render real ProRes 4444 through a real Chromium and a
  real ffmpeg, then assert opaque pixels top-left and top-right at a
  known frame index. Reading the actual output, not the call -- a fix can
  be real and still invisible.

Every new test gets the mutation drill: revert the behaviour it claims to
cover and confirm it fails. A test that passes against the pre-change
code is not evidence.

### 7.3 The frame tool comes first

`scripts/render_overlay_frames.py` is the first deliverable -- the
missing single-shooter counterpart to `scripts/render_grid_frames.py`.
It builds its own media via `tests/synthetic_media.py`, renders the
overlay, composites it over the trim the way Final Cut would, and drops
frames at named moments (`pre-beep`, `first-shot`, `mid-action`,
`after-last-shot`, `tail-end`).

Compositing the alpha MOV over the trim is not new ground:
`src/splitsmith/mp4_render.py:454-457` already does exactly this, and the
tool should reuse its filter shape rather than invent one.

```
[{overlay_index}:v]setpts=PTS-STARTPTS[overlay_v]
[{base_label}][overlay_v]overlay=0:0[withov]
```

Frame extraction follows `render_grid_frames.py:218-238`: `select=eq(n,N)`
with `-fps_mode passthrough`, never a timestamp seek. Note that
`synthetic_media` builds at 30000/1001, not a whole rate -- selecting by
frame index is exact regardless, but any moment expressed in seconds must
be converted with `round(t * fps)` once, not seeked to.

On this project every overlay defect that mattered was found by rendering
and measuring, none by reading. The before/after procedure:

1. Render the **control** first -- `main` against `main`. During #693 a
   7% pixel difference on a frame that draws nothing looked like a
   regression and was x264 reallocating bits across a GOP whose other
   frames changed. Max delta 40, zero pixels above 60: all sub-noise.
2. Render the branch, diff against the control, and publish both as an
   Artifact. gaspode is headless; local files do not reach the user.

### 7.4 Performance

The kickoff's ~36s -> ~9s per stage is arithmetic over #693's per-sprite
figures, not a timing of this path. Measured directly on the dev host, a
20s/30-shot stage at 1920x1080:

| | draws | measured |
|---|---|---|
| today, per-frame PIL | 600 | **12.78 s** (21.3 ms/frame) |
| ported, 31 sprites | 31 | **4.33 s** (124 ms median/sprite + 0.40 s browser startup) |

Roughly 2.9x, against the kickoff's predicted 4x. Both absolute figures
are far below its estimate, which assumed 60 ms per PIL draw where this
host does 21.3 ms and ~300 ms per sprite where a 1x1 cell carrying two
short strings costs 124 ms. Neither number includes encode, which is
unchanged.

Re-measure at 4K before quoting these anywhere else -- a sprite's cost
scales with canvas area and PIL's does too, but not necessarily at the
same rate.

## 8. Sequencing

1. `scripts/render_overlay_frames.py` plus the control render on `main`.
2. `overlay_clock.py` extraction, with the grid's argv fingerprint tests
   green as the proof.
3. `overlay_html.single_html`.
4. `overlay_single.py` -- runs and declarations, with unit tests.
5. `overlay_render.py` rewrite: delete the PIL template, wire the
   rasterizer and the `drawtext` clock, handle both degradations.
6. CLI and `ui/exports.py`: remove `--font`, restate the cap flags'
   help, surface the no-browser error.
7. Before/after frames, measured timing, and the whole-branch seam pass.
