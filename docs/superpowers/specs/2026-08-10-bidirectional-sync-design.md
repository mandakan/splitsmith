# Bidirectional sync slice - design

Date: 2026-08-10
Status: draft for review
Parent: 2026-08-10-mobile-operator-surfaces-design.md (slice 2 of the build order)

## Context

Desktop-to-hosted sync today is one-way push (`sync/push.py`): plan ->
ensure_match -> media -> docs, with a sha256 doc-hash skip cache and an
rsync-style media digest cache in `sync_state.json`. Hosted state lives in
Postgres `state_docs` rows keyed `(user_id, match_id, doc_kind, slug,
stage_number)` with a per-row `version` and `updated_at`
(`db/project_state.py`). Every doc write is a whole-doc replace; docs carry no
per-field timestamps, and audit events have a `ts` but no id. `/api/sync/*`
has no GET routes; a pull path does not exist. Mirror matches
(`origin='desktop'`) are hard read-only hosted-side: the alias middleware 403s
every non-GET except share management (`server.py:6318`).

This slice makes the desktop sync pull-then-push so that hosted-side edits
(the coming mobile write surfaces) survive a desktop push. It is the
prerequisite for every mobile write surface; the mirror write gate itself
stays closed until the beep-review slice.

## Decisions (with rationale)

1. **Desktop re-derives on pull.** The parent spec assumed a phone beep edit
   re-runs trim and shot detection on hosted workers, with desktop
   downloading the new derived media. That is infeasible: push only uploads
   `trimmed/` files, so hosted never has raw video for mirror matches and
   cannot re-trim them. Instead, a merged-in beep change invalidates local
   derivations through the same path a local beep override uses; the normal
   desktop job chain re-derives, and the next push uploads fresh trims. This
   supersedes the parent spec's "Derived artifacts" paragraph and removes the
   derived-artifact download mechanism entirely: hosted never derives
   anything for mirror matches, and pull is docs-only. Media keeps flowing
   one way, desktop to hosted. Consequence: after a phone beep edit, phone
   results stay stale until the next desktop sync.

2. **Three-way merge against a stored base, not per-field timestamps.**
   Desktop keeps a snapshot of each doc as last synced (the base). Merge
   compares local-vs-base and remote-vs-base per field: changed on one side
   wins outright; changed on both is a true conflict, resolved
   last-writer-wins by doc `updated_at` and logged visibly. Change detection
   is exact and clock-free; clocks only tiebreak rare true conflicts. No doc
   schema changes beyond audit event ids. Rejected: per-field timestamps
   (schema churn at every write site, clock skew decides every merge) and
   whole-doc LWW (a project doc holds all stages for a shooter, so any
   desktop edit would clobber a phone beep edit in the same doc).

3. **Version-diff manifest instead of a time cursor.** Desktop records the
   remote `version` it last saw per doc; pull diffs a doc manifest against
   those. Clock-free, survives missed syncs, and doubles as the staleness
   check.

4. **Manual sync with a staleness hint.** The existing per-match sync button
   becomes pull-then-push. On match open, the cheap status check also reports
   whether hosted has newer docs, and the desktop SPA shows a "hosted has
   newer changes - sync now" hint. No auto-pull on open.

## Hosted API changes

All under `/api/sync` (`ui/sync_api.py`); the desktop-token `sync` scope
already admits them, RLS scopes rows, `_resolve_mirror` semantics unchanged.

- `GET /matches/{match_id}/docs` - manifest:
  `[{doc_kind, slug, stage_number, version, updated_at}]` for every
  state_doc of the match.
- `GET /matches/{match_id}/docs/match`, `.../project/{slug}`,
  `.../audit/{slug}/{stage_number}` - returns `{doc, version}`.
- The three existing `PUT` doc routes gain a required `expected_version`
  (0 = insert). `_mirror_save` changes from unconditional last-write-wins to
  a guarded save that returns 409 `version_conflict` on mismatch and the new
  `version` on success. Desktop is the only client; no compat fallback.

`ProjectStateStore` gains a manifest/listing query (follow the
`ORDER BY updated_at` pattern in `db/matches.py:175`) and version-returning
variants of load/save as needed. No schema change to `state_docs`; no new
tables.

## Audit event ids

