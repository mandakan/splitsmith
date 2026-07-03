# Railway Cost-Optimal Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un-break Railway deploys and make the hosted stack scale-to-zero: pay only while a user is actually doing something.

**Architecture:** Three code PRs plus interleaved ops steps. PR 1 fixes the stale `.railwayignore` that strips `pnpm-lock.yaml` from the build context (the root cause of every failed build since PR #506). PR 2 re-enables the auto-deploy triggers removed in #507. PR 3 adds trigger-on-enqueue behind a `WorkerLauncher` seam: the API process (the only enqueuer) fires a one-shot worker run after each defer via the configured launcher - Railway GraphQL redeploy today, non-Railway launchers (webhook/ntfy, GitHub Actions) addable later without touching the enqueue path. The queue of record stays in Postgres (Procrastinate); launchers are only the wake channel, so a lost signal is harmless. Serve re-fires the launcher on boot when jobs are already pending (Railway app sleeping cold-starts serve on every visit, with the DB awake from boot migrations), which lets the safety cron run 6-hourly instead of hourly - Neon sleeps except during actual use plus ~4 short cron wakes a day.

**Tech Stack:** Python 3.11 / FastAPI / Procrastinate / httpx (already a dependency - no new deps), Railway GraphQL API v2, GitHub Actions.

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
  - worker service `3586f358-7c20-41a5-a5c6-3ff6bc627e95` (serve service id fetched in Task 4)
- Railway GraphQL endpoint: `https://backboard.railway.com/graphql/v2`. Account token for ops curls comes from `~/.railway/config.json` key `.user.accessToken` (`Authorization: Bearer`); the in-app trigger uses a per-environment project token (`Project-Access-Token` header, validated in Task 8).

## Evidence recap (why these fixes)

- Staging build failure 2026-06-24: `"/src/splitsmith/ui_static/pnpm-lock.yaml": not found` - `.railwayignore` still excludes the pnpm lockfile from the `railway up` upload, but the Dockerfile has COPY'd it since PR #506 made the SPA pnpm-only.
- Worker: `cronSchedule: null`, start command `splitsmith worker --one-shot`, restart policy NEVER, in both envs. It only runs on deploy; enqueued jobs strand forever. The old ~13-minute cron kept Neon awake (~5 min idle-suspend window per wake) and crash-looped on cold-start PoolTimeout before #489/#491.
- `serve` is always-on in both envs (`sleepApplication: false`) - the main Railway burn, and its DB connections keep Neon awake.

## Design note: queue of record vs wake channel

Procrastinate-on-Postgres stays. The queue tables only cost Neon compute at moments Neon must be awake anyway (enqueue during a user request; drain while the worker writes results to the same DB), and transactional enqueue (job row + queue entry in one commit) is a property an external queue cannot give back without an outbox poller. What must never exist is a process polling Postgres as its wake channel. The `WorkerLauncher` seam is that wake channel: dumb, lossy-tolerant, outside Neon. Nets for lost signals, in order: the boot re-check (next visit), the 6-hourly safety cron (worst case).

---

### Task 1: Fix `.railwayignore` (PR 1)

**Files:**
- Modify: `.railwayignore` (the "Regenerable / redundant SPA bits" block)

**Interfaces:**
- Produces: a `railway up` build context that contains `src/splitsmith/ui_static/pnpm-lock.yaml`. Tasks 2-3 depend on builds succeeding.

There is no unit test for an ignore file; verification is the staging deploy in Task 2. Steps:

- [ ] **Step 1: Create branch**

```bash
git checkout main && git pull
git checkout -b fix/railwayignore-pnpm-lock
```

- [ ] **Step 2: Edit `.railwayignore`**

Replace this block:

```
# Regenerable / redundant SPA bits (node_modules rebuilt by `npm ci`, dist
# rebuilt by the spa stage, alternate lockfile unused).
src/splitsmith/ui_static/node_modules
src/splitsmith/ui_static/dist
src/splitsmith/ui_static/pnpm-lock.yaml
**/*.map
```

with:

```
# Regenerable SPA bits (node_modules rebuilt by `pnpm install`, dist
# rebuilt by the spa stage). pnpm-lock.yaml MUST ship: the Dockerfile
# COPYs it (the SPA is pnpm-only since PR #506) and the Railway build
# fails with "pnpm-lock.yaml: not found" when this file strips it.
src/splitsmith/ui_static/node_modules
src/splitsmith/ui_static/dist
**/*.map
```

- [ ] **Step 3: Sanity-check locally**

```bash
grep -n "pnpm-lock" .railwayignore
```

Expected: only the comment lines match; no bare `src/splitsmith/ui_static/pnpm-lock.yaml` entry remains.

- [ ] **Step 4: Run CI gates**

```bash
uv run ruff check . && uv run black --check . && uv run pytest -q
```

Expected: all pass (no Python touched; this catches unrelated local drift).

- [ ] **Step 5: Commit, push, PR, merge**

```bash
git add .railwayignore
git commit -m "fix(deploy): stop stripping pnpm-lock.yaml from the Railway build context

.railwayignore predates PR #506 (SPA moved to pnpm-only) and still
excluded the lockfile as an 'alternate lockfile unused'. The Dockerfile
COPYs it, so every railway up since #506 failed with
'pnpm-lock.yaml: not found' - the failure that forced #507 to disable
auto-deploys."
git push -u origin fix/railwayignore-pnpm-lock
gh pr create --fill && gh pr merge --squash --auto
```

---

### Task 2: Verify the staging build is green (ops)

**Interfaces:**
- Consumes: merged Task 1 on `main`.
- Produces: proof the Railway build works, gating Task 3.

- [ ] **Step 1: Dispatch a staging deploy**

```bash
gh workflow run deploy-app.yml -f environment=staging
sleep 10
gh run watch "$(gh run list --workflow=deploy-app.yml -L1 --json databaseId -q '.[0].databaseId')" --exit-status
```

Expected: both `railway up` steps (serve, worker) succeed; the run exits 0. If it fails on the same `pnpm-lock.yaml` error, the upload still excludes it - stop and re-investigate (`railway up` also respects `.gitignore`).

- [ ] **Step 2: Confirm serve is healthy on staging**

```bash
curl -sf https://my.staging.splitsmith.app/api/health && echo OK
railway deployment list -s serve -e staging 2>/dev/null | head -3
```

Expected: `OK`; newest serve deployment shows `SUCCESS`. The worker deployment will run `--one-shot` and exit - any terminal status is fine here; its semantics are recorded in Task 4.

---

### Task 3: Re-enable auto-deploy triggers (PR 2)

**Files:**
- Modify: `.github/workflows/deploy-app.yml` (the `on:` block and header comment)

**Interfaces:**
- Consumes: green staging build from Task 2.
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
the stale .railwayignore pnpm-lock.yaml exclusion, fixed in the
previous commit; staging built green on manual dispatch."
git push -u origin ci/reenable-railway-autodeploy
gh pr create --fill && gh pr merge --squash --auto
```

Note: merging this PR itself triggers a staging deploy - that is the live test.

---

### Task 4: Validate the Railway trigger calls by hand (ops)

Manually exercise the exact GraphQL calls PR 3 will make, and record the status semantics of a one-shot worker deployment. Also the first live test of whether the #491 connect-retry survives a Neon cold start.

**Interfaces:**
- Produces: (a) the serve service id, (b) the observed terminal status of an exited one-shot worker (expected `COMPLETED` on exit 0, `CRASHED` on non-zero), (c) confirmation `serviceInstanceRedeploy` works. Task 6 encodes (b) in `ACTIVE_STATUSES`.

- [ ] **Step 1: Set up shell vars**

```bash
TOKEN=$(python3 -c "import json,pathlib;print(json.load(open(pathlib.Path.home()/'.railway'/'config.json'))['user']['accessToken'])")
API=https://backboard.railway.com/graphql/v2
STAGING=1fe9b8f7-6096-46bd-8cb0-d56ca5c1378b
WORKER=3586f358-7c20-41a5-a5c6-3ff6bc627e95
```

- [ ] **Step 2: Fetch the serve service id (record it in this plan)**

```bash
curl -s "$API" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"query":"query { project(id: \"e77bded4-ddb2-430c-816f-156f2b6fe36a\") { services { edges { node { id name } } } } }"}' | python3 -m json.tool
```

Expected: two services; note the `serve` id for Task 8.

- [ ] **Step 3: Query the worker's latest deployment status (the PR 3 status gate)**

```bash
curl -s "$API" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"query\":\"query { deployments(first: 1, input: {serviceId: \\\"$WORKER\\\", environmentId: \\\"$STAGING\\\"}) { edges { node { id status createdAt } } } }\"}" | python3 -m json.tool
```

Expected: one edge with a terminal status (the Task 2 deploy's run).

- [ ] **Step 4: Fire the redeploy mutation (the PR 3 trigger) and watch the run**

```bash
curl -s "$API" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"query\":\"mutation { serviceInstanceRedeploy(serviceId: \\\"$WORKER\\\", environmentId: \\\"$STAGING\\\") }\"}" | python3 -m json.tool
railway logs -s worker -e staging -d 2>&1 | tail -40
```

Expected: mutation returns without errors; logs show the one-shot worker connect (possibly with #491 retries against a cold Neon - note how many attempts), drain nothing, exit.

- [ ] **Step 5: Record the terminal status after exit**

Re-run the Step 3 query ~2 minutes after the worker exits.

Expected: `COMPLETED` (clean exit) or `CRASHED` (non-zero). **Checkpoint:** write the observed value down. If a clean exit shows anything other than `COMPLETED`/`REMOVED`, adjust `ACTIVE_STATUSES` in Task 6 so that status is treated as idle. If the run crash-looped on PoolTimeout despite #491, STOP - that root cause must be fixed before PR 3 has a point.

---

### Task 5: `worker_trigger` config loading (PR 3, part 1)

**Files:**
- Create: `src/splitsmith/worker_trigger.py`
- Create: `tests/test_worker_trigger.py`

**Interfaces:**
- Produces: `RailwayLauncherConfig` (Pydantic: `token: str`, `service_id: str`, `environment_id: str`, `api_url: str = RAILWAY_API_URL`, `cooldown_seconds: float = 30.0`) and `load_railway_config() -> RailwayLauncherConfig | None`. Env var names exported as `ENV_WORKER_LAUNCHER = "SPLITSMITH_WORKER_LAUNCHER"`, `ENV_TRIGGER_TOKEN = "SPLITSMITH_WORKER_TRIGGER_TOKEN"`, `ENV_WORKER_SERVICE_ID = "SPLITSMITH_WORKER_SERVICE_ID"`, `ENV_WORKER_ENVIRONMENT_ID = "SPLITSMITH_WORKER_ENVIRONMENT_ID"`, `ENV_RAILWAY_ENVIRONMENT_ID = "RAILWAY_ENVIRONMENT_ID"`.

- [ ] **Step 1: Create branch**

```bash
git checkout main && git pull
git checkout -b feat/worker-launcher-on-enqueue
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_worker_trigger.py`:

```python
"""Unit tests for the worker launcher seam - no network, httpx.MockTransport only."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from splitsmith.worker_trigger import (
    ENV_RAILWAY_ENVIRONMENT_ID,
    ENV_TRIGGER_TOKEN,
    ENV_WORKER_ENVIRONMENT_ID,
    ENV_WORKER_LAUNCHER,
    ENV_WORKER_SERVICE_ID,
    load_railway_config,
)

_ALL_ENV_VARS = (
    ENV_WORKER_LAUNCHER,
    ENV_TRIGGER_TOKEN,
    ENV_WORKER_SERVICE_ID,
    ENV_WORKER_ENVIRONMENT_ID,
    ENV_RAILWAY_ENVIRONMENT_ID,
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ALL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_load_railway_config_disabled_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    assert load_railway_config() is None


def test_load_railway_config_disabled_when_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token without a service id (or vice versa) must not half-enable the launcher."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_TRIGGER_TOKEN, "tok")
    assert load_railway_config() is None


def test_load_railway_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_TRIGGER_TOKEN, "tok")
    monkeypatch.setenv(ENV_WORKER_SERVICE_ID, "svc-id")
    monkeypatch.setenv(ENV_WORKER_ENVIRONMENT_ID, "env-id")
    config = load_railway_config()
    assert config is not None
    assert (config.token, config.service_id, config.environment_id) == ("tok", "svc-id", "env-id")


def test_load_railway_config_falls_back_to_railway_env_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Railway injects RAILWAY_ENVIRONMENT_ID into every container; the
    SPLITSMITH_ override exists only for running outside Railway."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_TRIGGER_TOKEN, "tok")
    monkeypatch.setenv(ENV_WORKER_SERVICE_ID, "svc-id")
    monkeypatch.setenv(ENV_RAILWAY_ENVIRONMENT_ID, "railway-env-id")
    config = load_railway_config()
    assert config is not None
    assert config.environment_id == "railway-env-id"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_worker_trigger.py -q
```

Expected: FAIL - `ModuleNotFoundError: No module named 'splitsmith.worker_trigger'`.

- [ ] **Step 4: Write the module skeleton with config loading**

Create `src/splitsmith/worker_trigger.py`:

```python
"""Launch a one-shot worker run when a job is enqueued (the wake channel).

