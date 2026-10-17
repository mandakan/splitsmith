# Draw regions (design)

Date: 2026-10-17. Supersedes PR #1522 (a movement proposal before a late
first shot), whose rule this folds in.

## Why

Little Trouble stage 2 starts with a movement, and the draw happens during
it. Today the only region kinds are movement, reload and activation, so the
draw cannot be marked apart from the move. The stage's Draw figure is
always the first shot's time from the beep, which reads as a 2.3 s draw for
what was a move plus a ~1 s draw.

## What a draw region is

A fourth region kind, `draw`: from when the hands start the draw to the
first shot. One per stage at most (a second one is refused like any
same-lane overlap would be, and the editor offers no second).

- It sits on its own lane and may overlap a movement: that is a draw on the
  move.
- Its end is the first shot's time. The editor snaps the end to the first
  shot; the start is the user's.
- Confirmed (`source == "manual"`) like every region before any output
  reads it.

## Proposals (the seeder)

Seed version 4. Every stage with a first shot after the beep gets:

- a **draw** proposal from the beep to the first shot;
- a **movement** proposal over the same span, inset from the first shot
  (`MOVEMENT_SEED_INSET_S`), when the first shot comes more than
  `draw_movement_min_s` after the beep. That setting is in
  `CoachAutoClassifyConfig`, default **2.0 s**.

The user shrinks the draw's start to where the hands moved, and keeps,
shortens or deletes the movement. A wrong guess either way is one tap to
fix, which is why the cutoff can sit at 2.0 s even though 47% of the
corpus's stages pass it. (The corpus has 265 audited stages; the median
first shot is 1.94 s.)

Re-seeding follows the existing rule. A stage whose regions are all
untouched proposals from an earlier seeder is seeded again; a stage with a
kept, edited or deleted region is left alone.

## Figures

- **Draw.** With a confirmed draw region, the stage's draw is that region's
  length. Without one, it is what it is today: the first shot's time from
  the beep. The rule has one owner, `events.draw_seconds(first_shot,
  confirmed)`, with its TypeScript twin in `lib/events.ts`, held to the
  shared `tests/fixtures/events/cases.json`. Every place that computes a
  draw today calls it:
  - `stage_summary_data`'s figures (the summary card and the summary
    template's `data.figures.draw`);
  - `match_summary` (Best draw, and the per-stage rows);
  - the share card's stage figures;
  - the project payload's `stages[].figures`, which feeds Splits, the stage
    page and Compare;
  - the compare grid's cells.
- **Draw on the move.** A confirmed draw that overlaps a confirmed movement
  is "on the move". It is derived, never stored: `figures.draw_moving`, a
  boolean, which the summary card shows as a note under Draw.
- Nothing else changes. The split statistics, the time budget and the
  interval classes do not read regions, as now.

## Where the kind reaches

- **Model.** `StageEvent.kind` gains `draw` (`config.py`; `StageEventKind`
  in `lib/api.ts`).
- **Tolerant reading** (it must ship in the same release). An events list
  with a kind this version does not know skips that region instead of
  failing the list: `events_from_doc`, and its TS reader if it has one.
  Without this, an install one version behind fails the stage's coach read
  (a 422) on a doc that holds a draw. Older installs already in the field
  cannot be fixed. They meet a draw only by pulling a hosted-native match
  someone edited on a newer version, and hosted and the desktop update
  together on a release.
- **Editor.** `laneDrag.LANES`, `LANE_LABEL`, `LANE_FILL` and the
  LaneEditor's rows; the phone's Kind sheet; `EventCard`, `EventList` and
  `StageStrip`. The colour is the draw chip tick the interval class already
  uses.
- **Outputs** (confirmed regions only, as now):
  - `region_marker_label` gives "Draw 0.98" in the FCPXML and FCP7 XML;
  - `<base>_events.csv` carries the kind as it carries the others;
  - the HUD contract's `data.stage.events` already passes every kind. The
    five shipped templates and `_shared/hud.js` draw a draw band in the
    theme's `draw` colour: an optional `look.json` palette key, defaulting
    to the shipped draw hue. Classic and the grid draw no regions, as now.
- **What's new.** One entry: "Mark the draw, even on the move".

## Slices

1. **The kind.** The model, tolerant reading, the editor (desktop and
   phone), the seeder (v4: draw always, movement past 2.0 s), the markers
   and the CSV. PR #1522 is closed in favour of this.
2. **The figures.** `draw_seconds` and `draw_moving`, every consumer above,
   and the summary card's note.
3. **The overlay.** The HUD band colour and `_shared/hud.js`.

Each slice ships with its tests and a frame check where pixels change.

## Open

- Whether a draw is allowed on a stage that starts with the gun in hand
  (no draw at all). The editor allows deleting the proposal; a stage
  without a draw region keeps today's figure.
