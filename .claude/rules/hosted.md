---
paths:
  - "src/splitsmith/access.py"
  - "src/splitsmith/async_bridge.py"
  - "src/splitsmith/db/**"
  - "src/splitsmith/ui/access_gate.py"
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