The hosted worker runs ``splitsmith worker --one-shot`` with restart
policy NEVER: it drains the queue and exits, so no idle process holds a
connection that keeps the Neon compute awake. Something must therefore
start it when work arrives. The API process is the only enqueuer, so
after a successful defer it fires the configured :class:`WorkerLauncher`.

The queue of record stays in Postgres (Procrastinate); a launcher is
only the wake signal and must be safe to lose - the serve-boot pending
re-check and the 6-hourly safety cron on the worker service are the
nets. The only implementation today is Railway (GraphQL
``serviceInstanceRedeploy``); the seam exists so non-Railway workers
(webhook/ntfy to a home machine, GitHub Actions) can be added without
touching the enqueue path.

Configuration comes entirely from env vars (:func:`build_worker_launcher`);
when they are absent the launcher is disabled and enqueue behaves exactly
as before, so local / docker-compose deployments never talk to Railway.
"""

from __future__ import annotations

import logging
import os

from pydantic import BaseModel

logger = logging.getLogger(__name__)

RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"

# Selects the launcher implementation in build_worker_launcher();
# "railway" is the only one today.
ENV_WORKER_LAUNCHER = "SPLITSMITH_WORKER_LAUNCHER"
ENV_TRIGGER_TOKEN = "SPLITSMITH_WORKER_TRIGGER_TOKEN"
ENV_WORKER_SERVICE_ID = "SPLITSMITH_WORKER_SERVICE_ID"
ENV_WORKER_ENVIRONMENT_ID = "SPLITSMITH_WORKER_ENVIRONMENT_ID"
# Railway injects this into every deployed container; the SPLITSMITH_
# variable above exists only to override it outside Railway.
ENV_RAILWAY_ENVIRONMENT_ID = "RAILWAY_ENVIRONMENT_ID"


class RailwayLauncherConfig(BaseModel):
    """Railway coordinates + auth for starting a one-shot worker run."""

    token: str
    service_id: str
    environment_id: str
    api_url: str = RAILWAY_API_URL
    cooldown_seconds: float = 30.0


def load_railway_config() -> RailwayLauncherConfig | None:
    """Read the Railway launcher config from env vars; ``None`` disables it."""
    token = os.environ.get(ENV_TRIGGER_TOKEN, "").strip()
    service_id = os.environ.get(ENV_WORKER_SERVICE_ID, "").strip()
    environment_id = (
        os.environ.get(ENV_WORKER_ENVIRONMENT_ID, "").strip()
        or os.environ.get(ENV_RAILWAY_ENVIRONMENT_ID, "").strip()
    )
    if not (token and service_id and environment_id):
        return None
    return RailwayLauncherConfig(token=token, service_id=service_id, environment_id=environment_id)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_worker_trigger.py -q
```

Expected: 4 passed.

- [ ] **Step 6: Gates + commit**

```bash
uv run ruff check . && uv run black --check . && uv run pytest -q
git add src/splitsmith/worker_trigger.py tests/test_worker_trigger.py
git commit -m "feat(worker): Railway launcher config loading from env"
```

---

### Task 6: `RailwayWorkerLauncher` + the `WorkerLauncher` seam (PR 3, part 2)

**Files:**
- Modify: `src/splitsmith/worker_trigger.py`
- Modify: `tests/test_worker_trigger.py`

**Interfaces:**
- Consumes: `RailwayLauncherConfig` from Task 5.
- Produces:
  - `class WorkerLauncher(Protocol)` with `def schedule(self) -> None` and `async def trigger(self) -> bool` - the seam every future launcher implements.
  - `class RailwayWorkerLauncher` implementing it: `__init__(config: RailwayLauncherConfig, transport: httpx.AsyncBaseTransport | None = None)`; `trigger()` never raises.
  - `build_worker_launcher(transport: httpx.AsyncBaseTransport | None = None) -> WorkerLauncher | None` - dispatches on `SPLITSMITH_WORKER_LAUNCHER` (default `railway`); unknown names raise `ValueError` (misconfigured hosted boot must fail loudly, not silently strand jobs).
  - Module constant `ACTIVE_STATUSES`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_worker_trigger.py` (extend the import block with `RailwayLauncherConfig`, `RailwayWorkerLauncher`, `build_worker_launcher`):

```python
def _config(**overrides: Any) -> RailwayLauncherConfig:
    base: dict[str, Any] = {"token": "tok", "service_id": "svc", "environment_id": "env"}
    base.update(overrides)
    return RailwayLauncherConfig(**base)


def _transport(latest_status: str | None, requests: list[dict[str, Any]]) -> httpx.MockTransport:
    """Answer the status query with ``latest_status`` (None = no deployments
    yet) and ack the redeploy mutation. Records every GraphQL request body."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if "serviceInstanceRedeploy" in body["query"]:
            return httpx.Response(200, json={"data": {"serviceInstanceRedeploy": True}})
        edges = [] if latest_status is None else [{"node": {"id": "d1", "status": latest_status}}]
        return httpx.Response(200, json={"data": {"deployments": {"edges": edges}}})

    return httpx.MockTransport(handler)


