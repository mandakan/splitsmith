# Hosted connection pooling

Issue #1178, part of epic #1183. Status: approved design, 2026-10-03.

## Goal

Stop opening a new Postgres connection for every database call in hosted
mode, without reintroducing the crash that made us stop pooling, and
without changing anything the desktop app or the docker compose stack
does.

Measured on production (Railway `serve`, 2026-10-03): a route that
touches no database answers in 8-12 ms; one that does one user lookup
(`GET /api/me`) takes 220-275 ms. The gap is one TCP + TLS + auth
handshake to Neon's pooler in eu-central-1 from Railway EU West, paid on
every call because the hosted engine is `NullPool`. The match picker and
the open-match routes make dozens of such calls in sequence (#1179,
#1180); shrinking each call is this spec, shrinking the count is theirs.

Done when: the docker-compose regression test below passes, and the
production HTTP log shows `GET /api/me` under 60 ms warm.

## What exists today (2026-10-03)

`splitsmith.db.engine.create_engine(url, pool_disabled=True)` builds the
one hosted engine (`ui/server.py:6865`) with `poolclass=NullPool`. Every
`session()` opens a fresh asyncpg connection and closes it on release.

That was deliberate (#423, 2026-05-26). asyncpg binds a connection to the
event loop that created it. `docker compose up` crashed at boot with
`RuntimeError: Task <... _sweep_stuck_jobs_on_boot()> got Future <...>
attached to a different loop` because two `asyncio.run(...)` calls on the
boot path each made a loop, and SQLAlchemy's default pool handed the
second loop a connection owned by the first. aiosqlite tolerates that, so
the unit suite was green; only the real stack crashed.

The pattern has since spread rather than shrunk. Database work runs on a
brand-new event loop from:

- `splitsmith.async_bridge.run_sync` (13 call sites: the model `save()`
  paths in `match_model.py` / `match_project.py`, the `AppState` state
  accessors in `ui/server.py`). With no running loop it calls
  `asyncio.run`; with a running loop (an async handler) it spawns a
  throwaway thread and runs `asyncio.run` there.
- ~30 direct `asyncio.run(...)` sites: `ui/server.py`,
  `db/job_backend.py` (every worker callback: `_begin_run`,
  `_finalize_run`, `_patch_async`, `_is_cancel_requested_async`, and the
  boot sweep), `ui/youtube_api.py`, `ui/embedded.py`.

So a pooled connection would be created on loop A and reused from loop B
on essentially the next call. NullPool is the only thing preventing the
#423 crash. "Per-call TCP handshake cost is fine at the call rates here"
(the docstring) was true for one loopback user; it is the dominant cost
on the hosted picker and open-match routes now.

Two facts about the surroundings that bind the design:

- **Neon must still scale to zero.** The hosted stack is designed to
  cost nothing while unused (plan 2026-07-03, Railway cost-optimal
  deploy). Neon's documented behaviour: connections and queries reset the
  5-minute suspend timer, and the Activity Monitor closes idle
  connections after 5 minutes. An idle pooled connection therefore does
  not keep the compute awake; Neon closes it, then suspends. A pool is
  compatible as long as it generates no traffic of its own.
- **Neon's pooler is PgBouncer in transaction mode.** The URL carries
  `prepared_statement_cache_size=0` for that reason; it stays.

Async handlers already run on one long-lived loop (uvicorn's) and await
the stores directly (53 `await state.<store>.` sites in `ui/server.py`).
The Procrastinate worker (`queue.run_worker`, and the self-hosted agent's
`_run_worker_once`) runs on one long-lived loop too and reaches the same
`_build_tenant` stores; job bodies run on `asyncio.to_thread` threads
and bridge back with `asyncio.run` per call.

## Design

### Shape: a pool per long-lived loop, and one shared database thread

After this change every hosted process has exactly two long-lived event
loops that run database work:

1. **The main loop** -- uvicorn's in `serve`, the Procrastinate worker's
   in `worker`. Async handlers and the worker's task function keep
   awaiting the stores on it, unchanged.
2. **The `DbRunner` loop** -- one daemon thread per process with a
   persistent event loop, started by the hosted wiring. Every sync
   caller submits its coroutine there and blocks on the result.

Each loop lazily gets its own pooled engine. No database work ever runs
on a throwaway loop again, so no connection is ever used from a loop
other than the one that created it.

Why two pools and not one on the runner: `run_sync` is also called from
async handlers (the "loop already running" branch), and a coroutine
cannot run on the loop that is currently executing its caller. That
branch needs a second loop. Today that is a fresh thread per call; here
it is one persistent thread. Routing the async handlers' 53 awaits
through the runner instead would add a thread hop per query on the hot
path and change every site for no gain.

Why not a sync driver (psycopg) for the threaded paths: two drivers, two
dialects, and the RLS `after_begin` listener implemented twice.

### `splitsmith.db.engine`

`create_engine(url, *, echo=False, pool_disabled=False)` keeps its
signature for the tests that call it directly; after this change nothing
under `src/` passes `pool_disabled=True` (`alembic/env.py` builds its own
engine with `poolclass=NullPool` and does not go through this function).
New:

```python
class LoopEngines:
    """One pooled AsyncEngine per *adopted* long-lived event loop."""

    def __init__(self, url: str, *, echo: bool = False, pool_size: int = 5, max_overflow: int = 10) -> None: ...

    def adopt_current_loop(self) -> AsyncEngine:
        """Mark the running loop long-lived; its pooled engine, created on first call."""

    def for_current_loop(self) -> AsyncEngine:
        """The running loop's engine if adopted, else the shared NullPool fallback."""

    async def dispose_current_loop(self) -> None: ...
    async def dispose_fallback(self) -> None: ...
```

- Only an **adopted** loop gets a pool. The two long-lived loops adopt
  themselves at known points: the `DbRunner` on its thread's start, the
  app loop in the hosted lifespan's startup, the worker loop at the top
  of `queue.run_worker`. Any other loop -- a `concurrent.futures`
  thread's `asyncio.run`, the re-entrant fallback in `run_sync`, a test's
  own loop -- gets one shared **fallback** engine with `NullPool`, which
  never reuses a connection and is therefore safe from every loop. That
  fallback is exactly what the whole process used before this change,
  so an un-adopted path is never worse than today, only not faster.
- For a `postgresql+asyncpg` URL, adopted engines are keyed on the loop
  in a `weakref.WeakKeyDictionary` (loops are weak-referenceable, checked
  on 3.12 / asyncpg 0.31 / SQLAlchemy 2.0.50); a collected loop takes its
  entry with it, and `dispose_current_loop` disposes explicitly on
  shutdown, on the loop that owns the connections.
- For any other URL (SQLite in tests and `test_hosted_mode_boot`) every
  method returns the fallback engine: **one** `NullPool` engine, whatever
  loop asks, which is byte for byte what the hosted wiring built before.
  A per-loop engine on `sqlite:///:memory:` would be a separate database
  per loop, which the 32 tests that set `SPLITSMITH_DATABASE_URL` to
  SQLite would not notice until they failed on data that "vanished".
- Pool settings for asyncpg engines: `pool_size=5`, `max_overflow=10`,
  `pool_pre_ping=True`, `pool_timeout=30`. No `pool_recycle`, no warm-up,
  no background ping: the pool makes no traffic the request did not ask
  for, which is the scale-to-zero condition. `pre_ping` is what turns a
  connection Neon closed at the 5-minute mark into a silent reconnect.
  `connect_args` stay as today (SSL from the URL).
- Bound: per process at most 2 x (5 + 10) = 30 connections, inside
  Neon's pooler limits and `postgres:16`'s default 100.

`sessionmaker(engine)` gains a sibling that is what the hosted wiring
hands out:

```python
def loop_sessionmaker(engines: LoopEngines) -> Callable[[], AsyncSession]:
    """A session factory that picks the current loop's engine at open time."""
```

It returns a callable with the same `() -> AsyncSession` contract the
stores already type as `async_sessionmaker` (checked: every store calls
it with no arguments). `tenant_session_factory(base_factory, user_id)`
wraps it unchanged; the `after_begin` listener attaches per session as
today, so the RLS GUC and the read-only guard are untouched.

### `splitsmith.async_bridge`

```python
class DbRunner:
    """A daemon thread with a persistent event loop for sync callers."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run ``coro`` on the runner loop and block until it is done."""
```

`run` captures `contextvars.copy_context()` on the **calling** thread and
creates the task on the runner loop with `context=ctx`
(`loop.create_task(coro, context=ctx)`, 3.11+). This is the one subtle
requirement: `asyncio.run_coroutine_threadsafe` creates the task on the
target thread, so without the explicit context the RLS listener's
`share_request_is_read_only()` would read the runner thread's empty
`current_share_scope` and a read-scoped share request would stop getting
`SET TRANSACTION READ ONLY` (#779). A test pins this.

`run_sync(coro)` becomes:

1. A runner is installed (`async_bridge.install_runner(runner)`, done by
   the hosted wiring) -> `runner.run(coro)`, whether or not a loop is
   running on the calling thread. Blocking an async handler's loop for
   one small query is what the throwaway-thread branch already does
   today; this does not change loop-friendliness.
2. The one re-entrant case -- the calling coroutine is itself running
   *on* the runner loop (a sync state accessor reached from inside a
   coroutine a sync handler submitted) -> the current throwaway-thread
   behaviour, because blocking the runner on itself would deadlock. That
   fresh loop is un-adopted and gets `LoopEngines`' NullPool fallback, so
   it is safe, merely not pooled.
3. No runner (local mode, tests that never built the hosted app) -> the
   current behaviour, byte for byte: `asyncio.run` with no running loop,
   the throwaway thread otherwise.

The runner is process-global and installed once; `stop()` is called from
the app's shutdown hook (which also runs `LoopEngines.dispose_all()` for
the main loop's engine and schedules the runner loop's disposal before
the thread exits). Tests that build several hosted apps install and tear
down through a fixture.

### Callers

Every `asyncio.run(<db coroutine>)` in hosted-reachable code becomes
`run_sync(...)`: `ui/server.py` (the sync-handler sites, the boot sweep
in `PostgresJobBackend.__init__`), `db/job_backend.py` (`_run`,
`_finalize_with_timings`, `_patch`, `_is_cancel_requested`),
`ui/youtube_api.py`. `ui/embedded.py` and the local-mode boot sites may
move to `run_sync` for uniformity; with no runner installed they behave
exactly as before.

Not a caller: `cli.py`'s `asyncio.run(_run_worker...)` and the agent's
drainer are the main loop, not a bridge.

`JobHandle` (the body-side bridge: `patch`, `is_cancel_requested`) gets
the same treatment through the backend methods it calls; a job body on an
`asyncio.to_thread` thread submits to the runner like any sync caller.

### Hosted wiring (`ui/server.py:6859`)

```python
engines = LoopEngines(url)
state.db_engines = engines
session_factory = loop_sessionmaker(engines)
runner = get_runner()
if runner is None or not runner.is_running:
    runner = DbRunner(on_start=engines.adopt_current_loop, on_stop=engines.dispose_current_loop)
    runner.start()
    install_runner(runner)
```

The runner is process-global: a second `create_app` in the same process
(tests) reuses it. The hosted lifespan adopts the app loop at startup and
disposes its engine at shutdown; `queue.run_worker` does the same for the
worker loop.

The comment block that explains `pool_disabled=True` is replaced by one
that names the loop-binding rule and points here. The shutdown event
disposes and stops.

### What does not change

- **Desktop / local mode.** Never builds a Postgres engine, never installs
  a runner. Its stores are filesystem and `JobRegistry`. Every path it
  shares with hosted (`run_sync`, `embedded.py`) takes the "no runner"
  branch, which is the current code.
- **Docker compose.** Hosted mode on `postgres:16-alpine` with the
  `worker` service: the stack #423 crashed on, now the regression gate.
  No compose or env change.
- **Alembic.** `alembic/env.py` keeps NullPool and the #559 connect retry.
- **Procrastinate.** The deferrer is psycopg with its own pool
  (`queue.py`); untouched.
- **Store code.** No store, model or handler changes how it opens a
  session or awaits a query.

## Testing

Unit (`tests/test_async_bridge.py`, `tests/test_db_engine_loops.py`):

- `DbRunner.run` from a thread with no loop; from inside a running loop;
  propagates a ContextVar set on the caller (set `current_share_scope`,
  assert `share_request_is_read_only()` is true inside the coroutine);
  surfaces the coroutine's exception; `stop()` joins.
- `LoopEngines`: an asyncpg URL yields distinct engines on two loops and
  the same engine twice on one (constructed with `create_async_engine`
  patched to a recorder; no Postgres needed); a SQLite URL yields one
  engine from two loops; `dispose_all` disposes each once.
- `run_sync`: with a runner installed, from both caller kinds, lands on
  the runner loop (assert `asyncio.get_running_loop() is runner.loop`);
  with none, keeps today's behaviour.
- Mutation drill before merge: delete the `context=ctx` argument and
  watch the ContextVar test fail; swap the WeakKeyDictionary for a plain
  dict keyed on `id(loop)` and watch the closed-loop test fail.

Docker (`tests/test_pooling_docker.py`, `@pytest.mark.docker`, run with
`-n0`):

- Boot the compose stack. Hit one sync handler (`GET /api/match/shooters`
  on a seeded match) and one async handler (`GET /api/me`) 20 times each,
  submit a job and wait for the worker to finish it. Assert no 500 and no
  "attached to a different loop" in either container's log -- the #423
  repro.
- Read `pg_stat_activity` for the app role through the compose
  database: the connection count after the 20th request is within the
  pool bound and does **not** grow with the request count. On today's
  code it holds steady too (NullPool closes), so the proof that pooling
  is real is the `backend_start` column: with a pool the 20th request's
  connections have `backend_start` from the first request; with NullPool
  they are fresh. Assert at least one connection older than the run.

Production, after deploy: `railway logs --http --json -s serve -e
production`, `GET /api/me` and `GET /api/me/recent-projects` before and
after. The before numbers are in #1183.

## Rollout

One PR. Behind no flag: the local-mode path is unchanged by
construction, and the hosted path cannot be half-pooled. Staging first
(the merge to `main` deploys it), a day of ordinary use with the desktop
app syncing against it, then release.

## Out of scope (follow-ups)

- Per-route call counts: #1179 (picker), #1180 (R2 HEADs), #1181
  (triage / beep-queue refetch), #1182 (jobs poll).
- Background traffic that already keeps Neon awake: the desktop
  fingerprint poll at 300 s idle, and the agent's `/api/workers/channel`
  re-authentication every 15 minutes (about a one-third duty cycle from
  one always-on agent). Worth its own issue.
- Replacing the sync state accessors with async ones so `run_sync` can
  go away. The runner makes that unnecessary for performance; it stays a
  readability question.
