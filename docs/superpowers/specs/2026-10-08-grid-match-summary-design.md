# Match summary card on the compare grid

Status: approved 2026-10-08 (conversation). Follows the single-shooter card
(`2026-10-07-match-summary-design.md`).

## What it shows

One card, its own segment, after the last stage and before the closing card.
A thin title strip across the top ("Match summary" and the match name), and
below it the grid, every shooter in their own slot (the slot order every stage
uses), over their own blurred tail frame. No ranking between shooters: the grid's
stage summary dropped its placing on purpose and this card follows it.

A tile, as bands like the stage summary hold's:

- Name row: the shooter's name, a `DQ` plate when they were DQ'd on any stage.
- **Scoring**: A, C, D, M, NS, P summed over the stages that reported each count
  (an unreported count is left out, never 0), colour-coded and plated as the
  stage hold's counts are; then "Scored N of M" when not every stage was.
- **Splits**: Avg split, Best draw, Rounds; then "Splits from N of M" when not
  every stage had splits.

The figures are `match_summary.build_match_summary` per shooter, over that
shooter's `TileStageData` on every rendered stage (a stage with none counts as
a stage with nothing, so M is the rendered stage count for everyone). Small cells
(3x3, 4x4) shrink through the stage hold's own drop order: counts go first, the
splits are never dropped.

## How it is drawn

- `overlay_summary_cell.match_summary_groups(summary, label, ...)` declares the
  tile, beside `summary_groups`; `overlay_html.grid_html` draws it, so the fit
  policy, the per-cell clip and the identity accent come with it.
- The cells sit in a geometry `rows x cols` over the composed height less the
  strip (`strip = H - rows * ((H - H // 10) // rows)`), pasted below the strip.
- Backdrop per shooter: their tile's tail frame on the last rendered stage that
  has footage of them (`extract_freeze_frames`); none at all is a black cell.
- Encoded as a card (`_encode_card`, `build_card_segment_command`: the grid's N+1
  silent tracks; through the segment cache by PNG content). No chapter (YouTube
  drops the list when one is under ten seconds). Transitions are only between
  stages, so none touches it.
- No browser: the blurred frames alone, under the render's one rasterizer
  degradation. It never requires the overlay; it reads `load_overlay_data` itself.

## Surfaces

`match_summary: bool = False` and `match_summary_seconds: float = 6.0` (0.5 to 30)
on `render_grid_mp4`, the compare grid request, the grid job, `compare export`
(`--match-summary`, `--match-summary-seconds`). The preset already carries both.
The gallery tile is shown in grid mode with its own grid thumbnail. The grid has
no rail preview for any card, so none here. The What's new entry is edited, not
duplicated.

## Tests

Group declarations (absent counts, DQ, partial coverage); a Chromium render at
2up, 2x2 and 4x4 with nothing cut off; spine order and the segment's stream
layout; the backdrop fallback for a shooter missing the last stage; request,
job and CLI wiring; `scripts/render_grid_frames.py --match-summary` for pixels.
