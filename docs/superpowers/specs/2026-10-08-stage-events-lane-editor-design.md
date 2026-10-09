# Stage events and the lane editor

Date: 2026-10-08
Status: approved design, awaiting implementation plan
Wireframe: https://art.urdr.dev/lane-editor-wireframe (private)

## Why

Splitsmith classifies every gap between two shots with one word
(`interval_class`: `first_shot / split / transition / movement / reload /
activation`). That partition is what the time budget sums and what the
split statistics filter on, and it stays. It cannot say two things a
coach needs:

1. **Where inside a gap the reload was.** A 2.8 s gap that is "move
   1.2 s, reload overlapping 1.0 s, settle 0.6 s" is one word. The reload
   itself -- hand off the grip to gun back on target -- is unmeasured,
   and whether it was hidden inside the movement or stuck out past it is
   the number nobody else gives.
2. **Movement that spans shots.** Shooting on the move puts splits inside
   a movement. The interval model has no way to mark the movement at all
   without mislabelling the splits.

This spec adds **stage events**: regions on the stage's timeline, in
lanes per kind, edited on the Coach page under the video, consumed by the
statistics, the renderers and the exports.

## Decisions taken during brainstorming

- Regions are a per-stage list on the audit doc, independent of shots
  (not a sub-span on the shot dict, not shot-anchored).
- A reload region's handles mean **hand leaves the grip -> gun back on
  target**: the full manipulation cost. The mechanical reload (mag out ->
  mag seated) is not modelled.
- Regions **hint** the auto-classifier for one case only; they never own
  a class. A split fired inside a movement region stays `split` and feeds
  the split statistics, tagged `moving`.
- The time budget keeps its gap partition. A region-based budget is a
  possible later view, not part of this work.
- The Coach player uses the scrub rendition through `useScrubSource`; the
  existing full-resolution switch is exposed on the lane editor too, so
  deciding later is deleting a menu entry.
- Tier 1 (a strip per gap) is skipped: it cannot express movement across
  shots. The first cut is a minimal lane editor.

## Model

### `StageEvent` (`config.py`, next to `Shot`)

```
id:     str                     # "evt-<n>", unique per stage, never reused
kind:   Literal["movement", "reload", "activation"]
start:  float                   # seconds from beep, like time_from_beep
end:    float                   # > start
source: IntervalClassSource     # "auto" | "manual"
note:   str | None = None
```

Validator: `end > start`. Lane rule (validated by `events.validate_lanes`,
not by the model, because it is a property of the list): two events of
the same kind never overlap; across kinds anything goes. A clamped edge
is the editor's behaviour, a rejected PUT is the server's.

### On the audit doc

The audit JSON is dict-shaped (`shots` is a list of dicts; `coach.py`
reads and writes them by field name). Two new top-level keys:

- `events`: list of `StageEvent`-shaped dicts, default `[]`. Every
  existing doc loads unchanged.
- `events_seeded`: bool, default false. Set when the seeder has run once
  for this stage, so deleting every proposal does not resurrect them.

