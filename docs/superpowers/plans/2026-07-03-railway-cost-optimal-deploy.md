# Railway Cost-Optimal Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Amended 2026-07-03 (after Task 4 live findings):** (a) a one-shot worker deployment keeps Railway status `SUCCESS` after its container exits, so deployment status CANNOT gate "is a drain running" - the gate is now Procrastinate's worker-heartbeat table; (b) the one-shot worker intermittently never exits: cancelling Procrastinate's LISTEN/NOTIFY listener hangs inside psycopg's `notifies()` generator (~1 in 5 local runs) - fixed by disabling `listen_notify` for one-shot drains; (c) procrastinate's logs were invisible in deploys (only the `splitsmith` logger gets a stdout handler), making clean exits indistinguishable from hangs - fixed by sharing the handler. Tasks 6-9 are rewritten accordingly.

**Goal:** Un-break Railway deploys and make the hosted stack scale-to-zero: pay only while a user is actually doing something.

**Architecture:** Three code PRs plus interleaved ops steps. PR 1 (MERGED, #528) fixed the stale `.railwayignore` that stripped `pnpm-lock.yaml` from the build context. PR 2 re-enables the auto-deploy triggers removed in #507. PR 3 adds trigger-on-enqueue behind a `WorkerLauncher` seam: the API process (the only enqueuer) fires a one-shot worker run after each defer via the configured launcher - Railway GraphQL redeploy today, non-Railway launchers (webhook/ntfy, GitHub Actions) addable later. The launcher skips when a live worker heartbeat exists in `procrastinate_workers` (the queue-of-record's own registry; Railway deployment status stays `SUCCESS` after exit and is useless for this). The queue of record stays in Postgres (Procrastinate); launchers are only the wake channel, so a lost signal is harmless. Serve re-fires the launcher on boot when jobs are already pending, which lets the safety cron run 6-hourly - Neon sleeps except during actual use plus ~4 short cron wakes a day.

**Tech Stack:** Python 3.11 / FastAPI / Procrastinate 3.8.1 / httpx (already a dependency - no new deps), Railway GraphQL API v2, GitHub Actions.

## Global Constraints

- Python 3.11+, type hints everywhere, Pydantic for data crossing module boundaries, `pathlib.Path` for paths, f-strings.
- `uv` only, never pip. Black line length 110. Ruff clean.
- **No new dependencies.** `httpx>=0.28.0` is already in `[project.dependencies]`.
- New prose/comments use ASCII punctuation and single `-` dashes (never em dash, never `--`).
- Run CI gates locally before every push: `uv run ruff check . && uv run black --check . && uv run pytest`.
- All changes land via PR to `main`; never commit to `main` directly. `git add` explicit paths only, never globs.
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` plus the session link.
- Railway IDs (from live account, 2026-07-03):
  - project `e77bded4-ddb2-430c-816f-156f2b6fe36a`
  - staging env `1fe9b8f7-6096-46bd-8cb0-d56ca5c1378b`, production env `6ce9da43-55d3-47d0-a7d2-9542a6c7e820`
  - worker service `3586f358-7c20-41a5-a5c6-3ff6bc627e95`, serve service `256c099f-511f-47c1-a290-7b3adb1b6d60`
- Railway GraphQL endpoint: `https://backboard.railway.com/graphql/v2`. Account token for ops curls comes from `~/.railway/config.json` key `.user.accessToken` (`Authorization: Bearer`; refresh by running any `railway` CLI command first); the in-app trigger uses a per-environment project token (`Project-Access-Token` header, validated in Task 8).

## Evidence (updated with Task 4 live findings, 2026-07-03)

- Build failure root cause CONFIRMED FIXED: staging deployed green after removing the `pnpm-lock.yaml` exclusion (PR #528; workflow run 28675764303; serve `1f33602b` SUCCESS, worker `cd1565ab` SUCCESS).
- **One-shot exit semantics:** the staging worker registered, drained the empty queue, unregistered (`procrastinate_workers` empty) and exited within minutes - while Railway's deployment status stayed `SUCCESS` indefinitely (ssh: "container is not running (status: exited)"). Deployment status therefore cannot distinguish running from exited.
- **Intermittent no-exit bug (local repro, slim venv, empty queue):** 1 of 5 one-shot runs never exits. Coroutine dump: `Worker._run_loop` blocked in `_shutdown` gathering side tasks; the `listener` task is in state *cancelling* but stuck at `manager.py:587 listen_notify` inside psycopg's `notifies()` async generator. The zombie holds no DB connection (Neon suspends) but the container never exits - silently defeating scale-to-zero and billing 24/7.
- **Log visibility:** `_configure_app_logging` attaches the stdout handler only to the `splitsmith` logger; procrastinate's records hit a bare root logger at WARNING and vanish. A clean drain logs nothing after the CLI banner.
- `serve` is always-on in both envs (`sleepApplication: false`) - the main Railway burn.
- Worker services: `cronSchedule: null`, start command `splitsmith worker --one-shot`, restart policy NEVER - runs only on deploy today.

## Design note: queue of record vs wake channel

Procrastinate-on-Postgres stays. The queue tables only cost Neon compute at moments Neon must be awake anyway (enqueue during a user request; drain while the worker writes results to the same DB), and transactional enqueue (job row + queue entry in one commit) is a property an external queue cannot give back without an outbox poller. What must never exist is a process polling Postgres as its wake channel. The `WorkerLauncher` seam is that wake channel: dumb, lossy-tolerant, outside Neon. The "is a worker already draining" gate reads `procrastinate_workers.last_heartbeat` - refreshed every ~10s by a live worker, removed on clean exit - via the API's own session factory, only during an enqueue when the DB is already awake. Nets for lost signals, in order: the boot re-check (next visit), the 6-hourly safety cron (worst case).

---

### Task 1: Fix `.railwayignore` (PR 1) - COMPLETE

Merged as PR #528 (squash `0773ac7`). `.railwayignore` no longer strips `src/splitsmith/ui_static/pnpm-lock.yaml`; plan file committed alongside.

---

### Task 2: Verify the staging build is green (ops) - COMPLETE

Workflow run 28675764303 deployed both services green; `https://my.staging.splitsmith.app/api/health` returns ok (v0.6.0).

---

### Task 3: Re-enable auto-deploy triggers (PR 2)

**Files:**
- Modify: `.github/workflows/deploy-app.yml` (the `on:` block and header comment)

**Interfaces:**
- Consumes: green staging build from Task 2 (done).
- Produces: push-to-main deploys staging, published release deploys production (the pre-#507 behavior; the workflow's `target` job already maps events to environments).

- [ ] **Step 1: Create branch**

```bash
git checkout main && git pull
git checkout -b ci/reenable-railway-autodeploy
```

- [ ] **Step 2: Restore the triggers**

In `.github/workflows/deploy-app.yml` replace:

```yaml
on:
  workflow_dispatch:
    inputs:
      environment:
        description: Target environment
        type: choice
        options: [staging, production]
        default: staging
```

with:

```yaml
on:
  push:
    branches: [main]
  release:
    types: [published]
  workflow_dispatch:
    inputs:
      environment:
        description: Target environment
        type: choice
        options: [staging, production]
        default: staging
```

Also update the header comment: replace the paragraph beginning `# Automatic deploys (push to main -> staging, release published ->` and ending `# failing; re-add the \`push\`/\`release\` triggers below once it's stable.` with:

```yaml
#   push to main           -> staging
#   release published      -> production
#   workflow_dispatch      -> the chosen environment (manual button)
```

(and delete the now-duplicated `#   workflow_dispatch      -> the chosen environment (manual button)` line above it).

- [ ] **Step 3: Validate workflow syntax**

```bash
gh workflow view deploy-app.yml >/dev/null && python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy-app.yml')); print('yaml ok')"
```

Expected: `yaml ok`.

- [ ] **Step 4: Commit, push, PR, merge**

```bash
git add .github/workflows/deploy-app.yml
git commit -m "ci: re-enable Railway auto-deploy (push -> staging, release -> production)

Reverts #507's trigger removal. The build failure it worked around was
the stale .railwayignore pnpm-lock.yaml exclusion, fixed in #528;
staging built green on manual dispatch."
git push -u origin ci/reenable-railway-autodeploy
gh pr create --fill && gh pr merge --squash --auto
```

Note: merging this PR itself triggers a staging deploy - that is the live test.

---

### Task 4: Validate the Railway trigger calls by hand (ops) - COMPLETE (findings folded into the amendment)

Findings recorded 2026-07-03: serve service id `256c099f-511f-47c1-a290-7b3adb1b6d60`; one-shot worker exits cleanly but Railway deployment status stays `SUCCESS` forever (gate redesigned to heartbeats); intermittent listener-cancellation hang reproduced locally (fixed in Task 6 Part A); procrastinate logs invisible (fixed in Task 6 Part A). `serviceInstanceRedeploy` with the account token remains to be exercised - it is now exercised by Task 8 Step 2 with the project token instead.

---

### Task 5: `worker_trigger` config loading (PR 3, part 1) - COMPLETE

Commit `5e72792` on branch `feat/worker-launcher-on-enqueue`: `RailwayLauncherConfig`, `load_railway_config`, env constants (`ENV_WORKER_LAUNCHER`, `ENV_TRIGGER_TOKEN`, `ENV_WORKER_SERVICE_ID`, `ENV_WORKER_ENVIRONMENT_ID`, `ENV_RAILWAY_ENVIRONMENT_ID`), 4 tests.

---

### Task 6: One-shot worker exit fixes + `RailwayWorkerLauncher` with heartbeat gate (PR 3, part 2)

Two commits on `feat/worker-launcher-on-enqueue`: Part A fixes the worker's own exit behavior and log visibility; Part B adds the launcher gated on worker heartbeats.

**Files:**
- Modify: `src/splitsmith/queue.py` (Part A)
- Modify: `tests/test_worker_queue.py` (Part A)
- Modify: `src/splitsmith/worker_trigger.py` (Part B)
- Modify: `tests/test_worker_trigger.py` (Part B)

**Interfaces:**
- Consumes: `RailwayLauncherConfig` / `load_railway_config` from Task 5; `run_worker(database_url, queues=None, concurrency=1, wait=True)` in `queue.py` whose body calls `app.run_worker_async(queues=..., concurrency=..., wait=wait)`.
- Produces (Part B, used by Task 7):
  - `class WorkerLauncher(Protocol)` with `def schedule(self) -> None` and `async def trigger(self) -> bool`.
  - `WorkerActiveCheck = Callable[[], Awaitable[bool]]`
  - `class RailwayWorkerLauncher` implementing the protocol: `__init__(config: RailwayLauncherConfig, worker_active: WorkerActiveCheck | None = None, transport: httpx.AsyncBaseTransport | None = None)`; `trigger()` never raises.
  - `make_worker_active_checker(session_factory: Callable[[], Any], max_age_seconds: float = 20.0) -> WorkerActiveCheck`
  - `build_worker_launcher(worker_active: WorkerActiveCheck | None = None, transport: httpx.AsyncBaseTransport | None = None) -> WorkerLauncher | None`

#### Part A - worker exits reliably and visibly

- [ ] **Step A1: Extend the forwarding tests (failing first)**

In `tests/test_worker_queue.py`, extend the two existing forwarding tests:
- `test_run_worker_long_lived_blocks_for_new_jobs` (the `wait=True` test asserting `captured["wait"] is True`): add `assert captured["listen_notify"] is True` with a comment that the long-lived fleet worker still wakes on LISTEN/NOTIFY.
- `test_run_worker_one_shot_drains_and_exits` (asserts `captured["wait"] is False`): add `assert captured["listen_notify"] is False`.

Then add one new test:

```python
def test_attach_procrastinate_logging_shares_stdout_handler() -> None:
    """The worker must make procrastinate's INFO logs visible: only the
    ``splitsmith`` logger gets a stdout handler, so procrastinate records
    propagate to a bare root logger at WARNING and vanish - a clean drain
    is then indistinguishable from a hang in Railway logs."""
    import logging

    from splitsmith.queue import _attach_procrastinate_logging

    pkg_logger = logging.getLogger("splitsmith")
    proc_logger = logging.getLogger("procrastinate")
    handler = logging.StreamHandler()
    handler._splitsmith_stdout = True  # type: ignore[attr-defined]
    pkg_logger.addHandler(handler)
    try:
        _attach_procrastinate_logging()
        assert handler in proc_logger.handlers
        assert proc_logger.getEffectiveLevel() <= logging.INFO
        _attach_procrastinate_logging()  # idempotent: no duplicate handler
        assert proc_logger.handlers.count(handler) == 1
    finally:
        pkg_logger.removeHandler(handler)
        proc_logger.removeHandler(handler)
```

- [ ] **Step A2: Run to verify failures**

```bash
uv run pytest tests/test_worker_queue.py -q
```

Expected: FAIL - `KeyError: 'listen_notify'` on the two extended tests, `ImportError` on the new one.

- [ ] **Step A3: Implement in `queue.py`**

(1) In `run_worker`, replace the `run_worker_async` call:

```python
        await app.run_worker_async(
            queues=queues,
            concurrency=concurrency,
            wait=wait,
            # A one-shot drain exits when the queue is empty; LISTEN/NOTIFY
            # exists to wake a long-lived worker for jobs that arrive later.
            # Beyond being useless mid-drain, cancelling the listener at
            # shutdown intermittently hangs inside psycopg's notifies()
            # generator (~1 in 5 one-shot runs locally), leaving a zombie
            # container that never exits - the opposite of scale-to-zero.
            listen_notify=wait,
        )
        logger.info("worker: drain complete (wait=%s); shutting down", wait)
```

(2) After the `_configure_app_logging()` call in `run_worker`, add `_attach_procrastinate_logging()`.

(3) Add the function (module level, near `run_worker`):

```python
def _attach_procrastinate_logging() -> None:
    """Share splitsmith's stdout handler with the ``procrastinate`` logger.

    ``_configure_app_logging`` wires only the ``splitsmith`` package logger;
    procrastinate's records propagate to a bare root logger left at WARNING,
    so a deployed worker's drain lifecycle ("Starting worker", "No job found.
    Stopping worker because wait=False") is invisible - a silent clean exit
    is indistinguishable from a hang in Railway logs (observed live,
    2026-07-03). Reuses the handler objects marked ``_splitsmith_stdout`` so
    hosted-mode JSON formatting stays consistent. Idempotent.
    """
    pkg_logger = logging.getLogger("splitsmith")
    proc_logger = logging.getLogger("procrastinate")
    for handler in pkg_logger.handlers:
        if getattr(handler, "_splitsmith_stdout", False) and handler not in proc_logger.handlers:
            proc_logger.addHandler(handler)
    if proc_logger.level == logging.NOTSET or proc_logger.level > logging.INFO:
        proc_logger.setLevel(logging.INFO)
```

- [ ] **Step A4: Verify green, then live-verify the flake is gone**

```bash
uv run pytest tests/test_worker_queue.py -q
```

Expected: all pass. Then run the one-shot drain 10x against the local docker Postgres (compose `postgres` service must be up; DB migrated):

```bash
export SPLITSMITH_DATABASE_URL="postgresql+asyncpg://splitsmith:splitsmith@localhost:5432/splitsmith"
export SPLITSMITH_PUBLIC_URL="http://localhost:5174"
export SPLITSMITH_MODE=hosted
for i in $(seq 1 10); do
  uv run splitsmith worker --one-shot >/tmp/oneshot-$i.log 2>&1 &
  P=$!; ok=""
  for s in $(seq 1 60); do sleep 1; kill -0 $P 2>/dev/null || { ok="exited at ${s}s"; break; }; done
  [ -z "$ok" ] && { echo "run $i: HUNG"; kill -9 $P; } || echo "run $i: $ok"
done
```

Expected: 10/10 exit (the dev venv exercises the same procrastinate shutdown path even though its model stack differs). If any run hangs, STOP - the listener was not the only shutdown race; re-investigate with the SIGUSR1 task-dump harness before proceeding.

- [ ] **Step A5: Gates + commit**

```bash
uv run ruff check . && uv run black --check . && uv run pytest -q
git add src/splitsmith/queue.py tests/test_worker_queue.py
git commit -m "fix(worker): one-shot drain exits reliably and visibly

Disable LISTEN/NOTIFY for wait=False drains: the listener task's
cancellation intermittently hangs inside psycopg's notifies() generator
(~1 in 5 runs), leaving a zombie container that defeats scale-to-zero.
A one-shot drain never needs the listener - it exits when the queue is
empty. Also share the stdout log handler with the procrastinate logger
and log drain completion, so a deployed worker's lifecycle is actually
visible in Railway logs (a clean exit previously looked identical to a
hang)."
```

#### Part B - the launcher, gated on worker heartbeats

- [ ] **Step B1: Write the failing tests**

Append to `tests/test_worker_trigger.py` (extend the import block with `RailwayLauncherConfig`, `RailwayWorkerLauncher`, `build_worker_launcher`, `make_worker_active_checker`):

```python
def _config(**overrides: Any) -> RailwayLauncherConfig:
    base: dict[str, Any] = {"token": "tok", "service_id": "svc", "environment_id": "env"}
    base.update(overrides)
    return RailwayLauncherConfig(**base)


def _transport(requests: list[dict[str, Any]]) -> httpx.MockTransport:
    """Ack the redeploy mutation, recording every GraphQL request body."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"data": {"serviceInstanceRedeploy": True}})

    return httpx.MockTransport(handler)


async def _active() -> bool:
    return True


async def _inactive() -> bool:
    return False


def test_launcher_redeploys_when_no_checker_configured() -> None:
    requests: list[dict[str, Any]] = []
    launcher = RailwayWorkerLauncher(_config(), transport=_transport(requests))
    assert asyncio.run(launcher.trigger()) is True
    assert len(requests) == 1
    assert "serviceInstanceRedeploy" in requests[0]["query"]
    assert requests[0]["variables"] == {"serviceId": "svc", "environmentId": "env"}


def test_launcher_redeploys_when_worker_inactive() -> None:
    requests: list[dict[str, Any]] = []
    launcher = RailwayWorkerLauncher(_config(), worker_active=_inactive, transport=_transport(requests))
    assert asyncio.run(launcher.trigger()) is True
    assert len(requests) == 1


def test_launcher_skips_while_worker_active() -> None:
    """A live heartbeat means a drain is in flight; redeploying now would
    kill its job mid-run. The running drain picks up new jobs itself."""
    requests: list[dict[str, Any]] = []
    launcher = RailwayWorkerLauncher(_config(), worker_active=_active, transport=_transport(requests))
    assert asyncio.run(launcher.trigger()) is False
    assert requests == []


def test_launcher_fail_safe_when_checker_raises() -> None:
    """If the heartbeat check itself fails, do NOT redeploy - a possibly
    running drain must never be killed on bad information. The boot
    re-check / safety cron recover a missed launch."""
    requests: list[dict[str, Any]] = []

    async def _broken() -> bool:
        raise RuntimeError("db unavailable")

    launcher = RailwayWorkerLauncher(_config(), worker_active=_broken, transport=_transport(requests))
    assert asyncio.run(launcher.trigger()) is False
    assert requests == []


def test_launcher_cooldown_suppresses_burst() -> None:
    requests: list[dict[str, Any]] = []

    async def scenario() -> tuple[bool, bool]:
        launcher = RailwayWorkerLauncher(_config(), transport=_transport(requests))
        return await launcher.trigger(), await launcher.trigger()

    first, second = asyncio.run(scenario())
    assert first is True
    assert second is False
    assert len(requests) == 1  # the second call never reached the API


def test_launcher_swallows_transport_errors() -> None:
    """A Railway outage must never fail (or slow) the enqueue path."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("railway down", request=request)

    launcher = RailwayWorkerLauncher(_config(), transport=httpx.MockTransport(handler))
    assert asyncio.run(launcher.trigger()) is False


def test_launcher_treats_graphql_errors_as_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Unauthorized"}]})

    launcher = RailwayWorkerLauncher(_config(), transport=httpx.MockTransport(handler))
    assert asyncio.run(launcher.trigger()) is False


def test_schedule_fires_trigger_in_background() -> None:
    requests: list[dict[str, Any]] = []

    async def scenario() -> None:
        launcher = RailwayWorkerLauncher(_config(), transport=_transport(requests))
        launcher.schedule()
        await asyncio.gather(*list(launcher._tasks))

    asyncio.run(scenario())
    assert len(requests) == 1


class _StubResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one(self) -> Any:
        return self._value


class _StubSession:
    def __init__(self, value: Any) -> None:
        self._value = value
        self.queries: list[str] = []

    async def execute(self, query: Any, params: Any = None) -> _StubResult:
        self.queries.append(str(query))
        return _StubResult(self._value)


class _StubSessionCtx:
    def __init__(self, session: _StubSession) -> None:
        self._session = session

    async def __aenter__(self) -> _StubSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> None:
        return None


def test_worker_active_checker_true_on_fresh_heartbeat() -> None:
    session = _StubSession(True)
    checker = make_worker_active_checker(lambda: _StubSessionCtx(session))
    assert asyncio.run(checker()) is True
    assert "procrastinate_workers" in session.queries[0]


def test_worker_active_checker_false_when_no_live_worker() -> None:
    session = _StubSession(False)
    checker = make_worker_active_checker(lambda: _StubSessionCtx(session))
    assert asyncio.run(checker()) is False


def test_build_worker_launcher_defaults_to_railway(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_TRIGGER_TOKEN, "tok")
    monkeypatch.setenv(ENV_WORKER_SERVICE_ID, "svc")
    monkeypatch.setenv(ENV_WORKER_ENVIRONMENT_ID, "env")
    launcher = build_worker_launcher()
    assert isinstance(launcher, RailwayWorkerLauncher)


def test_build_worker_launcher_none_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    assert build_worker_launcher() is None


def test_build_worker_launcher_unknown_kind_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in SPLITSMITH_WORKER_LAUNCHER must fail the boot, not silently
    disable job execution."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_WORKER_LAUNCHER, "sqs")
    with pytest.raises(ValueError, match="unknown worker launcher"):
        build_worker_launcher()
```

- [ ] **Step B2: Run to verify failure**

```bash
uv run pytest tests/test_worker_trigger.py -q
```

Expected: FAIL - `ImportError: cannot import name 'RailwayWorkerLauncher'`.

- [ ] **Step B3: Implement the seam and the Railway launcher**

Append to `src/splitsmith/worker_trigger.py`, extending the import block to:

```python
import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable, Protocol

import httpx
from pydantic import BaseModel
```

then:

```python
class WorkerLauncher(Protocol):
    """Wake channel for the scale-to-zero worker.

    The queue of record stays in Postgres; a launcher only signals
    "work exists" and must be safe to lose a signal - the serve-boot
    pending re-check and the safety cron are the nets. Implementations
    must make ``trigger`` never raise.
    """

    def schedule(self) -> None: ...

    async def trigger(self) -> bool: ...


WorkerActiveCheck = Callable[[], Awaitable[bool]]

_REDEPLOY_MUTATION = """\
mutation TriggerWorkerRun($serviceId: String!, $environmentId: String!) {
  serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
}
"""


class RailwayWorkerLauncher:
    """Fires ``serviceInstanceRedeploy`` for the worker service, at most
    once per cooldown window, and only when no live worker heartbeat exists.

    Railway's deployment status cannot gate "is a drain running": a
    one-shot deployment keeps status SUCCESS after its container exits
    (observed live on staging, 2026-07-03). The authoritative signal is
    Procrastinate's own worker registry - ``worker_active`` (built by
    :func:`make_worker_active_checker`) reads it. With no checker
    configured the launcher redeploys unconditionally, subject to the
    cooldown.
    """

    def __init__(
        self,
        config: RailwayLauncherConfig,
        worker_active: WorkerActiveCheck | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._worker_active = worker_active
        self._client = httpx.AsyncClient(
            headers={"Project-Access-Token": config.token},
            timeout=10.0,
            transport=transport,
        )
        self._lock = asyncio.Lock()
        self._last_attempt: float | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    def schedule(self) -> None:
        """Run :meth:`trigger` as a background task; never blocks the caller.

        Holding the task in ``self._tasks`` keeps it from being garbage
        collected mid-flight (asyncio only holds weak refs to tasks).
        """
        task = asyncio.get_running_loop().create_task(self.trigger())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def trigger(self) -> bool:
        """Redeploy the worker unless one is draining or we are in cooldown.

        Returns True when a redeploy was fired. Never raises: a Railway or
        DB hiccup must not break enqueue - a missed signal is repaired by
        the boot re-check or the safety cron. A failing heartbeat check
        counts as "worker may be active": redeploying on bad information
        could kill a live drain, and the nets cover the missed launch.
        """
        async with self._lock:
            now = time.monotonic()
            if (
                self._last_attempt is not None
                and now - self._last_attempt < self._config.cooldown_seconds
            ):
                return False
            # Stamp before the checks so a slow/failing backend is not
            # hammered by every enqueue in a burst.
            self._last_attempt = now
            try:
                if self._worker_active is not None and await self._worker_active():
                    logger.info("worker launcher: skipped, a live worker heartbeat exists")
                    return False
                await self._redeploy()
                logger.info("worker launcher: redeploy fired")
                return True
            except Exception:
                logger.warning(
                    "worker launcher: failed; boot re-check or safety cron will drain the queue",
                    exc_info=True,
                )
                return False

    async def _redeploy(self) -> None:
        variables = {
            "serviceId": self._config.service_id,
            "environmentId": self._config.environment_id,
        }
        response = await self._client.post(
            self._config.api_url, json={"query": _REDEPLOY_MUTATION, "variables": variables}
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"Railway GraphQL error: {payload['errors']}")


def make_worker_active_checker(
    session_factory: Callable[[], Any], max_age_seconds: float = 20.0
) -> WorkerActiveCheck:
    """True while any Procrastinate worker heartbeat is fresher than
    ``max_age_seconds``.

    A live worker refreshes ``procrastinate_workers.last_heartbeat`` every
    ~10s and removes its row on clean exit; 20s tolerates one missed beat
    while letting a crashed worker's stale row unblock the launcher within
    seconds. Runs on the API's session factory - the launcher only fires
    during an enqueue, when the DB is already awake, so this adds no wake.
    """

    async def _worker_active() -> bool:
        from sqlalchemy import text

        async with session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM procrastinate_workers "
                    "WHERE last_heartbeat > now() - make_interval(secs => :age))"
                ),
                {"age": max_age_seconds},
            )
            return bool(result.scalar_one())

    return _worker_active


def build_worker_launcher(
    worker_active: WorkerActiveCheck | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WorkerLauncher | None:
    """Select and build the launcher from ``SPLITSMITH_WORKER_LAUNCHER``
    (default ``railway``). ``None`` (launcher disabled) when the selected
    implementation's env config is absent; unknown names raise so a
    misconfigured hosted boot fails loudly instead of stranding jobs."""
    kind = os.environ.get(ENV_WORKER_LAUNCHER, "").strip().lower() or "railway"
    if kind == "railway":
        config = load_railway_config()
        return (
            RailwayWorkerLauncher(config, worker_active=worker_active, transport=transport)
            if config
            else None
        )
    raise ValueError(f"{ENV_WORKER_LAUNCHER}={kind!r}: unknown worker launcher")
```

- [ ] **Step B4: Run to verify green**

```bash
uv run pytest tests/test_worker_trigger.py -q
```

Expected: 17 passed (4 from Task 5 + 13 new).

- [ ] **Step B5: Gates + commit**

```bash
uv run ruff check . && uv run black --check . && uv run pytest -q
git add src/splitsmith/worker_trigger.py tests/test_worker_trigger.py
git commit -m "feat(worker): WorkerLauncher seam + heartbeat-gated Railway launcher

Railway deployment status stays SUCCESS after a one-shot container
exits, so it cannot gate 'is a drain running'. Gate on Procrastinate's
worker registry instead: procrastinate_workers.last_heartbeat is
refreshed every ~10s by a live worker and removed on clean exit. The
checker fails safe - on any error the launcher does not redeploy (never
kill a possibly-live drain on bad information)."
```

---

### Task 7: Wire the launcher into the hosted enqueue path + boot re-check (PR 3, part 3)

**Files:**
- Modify: `src/splitsmith/worker_trigger.py` (add `wrap_deferrer`, `make_boot_retrigger`)
- Modify: `src/splitsmith/ui/server.py` (hosted wiring at `deferrer = make_deferrer(url)` ~line 4236; `class AppState` hosted attrs ~lines 786-850; `create_app` after `app = FastAPI(...)` ~line 4457)
- Modify: `tests/test_worker_trigger.py`
- Modify: `docs/saas-readiness/04-compute-backends.md` (document the mechanism)

**Interfaces:**
- Consumes: `WorkerLauncher`, `build_worker_launcher`, `make_worker_active_checker` from Task 6; `make_deferrer(url)` returning `Callable[..., Awaitable[None]]` with keyword-only params `(job_id, user_id, kind, args, match_id)`; the hosted `session_factory` (async SQLAlchemy sessionmaker) in `_apply_hosted_mode_wiring`; the `_StubSession`/`_StubSessionCtx` test helpers from Task 6.
- Produces:
  - `wrap_deferrer(deferrer: Callable[..., Awaitable[None]], launcher: WorkerLauncher) -> Callable[..., Awaitable[None]]`
  - `make_boot_retrigger(session_factory: Callable[[], Any], launcher: WorkerLauncher) -> Callable[[], Awaitable[None]]`
  - `AppState.boot_retrigger: Callable[[], Awaitable[None]] | None` (default None), registered by `create_app` as a FastAPI startup handler.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_worker_trigger.py` (add `make_boot_retrigger`, `wrap_deferrer` to the import block):

```python
class _StubLauncher:
    """Minimal WorkerLauncher for wiring tests (the Protocol is structural)."""

    def __init__(self) -> None:
        self.scheduled = 0

    def schedule(self) -> None:
        self.scheduled += 1

    async def trigger(self) -> bool:
        self.schedule()
        return True


def test_wrap_deferrer_defers_then_schedules() -> None:
    calls: list[dict[str, Any]] = []

    async def deferrer(**kwargs: Any) -> None:
        calls.append(kwargs)

    stub = _StubLauncher()
    wrapped = wrap_deferrer(deferrer, stub)
    asyncio.run(wrapped(job_id="j1", user_id="u1", kind="detect_beep", args={}, match_id=None))
    assert calls == [{"job_id": "j1", "user_id": "u1", "kind": "detect_beep", "args": {}, "match_id": None}]
    assert stub.scheduled == 1


def test_wrap_deferrer_failed_defer_does_not_schedule() -> None:
    """If the enqueue itself failed there is no job to run - and the caller
    must see the original exception, not a swallowed one."""

    async def deferrer(**kwargs: Any) -> None:
        raise RuntimeError("boom")

    stub = _StubLauncher()
    wrapped = wrap_deferrer(deferrer, stub)
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(wrapped(job_id="j1", user_id="u1", kind="detect_beep", args={}, match_id=None))
    assert stub.scheduled == 0


def test_boot_retrigger_fires_when_jobs_pending() -> None:
    stub = _StubLauncher()
    session = _StubSession(2)
    hook = make_boot_retrigger(lambda: _StubSessionCtx(session), stub)
    asyncio.run(hook())
    assert stub.scheduled == 1
    assert "procrastinate_jobs" in session.queries[0]


def test_boot_retrigger_quiet_when_queue_empty() -> None:
    stub = _StubLauncher()
    hook = make_boot_retrigger(lambda: _StubSessionCtx(_StubSession(0)), stub)
    asyncio.run(hook())
    assert stub.scheduled == 0


def test_boot_retrigger_swallows_db_errors() -> None:
    """A failed pending-check must not fail the serve boot."""

    class _BrokenCtx:
        async def __aenter__(self) -> Any:
            raise RuntimeError("db down")

        async def __aexit__(self, *exc: Any) -> None:
            return None

    stub = _StubLauncher()
    hook = make_boot_retrigger(lambda: _BrokenCtx(), stub)
    asyncio.run(hook())  # must not raise
    assert stub.scheduled == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_worker_trigger.py -q
```

Expected: FAIL - `ImportError: cannot import name 'wrap_deferrer'`.

- [ ] **Step 3: Implement `wrap_deferrer` and `make_boot_retrigger`**

Append to `src/splitsmith/worker_trigger.py`:

```python
def wrap_deferrer(
    deferrer: Callable[..., Awaitable[None]], launcher: WorkerLauncher
) -> Callable[..., Awaitable[None]]:
    """After each successful defer, schedule a worker launch.

    The launch is fire-and-forget so enqueue latency stays flat; the
    defer's own exceptions propagate untouched (a failed enqueue must
    not fire a worker, and the caller needs the original error).
    """

    async def _defer_and_trigger(**kwargs: Any) -> None:
        await deferrer(**kwargs)
        launcher.schedule()

    return _defer_and_trigger


def make_boot_retrigger(
    session_factory: Callable[[], Any], launcher: WorkerLauncher
) -> Callable[[], Awaitable[None]]:
    """Build the serve-boot pending-jobs re-check.

    Railway app sleeping cold-starts serve on each visit, so this runs
    on every wake - with the DB already awake from boot migrations. It
    closes the launcher's accepted races (missed signal, enqueue during
    worker exit) at the next visit instead of waiting for the safety
    cron, which can therefore run 6-hourly. Never raises: a failed check
    must not fail the boot.
    """

    async def _retrigger_pending_jobs() -> None:
        from sqlalchemy import text

        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("SELECT count(*) FROM procrastinate_jobs WHERE status = 'todo'")
                )
                pending = result.scalar_one()
        except Exception:
            logger.warning("boot re-trigger: pending-job check failed", exc_info=True)
            return
        if pending:
            logger.info("boot re-trigger: %s pending job(s) - firing worker launcher", pending)
            launcher.schedule()

    return _retrigger_pending_jobs
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_worker_trigger.py -q
```

Expected: 22 passed (17 from Tasks 5-6 + 5 new).

- [ ] **Step 5: Wire into the hosted boot path**

In `src/splitsmith/ui/server.py`:

(a) In `_apply_hosted_mode_wiring`, extend the local import block (~line 4188):

```python
    from ..queue import make_deferrer
    from ..worker_trigger import (
        build_worker_launcher,
        make_boot_retrigger,
        make_worker_active_checker,
        wrap_deferrer,
    )
```

and replace (~line 4236):

```python
    deferrer = make_deferrer(url)
```

with:

```python
    deferrer = make_deferrer(url)
    # Scale-to-zero worker: this API process is the only enqueuer, so it
    # fires a one-shot worker run through the configured launcher after
    # each defer, gated on the procrastinate_workers heartbeat so a live
    # drain is never redeployed out from under its job, and re-checks for
    # stranded jobs on every boot (see splitsmith.worker_trigger). No-op
    # when the launcher env vars are unset - local / docker-compose runs
    # an always-on worker instead.
    worker_launcher = build_worker_launcher(
        worker_active=make_worker_active_checker(session_factory)
    )
    if worker_launcher is not None:
        deferrer = wrap_deferrer(deferrer, worker_launcher)
        state.boot_retrigger = make_boot_retrigger(session_factory, worker_launcher)
```

(b) In `class AppState`, next to the other hosted-mode attributes (the `_build_tenant` / auth block, ~lines 786-850; match the surrounding attribute-declaration style):

```python
    # Hosted + launcher only: serve-boot pending-jobs re-check, registered
    # as a FastAPI startup handler by create_app. Runs on every cold start
    # (incl. each wake from Railway app sleeping).
    boot_retrigger: Callable[[], Awaitable[None]] | None = None
```

(c) In `create_app`, immediately after the `app = FastAPI(...)` construction (~line 4457):

```python
    if state.boot_retrigger is not None:
        # Cold starts include every wake from Railway app sleeping, so a
        # stranded queue job recovers on the next visit instead of waiting
        # for the 6-hourly safety cron.
        app.add_event_handler("startup", state.boot_retrigger)
```

- [ ] **Step 6: Document the mechanism**

Append to `docs/saas-readiness/04-compute-backends.md`:

```markdown
## Scale-to-zero worker launcher (2026-07)

The hosted worker runs `splitsmith worker --one-shot` (restart policy
NEVER) and is started on demand, not by polling. The queue of record
stays in Postgres (Procrastinate - transactional enqueue with the job
row); the *wake channel* is a `WorkerLauncher` (`splitsmith.worker_trigger`),
selected by `SPLITSMITH_WORKER_LAUNCHER` (default `railway`): after each
successful defer the API fires `serviceInstanceRedeploy` for the worker
service via Railway's GraphQL API. The "already draining" gate is
`procrastinate_workers.last_heartbeat` (fresh within 20s = live drain,
skip) - NOT Railway deployment status, which stays SUCCESS after a
one-shot container exits. One-shot drains run with `listen_notify=False`:
the LISTEN/NOTIFY listener is for long-lived workers, and cancelling it
at shutdown intermittently hung the exit (zombie container). Railway
config: `SPLITSMITH_WORKER_TRIGGER_TOKEN` (project token),
`SPLITSMITH_WORKER_SERVICE_ID`, and the Railway-injected
`RAILWAY_ENVIRONMENT_ID`. With the env unset the launcher is disabled
(local / docker-compose keeps its always-on worker).

Lost wake signals are harmless by design, with two nets: serve re-checks
`procrastinate_jobs` for pending work on every boot (app sleeping
cold-starts serve per visit, DB already awake from migrations) and
re-fires the launcher; a 6-hourly safety cron on the worker service is
the final net (~2% of the Neon free tier). This keeps both Railway
containers and the Neon compute asleep whenever no one is using the
app. Non-Railway launchers (webhook/ntfy to a home machine, GitHub
Actions dispatch) implement the same protocol; the worker itself is
portable - any machine with the DATABASE_URL, R2 credentials, and
models can run `splitsmith worker --one-shot`.
```

- [ ] **Step 7: Gates + commit + PR**

```bash
uv run ruff check . && uv run black --check . && uv run pytest -q
git add src/splitsmith/worker_trigger.py tests/test_worker_trigger.py src/splitsmith/ui/server.py docs/saas-readiness/04-compute-backends.md
git commit -m "feat(worker): launch one-shot worker on enqueue via WorkerLauncher seam

The API is the only enqueuer, so after a successful defer it fires the
configured launcher (Railway serviceInstanceRedeploy today; the seam
admits webhook/ntfy or GitHub Actions workers later). Gated on the
procrastinate_workers heartbeat so a live drain is never killed,
cooldown-limited, fire-and-forget so enqueue latency is unaffected.
Serve re-checks for stranded jobs on every boot, so the worker safety
cron only needs to run 6-hourly: Railway and Neon both sleep whenever
the app is idle."
git push -u origin feat/worker-launcher-on-enqueue
gh pr create --fill && gh pr merge --squash --auto
```

Merging auto-deploys staging (Task 3's trigger) - the env vars from Task 8 must be set for the launcher to activate; until then it is a silent no-op, which is safe.

---

### Task 8: Staging ops - token, env vars, serve sleeping, safety cron

All via CLI/GraphQL; nothing here touches the repo. Shell vars: `API=https://backboard.railway.com/graphql/v2`, `STAGING=1fe9b8f7-6096-46bd-8cb0-d56ca5c1378b`, `WORKER=3586f358-7c20-41a5-a5c6-3ff6bc627e95`, `SERVE=256c099f-511f-47c1-a290-7b3adb1b6d60`, `TOKEN` from `~/.railway/config.json` `.user.accessToken` (run any `railway` CLI command first to refresh it).

- [ ] **Step 1: Create a staging project token**

```bash
curl -s "$API" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"query\":\"mutation { projectTokenCreate(input: {projectId: \\\"e77bded4-ddb2-430c-816f-156f2b6fe36a\\\", environmentId: \\\"$STAGING\\\", name: \\\"worker-trigger-staging\\\"}) }\"}"
```

Expected: `{"data": {"projectTokenCreate": "<token>"}}`. Save as `PT` (do NOT paste into any file that could be committed).

- [ ] **Step 2: Verify the project token authenticates the exact call the code makes**

The launcher's only Railway call is the redeploy mutation. Verify the token by firing it once (this starts a real one-shot run, which doubles as the live redeploy test):

```bash
curl -s "$API" -H "Project-Access-Token: $PT" -H "Content-Type: application/json" -d "{\"query\":\"mutation { serviceInstanceRedeploy(serviceId: \\\"$WORKER\\\", environmentId: \\\"$STAGING\\\") }\"}"
railway logs -s worker -e staging -d 2>&1 | tail -20
```

Expected: mutation acked (no `Unauthorized`); worker logs show the drain lifecycle (with the Task 6 logging fix: "Starting worker", "No job found. Stopping worker because wait=False", "worker: drain complete"). **Checkpoint:** if the `Project-Access-Token` header is rejected, test `-H "Authorization: Bearer $PT"`; if that works instead, change the header in `RailwayWorkerLauncher.__init__` (one line) plus this plan before rollout.

- [ ] **Step 3: Set the launcher env vars on staging serve**

```bash
railway variables --set "SPLITSMITH_WORKER_TRIGGER_TOKEN=$PT" --set "SPLITSMITH_WORKER_SERVICE_ID=$WORKER" --service serve --environment staging
```

(Environment id comes from Railway's injected `RAILWAY_ENVIRONMENT_ID`; launcher kind defaults to `railway`; no other vars needed.)

- [ ] **Step 4: Enable app sleeping on staging serve**

```bash
curl -s "$API" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"query\":\"mutation { serviceInstanceUpdate(serviceId: \\\"$SERVE\\\", environmentId: \\\"$STAGING\\\", input: {sleepApplication: true}) }\"}"
```

- [x] **Step 5 (AMENDED live): safety net is a GitHub Actions schedule, NOT a Railway cron**

Setting `cronSchedule` on the worker service converts it to schedule-only starts: `serviceInstanceRedeploy` then creates deployments that never run until the next tick (observed live - deployment SUCCESS, zero logs, job stuck `todo`). The worker service must keep `cronSchedule: null`. The net is `.github/workflows/worker-safety-net.yml`: every 6 hours it curls `/api/health` on staging + production, waking a sleeping serve whose startup boot re-check fires the gated launcher if stranded jobs exist. Cost is unchanged (~2% of the Neon free tier from 4 wakes/day).

- [ ] **Step 6: Redeploy staging serve + worker so the config applies**

```bash
curl -s "$API" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"query\":\"mutation { serviceInstanceRedeploy(serviceId: \\\"$SERVE\\\", environmentId: \\\"$STAGING\\\") }\"}"
curl -s "$API" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"query\":\"mutation { serviceInstanceRedeploy(serviceId: \\\"$WORKER\\\", environmentId: \\\"$STAGING\\\") }\"}"
```

- [ ] **Step 7: Confirm the config stuck**

```bash
curl -s "$API" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"query\":\"query { environment(id: \\\"$STAGING\\\") { serviceInstances { edges { node { serviceName cronSchedule sleepApplication restartPolicyType startCommand } } } } }\"}" | python3 -m json.tool
```

Expected: worker `{cronSchedule: "0 */6 * * *", restartPolicyType: NEVER, startCommand: "splitsmith worker --one-shot"}`; serve `{sleepApplication: true}`.

---

### Task 9: Staging end-to-end verification

- [ ] **Step 1: Launcher fires on enqueue**

Log in at `https://my.staging.splitsmith.app`, open an existing match, and run any detection (e.g. re-run beep detect on a stage). Then:

```bash
railway deployment list -s worker -e staging 2>/dev/null | head -3
railway logs -s worker -e staging -d 2>&1 | tail -30
```

Expected: a worker deployment created within ~30 s of the click; worker logs (now visible via the Task 6 logging fix) show "Starting worker", the job run, "Stopping worker because wait=False", and "worker: drain complete"; the job completes in the UI. Serve logs (`railway logs -s serve -e staging -d`) show `worker launcher: redeploy fired`.

- [ ] **Step 2: Worker survives a cold Neon (the #491 checkpoint)**

Leave staging untouched until the Neon staging branch compute suspends (Neon console, typically ~5 min idle), then repeat Step 1. Expected: the drain succeeds - possibly after logged PoolTimeout retries, never a crash. If it crash-loops, STOP and debug `_open_app_with_retry` before touching production.

- [ ] **Step 3: Everything actually sleeps**

Close all app tabs (open SSE connections hold serve awake - expected behavior, note it if observed). After the sleep window: Railway dashboard shows serve sleeping; Neon console shows the staging compute suspended; `procrastinate_workers` on staging is empty (all drains exited - the Task 6 Part A fix holding in production conditions); no worker deployments except the 6-hourly cron ticks, each starting, draining nothing, and exiting within ~a minute.

- [ ] **Step 4: Heartbeat-gate sanity**

While a worker drain is visibly running (start a slow job like shot-detect), enqueue a second job immediately. Expected: serve logs `worker launcher: skipped, a live worker heartbeat exists`, and the running worker drains the second job itself before exiting.

- [ ] **Step 5: Boot re-check sanity**

After serve has gone to sleep, open the app again and check serve logs for the startup handler: either silence (queue empty - the normal case) or `boot re-trigger: N pending job(s)`. No exceptions during startup.

---

### Task 10: Production rollout

- [ ] **Step 1: Repeat Task 8 for production**

Same commands with `ENV=6ce9da43-55d3-47d0-a7d2-9542a6c7e820` and token name `worker-trigger-production`.

- [ ] **Step 2: Deploy production**

Cut a release (release-please PR merge publishes one, which now auto-deploys production via Task 3), or manually:

```bash
gh workflow run deploy-app.yml -f environment=production
```

- [ ] **Step 3: Production smoke**

Repeat Task 9 Steps 1 and 3 against `https://my.splitsmith.app`.

- [ ] **Step 4: Watch the meters for a week**

Railway usage page: serve should show substantial sleeping time; worker only minutes/day. Neon: compute-hours tracking toward ~10-30/month (cron floor + real use), far under the 191.9 free-tier cap. If Neon burn is materially higher, something is holding connections - investigate before it eats the tier.

---

## Race conditions and known limits (accepted by design)

- **Enqueue-during-worker-exit:** a job enqueued between the worker's final empty-queue poll and its exit is missed by both the running drain and the heartbeat-gated launcher (the heartbeat is live until unregister). The boot re-check catches it on the next visit; the 6-hourly cron is the final net. Window is seconds; single-operator; accepted.
- **Cooldown suppression:** a job enqueued < 30 s after a launch that fired for an already-drained queue waits for the boot re-check or cron. Same acceptance; the common burst case (N jobs enqueued together) is handled because the launched worker drains everything present when it polls.
- **Open SSE tabs keep serve awake** and therefore Neon awake. That is "user is active" by definition; no action.
- **Failed heartbeat check suppresses the launch** (fail-safe: never redeploy over a possibly-live drain). In practice the enqueue that fired the trigger just used the same DB, so the checker failing is rare; the nets recover.
- **Crashed-image loop is bounded:** if the latest worker image crashes on boot, each launch redeploys it once per cooldown/net cycle - same behavior a cron would have; fix the image, the nets stop firing it.
- **Mid-boot blind spot:** between `serviceInstanceRedeploy` and the worker's heartbeat registration (~60-90s: container start + state build + model warmup), the gate sees no live worker, so an enqueue in that window fires a second redeploy that replaces the booting container. Observed live; harmless - the replacement drains everything, jobs stay `todo` until fetched, and the 30s cooldown bounds the churn.
- **Railway cron is incompatible with the launcher** (schedule-only starts, above); never set `cronSchedule` on the worker service.
- **Future non-Railway workers:** implement `WorkerLauncher` (webhook/ntfy publish, GitHub Actions dispatch), select via `SPLITSMITH_WORKER_LAUNCHER`. The worker binary is already portable (DATABASE_URL + R2 creds + models); external workers must be launched, never left polling Postgres.
