---
paths:
  - "src/splitsmith/access.py"
  - "src/splitsmith/async_bridge.py"
  - "src/splitsmith/db/**"
  - "src/splitsmith/ui/access_gate.py"
  - "src/splitsmith/ui/context.py"
  - "src/splitsmith/ui/docstore/**"
  - "src/splitsmith/ui/state.py"
  - "src/splitsmith/ui/admin_access_api.py"
  - "src/splitsmith/ui/presence.py"
  - "src/splitsmith/ui/jobs.py"
  - "src/splitsmith/ui/youtube_api.py"
  - "src/splitsmith/ui/capabilities.py"
  - "src/splitsmith/ui/embedded.py"
  - "src/splitsmith/ui/share_og.py"
  - "src/splitsmith/ui/account_backfill.py"
  - "src/splitsmith/ui/shooter_roster.py"
  - "src/splitsmith/queue.py"
  - "src/splitsmith/account_profile.py"
  - "src/splitsmith/shooter_book.py"
  - "src/splitsmith/match_model.py"
  - "src/splitsmith/match_project.py"
  - "src/splitsmith/ui_static/src/lib/jobs.ts"
  - "src/splitsmith/ui_static/src/lib/access*.ts"
  - "src/splitsmith/ui_static/src/lib/adminAccess*.ts"
  - "src/splitsmith/ui_static/src/lib/jobBatch*.ts"
  - "src/splitsmith/ui_static/src/components/admin/**"
  - "tests/test_access*.py"
  - "tests/test_admin_access_routes.py"
  - "tests/test_pooling_docker.py"
  - "tests/test_presence_index.py"
  - "tests/test_recent_projects_batch.py"
  - "tests/test_match_bundle_queries.py"
  - "tests/test_jobs_poll_payload.py"
  - "tests/test_share_shooters_presence.py"
---

# Hosted mode: access tiers, database connections, request-path cost

Deploy facts (worker env, `FORWARDED_ALLOW_IPS`, the waitlist cut-over) are in
`docs/saas-readiness/11-environment-strategy.md`.

## Access tiers (spec 2026-10-03)

- An account has **features** (`splitsmith.access.Feature`: `sync`, `share`,
  `create_match`, `raw_upload`, `hosted_compute`); a match has
  **capabilities** (by origin). A request needs both.
- A tier is a named feature set in `AccessConfig` (`config.py`, overridable via
  `SPLITSMITH_CONFIG`). `SPLITSMITH_ACCESS_DEFAULT_TIER` on `serve` sets new
  accounts' tier, validated against the registry at boot. `features_for`
  resolves a user: env admins get everything, an unknown tier gets nothing.
- **Code checks features, never tier names**, in Python and in the SPA
  (`lib/access.can` / `useCan`; a stray refusal maps through `featureRefusal`).
- Local mode never consults access.
- The backstop is `submit_allowed` on `PostgresJobBackend`, wired per tenant in
  `_build_tenant`: no `hosted_compute`, no job of any kind, so a new job kind is
  covered with no edit. A new hosted entry point that creates matches, uploads
  or submits still declares `Depends(require_feature(...))`
  (`ui/access_gate.py`) so the refusal is a clean `feature_required` 403. A
  handler that chains a job after a committed write catches
  `FeatureRequiredError` and skips the chain rather than reporting the write as
  refused.
- The intake (`POST /api/v1/access-requests` and sign-in's blocked branch)
  answers the same bytes whatever the email's state, rate-limited or not, and
  never mails the requester; only admins are alerted.
- Admin routes are in `ui/admin_access_api.py` with the db imports inside the
  functions, because the slim local install imports that module.

## The storage port (`ui/docstore/`, spec 2026-10-19)

- `DocStore` is the one interface for a match's documents; `FileDocStore` and
  `DbDocStore` are its two worlds, and `tests/ui/test_docstore_contract.py`
  runs every contract test against both. A behaviour one implementation has
  and the other lacks is a bug, and the suite is where it shows.
- `update_*` is the read-modify-write: `merge` is a pure function of the
  current document and must be safe to run more than once, because the
  database implementation re-applies it to the winner's document after a
  version conflict (with a jittered backoff; eight simultaneous writers
  exhaust a flat four attempts).
- `update_audit_at` is a handler's shape of it: `merge(doc | None, version)`
  raises the handler's own 404 or 409 part-way (nothing is written, nothing
  retried) or returns the doc to write, and the call returns that doc with
  its new version, which the response is built from. A merge whose write is
  conditional declines by raising `server._AuditUnchanged`, which the handler
  catches. `tests/test_audit_lock_wiring.py` pins that every router site
  reads and writes the audit file under `audit_lock` this way.