def test_launcher_redeploys_when_worker_idle() -> None:
    requests: list[dict[str, Any]] = []
    launcher = RailwayWorkerLauncher(_config(), transport=_transport("COMPLETED", requests))
    assert asyncio.run(launcher.trigger()) is True
    assert len(requests) == 2
    assert "serviceInstanceRedeploy" in requests[1]["query"]
    assert requests[1]["variables"] == {"serviceId": "svc", "environmentId": "env"}


def test_launcher_redeploys_when_no_deployment_history() -> None:
    requests: list[dict[str, Any]] = []
    launcher = RailwayWorkerLauncher(_config(), transport=_transport(None, requests))
    assert asyncio.run(launcher.trigger()) is True


def test_launcher_skips_while_worker_active() -> None:
    """SUCCESS means the one-shot container is still up mid-drain; a redeploy
    now would kill the in-flight job."""
    requests: list[dict[str, Any]] = []
    launcher = RailwayWorkerLauncher(_config(), transport=_transport("SUCCESS", requests))
    assert asyncio.run(launcher.trigger()) is False
    assert len(requests) == 1  # status query only, no mutation


def test_launcher_cooldown_suppresses_burst() -> None:
    requests: list[dict[str, Any]] = []

    async def scenario() -> tuple[bool, bool]:
        launcher = RailwayWorkerLauncher(_config(), transport=_transport("COMPLETED", requests))
        return await launcher.trigger(), await launcher.trigger()

    first, second = asyncio.run(scenario())
    assert first is True
    assert second is False
    assert len(requests) == 2  # the second call never reached the API


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
        launcher = RailwayWorkerLauncher(_config(), transport=_transport("COMPLETED", requests))
        launcher.schedule()
        await asyncio.gather(*list(launcher._tasks))

    asyncio.run(scenario())
    assert len(requests) == 2


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

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_worker_trigger.py -q
```

Expected: FAIL - `ImportError: cannot import name 'RailwayWorkerLauncher'`.

- [ ] **Step 3: Implement the seam and the Railway launcher**

Append to `src/splitsmith/worker_trigger.py`, extending the import block to:

```python
import asyncio
import logging
import os
import time
from typing import Any, Protocol

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


