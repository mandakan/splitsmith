# 09 -- SaaS progress

This is the **status doc**. It tracks what's shipped, what's in
flight, and what's deferred across the v1 / v2 / v3 milestones.

Update this file as work lands. The other docs (00-08) describe the
target architecture; this doc is the current state.

## How to read this doc

Each milestone has a checklist. Items use one of four marks:

- `[x]` -- shipped on the named branch / PR
- `[~]` -- in flight (work started, not merged)
- `[ ]` -- not started
- `[-]` -- explicitly deferred (with a note saying when to reconsider)

Each shipped item links to the PR. Each in-flight item links to the
issue or branch.

When this file is updated, the date at the top of the relevant
section is bumped.

## v1 -- abstractions + minimal hosted MVP

Goal (per doc 00, Q5): the three abstractions (Storage, Auth,
ComputeBackend) live in the codebase with local-mode implementations
matching today's behaviour, plus a working hosted deployment with
magic-link signup, audio upload, Tier 2 compute, R2 storage, Stripe
Checkout gating premium.

**Last updated:** 2026-05-15 (initial draft of doc set).

### Foundation -- abstractions in the local codebase

These can ship before any hosted-mode infrastructure exists. Each is
a refactor that makes today's local-mode code pass through the
abstraction without behavioural change.

The abstractions are necessary but **not sufficient** for hosted
mode -- the singleton-elimination work in
[10-singleton-elimination.md](./10-singleton-elimination.md) is the
parallel track. Storage callsite migration in particular is blocked
on Tier 1 of that doc (killing `AppState._bound_root`); doing it
before that would entrench the singleton.

- [~] Extract `Storage` interface; replace direct `pathlib.Path` use
  in project layout with `FilesystemStorage` calls.
  (See 03-storage-layer.md.) Protocol + `FilesystemStorage` (local)
  + `S3Storage` (hosted, boto3) both shipped, with shared
  path-traversal guard and structural-Protocol round-trip tests
  via ``moto``. Not yet wired into `AppState` -- local mode opens
  projects at arbitrary user-chosen paths and hosted mode wants a
  per-request per-tenant factory, so the right scope is per-project,
  not a process singleton. Migration of existing callsites
  (audit JSON, project state) is iterative follow-up work; the
  Protocol stays sync today.
- [x] Extract `Auth` interface; introduce `LoopbackAuth`; route
  every API handler through `auth.authenticate_request`.
  (See 02-tenancy-and-identity.md.) Interface + `LoopbackAuth` +
  `GET /api/me` landed; every `/api/me/*` route gated by the
  `get_current_user` dep; an `_auth_gate` ASGI middleware now
  resolves `state.auth` on every `/api/*` request and 401s when
  the backend returns anonymous, with a small allowlist
  (`/api/health`, `/api/server/features`, `/api/shutdown`). In
  local mode `LoopbackAuth` never 401s; the wiring activates when
  a hosted backend swaps in.