- `FileDocStore` takes the **same** `audit_lock` object the sync pull holds
  (#1075); a store that made its own lock would silently undo that.
- `MatchProject.bind_state(docs, slug=, version=)` and `Match.bind_state(docs,
  version=)` take the port, and `save()` writes through it at the version the
  doc was loaded at; the models never see a `ProjectStateStore`. In a request
  the port is `state.docstore()`; a creation route, whose match the alias
  middleware has not seen, uses `state.docstore_for(match_id, root)`. Whether
  to `confine_paths` is the store's answer (`confines_paths`), not a mode
  check. `save_async` runs a bound save on a worker thread, since the port
  is synchronous and `run_sync` from the loop thread would block the loop.
- `MatchProject.stage_statuses`, `audited_count` and `export_overview` never
  read an audit file: `audit_docs` is required, from `state.load_audit_docs`
  or the match bundle in a request and `audit_docs_from_disk` where there is
  none (the CLI, the reel, the local picker). `audit_docs_on_disk` is the one
  reader of the desktop's audit files and the port's file store goes through
  it, so the two worlds derive a status from the same documents.
- The one mode fact a consumer may read is `documents_on_disk`: whether
  the documents are the files under the match root. It decides whether a
  file-path consumer materializes a copy (`AppState.materialize_audit`) and
  whether a file read-modify-write takes `audit_lock` (`_audit_rmw`).
  Nothing in `ui/*.py` outside `state.py` asks whether a state store is
  bound; a hosted-only walk over a tenant's other matches binds each with
  `docstore_for(match_id)` and no root. `delete_match_docs` removes every
  document of a match and nothing else; the delete cascade's media and
  registry steps stay its own.
- The port holds documents only: media stays on `Storage` and
  `StoragePresence`, jobs on the job backends, merge rules in `sync.run`.

## Database connections

- asyncpg binds a connection to the event loop that created it; using it from
  another loop crashes ("attached to a different loop"). Hosted mode therefore
  runs database work on exactly two long-lived loops per process: the main loop
  (uvicorn's or the Procrastinate worker's) and the `DbRunner` thread's
  (`splitsmith.async_bridge`). `db.engine.LoopEngines` gives each *adopted* loop
  its own pooled engine (`pool_size=5, max_overflow=10, pool_pre_ping=True`);
  any other loop gets the shared NullPool fallback, which never reuses a
  connection.
- **Every sync caller goes through `run_sync`, never `asyncio.run`** (the state
  accessors, the job backend's thread-side bridges, `youtube_api`). A new
  `asyncio.run(<store coroutine>)` on the hosted path is a per-call connection
  again. Local mode builds no engine and installs no runner, so `run_sync` is
  `asyncio.run` there.
- The pool adds no traffic of its own (no warm-up, no ping, no `pool_recycle`):
  Neon closes idle connections after five minutes and scales to zero, and
  `pre_ping` reconnects on the next request.
- The regression gate is `tests/test_pooling_docker.py`, which proves pooling
  by a `pg_stat_database.sessions` delta over 40 requests, not by connection
  count (NullPool also keeps that flat). SQLite in tests keeps one NullPool
  engine on purpose: a per-loop `:memory:` engine would be a database per loop.

## Jobs run outside a request

- A job body may ask `_is_mirror()` or read `current_match_origin` (the audit
  audio job's web-rendition fallback does). Only the alias middleware pins the
  origin per request, so every place that runs a body without one pins it
  itself: the worker's compute task reads it from the match row it resolves
  the match through, and the local manager's `retry` keeps it in `_match_ctx`
  with the id and root. Without it a desktop mirror's audio job looks for a
  raw source no mirror has. `tests/test_worker_match_origin.py` and the retry
  test in `tests/test_jobs.py` pin both.

## Request-path cost: batch, don't loop

- **Existence checks** (is this source in storage? is this trim cached?) go
  through `ui/presence.StoragePresence`, one per request: it lists the tenant's
  `raw/` prefix once and each shooter's `trimmed/` prefix once, with the same
  local-disk-first semantics as `MatchProject.source_present` and
  `audio.trim_available`. `_audit_trim_targets` takes it as `presence=`; its two
  callers (the shooters list, the bulk rebuild) each build one. A new per-angle
  existence check on a list route belongs on the index, never on
  `storage.exists` (two R2 HEADs per angle). A share request skips the
  `stages_missing_trim` count entirely, since it gates a button the anonymous
  shell never renders (`tests/test_share_shooters_presence.py`).
- **The hosted picker** (`GET /api/me/recent-projects?detail=true`) loads every
  match's detail in two queries: `ProjectStateStore.load_docs_for_matches` and
  `PostgresMatchStore.list`, then `_hosted_detail_from_docs` derives each card
  with no I/O. A new per-match fact goes into that batch, never a per-row
  `await` (`tests/test_recent_projects_batch.py` pins the SELECT count).
- **Routes that walk every shooter** (triage grid, triage summary, beep queue)
  take `AppState.match_bundle`: one `load_docs_for_matches` query, each project
  bound by `_project_from_doc` as `shooter_project` binds one. Never
  `shooter_project` in a loop (`tests/test_match_bundle_queries.py` pins two
  `state_docs` SELECTs per request). The SPA shell fetches the beep queue once
  per load and hands it down as `beepQueue` on the outlet context; the Overview
  reads it there (no refetch) and fetches triage once on mount, not again when
  the project arrives.
- **The jobs poll** (`GET /api/me/jobs`, every 5 s per tab) is bounded and is
  never the account's whole history: hosted `PostgresJobBackend.list` returns
  active jobs, unacknowledged failures and the `RECENT_FINISHED_RETAINED` most
  recent others (the SyncCard's job echo is the only reader of terminal rows),
  and the route nulls `result` and `timings`. A result is read through `GET /api/me/jobs/{id}`
  (`pollJob`); a new SPA surface that needs one fetches the job, never the list
  (`tests/test_jobs_poll_payload.py`). The strip's "N of M" counts its own batch
  (`lib/jobBatch`), never the list's terminal rows.
