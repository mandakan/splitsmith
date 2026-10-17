# Draw regions, slice 1: the kind (plan)

Spec: `docs/superpowers/specs/2026-10-17-draw-region-design.md`. Branch
`feat/draw-region` from `origin/main`, carrying #1522's commit (the movement
before a late first shot), which this slice adjusts.

Global: TDD per task; `cases.json` changes on both sides or neither; every
region kind map in TS is a `Record<StageEventKind, ...>` so the typecheck
finds a missed one; ASCII copy; Black 110, ruff, `pnpm typecheck`/`lint`.

## Tasks

1. **The kind, one draw, tolerant reading.**
   - Python: `config.EventKind` and TS `StageEventKind` gain `draw`.
   - `validate_lanes` (`events.py`) and `validateLanes` (`lib/events.ts`)
     refuse a second draw even when the two do not overlap ("only one draw
     per stage").
   - `cases.json`: `lane_violations` gains `two_draws`; `lane_ok` gains
     `draw_on_the_move` (a draw overlapping a movement).
   - `events_from_doc` skips, with a warning, an entry whose `kind` this
     version does not know, instead of failing the list. Tested with a doc
     holding a `"juggle"` region beside a movement.
2. **Seeder v4.**
   - `seed_events` proposes a draw `[0, first_shot]` whenever the first shot
     is after the beep, plus the movement past `draw_movement_min_s`, whose
     default drops from 3.0 to 2.0 s (the spec: relabelling is cheap). The
     config comment is reworded.
   - Tests: a standing draw gets a draw only; a late first shot gets both;
     a pre-beep first shot gets neither; ids are stable; the coach GET
     shows both.
   - Existing seeding tests that count events: update the counts to
     include the draw, and say so in the commit.
3. **Outputs.**
   - `region_marker_label` gives `Draw 0.98`; tested.
   - `events.csv` writes the kind as-is: a test writes a draw region and
     reads the row back.
4. **Editor.**
   - `LANES` puts `draw` first (it happens first), with `LANE_LABEL` and
     `LANE_FILL` (the draw chip hue, `bg-led/30 border-led`).
   - `LaneEditor`'s `LANE_ROWS` gains its row.
   - The typecheck finds every other kind map (EventCard, EventList,
     StageStrip, chips); fill each.
   - Tests: the lane editor renders a Draw lane. The phone Kind sheet
     reads `LANES`, so it gains Draw when #1521 rebases on this; its test
     belongs there.
5. **Docs.**
   - What's new: fold #1522's line into one entry about the draw.
   - CLAUDE.md "Stage events": draw kind, one per stage, seed v4,
     tolerant reading.

Then one review pass over the branch (claims: no existing figure moves
for a stage without a draw region; an unknown kind never fails a read;
v4 re-seeds only untouched stages), and the PR, which closes #1522.