- [x] Extract `ComputeBackend` interface; wrap today's ensemble
  pipeline as `LocalComputeBackend`. The orchestration in
  `cli.py` and the FastAPI server call the backend, not the
  ensemble directly.
  (See 04-compute-backends.md.) Protocol + `LocalComputeBackend`
  shipped; `state.compute` wired in `create_app`; the shot-
  detect worker (per-stage + bulk) goes through
  `state.compute.detect_stage`. ``cli.py``'s ``detect`` /
  ``process`` commands run the older single-voter
  ``shot_detect.detect_shots`` path, not the ensemble, so there
  was nothing to migrate there. Dev/lab surfaces (``lab_cli``,
  ``splitsmith.mcp.detect_tools``, ``/api/lab/*``) and auxiliary
  endpoints that use the runtime for non-detect work (CLAP
  probes for promote-secondary, calibrated-camera-models) keep
  direct runtime access -- hosted mode would never run those
  paths and the Protocol stays narrow until something needs more.
- [ ] Add a `splitsmith serve` entry point alongside `splitsmith ui`.
  Same code; different mode wiring at startup.
- [ ] Add `docker compose` for hosted-mode dev (Postgres + LocalStack
  S3 + the worker + the API server). Contributors without R2
  credentials can exercise the hosted path locally.

### Singleton elimination (parallel track, see doc 10)

Process-level state that must come out before the abstractions can do
anything useful in a multi-tenant deployment. Ordering matters --
later steps are blocked on earlier ones.

- [x] **Tier 1:** kill `AppState._bound_root` + `_bound_kind` +
  `_bound_name` + `_bound_match_id`. Done across four steps
  (PRs #409, #410, #411, #412). Match identity is per-request via
  the `/api/matches/{match_id}/` URL prefix; the alias middleware
  sets a ContextVar and `state.shooter_root` / `state.match_root`
  read it. The picker registers matches in `state.matches` and
  returns a HealthResponse so the SPA can navigate by URL --
  there is no server-side bound state to flip.
- [~] **Tier 2:** move `JobRegistry` off in-memory `dict` to the
  `compute_jobs` table (Postgres in hosted; SQLite in local if/when
  the desktop needs job persistence). Same handler-facing interface;
  different backend. Abstraction landed -- `JobBackend` Protocol +
  `AppState.jobs` typed against it; hosted backend land alongside
  the actual hosted PR.
- [~] **Tier 3:** `user_config.recent_projects` and
  `scoreboard_identity` become per-user Postgres rows in hosted
  mode; introduce a `RecentProjectsStore` / `ScoreboardIdentityStore`
  abstraction with a JSON-file backend for local mode.
  Protocols + JSON impls landed; `AppState.*` typed against them;
  handlers go through the abstraction. Per-user Postgres
  backend lands alongside the actual hosted PR.
- [x] **Tier 4:** `splitsmith ui --project <path>` resolves the path
  to a `match_id` via `Match.load(path).match_id` and opens the
  browser at `/match/<match_id>/` directly. No server-side bind
  involved; the URL carries identity from the first paint.

### DB foundation

- [x] SQLAlchemy 2.x async + Alembic + asyncpg/aiosqlite. ``users``
  table per doc 02 (ULID PK, email-unique, soft delete,
  entitlement block). Migration applies cleanly to SQLite
  in-memory (tests) and Postgres (production); the engine swaps
  via ``SPLITSMITH_DATABASE_URL``. Subsequent tables
  (``sessions``, ``compute_jobs``, ``recent_projects``, etc.)
  land as the corresponding hosted-impl PRs need them.

### Hosted infrastructure

- [x] Pick deploy target -- **Railway** (API + worker containers),
  resolved 2026-05-30. Fly.io dropped.
- [x] Pick auth -- **in-house `MagicLinkAuth`** (not Clerk/WorkOS/Auth.js),
  shipped in the 2026-05-30 auth-swap stack.
- [x] Define the **environment strategy** (staging + prod across all
  providers; apex-marketing / `my.splitsmith.app` app domain layout) --
  doc 11, resolved 2026-05-31.
- [ ] Provision R2 buckets (`splitsmith-uploads-prod` +
  `splitsmith-uploads-staging`) + lifecycle rule for incomplete
  multipart uploads.
- [ ] Provision Neon (`main` = prod, long-lived `staging` branch).
- [x] ~~Provision Redis if going with `arq`~~ -- not needed:
  Procrastinate (Postgres-native) is the picked job queue
  (resolved 2026-05-27, doc 00).
- [ ] Wire Sentry on the API + worker.
- [x] Wire Lettermint for magic-link email delivery
  (`SPLITSMITH_EMAIL_BACKEND=lettermint`; `splitsmith.app` DNS verified).
- [ ] Provision Railway (`staging` + `production` envs); add the `www`,
  `my`, `my.staging` Cloudflare records (doc 11 provisioning order).

### Auth + identity (doc 02)

- [ ] `MagicLinkAuth` implementation against the picked provider.
- [ ] `users` table + Alembic migration.
- [ ] `sessions` table + cookie middleware.
- [ ] `desktop_links` table + the desktop link flow API.
- [ ] `/api/v1/auth/begin`, `/auth/callback`, `/auth/refresh`,
  `/auth/desktop/exchange` endpoints.
- [ ] Magic-link email templates (text + HTML).
- [ ] First-login display-name prompt on the web UI.
- [ ] Devices page in account settings (list + revoke
  desktop_links).
- [ ] CSRF middleware.

### Storage (doc 03)

- [x] `S3Storage` implementation (boto3 against R2 / S3 / MinIO),
  shipped in #415 alongside `FilesystemStorage`. Shared
  path-traversal guard, structural Protocol round-trip tests via
  ``moto``.
- [x] `Storage` slot on `AppState`, wired per-user in hosted mode
  (`users/<user_id>/` prefix) from `SPLITSMITH_S3_*` env vars.
  Local mode leaves it `None`; the desktop continues to write
  `raw/<name>` symlinks via `pathlib.Path`.
- [x] First hosted upload route: `POST /api/me/raw/upload` streams
  to S3 via `upload_stream` (boto3 multipart), atomic + idempotent
  overwrite, server-computed sha256 returned to the client, optional
  `X-Content-SHA256` verifier that rolls back on mismatch. The
  desktop is unchanged -- this route 503s outside hosted mode.
- [-] `MemoryStorage` for tests. Deferred: `moto`-backed
  `S3Storage` + `tmp_path`-backed `FilesystemStorage` cover the
  test matrix without a third impl.
- [ ] Per-project storage layout standardised; existing local
  project directories remain compatible.
- [ ] `signed_url` implementation for both backends.
- [ ] Reindex helper (rebuild `projects` rows from R2 walk).
- [x] Per-user prefix decision pinned: `users/<user_id>/...`
  (project-id scoping nests under that once the `projects` table
  lands).

### ACL + projects (doc 02)

- [ ] `projects` + `project_members` tables.
- [ ] ACL middleware (`require_project_member(role)`).
- [ ] `POST /api/v1/projects` (create -- inserts ACL row).
- [ ] `GET /api/v1/projects` (list mine).
- [ ] `GET /api/v1/projects/<id>` (read -- after ACL check).
- [ ] `DELETE /api/v1/projects/<id>` (owner-only).
- [ ] `GET /api/v1/projects/<id>/manifest` (file listing for
  desktop pull).

### Uploads (doc 05)

- [~] Single-shot raw upload as the v1 stopgap: `POST /api/me/raw/upload`
  streams an `UploadFile` to S3 via `boto3` multipart, idempotent on
  retry, server-computed sha256 returned (and verified against an
  optional `X-Content-SHA256` header). Resume-from-byte-N is out of
  scope here -- tus is the upgrade path.
- [ ] tus-py-server wired with S3 backend pointing at R2.
- [ ] `upload_sessions` table + lifecycle (24h expiry + cron abort).
- [ ] `POST /api/v1/uploads` to start a session.
- [ ] tus-js-client integration in the browser SPA.
- [ ] Browser audio extraction (WebAudio -> 16 kHz mono WAV).
- [x] Sha256 verification on upload completion (single-shot today;
  tus extends this to the resumed-chunks flow).

### Compute (doc 04)

- [ ] CLAP + PANN model artifacts baked into the worker Docker image.
- [ ] ONNX-Web GBDT artifact built + served from `/static/models/`.
- [ ] `compute_jobs` table.
- [ ] `RemoteComputeBackend` (server-side wrapper that creates jobs).
- [ ] Procrastinate worker (`splitsmith worker` CLI) that processes
  Tier 2 jobs (CLAP + PANN inference + features back). PR-alpha
  landed the schema + queue module; PR-beta lands the CLI.
- [ ] `POST /api/v1/projects/<id>/jobs` (enqueue).
- [ ] `GET /api/v1/jobs/<id>` (poll status).
- [ ] SSE stream `GET /api/v1/jobs/stream` for the jobs drawer (per
  06 open question; v1 if cheap).
- [ ] Browser tier picker + capability bench.
- [ ] Tier 2 end-to-end test: upload a fixture audio, run job, get
  results matching the local-mode reference within tolerance.

### Sync (doc 07)

- [ ] Desktop URL handler registration (`splitsmith://`).
- [ ] Desktop "Sign in to cloud" UI.
- [ ] Desktop sync_state.json persistence.
- [ ] Per-project "Sync to cloud" button + status.
- [ ] Tarball import flow (browser + worker).

### Billing (doc 08)

- [ ] Stripe account + product set up.
- [ ] `billing_events` table.
- [ ] `POST /api/v1/billing/checkout` (creates Checkout Session).
- [ ] `POST /api/v1/billing/portal` (creates Customer Portal session).
- [ ] `POST /api/v1/billing/webhooks/stripe` with signature
  verification.
- [ ] Free-tier "1 project max" enforcement.
- [ ] Premium-only paywall on Tier 2/3 endpoints.
- [ ] Account settings: storage used, jobs run, billing portal link.
- [ ] Account deletion flow (with 7-day grace).

### Frontend

- [ ] Auth screens (sign in, sign in success, sign in expired).
- [ ] First-run flow for hosted users (upload audio / sign up free).
- [ ] Project picker (lists user's hosted projects).
- [ ] Per-project view (reuses existing audit + coach UIs from the
  desktop, but pulls data via API).
- [ ] Tier override controls in project settings.
- [ ] Pricing page + Stripe Checkout entry point.
- [ ] Account settings page.
- [ ] Devices page.
- [ ] Marketing landing page.

### Cross-cutting

- [ ] OpenAPI schema reviewed; TypeScript types regenerated end-to-
  end.
- [ ] All API responses include `X-Splitsmith-API-Version: v1`.
- [ ] Trace IDs propagate from request through worker.
- [ ] Sentry sampling configured (low for healthy traffic, high for
  errors).
- [ ] Privacy policy + terms of service (template to draft, lawyer
  to review).
- [ ] Status page (Statuspage.io free tier or similar) for hosted
  uptime.

### Launch readiness

- [ ] Load test the hosted path (10 concurrent Tier 2 jobs;
  observe latency + error rate).
- [ ] DR plan: R2 backups (R2 has built-in replication option),
  Postgres point-in-time restore (Neon free tier supports 24h),
  documented restore procedure.
- [ ] Documentation: hosted user guide, desktop-link guide, FAQ.
- [ ] Beta-tester invite list (the user has IPSC squadmates who'd
  trial it).

## v2 -- bidirectional sync, raw upload, squad sharing

Goal (per doc 00, locked-in decisions): hybrid sync, opt-in raw
video, squad-level sharing. Postgres index for cross-match queries.

**Status:** not started. Detailed planning happens after v1 launch.

High-level scope:

- [ ] Bidirectional sync engine (per-file conflict copies, polled
  reconciliation).
- [ ] Raw video upload + Tier 3 compute path.
- [ ] Squad-share invites (`POST /projects/<id>/invitations`).
- [ ] Public read links (limited-scope share URLs).
- [ ] Cross-match indexing (Postgres tables for shot-time queries
  across all of a user's matches).
- [ ] Federated SSO (Google OAuth at minimum).
- [ ] Usage metering (informed by v1's measured usage).
- [ ] Soft + hard quota enforcement.
- [ ] Annual billing option.
- [ ] PostHog analytics + feature flags.

## v3+ -- public match repository

Goal (per doc 00): community-curated catalog of public matches.

**Status:** deferred. Architecture must support it (visibility =
'public' on `projects`); implementation is post-v2.

High-level scope:

- [ ] Public-visibility `visibility = 'public'` enforcement path
  (read-only without ACL membership).
- [ ] Discovery UI (browse / search public matches).
- [ ] Curation moderation (admin tools).
- [ ] Per-tenant retrain (optional premium feature).
- [ ] Cross-region replication for a global public catalogue.

## Deferred / explicitly out of scope

These came up during ideation and are not on any milestone:

- [-] Real-time collaborative editing. Not a fit for the workflow.
- [-] Mobile apps (iOS/Android). The web UI is responsive; native
  apps are a "if a real demand emerges" item.
- [-] Self-serve enterprise SSO (SAML, SCIM). Premium individual is
  the target market; enterprise SSO is a "if a customer asks"
  item.
- [-] On-prem appliance. The self-hosted Docker path covers this.
- [-] Real-time stage detection (live during a match). Stays out
  of scope per the project guidance ("real-time tool" is in the
  "what this project is NOT" list).

## Decision log (changes to the architecture)

Append-only. Format: `YYYY-MM-DD -- doc -- summary`. Bigger changes
get their own short note.

- 2026-05-15 -- 00 -- Initial doc set drafted. Four-question + three-
  question ideation captured in 00. Locked-in decisions table
  established.
- 2026-05-31 -- 11 -- Environment strategy decided. Two environments
  (staging + prod) across Railway / Neon / R2 / Lettermint /
  Cloudflare. Domain layout: apex `splitsmith.app` = marketing,
  `my.splitsmith.app` = the app (chose `my.` over `app.` to avoid the
  `.app` TLD stutter). Neon staging = long-lived branch. Promotion:
  merge to main -> staging, release-please release -> prod. Staging
  email = console (no real sends).

(future entries appended below as the architecture evolves.)
