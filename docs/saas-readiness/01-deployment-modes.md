# 01 -- Deployment modes

This doc defines the **two deployment modes Splitsmith ships in** and
the contracts each mode must honour. It exists so that every later doc
can say "in local mode X, in hosted mode Y" without re-litigating what
those modes mean.

The two modes are **local** and **hosted**. They share the same
codebase. The differences are which implementations of the three
abstractions (Storage, Auth, ComputeBackend -- see docs 02-04) are
wired in at startup, plus a small number of UI affordances.

A third mode -- **self-hosted** (a user runs the FastAPI app on their
own server) -- is not a v1 deliverable but the abstractions must not
foreclose it. See "Self-hosted (deferred)" below.

## Mode summary

| Mode       | Who runs the server           | Where data lives         | Auth                | Compute       | v1? |
| ---------- | ----------------------------- | ------------------------ | ------------------- | ------------- | --- |
| Local      | The user (`splitsmith ui`)    | User's disk              | None (loopback)     | Tier 1 only   | yes |
| Hosted     | We do (Fly.io / Railway)      | Cloudflare R2 + Postgres | Magic link (Clerk)  | Tier 2 + 3    | yes |
| Self-host  | A third party                 | Their disk + their S3    | Whatever they wire  | Their choice  | no  |

## Local mode -- the default

**Promise:** today's `splitsmith ui` user must not see any change.
Same install, same UX, same offline guarantee.

**What it means concretely:**

- Server bound to `127.0.0.1` by default; no external listeners.
- `Storage` = `FilesystemStorage` rooted at `~/.splitsmith/projects/`
  (or `$SPLITSMITH_PROJECTS_DIR` if set).
- `Auth` = `LoopbackAuth` -- a no-op implementation that returns a
  fixed `User` for every request. No login screen, no cookies, no
  CSRF tokens.
- `ComputeBackend` = `LocalComputeBackend` -- runs the existing
  Python ensemble in-process or via the existing worker.
- No outbound network calls except: (a) optional update check, (b)
  optional crash report to Sentry if the user opted in.
- No SQLite database is created. State lives in `match.json` +
  per-shooter JSON files exactly as today. (If we need a DB later
  for indexing, it lives at `~/.splitsmith/index.sqlite` and is
  rebuildable from the JSON canonical files at any time.)
- No quota display. No "upgrade" buttons. No "sign in" affordance
  unless the user explicitly enables hosted-account integration.

**What local mode is NOT:**

- Not a multi-user mode. The loopback assumption is "one human at the
  keyboard". If two people share a desktop OS account they share one
  Splitsmith identity.
- Not network-reachable. The server refuses to bind to `0.0.0.0` in
  local mode unless the user passes `--unsafe-bind-all`. This is a
  guardrail against accidentally exposing private match data.

**Optional hosted-account hook:** the local app can be linked to a
hosted account (see 07-sync-and-migration.md). Linking is opt-in,
adds a "Sign in to cloud" action in settings, and unlocks the
per-match "Sync to cloud" buttons in the picker. Linking does NOT
change any other local-mode behaviour -- detection still runs
locally, files still live on disk, the loopback auth still applies
to local API calls.

## Hosted mode -- the SaaS option

**Promise:** zero install, works in any modern browser, audio-only
upload by default, premium pricing tier gates access.

**What it means concretely:**

- Server runs behind a public TLS endpoint (`splitsmith.app` or
  similar). Bound to `0.0.0.0` inside the deploy.
- `Storage` = `S3Storage` pointing at Cloudflare R2 (S3-compatible,
  $0 egress -- see 03-storage-layer.md for why).
- `Auth` = `MagicLinkAuth` backed by Clerk or WorkOS (final pick
  per 02-tenancy-and-identity.md). Sessions are httpOnly cookies.
- `ComputeBackend` = `RemoteComputeBackend` running CLAP + PANN
  server-side, with the GBDT shipped to the browser as ONNX-Web (see
  04-compute-backends.md, Tier 2). Tier 3 (full cloud, raw video)
  available per-match.
- Database: Postgres on Neon or whatever the deploy target offers.
  Holds users, projects, project members, billing entitlements,
  upload sessions. **Does NOT hold detection results -- those stay
  in the JSON files in R2** (principle 6 in doc 00).
- Background jobs run on `arq` workers (or hosted Inngest / Trigger
  if ops cost flips against us -- see doc 00).
- Stripe Checkout is the paywall. Webhooks update entitlements in
  Postgres. See 08-billing-and-quotas.md.
- Sentry is on, with PII scrubbing. PostHog optional in v2.

**What hosted mode is NOT (in v1):**

- Not multi-shooter shared. Each user sees only their own matches.
  (Squad sharing is v2 per 02.)
- Not raw-video-by-default. Audio upload is the default; raw upload
  is opt-in per match. (See 05-uploads-and-streaming.md.)
