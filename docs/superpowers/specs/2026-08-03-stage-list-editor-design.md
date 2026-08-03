# Stage-list editor in the SPA (#521)

Add, remove, and rename stages on an existing match from the SPA, without
losing audit progress on the stages the user did not touch.

Issue #521. Tier 1 of the hosted feature-completeness epic (#632).

## Why this is a blocker, not polish

On desktop a wrong stage list is an annoyance: drop to `splitsmith match`
and fix it. A hosted user on my.splitsmith.app has no CLI, so a wrong
stage list chosen at creation is unrecoverable short of recreating the
match and re-attaching every upload.

## What already exists, and why it is not reusable

`POST /api/shooters/{slug}/project/placeholder-stages` looks like the
backend half of this feature. It is not:

- It replaces the entire stage list rather than editing it, and moves
  every assigned video to `unassigned_videos` (`project.py:1518-1538`).
- It raises `ScoreboardImportConflictError` -> 409 when any
  non-placeholder stage exists, so it refuses on scoreboard-backed
  matches.
- It has zero SPA callers. `api.ts:2011` defines `createPlaceholderStages`
  and nothing imports it. Manual match creation goes through
  `POST /api/match/create-manual`, not this endpoint.

The issue body's claim that the endpoint is "implemented but unwired" and
"called from `lib/api.ts:2013` but only from the create flow" is
inaccurate on both counts.

## Data model

`Match.stages` (`list[MatchStageDefinition]` in `match.json`) is the
canonical stage list. Each shooter's `MatchProject.stages`
(`list[StageEntry]`) mirrors it, joined on `stage_number`. Every
per-stage artifact is keyed on `stage_number`:

| Artifact | Location |
| --- | --- |
| Audit doc | `audit/stage<N>.json` (local) / `state_docs` (hosted) |
| Per-cam audio | `<audio>/stage<N>_cam_<video_id>.wav` |
| Per-cam audit audio | `<audio>/stage<N>_cam_<video_id>_audit.wav` |
| Window audio | `<audio>/stage<N>_cam_<video_id>...` |
| Peaks | `<audio>/stage<N>_*.peaks-*.json` |
| Trimmed video | `<trimmed>/stage<N>_cam_<video_id>_trimmed.mp4` |
| Legacy tier | `stage<N>_primary.wav`, `stage<N>_audit.wav`, `stage<N>_trimmed.mp4`, `.params.json`, `.partial.mp4` |

That keying is the constraint the whole design turns on: renumbering a
stage orphans every artifact belonging to it.

## Stage identity

`stage_number` is stable for the lifetime of a stage. Three operations:

- **rename** sets `stage_name` (and `stage_rounds`) in place. Touches no
  artifacts.
- **add** allocates from `Match.next_stage_number`, the match's
  monotonic allocation counter, and writes the advanced value back.
- **remove** drops the entry and leaves the counter alone, so the freed
  number is never handed out again.

Removing a stage mid-list therefore leaves a gap: removing 3 from 1-6
yields 1, 2, 4, 5, 6.

### Non-reuse comes from the counter, not from leaving gaps

Never renumbering is what keeps *existing* stages' artifacts valid. It
says nothing about which number the next added stage gets, and the two
were conflated in the first cut of this design: allocation was
`max(existing_numbers) + 1`, computed fresh from the post-save list with
nothing persisted. That reuses a number as soon as the removed stage was
the highest-numbered one -- stages 1-6, remove 6, add, and the new stage
is 6 again, inheriting `stage6_cam_*.wav` and its trim from a worker that
landed a write after the purge.

`Match.next_stage_number` is therefore persisted on the match document
and only ever increases. It is `None` on matches written before the field
existed; `Match.resolve_next_stage_number()` backfills those from
`max(stages) + 1`, which is exactly what the old allocation would have
returned, so an existing match behaves identically on its first edit and
carries a stored mark from then on. That same expression is also a floor
on a stored mark, because `Match.stages` can be replaced wholesale from a
scoreboard shell without the counter being consulted.

The counter is written in the same save as the stage list it numbered
(the match doc, saved last), so a crash cannot leave a number spent but
unrecorded.

### This is a deliberate expedient, not a principle

Gaps were chosen to ship the feature without building an artifact
migration engine. The intended future is contiguous renumbering plus
reordering, which this design does not implement and does not preclude.