Every `audit_events` append site (desktop and hosted, e.g.
`server.py:2910/10517/10574` and the SPA-owned `save` event) stamps a ULID
`id` on new events. Merge unions events by id; legacy id-less events dedupe
by `(ts, kind)`. Existing docs are not migrated.

## Desktop sync state

`sync_state.json` bumps to `schema_version: 2`:

- `doc_versions: {doc_key: remote_version}` - last remote version seen per
  doc identity key (same keys as `doc_hashes`).
- `doc_hashes` and `items` (media digests) unchanged.

New `sync_base/` directory in the match root: one JSON file per doc identity
key holding the doc body as of last sync - the three-way merge base. Written
atomically. A missing base for a doc means "never synced" and is treated as
an empty base (everything local counts as a local change; everything remote
counts as a remote change).

## Sync run (pull -> merge -> push)

Inside the existing `sync_match` job (`server.py:3433` ->
`sync/push.py:run_push`, renamed/extended):

1. **Plan pull**: fetch the manifest, diff `version` against `doc_versions`.
2. **Pull**: GET each remotely-changed doc. No local mutation yet; a pull
   failure aborts the run before anything is touched.
3. **Merge** (per doc, three-way vs base) over a declarative whitelist of
   merge fields:
   - project doc: the per-video `beep_*` field-group. The whole cluster
     (`beep_time`, `beep_source`, `beep_reviewed`, confidence and candidate
     fields) moves atomically per video - mixing sides would be incoherent.
   - audit doc: `audit_events[]` (union by id) and the per-shot coach fields
     (`interval_class`, `interval_class_source`, `coaching_note`,
     `improvement_flag`) as one merge unit per shot, keyed by stable shot
     identity (exact keying - shot time vs index - verified at plan time
     against the audit doc shape).
   - match doc: no whitelisted fields.
   - The triage slice adds `needs_attention` as one whitelist entry.
   Remote changes outside the whitelist: local wins plus a loud log line
   (impossible while the mirror gate is closed; the log is the tripwire).
4. **Apply**: atomic local write of merged docs. A merged-in beep change
   invalidates derivations exactly like a local beep override
   (`processed.trim = False` path); sync does not block on re-derivation,
   the report says "N videos need re-processing".
5. **Base update**: base := the pulled remote snapshot, doc_versions :=
   manifest versions. This holds the three-way invariant across a crash
   before push: next run sees the merge results as plain local changes.
6. **Push**: the existing push phases, docs now sent with
   `expected_version`. On 409 (a hosted write landed mid-sync): re-pull,
   re-merge, retry, at most 3 attempts, then surface a sync error. After a
   successful PUT: base := pushed body, record the returned version, update
   `doc_hashes` (existing behavior).

## Conflict visibility

True conflicts (both sides changed the same field-group since base) resolve
LWW by doc `updated_at` but are never silent: the job report carries
`conflicts: [{doc, field, winner}]`, shown in the sync result UI and
persisted with the job record.

## Staleness hint

`GET /api/match/sync/status` (`server.py:6167`) additionally fetches the
manifest and reports `remote_changes`. The desktop SPA renders the hint on
the sync control when nonzero.

## Testing

- pytest merge conflict matrix: local-only change, remote-only, both-same,
  both-differ (LWW + logged), event union including legacy id-less events,
  non-whitelisted remote change, never-synced (empty base),
  crash-before-push replay.
- Integration tests construct `HostedSyncClient` in exact production shape
  (base_url prefix lesson from #712).
- `pytest -m docker` locally: new GET routes, manifest query, and the
  version-guarded PUT touch store and DB paths.
- Staging E2E (acceptance): synthetic coach-note edit via SQL in the staging
  Neon branch, then desktop sync - the edit survives locally, the merged doc
  pushes, and a second sync is a re-push-0.
- Standard gates: ruff, black, pytest; pnpm typecheck, test, scoped eslint
  for the hint UI.

## Out of scope (this slice)

- Lifting the mirror write gate (beep-review slice does it per-endpoint).
- `needs_attention` (triage slice; lands as one whitelist entry).
- Derived-artifact download (dead: desktop re-derives).
- Raw media upload to hosted.
- Deletion propagation for removed shooters/stages (pre-existing push gap,
  unchanged here).
- Auto-pull on match open.
