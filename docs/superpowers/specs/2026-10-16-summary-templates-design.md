# Summary templates: the stage and match summaries as Look slots

Status: draft, 2026-10-16

## Why

The single-shooter stage summary hold and the match summary are the two
cards a Look cannot restyle: both are engine HTML (`summary_still_html`,
`match_summary_html`) rasterized to a still. A Look that wants its own
summary (the first was a personal Look's results board) has had to draw it
inside the overlay after the last shot, which covers the run's tail and
leaves the real summary hold to be switched off. This makes both summaries
template slots like the title page and the slate, with the engine card kept
as the `default` variant.

## Scope

- In: the single-shooter **stage summary hold** (`summary` slot) and the
  single-shooter **match summary** (`match_summary` slot), in the MP4
  render, the stage export's summary deliverable (`summary_card.py`), the
  export preview, `looks check` / `looks preview`, the request bodies, the
  preset, both CLIs and the Export page's Style choice.
- Out: the compare grid's hold and grid match summary (a tile layout per
  shooter; a later slice may reuse the same data contract). Hosted
  behaviour is unchanged: hosted Looks carry no templates, so hosted never
  sees a non-default summary variant.

## The rule that keeps everything else still

`default` is the engine, exactly as Classic is the overlay's `default`:
`summary_template_for(look, variant)` and `match_summary_template_for`
return `None` for `default` (and for a Look that names no such slot), and
the render takes today's path, argv, PNG and cache key unchanged. Only a
named non-default variant loads a template. The shipped `splitsmith` Look
names no summary variant, so no export changes until a Look offers one.
Pinned by tests copied from the card and overlay ones (a default summary is
the document it was; no variant loads no template; the preview key does not
move).

## Data

### Stage summary: `window.splitsmith.data`

```
card:     { slot: "summary", variant, text: <stage label>, info: [], duration_seconds }
stage:    the HUD's stage data (overlay_hud.hud_stage_data) with the beep at 0,
          so shot and region times are seconds from the beep
figures:  stage_summary_figures(tile), below
shooters: the same identities the cards get (logo, accent, club)
safe_area on an upright canvas, as cards get it
<key>:    any registered HUD data provider the template declares with
          <meta name="splitsmith-data" content="scoring,ammo,bursts">
```

`figures` is new: `stage_summary_data.stage_summary_figures(tile) ->
StageSummaryFigures` (Pydantic). It calls the same rule owners the engine
card calls (`coach.statistic_split_shots`, `events.exposed_reload_s`, the
scorecard), and `tests/test_stage_summary_figures.py` holds its numbers
against the engine card's own text on the same stages, so the two can
never disagree. (`summary_groups` keeps formatting inline: it serves three
layouts and the grid, and rewiring it would risk pixels for nothing.)

```
time_seconds, time_is_manual, hit_factor, stage_pct, points,
counts { A, C, D, M, NS, P } (only what the scorecard has),
draw, splits { best, avg, worst, n }    (coach.statistic_split_shots),
moving { avg, n }, static { avg, n }    (only with confirmed movement),
reloads { count, avg, exposed }         (only with a confirmed reload),
rounds, min_rounds, dq
```

Absent stays absent (`None`), as `TileStageData` already promises. The
providers reach the summary through the same `HudExtras` the overlay uses,
resolved by `ui.exports.hud_extras_for` and carried on the IR (below), so a
summary template reads `data.scoring.spreads` exactly as its HUD does.

### Match summary: `window.splitsmith.data`

```
card:     { slot: "match_summary", variant, text: <match title>, info: [], duration_seconds }
match:    match_summary.MatchSummary as data: title, label, rows[] (number, name,
          time_seconds, time_is_manual, hit_factor, stage_pct, draw, avg_split, dq),
          avg_split, best_draw, rounds, hits, split_stages, scored_stages, dq
shooters, safe_area as above
```

No providers in this slice: they are per stage. A later key can add them.

## Rendering

Both items render like an animated card (`card_motion` / `render_template_frames`):

