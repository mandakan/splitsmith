# Draw regions, slice 2: the figures (plan, as built)

Spec: `docs/superpowers/specs/2026-10-17-draw-region-design.md`, "Figures".
Branch `feat/draw-figure` from `origin/main`.

Rule: with a confirmed draw region, the stage's draw is that region's
length; without one, the first shot's time from the beep, as today. No "on
the move" flag: a draw and a movement are independent regions (decided
while building; the first draft of this plan had one).

1. **Rule owner.**
   - `events.draw_seconds(first_shot_s, regions)`, with its TS twin
     `drawSeconds` in `lib/events.ts`. Both count confirmed draws only.
   - `cases.json` `draws` (no region, a confirmed draw, a proposed draw, a
     movement beside the draw, no shots), run by both test files.
2. **Tiles.**
   - `TileStageData.draw`. Every builder carries the stage's confirmed
     regions: the CLI summary card, the match summary in `match_exports`,
     the compare grid's `overlay_data`, and `export_preview`'s match
     summary.
   - `stage_summary_figures`, `match_summary`, the compare free cell and
     `overlay_summary_cell`'s Draw element read `tile.draw`.
   - A stage without a confirmed draw draws byte for byte what it drew
     before.
3. **Payloads.**
   - `share_card.stage_figures(shots, regions=)`, whose callers (the
     project payload, the triage cells, the share card) pass the doc's
     confirmed regions.
   - Coach's `reviewFigures` uses `drawSeconds`.
4. **Docs.** The spec, CLAUDE.md, and What's new.
