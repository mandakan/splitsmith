---
paths:
  - "src/splitsmith/events.py"
  - "src/splitsmith/coach*.py"
  - "src/splitsmith/detection_merge.py"
  - "src/splitsmith/match_model.py"
  - "src/splitsmith/stage_summary_data.py"
  - "src/splitsmith/share_card*.py"
  - "src/splitsmith/csv_gen.py"
  - "src/splitsmith/fcpxml_gen.py"
  - "src/splitsmith/fcp7xml_render.py"
  - "src/splitsmith/overlay_hud.py"
  - "src/splitsmith/sync/merge.py"
  - "src/splitsmith/sync/run.py"
  - "src/splitsmith/sync/pull.py"
  - "src/splitsmith/ui_static/src/lib/events*.ts"
  - "src/splitsmith/ui_static/src/lib/timeBudget*.ts"
  - "src/splitsmith/ui_static/src/lib/coachReview*.ts"
  - "src/splitsmith/ui_static/src/lib/stageStrip*.ts"
  - "tests/fixtures/events/**"
  - "tests/test_events.py"
  - "tests/test_coach_*.py"
  - "tests/test_stage_figures_regions.py"
  - "tests/test_sync_*regions.py"
  - "tests/test_sync_stage_note.py"
---

# Stage events: regions, seeding, classification, figures, outputs

Spec `2026-10-08`. The editor UI (Breakdown, Coach) is in `breakdown-ui.md`.

## The model

- Movement, reload, activation and draw are **regions** (`events` on the stage
  audit doc, `config.StageEvent`, seconds from beep), independent of shots: a
  movement may span several shots, which per-gap `interval_class` cannot say.
- The two views coexist. The gap partition is still what the time budget sums
  and `statistic_splits` filters on. **Confirmed** regions (`source ==
  "manual"`) only *hint* the auto-classifier: a gap over `transition_max_s`
  overlapping a confirmed reload auto-classes `reload`
  (`coach.gap_overlaps_reload`, which ignores anything not manual). A region
  never owns a class.
- **An auto proposal never touches a stored class**: not through a save, a note,
  Reclassify, `match reclassify`, the GET's heal or `stale`.
- Every figure is derived, never stored, by `splitsmith/events.py` and its TS
  twin `lib/events.ts`. **Both run `tests/fixtures/events/cases.json` case for
  case; a rule changes on both sides or not at all.** Figures: per-shot
  `moving`, `reload_figures` (**exposed** = reload duration minus its overlap
  with the union of movements, floored at 0, so a standing reload is exposed
  for all of it), `stage_event_summary` / `exposed_reload_s`.
- A reload's handles mean hand off the grip -> gun back on target.
- `StageEvent` ignores unknown keys (a doc a newer version wrote loads on an
  older one); the request's `StageEventIn` forbids them. `events_from_doc`
  skips a region of an unknown kind with a warning instead of failing the
  stage. A region starting before the beep starts at the beep, or is skipped
  when it ends there; any other corrupt known region fails the stage.
- A shot whose time is unreadable (`events.shot_ms`) is left out of the payload
  and skipped by the classifier, never a 500.

## Classification writers

- **Every audit-doc writer that classifies goes through `_classify_doc`** in
  `ui/server.py` (audit PUT, triage accept, coach PATCH, Reclassify, events
  PUT), which passes the doc's own confirmed events. A writer that called the
  classifier region-blind would silently flip a region-derived `reload` back
  to `movement` on the next unrelated save (`stale` is not rendered). A corrupt
  events list there is the GET's 422, raised before the save.
- `is_classification_stale` takes the same `reload_overlap` input as the
  classifier, or a region-derived `reload` would report stale.

## Draw

- **The stage's Draw figure is `events.draw_seconds`** (TS `drawSeconds`,
  `cases.json` `draws`): the first shot's time from the beep, what a shot timer
  reports, whatever the regions say. A confirmed draw region whose start was
  moved splits it into reaction and presentation (`events.draw_parts` /
  `drawParts`), shown beside the Draw on Coach and in the budget's Draw row.
