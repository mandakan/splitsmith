# Self-hosted workers: registration, wake channel, dispatch policy

Date: 2026-07-04
Status: approved

## Problem

Compute jobs run on a Railway worker that is woken per enqueue (scale-to-zero).
Railway compute is the dominant cost during demos and testing. The operator has
home servers with plenty of CPU/RAM where Docker runs for free. We want a
Gitea-runner-like flow: generate a token in an admin UI, `docker run` an agent
at home with server URL + token, and have the system prefer those workers over
Railway - including the ability to disable any worker (Railway included) so the
dispatcher never wakes it.

## Decisions (made during brainstorming)

1. **Connection model: direct DB + R2.** Home workers run the existing
   Procrastinate worker code against Neon + R2, same as the Railway worker.
   Registration is a one-time credential bootstrap, not a new job transport.
   Workers are operator infrastructure; tokens are only handed to trusted
   operators. The HTTP job-relay model (no DB creds on the worker) is out of
   scope and would be the path for community-run workers later.
2. **Wake signal: outbound wake channel.** The agent holds an outbound SSE
   connection to the API; the server pushes wake events on enqueue. No inbound
   exposure needed at home. Accepted costs: the serve instance does not sleep
   while a channel is connected; the channel registry is in-process serve
   memory (single-replica seam, wakes are best-effort, safety nets cover
   misses).
3. **Dispatch: priority + auto-fallback.** Targets (including Railway, modeled
   as a built-in row) have `enabled` + `priority`. Top tier is woken first;
   escalate to the next tier immediately if no target in the tier is available,
   or after a grace window (~90s) if a wake was delivered but no worker
   heartbeat appeared.
4. **Admin gate: env allowlist.** `SPLITSMITH_ADMIN_EMAILS`; a user is admin
   iff their authenticated email is in the list.

## Data model

New global table `workers` - operator infrastructure, NOT a tenant table
(no `user_id`, no RLS; the multi-tenant checklist does not apply):

| column | type | notes |
| --- | --- | --- |
| `id` | uuid pk | |
| `name` | text unique | human label from the admin UI |
| `kind` | text | `self_hosted` or `railway` |
| `enabled` | bool | default true |
| `priority` | int | lower wakes first; self-hosted default 10, railway 100 |
| `registration_token_hash` | text nullable | one-time bootstrap token (sha256) |
| `token_expires_at` | timestamptz nullable | registration token expiry (~24h) |
| `registered_at` | timestamptz nullable | set on successful registration |
| `worker_token_hash` | text nullable | permanent channel credential (sha256) |
| `last_seen_at` | timestamptz nullable | channel heartbeat |
| `last_wake_at` | timestamptz nullable | last wake pushed |
| `info` | jsonb | agent version, hostname, concurrency |
| `created_at` | timestamptz | |

The Railway target is seeded as a `kind='railway'` row at API startup when the
existing `SPLITSMITH_WORKER_TRIGGER_TOKEN` / `SPLITSMITH_WORKER_SERVICE_ID`
env vars are present (idempotent upsert by kind; there is at most one railway
row). This gives Railway the same enabled/priority knobs with no special-case
UI.

## Registration flow

1. Admin clicks "Register worker", names it. Server creates a pending
   `workers` row and returns a one-time registration token (plaintext shown
   once) plus a copy-paste `docker run` command.
2. Home box: `docker run -v splitsmith-agent:/data <image> agent
   --server-url https://my.staging.splitsmith.app --token <TOKEN>`.
3. Agent calls `POST /api/workers/register` with `{token, info}`. Server
   validates single-use + expiry, sets `registered_at`, generates the
   permanent worker token, and returns
   `{worker_id, worker_token, credentials: {database_url, s3: {...}}}` -
   the server's own DB URL and R2 settings (Neon and R2 are publicly
   reachable, so the same values work from home).
4. Agent persists the bundle to its state dir (file mode 0600) and never
   registers again; subsequent starts go straight to the channel.

Accepted limitation: deleting a worker revokes its channel token but not the
shared DB/R2 credentials it already holds; real revocation means rotating
those at Neon/R2. Documented in the admin UI copy.