- Not a public match catalog. (v3.)
- Not retrain-per-tenant. The shipped ensemble artifacts apply to
  everyone.

## Self-hosted (deferred)

A third party should be able to `pip install splitsmith[server]`,
set environment variables (`STORAGE_BACKEND=s3`, `AUTH_BACKEND=authjs`,
`STRIPE_SECRET_KEY=...` or omit billing entirely), and run a private
deployment for their squad / club / range.

This is **not a v1 deliverable**, but the abstractions in 02-04 must
not preclude it. Concretely:

- No hardcoded references to Clerk's hosted endpoints. The auth
  abstraction must accept any provider implementing the interface.
- No hardcoded references to Stripe webhook URLs in the data model.
  Entitlements are local rows; the source of those rows is pluggable.
- Configuration is environment-driven, not compiled in.

If we discover during v1 implementation that one of the picked
hosted services makes self-hosting impossible (e.g. an auth provider
that hard-requires their domain in the OAuth callback), we either pick
a different provider or accept that self-hosting needs an alternate
auth implementation. Both are acceptable -- what's NOT acceptable is
silently building in a coupling that someone discovers only when they
try to self-host.

## Mode selection at runtime

A single `splitsmith` binary does not exist in v1. There are two
entry points:

- `splitsmith ui` -- launches local mode. Reads `~/.splitsmith/
  config.yaml` for overrides; otherwise defaults documented above
  apply.
- `splitsmith serve` (or the deploy's equivalent, e.g.
  `uvicorn splitsmith.api:app`) -- launches hosted mode. Reads
  required env vars (`SPLITSMITH_MODE=hosted`,
  `STORAGE_BUCKET=...`, `DATABASE_URL=...`, `AUTH_PROVIDER=...`,
  etc.). Refuses to start if any required env var is missing.

The mode is **set at process startup** and immutable for the life of
the process. The same binary can run in either mode, but it cannot
switch modes dynamically. This keeps every "is this hosted?" check
in the codebase resolvable to a constant after startup.

## Cross-mode invariants

Both modes must honour these invariants. Violating them breaks the
"same codebase, two deployments" promise.

1. **JSON canonical.** The on-disk / in-bucket layout per project
   is identical -- `match.json`, `shooters/<slug>/project.json`,
   `audits/`, etc. (See 03-storage-layer.md.) A project tarball
   exported from local mode imports cleanly into hosted mode and vice
   versa.

2. **Same detection ensemble.** Local mode's Tier 1 and hosted
   mode's Tier 2/3 produce results from the same 3-voter ensemble
   with the same calibrated thresholds. Hosted may use ONNX-exported
   models for portability; the artifacts come from the same training
   run as the local Python models. We don't ship divergent
   detection per mode.

3. **Same JSON schemas.** Pydantic models for projects, audits,
   shots, etc. are mode-agnostic. No `local_only` or `hosted_only`
   fields. If hosted mode needs richer metadata (e.g. who uploaded
   what), it goes in a sidecar (the Postgres index) -- not in the
   canonical JSON.

4. **Same UI components.** The React frontend has no `if (hosted)`
   branches in component logic. Mode-specific affordances (login
   button, quota chip, upgrade CTA) are conditionally rendered at
   the layout level based on a single `mode` value supplied by the
   server at page load. Component-level code stays mode-blind.

5. **Same orchestration.** The detection-pipeline orchestration
   (split, calibrate, detect, audit) is the same code path in both
   modes. The differences are which `ComputeBackend` it dispatches
   to and where the input bytes come from, both of which are
   already abstracted.

## What this implies for the codebase

- The current `splitsmith ui` entry point becomes one of two entry
  points; it's not deprecated.
- The existing `Storage`-shaped helpers in the project layout module
  formalise into an actual interface (see 03).
- The existing implicit "no auth" assumption formalises into a
  `LoopbackAuth` implementation (see 02).
- The current worker becomes one of two `ComputeBackend`
  implementations (see 04).
- A new `splitsmith serve` entry point + dependency injection wiring
  for the hosted-mode implementations.
- Deployment: a `Dockerfile` + Fly.io / Railway config; a `docker-
  compose.yml` for local server-mode dev (so contributors can run
  hosted-mode locally with R2 LocalStack + Postgres in containers).

## Open questions

- **Should `splitsmith ui` ever optionally enable network listeners?**
  E.g. a user wants their phone on the same Wi-Fi to view match
  results from the desktop. This is a real ergonomic ask but it's a
  multi-device-single-user case, not multi-user. Defer until someone
  asks; the hosted mode covers cross-device for users who want it.
- **Where does the desktop "linked-to-cloud" state live?** Probably
  `~/.splitsmith/auth.json` with a refresh token. Detail in 02.
- **Does hosted mode ever need a SQLite fallback for local dev?**
  Probably yes -- a contributor without R2 + Postgres credentials
  should still be able to `docker compose up` and exercise the
  hosted-mode code paths. Detail in 03.
