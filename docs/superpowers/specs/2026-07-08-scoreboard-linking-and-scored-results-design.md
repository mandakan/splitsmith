# Post-creation scoreboard linking + scored results view

Date: 2026-07-08
Status: approved, ready for planning

## Problem

Two related gaps, one shared foundation.

1. **Linking.** A match can be created from the SSI Scoreboard, which pins each
   shooter's global `selected_shooter_id` and per-match `selected_competitor_id`.
   But a shooter *added after* creation only inherits the match-level
   `scoreboard_match_id` / `scoreboard_content_type` -- it never gets its own
   competitor ids. Result: that shooter has no scoreboard identity, so the
   detection pipeline cannot pull per-stage expected shot counts / times, and no
   scoring can be attributed to them. There is also no way to connect a
   manually-created match to the scoreboard after the fact.

2. **Results.** Scoreboard scoring (time, hit factor, stage %, points, hit
   breakdown) is fetched during `merge_stage_times()` and disk-cached, but then
   discarded -- only `time_seconds` + `scorecard_updated_at` are kept. The
   shareable results view therefore shows time only, and share viewers (who have
   no scoreboard identity and cannot fetch live) can never see scores. And the
   results view does not surface splitsmith's own unique output -- the splits.

## Goal

A single coherent capability, built in two phases:

- **Phase A -- Post-creation linking:** connect a match to the scoreboard and
  bind shooters to competitors after creation, so every shooter can carry a
  scoreboard identity and detection gets its expected-rounds prior.
- **Phase B -- Scored results view:** persist the scorecard into project/state
  and show it in the shareable results view, alongside the splits, which are the
  featured content.

## Existing code (verified)

- `POST /api/match/create-from-scoreboard` -- `server.py:3145`; eager path that
  does both links at creation.
- `MatchProject` link fields -- `project.py:749` (`scoreboard_match_id`,
  `scoreboard_content_type`, `selected_shooter_id`, `selected_competitor_id`,
  `competitor_name`).
- `Shooter.ssi_shooter_id` -- `fixture_schema.py:157`.
- `POST /api/match/shooters` (add shooter) -- `server.py:11040`; sets no
  competitor ids (the bug).
- `POST /api/shooters/{slug}/scoreboard/select-shooter` -- `server.py:7225`;
  existing per-shooter link endpoint to reuse.
- `merge_stage_times()` / `populate_from_match_data()` -- `project.py:1627`;
  turns competitor stage results into `StageEntry.stage_rounds.expected`
  (`project.py:534`) consumed by the ensemble apriori boost
  (`ensemble/voters.py:101`).
- `CompetitorStageResult` -- `scoreboard/models.py:260`; carries `time_seconds`,
  `hit_factor`, `stage_points`, `stage_pct`, `alphas`, `charlies`, `deltas`,
  `misses`, `no_shoots`, `procedurals`, `scorecard_updated_at`.
- Disk cache of full competitor stage results -- `scoreboard/cache.py:111`.
- Results UI -- `Results.tsx` (time-only overview) + `ResultsStage.tsx`
  (per-stage playback). No scoring, no split figures.

## Design

### 1. Data model (shared foundation)

- **Match link:** no new fields. Make `scoreboard_match_id` +
  `scoreboard_content_type` writable post-creation (today only
  create-from-scoreboard sets them).
- **Per-shooter, per-stage scorecard:** add an optional `scorecard` block to the
  per-shooter stage entry (`ShooterStageData` / `StageEntry`) holding the
  `CompetitorStageResult` fields: `time_seconds`, `hit_factor`, `stage_pct`,
  `stage_points`, `alphas`, `charlies`, `deltas`, `misses`, `no_shoots`,
  `procedurals`, `scorecard_updated_at`. Stop discarding these in
  `merge_stage_times()`. Persisting them is what lets the hosted DB/state_docs
  serve scores to share viewers who cannot fetch live.
- **Match totals:** derived (sum of stage points, total time, overall %); no
  need to store if cheaply computed at read time.

### 2. Phase A -- Post-creation linking