## Agent runtime

New CLI command `splitsmith agent` (same image). Long-lived process:

- Builds worker state once at boot so models stay warm across drains.
- Holds an outbound SSE connection to `GET /api/workers/channel`,
  authenticated by the worker token (public-path allowlist entry, same
  mechanism as share tokens). Sends periodic heartbeats; server updates
  `last_seen_at`.
- On `wake` event: runs the existing one-shot drain (`run_worker(wait=False)`)
  in-process, then disconnects from Postgres so Neon sleeps between jobs.
- Reconnects with exponential backoff; edge idle-timeouts are normal
  operation, not errors.
- On `disabled` event: stays connected, idles, does not drain.
- State dir via `SPLITSMITH_AGENT_STATE_DIR` (default `/data`).

## Dispatcher

`worker_trigger.py` generalizes from a single Railway launcher into a
dispatcher. `trigger()` keeps the existing 30s cooldown and heartbeat gate
(live `procrastinate_workers` heartbeat within 20s means a worker is already
draining - do nothing). Then:

1. Load enabled targets ordered by priority; group into tiers by priority
   value; take the top tier.
2. Self-hosted targets with a connected channel get a `wake` event. Targets
   without a connected channel are unavailable. Railway targets are woken via
   the existing `serviceInstanceRedeploy` mutation and always count as
   available.
3. If the top tier had no available target, escalate immediately to the next
   tier.
4. If a wake was delivered, arm a grace timer (default 90s, asyncio task in
   serve; a connected channel keeps serve awake so the timer survives): if
   pending jobs remain and no worker heartbeat appeared, escalate to the next
   tier.

Existing safety nets unchanged: boot retrigger, throttled pending-recheck on
`/api/health`, GH cron wake net. Channel connect triggers the same throttled
recheck (respecting the existing 300s throttle so reconnect churn does not
keep Neon awake).

Disable semantics: `enabled=false` means no wakes are sent; a connected but
disabled agent receives a `disabled` event and idles. Disabling the railway
row means the dispatcher never redeploys the Railway worker. Disable is
advisory for self-hosted workers (they hold DB creds), acceptable for
operator-owned infra.

## Admin gate, API, UI

- `SPLITSMITH_ADMIN_EMAILS` (comma-separated). `require_admin` FastAPI
  dependency. `/api/me` response gains `is_admin` so the SPA can show the nav
  entry.
- Endpoints:
  - `GET /api/admin/workers` - list with derived status
    (online = channel connected, offline, disabled)
  - `POST /api/admin/workers` - create pending worker, returns one-time token
  - `PATCH /api/admin/workers/{id}` - `enabled`, `priority`, `name`
  - `DELETE /api/admin/workers/{id}` - revoke tokens + remove row
  - Token rotation deferred: delete + re-register covers it.
- Worker-facing (worker-token or registration-token auth, on the public-path
  allowlist): `POST /api/workers/register`, `GET /api/workers/channel`.
- New admin SPA page "Workers": table with name, kind, status (word + dot,
  never color alone), priority, last seen, enabled toggle, delete; register
  dialog showing the token once plus the docker run snippet. Follows the
  overlay architecture conventions (z tokens, body portal, useDialogFocus).

## Environments

Staging and prod are independent registrations (separate DBs and tokens); one
agent container per environment. `SPLITSMITH_ADMIN_EMAILS` must be set per
environment in Railway.

## Testing

- Unit: dispatcher policy (tier selection, immediate + grace escalation,
  cooldown/heartbeat gates), registration semantics (single-use, expiry,
  hashing), channel auth, admin gate.
- Migration adds a table: run `pytest -m docker` locally before merge.
- SPA: typecheck + build + scoped eslint (no test runner exists).
- Manual staging validation: register a real home worker against
  my.staging.splitsmith.app, enqueue a detection job, confirm the home worker
  drains it and Railway does not boot; disable the home worker, confirm
  Railway fallback.

## Out of scope

- HTTP job relay (no-DB-creds workers) for untrusted operators.
- Per-worker scoped Neon/R2 credentials.
- Token rotation endpoint.
- Multi-replica wake-channel pub/sub.