Ids follow the `cand-<n>` rule (#842): the counter only grows, an id is
never reassigned, so the event log can reference an event after it is
gone. The counter is the max id seen in the list plus one; no separate
field.

Nothing new in `state_docs`: the audit doc is already a pullable kind,
versions through `audit_revision`, and every writer goes through
`_audit_rmw()`. A new `doc_kind` would need the sync allowlist in two
places and a merge rule; this needs none of it.

### Derived, never stored

- Per shot: `moving: bool` -- the shot's `time_from_beep` falls inside a
  movement region (inclusive at both ends).
- Per reload region: `ReloadFigure {duration, moving, overhang}` --
  `moving` when any movement region overlaps it; `overhang = reload.end -
  movement.end` against the overlapping movement with the latest end,
  `None` when standing. Positive overhang is time the reload cost;
  zero or negative means it was hidden in the movement.
- Per stage: `StageEventSummary {movement_s, moving_shots, reloads,
  reload_avg_s, overhang_s}` where `overhang_s` is the sum of positive
  overhangs.
- Capacity check: in a division with a capacity, more than `capacity + 1`
  shots between two reload regions (or before the first) is impossible;
  the summary carries `capacity_warning: str | None` ("17 shots without a
  reload") for the Coach card.

### Coupling to the interval classifier

`classify_intervals_in_dicts` and `classify_intervals_in_models` gain an
`events` argument. One rule: a gap whose auto-class would be `movement`
(gap > `transition_max_s`) and which overlaps a reload region
auto-classes `reload`. Manual classes still win, as today.
`splitsmith match reclassify` passes the stored events, so a manual
reload region keeps its gap on `reload` through a threshold change.

## Pure modules

### `splitsmith/events.py`

No I/O; the Python twin of `lib/events.ts`. Both load the same fixture
JSON under `tests/fixtures/events/` and the test files mirror each other
case for case.

- `validate_lanes(events) -> None` (raises `ValueError` naming the pair).
- `next_event_id(events) -> str`.
- `shot_is_moving(time_from_beep, events) -> bool`.
- `reload_figures(events) -> list[ReloadFigure]`.
- `stage_event_summary(shots, events, capacity) -> StageEventSummary`.
- `seed_events(shots, config, capacity) -> list[StageEvent]` (below).
- `capacity_for(division: str | None) -> int | None`.

### Capacity table

The division string as SSI spells it already carries the power factor
where a division allows both ("Classic Major", "Classic Minor"), and
`division.competitor_division` resolves it without the network. The
table is keyed on that string, exact match after whitespace
normalisation; an unknown string gives `None` and no capacity seed.

| Division string      | capacity |
|----------------------|----------|
| Production           | 15       |
| Production Optics    | 15       |
| Classic Minor        | 10       |
| Classic Major        | 8        |
| Revolver Minor       | 8        |
| Revolver Major       | 6        |
| Open, Standard (any) | none     |

The table is data in `config.py` (`DivisionCapacityConfig`, overridable
through `SPLITSMITH_CONFIG` like the classifier thresholds) because rule
books change and a user may shoot a regional variant.

### Seeding rule

Runs once per stage (`events_seeded`), only for `reload`. Movement is
never seeded: a gap says nothing about whether the shooter moved.

Shooters commonly start with one round chambered on top of a full
magazine, and a reload with retention or a chambered round restores that
state, so `capacity + 1` is the **latest possible** reload position
everywhere and never over-constrains. It is a bound, not a count.

```
hinted(gap)  := gap > reload_hint_min_s          (2.50 s default)
window       := gaps after shots [first .. first + capacity]   # capacity + 1 shots
repeat while shots remain:
    if capacity is None:
        seed every hinted gap; stop
    required := a shot exists at index first + capacity + 1
    pick in window: the first hinted gap, else (only if required) the longest gap
    if nothing picked: stop
    seed it (source auto, spanning the whole gap)
    first := the shot after the seeded gap
```

The `required` guard keeps a stage that fits in one magazine (a 14-shot
Production Optics stage) free of a capacity proposal: the longest gap is
seeded only when the shots run past the bound. With no capacity the hint
alone decides, as it does today for the badge. A reset re-detection
drops the `auto` regions and `events_seeded` (manual regions stay), so a
stage with no surviving manual region is seeded again over the new
shots.
A seed spans the whole gap; the user tightens the handles. A touched
proposal becomes `manual`.

## API

### `GET /api/shooters/{slug}/stages/{n}/coach` (under `/api/matches/{id}/`)

Adds to the existing payload:

- `events: StageEvent[]`
- per shot: `moving: bool`
- `event_summary: StageEventSummary`
- `_version`: `audit_revision` of the stored doc, which is what the events
  PUT sends back (the coach payload's integer `version` is the hosted row
  version and stays)
- on each `videos[]` entry: `trim_version`, `scrub_version` -- the same
  two fields `StageVideo` carries, from the request's `StoragePresence`
  listing, so the SPA's `useScrubSource.choose` can tell a fresh
  rendition from a stale one.

Seeds on first read when the audit has shots, `events` is empty and
`events_seeded` is false; the write goes under `_audit_rmw()` and sets the
flag. Like the heal, the seed is persisted only for an owner read, and
never on a mirror (`_is_mirror()`): `events` is a desktop-owned field --
`sync.merge.merge_audit_doc` starts from a deep copy of local, so local's
events stand on every pull and a hosted write would only trip the
non-whitelisted-change note. The share surface does reach the coach GET
(`_SHARE_PATH_RE` admits `shooters/{slug}/stages/{n}/coach`): a share
read seeds and heals in memory only, and the response strips
`events[].note` as it strips `coaching_note` and `improvement_flag`. The
PUT is not on the share surface. Share cards and pages get the reload
and moving figures through `stages[].figures` on the project payload.

The GET seeds but does not re-classify stored shots against a fresh
seed. Every audit-doc writer that classifies (the audit PUT, triage
accept, the coach PATCH, `POST /coach/reclassify`, the events PUT) does
so against the doc's own events through one helper (`_classify_doc`), so
a region-derived `reload` survives any later save.

### `PUT /api/shooters/{slug}/stages/{n}/events` (under `/api/matches/{id}/`)

Body `{events: StageEvent[], _version: str}`. Replaces the whole list.
`validate_lanes` failure is a 422 naming the pair. A `NaN` or `Infinity`
anywhere in the body is a 422 before the lock (`_reject_non_finite`, as
the audit PUT; #843). The request refuses unknown keys on an event; the
stored `StageEvent` ignores them, so a doc a newer version wrote still
loads. A stale `_version` is a
409 `version_conflict`, the same shape as the audit PUT; the page reloads.
Writes under `_audit_rmw()`. It is a match write under
`/api/matches/{id}/`, so auto-sync's dirty middleware marks the match with
no change. Not in `_SHARE_WRITE_ROUTES`, and not in `_REVIEW_ROUTES`:
`events` is a desktop-owned field. On hosted a desktop-origin mirror
answers 403 `read_only_mirror` (the existing gate), because
`sync.merge.merge_audit_doc` keeps local's copy and a hosted write there
would be overwritten by the desktop's next sync; a hosted-native match
keeps the PUT. The SPA renders the editor read-only wherever
`capabilityDenied(project.capabilities, "edit")` holds, so a mirror never
offers an edit it would refuse.

## SPA

### Coach page

- The player adopts `useScrubSource`: `choose(video)` where it pins
  `primary.kind` today, `markFailed` as its `error` handler. A
  `source`-kind video (no trim yet) is left alone; the hook is for the
  trim/rendition pair.
- Under the video: `components/coach/LaneEditor.tsx`. Beside the video:
  the selected-region card, which replaces `ShotEditor` while a region is
  selected and gives it back on deselect (one card level per view).
- The stat strip gains **On the move** (shots) and **Overhang** (s,
  amber: it is reload-hued, not a warning).
- The lane editor's overflow menu gets **Full-resolution video**, bound to
  the same hook as Audit's `TransportLine` entry, shown only when
  `scrub.available` (local mode).
- `lib/events.ts` holds every derivation (moving flags, reload figures,
  summary, clamp rules, snap); the page maps results to primitives.

### `LaneEditor`

Stage-long strip, beep at x = 0, stage time at the right edge, ruler on
top. Lanes: **Shots** (read-only; the audit's shots as ticks, a hollow cap
on a shot inside a movement region), **Movement**, **Reload**,
**Activation**. Playhead across all lanes follows `video.currentTime`;
clicking the ruler seeks.

Interactions, all on `MarkerLayer`'s conventions (pointer capture, a
travel threshold before a press becomes a drag -- wider for touch -- Esc
restores the pre-drag state):

- Press-and-drag on empty lane space creates a region; release commits.
  Under the threshold it is a click, which seeks. A lane click seeks to
  the press point snapped to the nearest shot; the ruler click does not
  snap. A snap that would land inside a same-lane region is dropped for
  the raw press time, so a create never starts inside a neighbour. A
  click on empty lane space also clears the selection. A region
  being created stops at its same-lane neighbours.
- `pointercancel` undoes the gesture like Esc.
- Drag an edge to resize, the body to move. **While an edge drags the
  video seeks to that edge's time.** Body drag seeks to the leading edge.
  A time pill follows the handle (time, frame number), and the moving
  edge during a create; both count from the beep, the frame at the
  nudge `fps`.
- Snap within ~8 px to a shot time or the beep; Alt skips. Neighbours in
  the same lane clamp; a region never overlaps another in its lane.
- Selected region, editor focused: ArrowLeft / ArrowRight nudge the start
  by one frame (`1 / fps` of the primary video, 30 when unknown),
  Shift+Arrow nudges the end, Alt+Arrow moves by 100 ms; Delete or
  Backspace removes. Arrows rather than brackets: bracket keys sit behind
  AltGr on Nordic layouts.
- While a reload region is selected, an overhang bracket is drawn from
  the enclosing movement's end to the reload's end, labelled with the
  signed difference.
- Auto proposals render with a dashed outline and an `AUTO ?` label.
- No zoom, no multi-select, no copy in this cut. At 16 s over ~900 px one
  pixel is ~18 ms; coarse placement by drag, fine by nudge.

Every edit PUTs the whole list with `_version`; a 409 reloads the coach
payload. Nudges commit after 350 ms idle so a held key is one PUT.

There is no separate mobile Coach page; under the phone breakpoint the
Coach page renders the same component read-only, with a region list under it (one row
per region: kind chip, range or duration, moving-shot count or overhang).
Editing stays on the desktop, where there is a frame to judge from.
As shipped, the Coach route itself is behind `DesktopGate` (`App.tsx`),
so a phone gets the desktop-only notice and never reaches this read-only
view until a phone Coach surface exists; the read-only rendering serves a
hosted mirror in a desktop browser today.

Visual budget: regions use the budget chip ticks (`movement` beep-cyan,
`reload` live-amber, `activation` ink-2), the playhead is `--color-led`
(current position), proposals are dashed outlines, nothing is a coloured
fill at full opacity. Built from `components/ui` primitives; the lane
geometry is inline SVG.

## Rendering and export

### Overlay (single and grid)

The renderer reads `events` through `stage_summary_data` the way it reads
`interval_class`. Two elements, both region-driven and both **Look
gallery slots** (`lib/lookGallery.ts` entries with thumbnails from
`scripts/render_look_thumbnails.py`), off by default:

- **Reload chip** beside the clock in the lower third: amber outline,
  `RELOAD` plus the elapsed figure counting up from `start`, holding its
  final value through a 0.4 s fade so a 1.42 s reload reads as "1.42".
  Enable expression `between(t, start, end + fade)` on the stage's own
  filter graph, upstream of the hold `concat` like the lower third, so it
  can never reach a summary frame.
- **Stage bar** under the clock: the stage as a thin bar with movement and
  reload bands and the playhead's progress.

Grid: the chip per tile, the bar on the audio-source tile only. A render
that does not pick either slot has a byte-identical argv (pinned).

### Summary card

The Splits band gains `Reloads N`, `Reload avg`, `Overhang` when a reload
region exists, and the split figures appear as two rows, `Static` and
`Moving`, when both populations exist. A stage with no events renders
exactly as today (pinned). `share_card.stage_figures` stays on
`statistic_splits` and grows no reload figure in this cut.

### CSV, FCPXML, Compare

- CSV: `moving` on each shot row; `events.csv` beside it (`id, kind,
  start, end, duration, source, note`).
- FCPXML: each region as a marker with duration on the stage clip, kind
  in the marker's value, so the editor can navigate to it. No burnt-in
  chrome. Compare export: markers on the audio-source shooter's clip only.

## Testing

Mutation drill on every new test: delete the behaviour, watch it fail,
restore it. A test that passes against the pre-change code is not a test.

- `tests/test_events.py`: `validate_lanes` (same-kind overlap rejected,
  cross-kind allowed, touching edges allowed); `shot_is_moving` inclusive
  ends; `reload_figures` standing / hidden (negative overhang) / positive
  overhang / two movements overlapping one reload (latest end wins);
  `stage_event_summary` sums; `capacity_for` on the SSI strings including
  an unknown one; the seeder over fixtures: a PO stage with a reload at
  shot 12 on a movement (hint wins inside the window), a Classic Minor
  stage (window of 11), an Open stage (hint only), the `capacity + 1` edge
  (16 shots then a reload, no false early seed), and a PO stage of 14
  shots (no seed). Classifier: a 2.8 s gap flips to `reload` only when a
  reload region overlaps it; a manual class wins; a split inside a
  movement stays `split`.
- Routes: `PUT /events` under the probe lock from
  `test_audit_lock_wiring.py`; 422 on a lane overlap naming the pair; 409
  on a stale `_version`; 422 on a non-finite number with the doc
  untouched; the share surface 404s the PUT and its coach GET strips
  `events[].note`; a region-derived `reload` survives the Reclassify
  route, the audit PUT, triage accept and the coach PATCH; the GET seeds once and `events_seeded` stops a second seed after
  the user deletes everything; a `test_match_bundle_queries`-style count
  that the GET adds no `state_docs` SELECT; the `videos[]` entries carry
  `scrub_version` from the presence listing.
- Renderer: argv tests for the chip's and bar's enable expressions and
  their position upstream of the hold `concat`; the zero-slot render's
  argv is byte-identical; `scripts/render_match_frames.py` and
  `scripts/render_grid_frames.py` grow `--events`, and the frames are
  looked at before the slot ships (a green argv test proves nothing about
  pixels).
- Summary card: a stage with no events renders exactly as before (pixel
  fixture); a stage with a reload shows the three figures; static and
  moving rows appear only when both populations exist.
- SPA: `lib/events.test.ts` over the shared fixture JSON, case for case
  with `test_events.py`; `LaneEditor.test.tsx` with pointer events the
  way `MarkerLayer.test.tsx` does it -- create, click-under-threshold
  seeks, resize seeks the video to the edge, snap and Alt, clamp against
  a neighbour, Esc restores, keyboard nudges by one frame, Delete; a
  Coach page test that the player's source comes from `useScrubSource`
  and that the overflow entry toggles `setFullRes`; `lookGallery.test.ts`
  pins the two new slots' per-format visibility and that their thumbnails
  are referenced.

## Out of scope

- Movement seeding or any video-derived proposal (the vision detector is
  not there).
- Zoom, multi-select, copy, undo beyond Esc.
- A region-based time budget.
- Reload figures on the share card.
- Phone-side editing or a desktop command to re-seed.
- The mechanical reload as a sub-marker.

## Part 2 as built: rendering on the template HUD (amended 2026-10-09)

The "Rendering and export" section above predates the template HUD engine
(overlay styles, #1306-#1311). The overlay paragraphs are replaced by this
section; the summary card, CSV, FCPXML and share paragraphs are refined by
it. Where the two disagree, this section wins.

### Confirmed regions only

Every rendered or exported output (overlay, summary card, `events.csv`,
FCPXML markers, share figures) reads **confirmed** regions only:
`source == "manual"`. An auto proposal is a guess the user has not looked
at, and a video or an export must never show a region nobody confirmed
(the conservative rule: under-report rather than invent). The Coach page
keeps showing proposals, dashed, as before.

So that confirming does not require moving a handle, the region card gains
**Keep** on an auto proposal: it flips `source` to `manual` and changes
nothing else. Dragging, nudging or changing the kind already confirm.

Movement regions are never seeded, so `moving` flags and the moving-split
figures are unaffected by this rule in practice.

### Overlay: data the HUD styles draw

The template HUD receives its stage through `overlay_hud.hud_stage_data`.
It gains, from confirmed regions only:

- `events`: `[{kind, start, end}]` in clip seconds (beep-offset like `shots[].t`).
- `reloads`: `[{start, end, duration, overhang}]` in clip seconds, from
  `events.reload_figures` (`overhang` is `null` for a standing reload).
  Templates never re-derive a figure.
- each shot gains `moving: bool`.

`HudOptions` / `OverlayStyleFields` gain two toggles, both **off** by
default: `reload_chip` (`overlay_reload_chip`) and `stage_bar`
(`overlay_stage_bar`). They ride the existing seam unchanged: preset body,
export and preview requests, `lib/overlayStyle.ts`, `STYLE_TOGGLES` in
`lib/lookGallery.ts`. A stage with no confirmed regions draws exactly as
today whatever the toggles say.

The palette gains two optional colours, `reload` (default `#FBBF24`, the
budget amber) and `movement` (default `#06B6D4`, the budget cyan), in
`OverlayTheme` and `look.json`'s `colors`; a Look without them loads with
the defaults. Red stays the brand.

All five shipped styles (Plate, Pips, Ticker, Timeline, Minimal) draw both:

- **Reload chip**: while `start <= t < end + 0.4` for a reload, a chip near
  the clock reads `RELOAD` and the elapsed reload time counting up from
  `start`, holding the final duration through a 0.4 s fade.
- **Stage bar**: a thin bar spanning the stage with movement and reload
  bands and the elapsed portion filled. Timeline draws the bands on its
  existing track; the other four place a thin bar under their clock.

Classic (the drawtext path) and the compare grid (sprite overlay, no
template styles) do not draw either in this cut; the toggles sit under the
HUD style tiles only, where they already live.

No cache change: the HUD MOV's key hashes the whole template context, so
event data reaches it as soon as it is in `data.stage`. `HUD_KEY_VERSION`
stays, since the frame plan does not change.

The authoring guide documents the new `data.stage` fields, the two options
and the two palette tokens; `splitsmith looks check` exercises a sample
stage that carries a movement and a reload, so a custom style is checked
against them. `scripts/render_overlay_frames.py` gains `--reload-chip`,
`--stage-bar` and a synthetic stage with regions; its frames are looked at
before the other four styles are done.

### Summary card

When the stage has confirmed reloads, the Splits band gains a row: Reloads,
Reload avg, Overhang (positive overhangs summed, as on the Coach page).
When both static and moving splits exist, the split figures appear as two
rows, Static and Moving. A stage with no confirmed regions renders
byte-identically to today. Single-shooter and grid holds share
`summary_groups`, so both get it; the match summary card does not change.

### CSV

`moving` is appended as the last column of the splits CSV;
`read_splits_csv` accepts the old header and the new one. An `events.csv`
(`id, kind, start, end, duration, source, note`) is written beside it when
the stage has confirmed regions.

### FCPXML

Single-stage and match FCPXML (and the FCP7 XML) carry each confirmed
region as a marker with duration on the stage clip, value
`Reload 1.42` / `Movement` / `Activation`. The compare export does not
carry per-shot markers today and does not carry regions either.

### Share figures

`stages[].figures` on the project payload gains `moving_shots`,
`reloads`, `reload_avg_s`, `overhang_s` from confirmed regions (`null`
when the stage has none). The share card does not change.