# Deployment statuses meaning "a worker container is running or about to
# be": redeploying now would kill an in-flight drain, so the launcher
# skips. Terminal statuses (COMPLETED, CRASHED, FAILED, REMOVED) mean
# idle. Verified against live one-shot runs on staging (plan task 4).
ACTIVE_STATUSES = frozenset({"QUEUED", "WAITING", "BUILDING", "DEPLOYING", "INITIALIZING", "SUCCESS"})

_STATUS_QUERY = """\
query WorkerDeploymentStatus($serviceId: String!, $environmentId: String!) {
  deployments(first: 1, input: {serviceId: $serviceId, environmentId: $environmentId}) {
    edges { node { id status } }
  }
}
"""

_REDEPLOY_MUTATION = """\
mutation TriggerWorkerRun($serviceId: String!, $environmentId: String!) {
  serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
}
"""


class RailwayWorkerLauncher:
    """Fires ``serviceInstanceRedeploy`` for the worker service, at most
    once per cooldown window, and only when no worker instance is active."""

    def __init__(
        self,
        config: RailwayLauncherConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
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
        """Redeploy the worker unless one is active or we are in cooldown.

        Returns True when a redeploy was fired. Never raises: a Railway
        outage must not break enqueue - a missed signal is repaired by
        the boot re-check or the safety cron.
        """
        async with self._lock:
            now = time.monotonic()
            if (
                self._last_attempt is not None
                and now - self._last_attempt < self._config.cooldown_seconds
            ):
                return False
            # Stamp before the API calls so a slow/failing Railway is not
            # hammered by every enqueue in a burst.
            self._last_attempt = now
            try:
                status = await self._latest_deployment_status()
                if status in ACTIVE_STATUSES:
                    logger.info("worker launcher: skipped, worker deployment is %s", status)
                    return False
                await self._redeploy()
                logger.info("worker launcher: redeploy fired (previous status: %s)", status)
                return True
            except Exception:
                logger.warning(
                    "worker launcher: failed; boot re-check or safety cron will drain the queue",
                    exc_info=True,
                )
                return False

    async def _graphql(self, query: str) -> dict[str, Any]:
        variables = {
            "serviceId": self._config.service_id,
            "environmentId": self._config.environment_id,
        }
        response = await self._client.post(
            self._config.api_url, json={"query": query, "variables": variables}
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"Railway GraphQL error: {payload['errors']}")
        return payload["data"]

    async def _latest_deployment_status(self) -> str | None:
        data = await self._graphql(_STATUS_QUERY)
        edges = data["deployments"]["edges"]
        return edges[0]["node"]["status"] if edges else None

    async def _redeploy(self) -> None:
        await self._graphql(_REDEPLOY_MUTATION)


def build_worker_launcher(
    transport: httpx.AsyncBaseTransport | None = None,
) -> WorkerLauncher | None:
    """Select and build the launcher from ``SPLITSMITH_WORKER_LAUNCHER``
    (default ``railway``). ``None`` (launcher disabled) when the selected
    implementation's env config is absent; unknown names raise so a
    misconfigured hosted boot fails loudly instead of stranding jobs."""
    kind = os.environ.get(ENV_WORKER_LAUNCHER, "").strip().lower() or "railway"
    if kind == "railway":
        config = load_railway_config()
        return RailwayWorkerLauncher(config, transport=transport) if config else None
    raise ValueError(f"{ENV_WORKER_LAUNCHER}={kind!r}: unknown worker launcher")
```

**Checkpoint from Task 4:** if the observed clean-exit status was NOT `COMPLETED` (and not already in the terminal set), update `ACTIVE_STATUSES` and its comment here to match reality before committing.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_worker_trigger.py -q
```

Expected: 14 passed.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff check . && uv run black --check . && uv run pytest -q
git add src/splitsmith/worker_trigger.py tests/test_worker_trigger.py
git commit -m "feat(worker): WorkerLauncher seam + Railway status-gated one-shot launcher"
```

---

### Task 7: Wire the launcher into the hosted enqueue path + boot re-check (PR 3, part 3)

**Files:**
- Modify: `src/splitsmith/worker_trigger.py` (add `wrap_deferrer`, `make_boot_retrigger`)
- Modify: `src/splitsmith/ui/server.py` (hosted wiring at `deferrer = make_deferrer(url)` ~line 4236; `class AppState` hosted attrs ~lines 786-850; `create_app` after `app = FastAPI(...)` ~line 4457)
- Modify: `tests/test_worker_trigger.py`
- Modify: `docs/saas-readiness/04-compute-backends.md` (document the mechanism)

**Interfaces:**
- Consumes: `WorkerLauncher` protocol and `build_worker_launcher` from Task 6; `make_deferrer(url)` returning `Callable[..., Awaitable[None]]` with keyword-only params `(job_id, user_id, kind, args, match_id)`; the hosted `session_factory` (async SQLAlchemy sessionmaker) in `_apply_hosted_mode_wiring`.
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


class _StubResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _StubSession:
    def __init__(self, pending: int) -> None:
        self._pending = pending

    async def execute(self, _query: Any) -> _StubResult:
        return _StubResult(self._pending)


class _StubSessionCtx:
    def __init__(self, pending: int) -> None:
        self._pending = pending

    async def __aenter__(self) -> _StubSession:
        return _StubSession(self._pending)

    async def __aexit__(self, *exc: Any) -> None:
        return None


def test_boot_retrigger_fires_when_jobs_pending() -> None:
    stub = _StubLauncher()
    hook = make_boot_retrigger(lambda: _StubSessionCtx(pending=2), stub)
    asyncio.run(hook())
    assert stub.scheduled == 1


def test_boot_retrigger_quiet_when_queue_empty() -> None:
    stub = _StubLauncher()
    hook = make_boot_retrigger(lambda: _StubSessionCtx(pending=0), stub)
    asyncio.run(hook())
    assert stub.scheduled == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_worker_trigger.py -q
```

Expected: FAIL - `ImportError: cannot import name 'wrap_deferrer'`.

- [ ] **Step 3: Implement `wrap_deferrer` and `make_boot_retrigger`**

Append to `src/splitsmith/worker_trigger.py` (add `Awaitable`, `Callable` to the `typing` import):

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

Expected: 18 passed.

- [ ] **Step 5: Wire into the hosted boot path**

In `src/splitsmith/ui/server.py`:

(a) In `_apply_hosted_mode_wiring`, extend the local import block (~line 4188):

```python
    from ..queue import make_deferrer
    from ..worker_trigger import build_worker_launcher, make_boot_retrigger, wrap_deferrer
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
    # each defer, and re-checks for stranded jobs on every boot (see
    # splitsmith.worker_trigger). No-op when the launcher env vars are
    # unset - local / docker-compose runs an always-on worker instead.
    worker_launcher = build_worker_launcher()
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
service via Railway's GraphQL API, skipping when the worker's latest
deployment is still active so an in-flight drain is never killed.
Railway config: `SPLITSMITH_WORKER_TRIGGER_TOKEN` (project token),
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
admits webhook/ntfy or GitHub Actions workers later). Status-gated so a
mid-drain worker is never killed, cooldown-limited, fire-and-forget so
enqueue latency is unaffected. Serve re-checks for stranded jobs on
every boot, so the worker safety cron only needs to run 6-hourly:
Railway and Neon both sleep whenever the app is idle."
git push -u origin feat/worker-launcher-on-enqueue
gh pr create --fill && gh pr merge --squash --auto
```

Merging auto-deploys staging (Task 3's trigger) - the env vars from Task 8 must be set for the launcher to activate; until then it is a silent no-op, which is safe.

---

### Task 8: Staging ops - token, env vars, serve sleeping, safety cron

All via CLI/GraphQL; nothing here touches the repo. Use the shell vars from Task 4 plus `SERVE=<serve service id from Task 4 Step 2>`.

- [ ] **Step 1: Create a staging project token**

```bash
curl -s "$API" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"query\":\"mutation { projectTokenCreate(input: {projectId: \\\"e77bded4-ddb2-430c-816f-156f2b6fe36a\\\", environmentId: \\\"$STAGING\\\", name: \\\"worker-trigger-staging\\\"}) }\"}"
```

Expected: `{"data": {"projectTokenCreate": "<token>"}}`. Save as `PT` (do NOT paste into any file that could be committed).

- [ ] **Step 2: Verify the project token authenticates the exact calls the code makes**

```bash
curl -s "$API" -H "Project-Access-Token: $PT" -H "Content-Type: application/json" -d "{\"query\":\"query { deployments(first: 1, input: {serviceId: \\\"$WORKER\\\", environmentId: \\\"$STAGING\\\"}) { edges { node { status } } } }\"}"
```

Expected: a status payload, no `Unauthorized`. **Checkpoint:** if this header is rejected, test `-H "Authorization: Bearer $PT"`; if that works instead, change the header in `RailwayWorkerLauncher.__init__` (one line) plus this plan before rollout.

- [ ] **Step 3: Set the launcher env vars on staging serve**

```bash
railway variables --set "SPLITSMITH_WORKER_TRIGGER_TOKEN=$PT" --set "SPLITSMITH_WORKER_SERVICE_ID=$WORKER" --service serve --environment staging
```

(Environment id comes from Railway's injected `RAILWAY_ENVIRONMENT_ID`; launcher kind defaults to `railway`; no other vars needed.)

- [ ] **Step 4: Enable app sleeping on staging serve**

```bash
curl -s "$API" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"query\":\"mutation { serviceInstanceUpdate(serviceId: \\\"$SERVE\\\", environmentId: \\\"$STAGING\\\", input: {sleepApplication: true}) }\"}"
```

- [ ] **Step 5: Add the 6-hourly safety cron on staging worker**

```bash
curl -s "$API" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"query\":\"mutation { serviceInstanceUpdate(serviceId: \\\"$WORKER\\\", environmentId: \\\"$STAGING\\\", input: {cronSchedule: \\\"0 */6 * * *\\\"}) }\"}"
```

Cost note: each cron tick wakes Neon ~6 min (run + idle-suspend window); 4 ticks/day is roughly 4.5 CU-hours/month, ~2% of the Neon free tier. The enqueue launcher carries the latency and the boot re-check catches strays on the next visit - do not shorten this interval, it is the net of last resort.

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

Log in at `https://my.splitsmith.app`'s staging twin `https://my.staging.splitsmith.app`, open an existing match, and run any detection (e.g. re-run beep detect on a stage). Then:

```bash
railway deployment list -s worker -e staging 2>/dev/null | head -3
railway logs -s worker -e staging -d 2>&1 | tail -30
```

Expected: a worker deployment created within ~30 s of the click; logs show it pick up exactly that job, finish, and exit; the job completes in the UI. Serve logs (`railway logs -s serve -e staging -d`) show `worker launcher: redeploy fired`.

- [ ] **Step 2: Worker survives a cold Neon (the #491 checkpoint)**

Leave staging untouched until the Neon staging branch compute suspends (Neon console, typically ~5 min idle), then repeat Step 1. Expected: the drain succeeds - possibly after logged PoolTimeout retries, never a crash. If it crash-loops, STOP and debug `_open_app_with_retry` before touching production.

- [ ] **Step 3: Everything actually sleeps**

Close all app tabs (open SSE connections hold serve awake - expected behavior, note it if observed). After the sleep window: Railway dashboard shows serve sleeping; Neon console shows the staging compute suspended; no worker deployments except the 6-hourly cron ticks, each starting, draining nothing, and exiting within ~a minute.

- [ ] **Step 4: Status-gate sanity**

While a worker drain is visibly running (start a slow job like shot-detect), enqueue a second job immediately. Expected: serve logs `worker launcher: skipped, worker deployment is SUCCESS` (or the observed active status), and the running worker drains the second job itself before exiting.

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

- **Enqueue-during-worker-exit:** a job enqueued between the worker's final empty-queue poll and its exit is missed by both the running drain and the status-gated launcher (which sees the container still active). The boot re-check catches it on the next visit; the 6-hourly cron is the final net. Window is seconds; single-operator; accepted.
- **Cooldown suppression:** a job enqueued < 30 s after a launch that fired for an already-drained queue waits for the boot re-check or cron. Same acceptance; the common burst case (N jobs enqueued together) is handled because the launched worker drains everything present when it polls.
- **Open SSE tabs keep serve awake** and therefore Neon awake. That is "user is active" by definition; no action.
- **`ACTIVE_STATUSES` is empirical:** Task 4 Step 5 records the real terminal status of a one-shot exit; Task 6 encodes it. If Railway changes deployment-status semantics, the failure mode is safe (launcher skips, boot re-check + cron drain).
- **Future non-Railway workers:** implement `WorkerLauncher` (webhook/ntfy publish, GitHub Actions dispatch), select via `SPLITSMITH_WORKER_LAUNCHER`. The worker binary is already portable (DATABASE_URL + R2 creds + models); external workers must be launched, never left polling Postgres.