- A template with `duration() > 0` animates (a count-up); one
  without is a still (`poster()` or the first frame).
- The backdrop is the stage's tail frame (the match summary: the last
  stage's), through `card_backdrop`, as cards do. The engine summary's own
  blur and dim stay on the default path only.
- The segment cache keys the clip by `template_digest` as a virtual input,
  as cards do, so a cached summary renders no frame.
- Logos: on a template variant the `summaries` logo spot is not pasted; the
  template gets the logos in `data.shooters` and draws them, as cards do.
- Failure: a template that throws, times out or is missing falls back to
  the engine card with a degradation line, never a skipped hold.

`summary_card.py` (the stage export's `<base>_summary.png` / `.mov`) takes
the same path through one shared helper, so the FCPXML deliverable and the
MP4 agree.

## IR and requests

- `composition.SummaryHold` gains `variant: str = "default"` and
  `extras: HudExtras | None = None`; `composition.MatchSummary` (the
  composition item) gains `variant`.
- Request bodies (`ui/exports_api.MatchExportRequest`,
  `ui/match_exports.MatchExportRequest`), `ExportPresetBody`, the stage
  export request and the preview request gain `summary_variant` and
  `match_summary_variant` (`None` is `default`, never `card_variant`: a
  card style such as `rise` names no summary, and falling back to it would
  add a degradation note to every export that sets it), validated by shape
  in the preset like `stage_card_variant`.
- CLI: `--summary-variant` and `--match-summary-variant` on `match export`.
- `looks.SLOT_NAMES` gains `match_summary` (`summary` is already reserved).
  `styles` may not name either: a summary's `default` is the built-in card
  by rule, and the Export page calls it Built-in, so a Look that remapped it
  would make that label false (found rendering the Style picker).
- `look_catalog` lists both slots; previews follow `preview_file`.

## Preview, check, gallery

- `export_preview.render_preview` honours `spec.variant` and `motion` for
  `summary` and `match_summary`; the key gains `variant`/`motion` only when
  not default (today's keys unchanged).
- `look_tools`: `check_folder` and `preview_look` probe summary templates on
  sample stages (the HUD samples as `TileStageData`, one with a confirmed
  reload and movement, one bare) and a sample `MatchSummary`, with the same
  findings as cards (script errors, `seek`, `poster`, fonts, text past the
  canvas, the upright safe area).
- SPA: `VARIANT_FIELD` gains `summaryHold` and `matchSummary`, so the Style
  picker shows under each when the chosen Look has variants;
  `exportPresets` and `renderOptions` carry the two fields (sent only when
  not default); `exportPreview.variantForCard` returns them.
- Docs: `docs/looks/authoring.md` gains "Summaries"; the `_starters` gain a
  `summary` starter; CLAUDE.md's rendered-cards section names the slots.

## The first template

A personal Look's results board (slot `summary`, a named variant), counting up
over `duration()`; its HUD no longer draws one after the last shot, and its
`settle()` drops back to the landing.

## Tests

- Default variant: summary still HTML, mp4 argv and preview keys unchanged
  (copies of `test_card_logos` 134, `test_mp4_render` 1502/1953/2040,
  `test_export_preview_api` 279).
- `stage_summary_figures` against the engine card's own numbers on the
  existing summary fixtures; `summary_groups` reads it (one source).
- A template variant renders through `render_template_frames`, keys by
  digest, falls back to the engine on a script error.
- Request plumbing: `api.exportBodies.test.ts` (`Required<...>`) and the
  preset round trip.

## Order

1. `stage_summary_figures` extracted, engine reads it (no pixel change).
2. Slots, resolvers, IR fields, the render path and `summary_card.py`.
3. Requests, preset, CLI, preview, `looks check` / `preview`, starter, docs.
4. SPA Style picker.
5. The personal Look's summary template; its HUD workaround removed.

What's new: one entry once a Look can offer a summary style (desktop Look
authors are the audience; no shipped Look changes).
