# Draw regions, slice 2: the figures (plan)

Spec: `docs/superpowers/specs/2026-10-17-draw-region-design.md`, "Figures".
Branch `feat/draw-figure` from `origin/main`.

Rule: with a confirmed draw region, the stage's draw is that region's
length; without one, the first shot's time from the beep, as today. A
confirmed draw overlapping a confirmed movement is "on the move".

1. **Rule owners.**
   - `events.draw_seconds(first_shot_s, confirmed)` and
     `events.draw_on_the_move(confirmed)`, with TS twins `drawSeconds` /
     `drawOnTheMove` in `lib/events.ts`.
   - `cases.json` gains a `draws` list (shots, events, expect `{draw,
     moving}`) that both test files run: no draw region, a confirmed draw,
     a proposed draw (ignored), a draw on the move, and a draw that only
     touches a movement (not on the move).
2. **Tiles.**
   - `TileStageData.draw` and `.draw_moving` (properties over `shots` and
     `events`).
   - Every `TileStageData` builder loads the stage's confirmed regions
     (`load_stage_events`, or `confirmed_from_doc` where the doc is in
     hand): the CLI summary card, the match summary in `match_exports`, the
     compare grid's `overlay_data`, and `export_preview`'s match summary.
   - Consumers read `tile.draw`: `stage_summary_figures` (plus
     `figures.draw_moving`), `match_summary` rows and Best draw, the
     compare free cell, and `overlay_summary_cell`'s Draw element, whose
     caption reads "Draw, on the move" when it is.
   - A stage without a confirmed draw draws byte for byte what it drew
     before.
3. **Payloads.**
   - `share_card.stage_figures(shots, regions=())`, with its callers
     passing the doc's confirmed regions: the project payload's
     `stages[].figures`, the second payload in `server.py`, and the share
     card.
   - Coach's `coachReview.reviewFigures` uses `drawSeconds`.
4. **Docs.**
   - CLAUDE.md: the draw rule's owner, next to the stage events
     paragraph.
   - `authoring.md`'s summary data gains `draw_moving`.
   - What's new: the draw entry says the Draw figure follows the region.

Tests per task, then one review pass (claims: no figure moves without a
confirmed draw; every Draw surface agrees on one stage).
