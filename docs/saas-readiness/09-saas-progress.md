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

- [ ] Extract `Storage` interface; replace direct `pathlib.Path` use
  in project layout with `FilesystemStorage` calls.
  (See 03-storage-layer.md.)
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
- [~] Extract `ComputeBackend` interface; wrap today's ensemble
  pipeline as `LocalComputeBackend`. The orchestration in
  `cli.py` and the FastAPI server call the backend, not the
  ensemble directly.
  (See 04-compute-backends.md.) Protocol + `LocalComputeBackend`
  shipped; `state.compute` wired in `create_app`; the shot-
  detect worker (per-stage + bulk) goes through
  `state.compute.detect_stage`. CLI orchestration (``splitsmith
  cli.py``) and `splitsmith.mcp.detect_tools` still call the
  ensemble directly. Auxiliary endpoints that use the runtime
  for non-detect purposes (CLAP probes for promote-secondary,
  calibrated-camera-models) keep direct runtime access until the
  Protocol exposes those methods.
- [ ] Add a `splitsmith serve` entry point alongside `splitsmith ui`.
  Same code; different mode wiring at startup.
- [ ] Add `docker compose` for hosted-mode dev (Postgres + LocalStack
  S3 + the worker + the API server). Contributors without R2
  credentials can exercise the hosted path locally.

### Hosted infrastructure

- [ ] Pick deploy target (Fly.io vs Railway -- doc 00 open question).
- [ ] Pick auth provider (Clerk vs WorkOS vs Auth.js -- doc 00 open
  question). Build a v1-shaped prototype with each first.
- [ ] Provision R2 bucket + lifecycle rule for incomplete multipart
  uploads.
- [ ] Provision Postgres (Neon free tier likely).
- [ ] Provision Redis if going with `arq` (or skip if going with
  Procrastinate).
- [ ] Wire Sentry on the API + worker.
- [ ] Wire Resend (or Postmark) for magic-link email delivery.
- [ ] Set up the `splitsmith.app` domain + Cloudflare in front.

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

- [ ] `S3Storage` implementation (fsspec-backed, R2 endpoint).
- [ ] `MemoryStorage` for tests.
- [ ] Per-project storage layout standardised; existing local
  project directories remain compatible.
- [ ] `signed_url` implementation for both backends.
- [ ] Reindex helper (rebuild `projects` rows from R2 walk).
- [ ] Per-user prefix decision (`users/<id>/projects/<id>/` vs
  `projects/<id>/`) -- pin during implementation.

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

- [ ] tus-py-server wired with S3 backend pointing at R2.
- [ ] `upload_sessions` table + lifecycle (24h expiry + cron abort).
- [ ] `POST /api/v1/uploads` to start a session.
- [ ] tus-js-client integration in the browser SPA.
- [ ] Browser audio extraction (WebAudio -> 16 kHz mono WAV).
- [ ] Sha256 verification on upload completion.

### Compute (doc 04)

- [ ] CLAP + PANN model artifacts baked into the worker Docker image.
- [ ] ONNX-Web GBDT artifact built + served from `/static/models/`.
- [ ] `compute_jobs` table.
- [ ] `RemoteComputeBackend` (server-side wrapper that creates jobs).
- [ ] `arq` worker that processes Tier 2 jobs (CLAP + PANN
  inference + features back).
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

(future entries appended below as the architecture evolves.)