**a. Connect a match (new).** A "Connect to scoreboard" action for a match with
no link reuses the existing `search_events -> get_match` flow to attach
`scoreboard_match_id` + content type to the existing project. For an
already-linked match it is a "change link" no-op-friendly path. Appears to need
a new endpoint (only create-from-scoreboard exists today); reuse its internals.

**b. Reconcile shooters (confirm-before-apply).** After a match is linked (or on
demand), fuzzy-match existing local shooters to roster competitors by name +
division, present the proposed mapping for the user to confirm/correct, then
apply. Applying sets each shooter's `selected_shooter_id` +
`selected_competitor_id` via the existing select-shooter endpoint. Unmatched
shooters stay manual (no silent wrong links -- matches the project's "don't
silently decide, keep an audit trail" rule).

**c. Add-shooter roster picker (fixes the bug).** When the match is linked, the
add-shooter UI (`Shooters.tsx`) offers the roster; picking a competitor fills
name + division and sets both ids in one step. A "not on scoreboard / manual"
fallback remains. Existing unlinked shooters get a "Link to scoreboard" action
that drops them into flow (b) for a single shooter.

**d. Detection benefit (free).** Once a shooter has `selected_competitor_id`,
linking triggers the existing `merge_stage_times()` path, which populates
`stage_rounds.expected`, so the ensemble apriori boost works for the
newly-linked shooter with no pipeline changes.

**Stage mismatch rule.** When connecting a manually-created match whose stages
do not line up with the scoreboard, attach scores by stage number and surface a
warning on mismatch. Do NOT rewrite local stages, trims, or audits.

### 3. Phase B -- Scored results view

Per stage, two data sources side by side:

- **Splits (splitsmith, featured):** first-shot time from the beep, then the
  sequence of shot-to-shot split times, derived from the audited/accepted shots
  (`audit/stageN.json` shot `time` / `ms_after_beep`). Show the sequence plus
  min/max/average where useful. This is the unique value and the headline
  content.
- **Scorecard (scoreboard, context):** time, HF, stage %, points, and the hit
  breakdown (A/C/D, misses, no-shoots, procedurals) from the persisted
  `scorecard` block.

Showing *how fast* (splits) next to *how well* (hits/HF) on the same stage is
the differentiator. Match totals summarize both. The split figures already
persist as audited shot times, so this is a read/format concern, not new
persistence. Mobile-first, consistent with the instrument-panel aesthetic.

**Graceful degradation:** unlinked shooter -> splits only; un-audited stage ->
scorecard only; neither -> time only (current behavior).

### 4. Refresh / staleness

Persist the scorecard snapshot whenever the owner links or runs the existing
stage-times merge, plus an explicit "Refresh from scoreboard" button on the
results/match page. Share viewers see the last-persisted snapshot with its
`scorecard_updated_at` shown, so freshness is visible and honest. No automatic
background polling. (Share viewers cannot fetch; only the owner's refresh
advances the snapshot.)

## Non-goals

- No automatic/background scoreboard polling.
- No rewriting of local stage structure/trims/audits on connect.
- No correlation analytics (e.g. which fast split produced a C/D) in this
  iteration -- possible future work, kept out for YAGNI.
- No change to the ensemble voters themselves.

## Testing

- Backend: connect-existing-match sets the link; add-shooter with a roster pick
  sets both competitor ids; reconciliation auto-match proposal is correct and
  confirm-apply persists the ids; scorecard persists into project/state and
  round-trips through the hosted store (state_docs); unlinked shooter degrades to
  splits-only / time-only; stage-number mismatch warns without mutating local
  stages.
- Detection: a newly-linked shooter gets `stage_rounds.expected` populated
  (assert the merge path runs), reusing existing fixtures.
- Fixtures: use real cached scoreboard payloads under `scoreboard/cache/`; do
  not fabricate scoring data (project rule).
- SPA: typecheck + build + scoped eslint (no test runner); manual check of the
  roster picker, connect flow, and results scorecard + splits layout.

## Phasing / PRs

Buildable as two PRs on the shared data-model change:

1. **Phase A** -- data-model scorecard field + connect-match endpoint +
   reconciliation + add-shooter roster picker + persist scorecard in
   `merge_stage_times()`. Ships the linking fix and starts persisting scores.
2. **Phase B** -- results view: splits + scorecard display, match totals,
   refresh button.