What that future needs, recorded here so it is not rediscovered:

1. A migration that renames per-stage artifacts across both storage
   backends and both naming tiers, per shooter, restartably.
2. Identity decoupled from display position. Either a stable opaque
   `stage_id` on `MatchStageDefinition` with artifacts keyed on it, or
   an explicit `display_order` field with `stage_number` retained as
   the key.
3. `Match.stages` list order becoming meaningful independently of
   `stage_number`.

Two cheap hedges are in scope now because they cost nothing today and
are tedious to retrofit:

- The editor renders rows in list order and shows `stage_number` as
  secondary identity, not as the row's ordinal. Nothing in the new UI
  equates position with number.
- No new artifact kind is keyed on `stage_number`. Every one added today
  is one more entry in the future migration's table.

Stored order stays ascending by `stage_number`, which is today's
invariant everywhere. Persisting submitted order instead would be the
more forward-compatible choice, but with no reordering UI the only way
to submit a non-ascending list is a bug, and honouring it would push a
silently non-ascending list past consumers that have always assumed
otherwise. Reordering later means dropping that normalisation
deliberately, alongside the identity change it needs anyway.

Do not write "stage_number is the permanent identity" into docstrings.
It is stable, not permanent.

## Scope

Rename applies to every stage regardless of origin. Remove applies to
every stage, scoreboard-backed included.

This is safe because `merge_stage_times` overlays onto existing stages
and silently drops unknown `stage_number` values -- it never grows the
list (`project.py:1741-1744`). A refresh-times cannot resurrect a
removed stage. Only a full overwrite-import can, and that path already
warns that it orphans video assignments.

`StageEntry.skipped` remains the softer marker: "this stage exists, I
did not shoot it". Remove means "this stage does not exist".

## Removal semantics

Uploaded footage is the only irreplaceable artifact, and in hosted it
may be the user's only copy. It is never deleted.

- Videos on a removed stage move to `unassigned_videos` with
  `role = "secondary"`, following `init_placeholder_stages`
  (`project.py:1520-1523`). The user can re-bind them to another stage.
- The audit doc and every derived cache for that stage are deleted.
  Derived state is cheap to regenerate, and leaving it orphaned invites
  a stale doc reattaching if the number is ever reused. The counter is
  the guarantee that it is not; the purge is the second line.

The purge glob is `stage<N>_*` per directory, which covers both the
per-cam and legacy naming tiers in one pattern. The trailing underscore
is load-bearing: a bare `stage<N>*` prefix makes removing stage 3 delete
stage 30's artifacts.

## API

`PUT /api/match/stages`, taking the desired list:

```json
{"stages": [{"stage_number": 1, "stage_name": "El Presidente",
             "stage_rounds": null}, ...]}
```

`stage_number: null` marks a new row. The server diffs against
`Match.stages`:

```
removed = existing_numbers - submitted_numbers
added   = submitted rows with stage_number == null
renamed = matching numbers whose stage_name or stage_rounds changed
```

A submission with no removals skips the destructive path entirely.

The response is a `StageEditSummary` modelled on
`match_delete.DeletionSummary`: per-shooter counts of videos unassigned,
audit docs deleted, cache objects deleted, jobs cancelled, plus
`errors: list[str]`. Individual failures are collected, not raised --
teardown is best-effort, and a failed cache delete must not strand the
stage list in a half-written state.

Each `ShooterStageEditResult` carries `saved: bool` alongside `error`,
because an error means two different things. A per-stage cleanup failure
is recorded and then execution falls through: that shooter's stage list
is written and only derived state is orphaned (`saved=True`). A failure
loading or saving the project doc means the shooter's list is unchanged
on disk while the match doc has moved on (`saved=False`). The two error
strings differ only in formatting, so a client cannot tell them apart by
matching on the message.

Concurrency uses the existing `expected_version` optimistic lock on
`save_match` and `save_project`. A lost race returns 409.

Validation: at least one stage must remain; `stage_name` is required and
trimmed; a submitted `stage_number` not present in `Match.stages` is a
400 rather than an implicit add.

## Execution order

Compute stops before storage moves, mirroring `_delete_hosted`
(`match_delete.py:130-196`):

