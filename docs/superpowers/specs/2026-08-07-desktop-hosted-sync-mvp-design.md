# Desktop-to-hosted sync MVP - design

Date: 2026-08-07
Status: approved (brainstorming session)
Tracking: #631 (this MVP is the desktop-to-hosted half, scoped to share-readiness)

## Problem

Local work (detection, trimming, rendering) is significantly faster than hosted,
but only hosted matches can be shared via the existing share-link mechanic.
Today the two deployment modes have no path between them (#631). The MVP: keep
doing the work locally, push the finished, audited parts up as you go, and share
the result with the existing mechanic as if the match had been done fully hosted.

## Scope decisions (made in session)

- Share visitors see trims + results: state docs plus the per-stage trimmed
  clips the results view streams. Raw footage and audio caches never leave the
  local disk. Rendered exports are out of the MVP.
- Trigger is a manual, re-runnable, incremental push from a button in the local
  UI. No background watcher, no auto-push on milestones.
- The hosted copy is a read-only mirror. Local is authoritative; re-push
  overwrites unconditionally. Future two-way audit sync must not be foreclosed,
  but no merge logic ships now.
- Auth is a paste-once personal desktop token for the MVP. A browser-assisted
  device flow is the next iteration (noted on #631).

## Shape

A local match gets a "Sync to splitsmith.app" button. Pressing it runs a local
job that pushes, incrementally and idempotently:

1. Per-stage trimmed clips, direct to R2 via the existing presigned-multipart
   path (big bytes never stream through the Railway API).
2. State docs (`match`, per-shooter `project`, per-shooter-per-stage `audit`)
   through new hosted sync endpoints.

Media first, docs last, so hosted docs never reference bytes that are not there
yet. The share mechanic (tokens, `/share/:token`, whitelisted routes, presigned
streaming) works on the mirror unchanged - no share code is touched.

## Auth: desktop tokens

New `desktop_tokens` table modeled on the worker-token pattern but carrying
`user_id`:

- Generated on the hosted account page, shown once, stored SHA-256 hashed,
  revocable from the same page.
- Local app stores the raw token in its settings and sends it as a bearer.
- The auth gate resolves this bearer to a normal tenant, so every downstream
  store and RLS policy behaves exactly as a logged-in session.
- Token resolution happens pre-tenant (like share-token resolution), so the
  table is read via the raw session factory and is not itself under RLS.
- `desktop_tokens` is account-scoped, not match-scoped, so the "new tenant
  tables carry `match_id`" invariant (#632) does not apply; it carries
  `user_id` only.

## Identity and sync state

- The stable key is the local `Match.match_id` - already a deterministic,
  frozen id (`generate_match_id` in `match_model.py`), and the hosted
  `matches` table is already unique on `(user_id, match_id)`. First push
  calls `POST /api/sync/matches` with it; the call is create-or-return, so
  re-runs never duplicate.
- The local project gains a `sync_state.json` in the match root (never part
  of any pushed doc): per-item digests (sha256 + size + mtime) from the last
  successful push, plus the last-synced timestamp.
- Re-push diffs current files against those digests and skips unchanged
  items; size + mtime gate the expensive re-hash, rsync-style.
- `sync_state.json` is a pure cache. Deleting it costs a full re-hash and
  re-upload on the next push - the match link itself lives in `match_id`
  and cannot be lost.

## Sync API

Small, idempotent, under `/api/sync/`:

- `POST /api/sync/matches` - create-or-return the hosted match for a stable
  client key.
- `PUT /api/sync/matches/{id}/docs/{kind}[/{slug}[/{stage}]]` - unconditional
  upsert of a state doc body. Mirror semantics: no 409 dance, the stored
  version just increments.
- `POST /api/sync/matches/{id}/media/presign` - given relative key + size +
  digest, returns either "already present" or presigned multipart parameters
  for the R2 key under `users/<user_id>/...`.

Doc bodies are pushed as the hosted shape expects them; filesystem-absolute
fields are stripped or rewritten client-side (storage-relative `storage_path`
is already the hosted convention). Raw-video entries stay in the docs but
their bytes are absent hosted-side; the share whitelist only streams trims, so
shares render fully. The owner-facing hosted view of a mirror may show raw
previews as unavailable - accepted for the MVP.

Open detail for planning (pin from code, do not invent): the exact hosted
object-key layout for trims, taken from the existing `resolve_video_path` /
trim-cache scheme, and the exact local-file-to-doc-kind mapping
(`match.json` / per-shooter `project.json` / `audit/stage<N>.json`).

## Read-only enforcement

- `matches` gains an `origin` column: `hosted` (default) or `desktop`.
- Hosted mutation endpoints reject `origin='desktop'` matches with a single
  server-side gate in the write paths.
- The SPA renders the existing read-only Results-style surface for mirrors,
  with a "synced from desktop" note.
- Sync endpoints only accept `origin='desktop'` matches, so a sync can never
  clobber a native hosted match.

## Local UI

- Match page shows sync status: never synced / synced at T / stale (local
  digests differ), plus the sync button.
- Push runs through the existing local job queue; progress lands in the jobs
  panel.
- After success: link out to the hosted match page, where the existing share
  dialog issues tokens.

## Error handling

Any failure leaves a consistent state by construction: media before docs,
digests recorded only after per-item success, everything re-runnable. Auth
failures surface as "token invalid or revoked - generate a new one on your
account page". No partial-push repair tooling.

## Testing

- Sync client logic (diff computation, doc rewriting) is pure functions with
  fixture tests.
- Endpoint tests run against the SQLite state store + `FilesystemStorage` like
  existing hosted tests.
- One docker-marked test covers the Postgres/RLS path (standing rule for DB
  changes).
- One integration test round-trips a small real project to a local "hosted"
  instance and asserts the share whitelist routes serve it.

## Out of scope (future work, tracked on #631)

- Hosted-to-desktop pull.
- Two-way audit sync. The mirror flag plus per-doc digests are chosen so this
  can grow later without rework.
- Browser-assisted device auth (next iteration after paste-token).
- Raw footage upload, rendered-export sync, auto-sync on milestones.

## Relation to docs/saas-readiness/07-sync-and-migration.md

That document's v1 push agrees in direction but predates hosted reality.
Deviations here supersede it: no tus (deferred indefinitely), no `/api/v1`
prefix, no tarball import endpoint, media prefix is the implemented
`users/<user_id>/...` rather than the doc's `projects/<project_id>/...`.
Doc 07 gets a pointer to this spec when the implementation lands.