- A `draw` region is the hands starting the draw to the first shot: its own
  lane, first; one per stage (`validate_lanes` on both sides); may overlap a
  movement. There is no "on the move" figure.
- A stage has a **start** (`MatchStageDefinition.start` -> `StageEntry.start`,
  kept across a scoreboard re-import): `standard` or `non_standard`; any other
  value reads as non-standard (`match_model.is_standard_start`). **Draws are
  compared across stages only between standard starts**: the match summary's
  Best draw (`TileStageData.standard_start`), Coach's baseline
  (`match_distributions(non_standard_starts=)`), the budget's Draw delta and
  outliers (`timeBudget`'s `standardStart`).
- Every Draw surface reads it: `TileStageData.draw` (every tile builder carries
  the stage's confirmed `events`), `share_card.stage_figures(..., regions=)`,
  Coach's `reviewFigures`. **A new Draw surface calls it.**

## Seeding

- `events.seed_doc` runs once per stage (`events_seeded` holds
  `events.SEED_VERSION`) on the coach GET **with `?seed=1`**, which only
  Breakdown sends. **Coach, the Splits stage page and Compare read every stage
  and must never seed.**
- Movement: one proposal per gap the stored classes call `movement`, inset
  `MOVEMENT_SEED_INSET_S` from both shots, since `shot_is_moving` is inclusive
  and a region on a shot's time would mark the neighbours moving.
- Draw: from the beep to the first shot on every stage whose first shot follows
  the beep, plus a movement over the same span (inset) when the first shot is
  more than `draw_movement_min_s` (2.0 s) after the beep. Both are opt-in seeder
  arguments only the coach GET passes.
- Reloads: every hinted gap (`reload_hint_min_s`); with a division capacity
  (`DivisionCapacityConfig`, keyed on the SSI string) the fewest reloads that
  keep every run within `capacity + 1` shots (a bound: one chambered), in the
  longest allowed gaps, each at least `reload_min_s`. One magazine, no reload.
- A first seed that proposes nothing writes nothing (`seed_doc` returns False),
  so it is recomputed later. Once proposals exist `events_seeded` is stored,
  which keeps deleted ones from coming back.
- A stage an older seeder proposed whose events are all untouched proposals is
  seeded again on the next seeding owner read and its shots re-classified
  (confirmed regions only). A manual region or deleted proposals keep it.
- A reset re-detection (`detection_merge.merge_detection_into`, shared by the
  server job and the MCP `detect_shots` tool) drops `auto` regions and
  `events_seeded`, keeps `manual` ones.
- The seed runs on a copy and is persisted only on a seeding owner read that is
  not a mirror. A seed that raises is logged and the stage served without
  proposals; `seed_events` never proposes a region that would not validate.
  The heal is persisted on any owner read, mirrors included; the seed never
  rides it. The GET never re-classifies stored shots against the seed.

## Coach payload and the events PUT

- The payload's `event_summary`, per-shot `moving` and `stale` read confirmed
  regions only; its `events` keep proposals for Breakdown's lanes.
- The GET's `_version` is the revision of the doc *as stored* (before the
  in-memory seed and heal), so a mirror or share read hands out a `_version`
  the PUT accepts. On the share surface `_build_coach_response` strips
  `events[].note` and `coaching_note`.
- `PUT .../stages/{n}/events` replaces the list under `_audit_rmw()` with the
  revision check (409 `version_conflict`; 422 `lane_overlap` names both ids).
  `NaN` / `Infinity` is a 422 before the lock (`_reject_non_finite`). Every PUT
  appends an `audit_events` entry, which is why the SPA saves on commit only.
- The PUT needs the `breakdown` capability: local and hosted-native always; a
  desktop mirror only once `matches.desktop_merges_events` is set (by
  `POST /api/sync/matches` with `X-Splitsmith-Sync-Features: events`). Before
  that the mirror answers 403 `read_only_mirror`, so an older desktop's push
  can never overwrite a phone's edit.

## Stage note

- `stage_note` on the audit doc (`coach.FIELD_STAGE_NOTE`), written by
  `PATCH .../stages/{n}/stage-note` under `_audit_rmw()` with the revision
  check; a `_REVIEW_ROUTES` entry. The audit event records length and a short
  hash, never the text (same for `coaching_note`).
- **A clear is an explicit `null` (the tombstone); an absent key means the
  writer never knew the field.** Never drop the key to clear it. The sync audit
  PUT keeps the stored note when the body lacks the key and answers
  `kept_fields`, and `run_push` then leaves the doc's version unseen so the next
  sync merges it. A sync state below `state.STAGE_NOTE_SCHEMA` runs
  `run._repull_audit_docs_for_stage_note` once.

## Sync merge

`sync.merge._merge_regions` merges `events` per region by id against the base:
a delete is the base having it; an edit beats a delete; the same id added on
both sides is two regions (remote's renamed); of two regions from different
sides that collide in a lane the older doc's goes, surfaced; `events_seeded`
takes the larger. `merge_audit_doc` merges `stage_note` three-way (newer doc
wins a conflict, surfaced). `sync.run` re-classifies the merged doc's shots when
its confirmed regions moved.

## Outputs read confirmed regions only

- **Every rendered or exported output** (overlay, summary card, `events.csv`,
  FCPXML markers, share figures) reads confirmed regions through
  `events.confirmed_from_doc`, which drops each unreadable region on its own and
  keeps the rest (dropping the whole list for one bad entry silently took
  regions out of every export). **A new consumer calls it.** Only Breakdown
  shows proposals. Coach's `StageStrip` and stat strip use
  `lib/events.confirmedEvents`.
- **Use Breakdown** (`breakdown`, default on) on every request body, preset,
  `MatchExportRequestData`, both renderers, both CLIs and `splitsmith overlay`
  (`--no-breakdown`), both MCP export tools: off reads every stage as one
  nobody broke down. **A new region reader takes it.** Engine half: the
  loaders' `breakdown=` (`stage_summary_data._confirmed_events`),
  `export_preview`, `overlay_hud_render`, Classic's draw. An overlay drawn
  without it records `breakdown: false` (see `rendering.md` for overlay reuse).
  The SPA sends it only when off and shows the switch only where it changes
  something.
- HUD contract (`overlay_hud.hud_stage_data`): `data.stage.events`
  (`{kind, start, end}`, clip seconds), `data.stage.reloads`
  (`{start, end, duration, exposed}`; templates never re-derive a figure),
  `shots[].moving`, `options.reload_chip` / `options.stage_bar` (off by
  default), palette `reload` / `movement` / `draw` in `OverlayTheme` and
  `look.json`. All five template styles draw them; Classic and the grid's
  sprite overlay draw no regions. Shot 1's split is the Draw on every overlay.
- Summary card: confirmed reloads add Reloads / Reload avg / Exposed to the
  Splits band. A stage with no confirmed region declares exactly the groups it
  did before regions existed. Fit rules are in `rendering.md`.
- CSV: the splits CSV gains `moving` last (`read_splits_csv` takes both
  headers); `<base>_events.csv` is written only with shots and confirmed
  regions. A `write_csv` export deletes an earlier events CSV (locally and
  hosted) when the stage has no confirmed region, and both CSVs when it has no
  shots; `StageExportResult.removed_paths` names them and the hosted push
  deletes exactly those keys. With `write_csv` off both earlier files stay.
- Markers (`events.region_marker_label`) on the stage clip in FCPXML and FCP7
  XML, clamped to the visible window; compare carries none.
- `stages[].figures` (`_stage_region_figures`): `moving_shots`, `reloads`,
  `reload_avg_s`, `exposed_reload_s`, all `null` with no confirmed region;
  `exposed_reload_s` a number whenever there is a confirmed reload. No capacity
  warning there (a Coach hint, not a shared figure).
