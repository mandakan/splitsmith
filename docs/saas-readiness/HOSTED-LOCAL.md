# Hosted-mode preview: `docker compose up` on your laptop

Reference for running the SaaS-foundation stack locally on a fresh
checkout. Used to validate that the Postgres-backed stores work
end-to-end before the production SaaS infrastructure exists.

This is a **preview**. There is only one user (the loopback
operator), no real authentication, and several feature areas don't
have hosted-mode codepaths yet -- see [What works today](#what-works-today)
below.

## Prerequisites

- Docker Engine 20.10+ with the Compose v2 plugin (`docker compose
  version` must succeed).
- The following host ports must be free:
  - `5174` -- splitsmith API
  - `5432` -- Postgres (exposed so you can `psql` into it for debugging)
  - `9000` / `9001` -- MinIO S3 API + web console
- ~500 MB free disk for the splitsmith image + Postgres / MinIO volumes.

That's it. No Python, ffmpeg, or auth-vendor credentials needed on
the host -- everything runs inside containers.

## Quick start

```bash
git clone https://github.com/mandakan/splitsmith.git
cd splitsmith
docker compose up --build
```

First boot takes 60-90 seconds (image build + Postgres healthcheck +
Alembic migrations). Subsequent boots are ~10 seconds because the
image is cached.

When the API is ready you'll see:

```
splitsmith-1  | INFO:     Uvicorn running on http://0.0.0.0:5174 (Press CTRL+C to quit)
```

## Validation

In another terminal:

```bash
# Process liveness
curl -s http://localhost:5174/api/health
# -> {"status":"ok","version":"0.3.0","bound":false,...}

# The Postgres-backed recent-projects store
curl -s http://localhost:5174/api/me/recent-projects
# -> {"projects": []}

# The Postgres-backed scoreboard-identity store
curl -s http://localhost:5174/api/me/scoreboard-identity
# -> null

# The Postgres-backed job backend
curl -s http://localhost:5174/api/me/jobs
# -> []

# The hosted loopback user (proves the auth bootstrap landed)
curl -s http://localhost:5174/api/me
# -> {"id":"01K...","email":"loopback@hosted.local","display_name":"Hosted Operator"}

# psql into Postgres to inspect the schema directly
docker exec splitsmith-postgres-1 psql -U splitsmith -d splitsmith \
  -c '\dt'
# -> alembic_version, compute_jobs, recent_projects, users
```

The driver smoke test that codifies the same flow is in
`tests/test_hosted_docker_smoke.py`; run it with:

```bash
pytest -m docker
```

## What works today

The hosted preview ships the persistence layer that the
SaaS-foundation ladder (#416-422) introduced. Everything below is
served from Postgres rather than `~/.splitsmith/*.json` or the
process-local job registry.

### Per-user state

| Endpoint | Behaviour | Backed by |
|----------|-----------|-----------|
| `GET /api/me` | Loopback hosted user (id + email) | `HostedLoopbackAuth` (bootstrapped row in `users`) |
| `GET /api/me/recent-projects` | List recent project entries | `recent_projects` table |
| `POST /api/me/recent-projects/forget` | Remove an entry by path | `recent_projects` table |
| `POST /api/me/recent-projects/bind` | Bind a path as recent | `recent_projects` table (path must resolve to a real Match folder; in-container that means a mounted volume) |
| `GET /api/me/scoreboard-identity` | Read pinned SSI identity | `users.scoreboard_identity` JSON column |
| `PUT /api/me/scoreboard-identity` | Pin an identity | `users.scoreboard_identity` JSON column |
| `DELETE /api/me/scoreboard-identity` | Unpin the identity | `users.scoreboard_identity` JSON column |
| `GET /api/me/jobs` | List job snapshots | `compute_jobs` table |
| `GET /api/me/jobs/{id}` | Single job snapshot | `compute_jobs` table |
| `POST /api/me/jobs/{id}/cancel` | Request cooperative cancel | `compute_jobs` table |
| `POST /api/me/jobs/{id}/acknowledge` | Dismiss a failed job | `compute_jobs` table |
| `POST /api/me/jobs/acknowledge-failures` | Dismiss all failures | `compute_jobs` table |

### Server-level

| Endpoint | Behaviour |
|----------|-----------|
| `GET /api/health` | Process liveness + version |
| `GET /api/server/features` | Lab feature flag |
| `GET /api/models/status` | Slim-model registry status (likely "no models" in the slim install) |

### Restart durability

Job state persists across container restarts. A job that was
`pending` or `running` at the moment of restart is swept to `failed`
with `error="server restarted before this job finished"` -- the SPA
sees a real terminal state instead of polling forever.

Try it:

```bash
# Restart just the splitsmith container; postgres + minio stay up.
docker compose restart splitsmith

# The recent-projects list still has what you put in it.
curl -s http://localhost:5174/api/me/recent-projects
```

## What does NOT work yet

The hosted preview is intentionally narrow. These areas don't have
hosted-mode codepaths and either won't function or will function in
local-machine-only mode:

### No SPA frontend

The Dockerfile doesn't build `ui_static/dist`, so visiting
`http://localhost:5174/` returns nothing useful. The API works;
the SPA does not. Drive it with `curl` or `httpie` instead.

### No real authentication

`HostedLoopbackAuth` upserts one row in `users` (email
`loopback@hosted.local`) and resolves every request to it.
**There is no login flow, no session token, no per-user data
separation.** Anything you POST is owned by the loopback user.
`MagicLinkAuth` + vendor selection lands separately; until then,
treat the stack as single-tenant and do not point it at the internet.

### No upload / storage pipeline

MinIO is up and the splitsmith container has `SPLITSMITH_S3_*` env
vars set, but no API codepath consumes them yet. `S3Storage` (the
class from #415) exists but is not wired into the upload routes.
Uploads + downloads still go through filesystem paths; in the
container those paths are container-local and lost on `down -v`.

Practical effect: workflows that need source video on disk
(detect-beep, trim, shot-detect, export) don't have a way to
deliver that video into the container yet. You can scaffold an
empty Match folder via `POST /api/match/create-manual` but you
can't ingest footage.

### Slim model install -- shot detection partly degraded

The Docker image uses the slim runtime (`uv sync --no-dev`), which
ships the ONNX-based voter B + voter C path. Heavy torch /
transformers / panns-inference are intentionally absent. Implications:

- Voter E (the optional visual probe) and the lab eval flow are
  unavailable.
- Model weights aren't bundled in the image. `_maybe_submit_model_download`
  fires on boot and tries to fetch them from the URLs in the
  shipped calibration; if those URLs require auth or your
  container has no outbound HTTP, the job ends in `failed` (which
  is fine -- it just means shot detection wouldn't work until the
  weights are present).

### Workers: monolith, not a separate fleet

There is **no `splitsmith worker` command**. The hosted preview is
intentionally serve-all-in-one:

- `PostgresJobBackend` writes job rows to Postgres on submit.
- The **same** `splitsmith serve` process runs a
  `ThreadPoolExecutor` that pops those jobs and executes them.
- One container = HTTP server + worker pool. Scaling the container
  scales both; there is no separate "API tier" vs "worker tier"
  split yet.

Practical effect: submitting via `POST /api/...` just works, the
container runs the work, and `GET /api/me/jobs/{id}` reflects
progress + terminal state. You don't need to start anything
separately. You don't need a queue broker; Postgres is the
durability layer but not the work-stealing queue yet.

On container restart, any `pending` / `running` row is swept to
`failed` with `error="server restarted before this job finished"`
-- no other process will resume them.

**What's missing for a real worker fleet** (deferred to a follow-up PR):

- A `splitsmith worker` CLI that boots a long-lived process which
  pops jobs from a real queue and registers task handlers by name.
- A Postgres-native queue layer ([Procrastinate](https://procrastinate.readthedocs.io/)
  is the planned pick -- reuses our existing DB; no Redis
  dependency). It would land a sibling `procrastinate_jobs` table
  alongside `compute_jobs`.
- A `submit()` shape change from `fn=callable` (closure-passing)
  to `task_name + args` (worker-side registered tasks) -- this
  cascades through ~30 callsites in `server.py`.

Until then, two splitsmith containers pointed at the same Postgres
would each only see / dispatch their own submissions; cross-container
work-stealing doesn't exist.

### Workflows that touch the local filesystem

`splitsmith ui` (local mode) opens recent projects from paths on
your laptop. In the container the filesystem is ephemeral, so the
recent-projects binding flow only works for paths that exist
inside the container -- there are no useful ones unless you mount
a volume.

## Teardown + state reset

```bash
# Stop containers, keep volumes (so a re-up resumes Postgres state)
docker compose down

# Stop + wipe volumes (fresh DB on next up)
docker compose down -v

# Full clean including images (forces a rebuild next time)
docker compose down -v
docker rmi splitsmith-splitsmith
```

The smoke test (`tests/test_hosted_docker_smoke.py`) runs
`down -v` at the end of every session so consecutive runs always
start from an empty Postgres.

## Troubleshooting

### "Container is unhealthy" right after `docker compose up`

The Compose healthcheck runs every 5s and gates on `/api/health`
returning 200. The API needs ~10-30s after the container starts to
finish migrations + bootstrap. Watch the logs instead:

```bash
docker compose logs -f splitsmith
```

Once you see `Uvicorn running on http://0.0.0.0:5174` the API is
live, even if Compose still shows `unhealthy` for a few more seconds.

### "Task ... got Future ... attached to a different loop"

You're on an older version. Pull main; #423 introduced `NullPool`
for the hosted engine, which fixes the asyncpg event-loop binding
crash.

### Postgres "could not receive data from client"

Normal -- shows up in Postgres logs every time the splitsmith
container exits cleanly. Ignore.

### Port already in use

Pick another host port in `docker-compose.yml`:

```yaml
services:
  splitsmith:
    ports:
      - "8174:5174"   # left side = host port
```

## Architecture recap

```
        +----------------------+
        |  docker compose      |
        +----------+-----------+
                   |
       +-----------+-----------+
       |           |           |
  +----v----+ +---v----+ +----v----------+
  | postgres| | minio  | | splitsmith    |
  | (5432)  | | (9000) | | api (5174)    |
  +---------+ +--------+ +----+----------+
                              |
                              |  splitsmith serve
                              |    SPLITSMITH_MODE=hosted
                              |    SPLITSMITH_DATABASE_URL=...
                              |
                   +----------v---------+
                   |  HostedLoopbackAuth|  -> upserts users row
                   |  PostgresRecent... |  -> recent_projects table
                   |  PostgresScoreb... |  -> users.scoreboard_identity
                   |  PostgresJobBack...|  -> compute_jobs table
                   +--------------------+
```

The same `create_app` factory powers both local mode
(`splitsmith ui`) and hosted mode (`splitsmith serve`). The only
runtime switch is `SPLITSMITH_MODE=hosted`, which gates the
`_apply_hosted_mode_wiring` call inside `create_app`.

## Related docs

- [`01-deployment-modes.md`](01-deployment-modes.md) -- the
  two-modes design and what each contains.
- [`02-tenancy-and-identity.md`](02-tenancy-and-identity.md) --
  the auth + identity model the future `MagicLinkAuth` lands into.
- [`04-compute-backends.md`](04-compute-backends.md) -- the worker
  dispatch model `PostgresJobBackend` plugs into.
- [`10-singleton-elimination.md`](10-singleton-elimination.md) --
  the doc that drove the AppState refactor every Postgres-backed
  store followed.
