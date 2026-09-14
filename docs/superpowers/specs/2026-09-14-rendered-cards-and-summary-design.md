# Rendered cards and the single-shooter stage summary (issues #973, #972)

Approved in chat 2026-09-14. No UI work: the SPA is under redesign in a
separate track (#974 tracks the UI wiring).

## Packaging

| PR | Issue | Contents |
|---|---|---|
| A | #973 | IR + card renderer + `mp4_render` cards/intro/outro + request model + new `splitsmith match export` verb |
| B | #973 | `mp4_grid` cards + `compare export` flags (flags land with the code that honours them, not before) |
| C | #972 opt 3 | Hoist of the summary declaration/data out of `compare/`, scorecard plumbing, summary hold in `mp4_render`, request field + CLI flag |
| D | #972 opt 1 | Per-stage `_summary.png` + `_summary.mov` beside the overlay MOV, opt-in |

Each PR is independently mergeable. C reuses A's still-segment mechanism;
D reuses C's still builder.

## IR (`composition.py`)

- `TitleCard` gains `info: tuple[str, ...] = ()` (optional lines under
  the text). FCPXML lowering keeps emitting Basic Title from `text` only
  and ignores `info`, so the #196 snapshot is unchanged.
- New `MatchTitle(text, info=(), duration_seconds=3.0)`. `Composition`
  gains `title_page: MatchTitle | None` and `closing: MatchTitle | None`.
- Spine order on the MP4: intro, title page, per stage (slate, stage,
  [summary hold, PR C]), closing card, outro. FCPXML/FCP7 ignore
  `MatchTitle`; the request layer records the usual "ignored by this
  renderer" anomaly.

Assumptions: the intro plays before the title page (a sting, then the
title); the title page's blurred background is stage 0's first visible
frame.

## Card renderer (`splitsmith/overlay_card.py`, new)

- Pure declaration: `card_groups(TitleCard | MatchTitle) -> tuple[Group, ...]`:
  `Role.HEADLINE` for the text, `Role.DETAIL` per info line,
  `Anchor.MIDDLE_CENTER`, centred. Same `overlay_layout` vocabulary and
  `overlay_theme` as the summary so the three read as one piece.
- `overlay_html.card_html(...)`: same building blocks as `single_html`
  but an opaque page. Full-frame slates and the title page sit on a
  blurred, dimmed first frame of the stage they precede (title page:
  stage 0) using the summary's blur/dim numbers; when the frame grab
  fails, a flat theme background. Lower-thirds rasterize to a
  transparent PNG anchored bottom-left.
- `build_card_still(...)` returns a PIL image. The rasterizer is
  injected (`overlay_raster.Rasterizer`), never launched here.

## `mp4_render.py`

- New pure `plan_timeline(composition) -> TimelinePlan`: the ordered
  spine items with durations; `TimelinePlan.duration_seconds` is the
  timeline length. `export_match` uses it for
  `MatchExportResult.duration_seconds` on the mp4 path. The fcpxml/fcp7
  figure is left as it is today (it already ignores slates; fixing that
  is not this issue).
- Every generated item is its own segment temp encoded with the existing
  `_encode_args` at sequence size and rate: stills via `-loop 1` plus
  `anullsrc` audio; intro/outro re-encoded with scale-to-fit/pad and
  `fps=` (so the mp4 path accepts a mismatched-rate intro, unlike FCPXML
  which raises). Lower-thirds go into the stage's own filter graph as an
  `overlay` with an alpha fade-out over the last 0.5 s of
  `duration_seconds`.
- Stitch: when any generated segment exists the concat step becomes
  `-c:v copy -c:a aac` (the grid's approach) so `anullsrc` and the
  trims' audio need not share a sample rate. With no cards the argv is
  byte-identical to today's.
- `render_mp4` gains `rasterizer: Rasterizer | None` and returns
  `Mp4RenderResult(output_path, duration_seconds, degradations)`. Cards
  requested with no rasterizer supplied: open one `ChromiumRasterizer`
  for the whole render, preflighted before any encode.
  `RasterizerUnavailableError` records a degradation, every card is
  skipped, the render proceeds. `export_match` folds degradations into
  `anomalies`.
- Known limit, documented not solved: a primary trim with no audio
  stream next to a card segment would break the stitch. Every primary is
  a camera trim with audio, so this does not occur in practice.

## Request model + CLI (PR A)

- `MatchExportRequest` / `MatchExportRequestData`: `title_page: bool =
  False`, `title_info: str | None = None`, `closing_card: bool = False`,
  `title_page_duration_seconds: float = 3.0`. The server builds
  `MatchTitle(text=project_name, info=(match_date, competitor_name,
  title_info))` from what `MatchProject` has; a stage `TitleCard.info`
  carries `"N rounds"` from `StageEntry.stage_rounds.expected` when
  known.
- Hoist the server's compose-block assembly into
  `ui/match_exports.stage_inputs_for_project(proj, root, stage_numbers)`;
  server and CLI both call it.
- New verb `splitsmith match export <path> --shooter SLUG --stage N...
  --format mp4|fcpxml|fcp7xml --titles slate|lower-third|none
  --title-duration --title-page/--no-title-page --title-info
  --closing-card --intro --outro --youtube-preset --output`. Accepts a
  match folder (`--shooter` required) or a legacy single-shooter folder.
  Reads existing per-stage trims/audits/overlays like `compare export`
  does; a missing trim is an error naming the stage and pointing at the
  UI's per-stage export. It never re-cuts.

## PR C (#972, option 3)

- Hoist `TileShot`, `TileStageData`, `_load_shots` into core
  `splitsmith/stage_summary_data.py` and `_cell_groups` + helpers into
  core `splitsmith/overlay_summary_cell.py` (`summary_groups`).
  `compare/overlay_data.py` and `compare/overlay_summary.py` import from
  there; names unchanged so the grid tests pass unmodified.
- `MatchStageInput` grows `scorecard`, `stage_time_seconds`,
  `stage_time_is_manual`, `stage_rounds`, filled by the hoisted
  assembler from `StageEntry`. No import from `ui/` in the reader.
- `Stage` IR gains `summary: SummaryHold | None` (`data: TileStageData`,
  `duration_seconds`). `mp4_render` appends a still segment after the
  stage: last visible frame of the effective window (a hoisted per-file
  `extract_last_frame`), blurred/dimmed with the summary's numbers,
  `summary_groups` rasterized full-frame. Same degradation path as
  cards.
- Request: `summary_hold_seconds: float = 0.0` (0 = off, as the grid);
  `--summary-hold` on the verb. Ignored by FCPXML/FCP7 with an anomaly.

## PR D (#972, option 1)

- `export_stage` gains `write_summary_card: bool = False`; when on,
  writes `<base>_summary.png` and `<base>_summary.mov` (ProRes 422,
  trim's fps/resolution, `summary_hold_seconds` long, default 3 s) next
  to the overlay MOV via PR C's still builder. `splitsmith overlay` gets
  `--summary-card`. Opt-in keeps every existing per-stage export
  byte-identical and the #684 overlay tests untouched. Not wired into
  the FCPXML; the user drops the clip on the timeline.

## Tests

- 2-stage fixture asserting concat list positions and
  `duration = footage + cards`.
- Injected rasterizer raising `RasterizerUnavailableError`: render
  succeeds, cards skipped, degradation recorded.
- FCPXML snapshot unchanged with no cards (the #196 test keeps passing
  unmodified).
- Every new test is checked against the pre-change code (delete the fix,
  watch it fail) per the review practice in CLAUDE.md.
- `scripts/render_match_frames.py`, a sibling of
  `render_grid_frames.py`, for the visual check, built on
  `tests/synthetic_media.py`.