1. Cancel active jobs whose `(slug, stage_number)` is in the removed
   set. Per-job `jobs.cancel(job_id)`, never `cancel_active_for_user` --
   the coarse version would kill the user's unrelated work.
2. Per shooter: move removed stages' videos to `unassigned_videos`.
3. Per shooter: delete the audit doc via a new
   `AppState.delete_audit(slug, n)` wrapper. Hosted delegates to the
   existing `project_state.delete_audit(match_id, slug, n)`
   (`db/project_state.py:206`); local unlinks the file. This mirrors the
   existing `load_audit` / `save_audit` local-vs-hosted split
   (`server.py:1280-1326`).
4. Per shooter: delete cache objects matching `stage<N>_*` in the audio
   and trimmed directories, collecting per-object errors.
5. Per shooter: apply adds and renames, then save the project doc.
6. Save the match doc last, carrying the advanced allocation counter, so
   a crash mid-fan-out leaves the canonical list describing the pre-edit
   world.

Nothing cancels jobs on removed stages today: `Job` carries no match id,
so filtering on `stage_number` alone would reach the user's other
matches, and step 1 shipped as a no-op (#645). A worker can therefore
land a write well after step 4. Because the allocation counter never
reissues a freed number, that write is inert garbage rather than a
correctness bug -- it can never be read as belonging to a live stage.
Restoring cancellation, precisely scoped, is what #645 tracks; until
then the counter is the only thing making the write harmless, which is
why it is persisted rather than derived. The same property is lost the
day renumbering lands, so the future migration needs its own
compute-quiescing step.

## SPA

`EditStagesDrawer`, a new component reusing the row shape from the
manual-create stage editor (`CreateMatch.tsx:1205-1264`): stage name
field, expected rounds, remove button.

- Entry points: the match overview, in both its empty and active
  variants. This restores the affordance PR #520 deleted along with the
  dead "Edit stage list" button and "Adjust the stage list" help card.
- Rows marked for removal stay visible, struck through, until Save.
- Save is gated behind a confirm naming what will be destroyed,
  aggregated across shooters from roster data the drawer already holds.
- `api.editMatchStages()` replaces `createPlaceholderStages`, which is
  deleted rather than left as a second, destructive path to the same
  concept.

The Shooters page is deliberately not an entry point. Stage lists are
match-level; offering the edit from a per-shooter screen implies a
per-shooter stage list that does not exist.

## Contiguity audit

Gaps are new: until now stage numbers were always 1..N. Nothing found so
far assumes contiguity -- `Pick.tsx`'s `TickStrip` renders anonymous
done/todo ticks from a count rather than from numbers, and the `idx + 1`
sites in `server.py` are progress-message cosmetics. That is a survey,
not a proof, so a deliberate sweep for count-derived stage lists is a
plan step with its own verification, not an assumption.

## Testing

Per the project's review practice, every test must fail against
pre-change code. The relevant lesson from #638: pick the assertion that
actually discriminates, because status codes are frequently identical
before and after.

- Rename preserves `audit/stage<N>.json` and the trim. Assert on
  artifact bytes, not on the response.
- Remove stage 3 of 5: stages 4 and 5 keep audit docs and trims
  byte-identical, stage 3's are gone, its videos are in
  `unassigned_videos`.
- After removing stage 3 from a 5-stage match, adding a stage allocates
  6, not the freed 3.
- After removing stage 5 from a 5-stage match, adding a stage allocates
  6, not the freed 5. This is the case a list-derived `max + 1` gets
  wrong, and it has to go through the HTTP layer: the counter is only
  worth anything if it survives the round trip through `match.json`.
- A shooter whose per-stage cleanup fails reports `saved=True`; one whose
  project save fails reports `saved=False`.
- Fan-out across three shooters where only one has content on the
  removed stage.
- Removing stage 3 does not delete stage 30's artifacts. This is the
  glob-underscore test and it needs a fixture with a two-digit stage.
- A removed stage with an active job has that job cancelled and no other
  job touched.
- A storage delete that raises lands in `errors` and the stage list
  still commits.
- Remove-everything is refused with 400.
- A concurrent edit losing the `expected_version` race returns 409.

Frontend: the drawer renders a gapped list correctly, and the confirm
reports the right per-shooter destruction counts. Read the rendered
output rather than trusting the assertion -- on #617 a fix reached the
table cell and rich ellipsized it away while the test stayed green.
