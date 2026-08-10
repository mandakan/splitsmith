# Mobile Jobs Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A mobile-first `/match/:matchId/jobs` page that shows the job pipeline (active, failed, recent with phase timings), supports retry of failed jobs end to end, and surfaces a failed-count nav badge.

**Architecture:** Frontend page inside the existing SPA under MatchShell, fed by the shell's single `useJobs()` poller via outlet context (no second poller). Retry is a new vertical: persist wire-serialized submit args on `compute_jobs` (Alembic migration), add `retry()` to both job backends (in-memory `JobRegistry`, `PostgresJobBackend`), expose `POST /api/me/jobs/{job_id}/retry`, and wire an SPA action.

**Tech Stack:** React 18 + TypeScript + Tailwind semantic tokens (SPA at `src/splitsmith/ui_static/`, pnpm + vitest), FastAPI + SQLAlchemy + Alembic (backend), pytest.

**Spec source:** `docs/superpowers/specs/2026-08-10-mobile-operator-surfaces-design.md` (Jobs page slice).

**Spec correction (recorded):** the spec says "phase progress bar fed by the existing compute_jobs.timings data". Timings are only persisted at job completion (`PhaseTimer.build()` in `_finalize_with_timings`), so running jobs cannot show phase progress. Running cards use the live `progress`/`message` fields (as the rail does today); the per-phase breakdown renders on finished jobs.

## Global Constraints

- New copy and comments use a single ASCII dash "-", never em dash, never "--".
- `ui_static` is pnpm-only; never introduce npm or package-lock.json.
- No new dependencies (either side) without asking the user first.
- The Jobs page must NOT be wrapped in `DesktopGate` - it is mobile-first and works at all widths.
- One jobs poller: reuse MatchShell's `useJobs()` state via outlet context; the page never calls `useJobs()` itself.
- Accessibility: WCAG 2.2 AA, minimum 44 px touch targets on actionable elements, status never carried by color alone (always pair dot/color with text), respect `prefers-reduced-motion`.
- Commit messages: conventional commits; UI commits use `feat(ui):`/`fix(ui):` scope (bare `ui:` is dropped from the changelog).
- DB schema changes require a local `pytest -m docker` run before the PR merges. Docker is not on the non-interactive PATH: prepend `~/.claude-tmp/bin` to PATH (it holds the docker symlink).
- Run all of ruff + black + pytest + `pnpm typecheck` + `pnpm test` + `pnpm lint` before opening the PR.
- Execute on a fresh branch `feat/jobs-page` cut from `main` (worktree per superpowers:using-git-worktrees).

---

### Task 1: Persist wire args on compute_jobs rows

**Files:**
- Create: `alembic/versions/<generated>_add_args_to_compute_jobs.py`
- Modify: `src/splitsmith/db/models.py` (ComputeJobRow, near the `timings` column at ~line 486)
- Modify: `src/splitsmith/db/job_backend.py` (`PostgresJobBackend.submit`, row construction at ~line 224)
- Test: `tests/test_postgres_job_backend.py`

**Interfaces:**
- Consumes: `to_wire_args` (already imported in job_backend.py line 45 as `_to_wire_args`).
- Produces: `ComputeJobRow.args: dict | None` column - wire-shaped submit args, NULL only on pre-migration rows. Task 3's retry depends on this. `args` is NOT added to the wire `Job` model or `_ROW_TO_JOB_FIELDS`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_postgres_job_backend.py`, mirroring the setup of `test_submit_persists_row_and_runs_to_succeeded` (line 180 - reuse that test's backend/session fixture idiom exactly):

```python
def test_submit_persists_wire_args(tmp_path) -> None:
    """Submit stores wire-serialised args on the row; empty args become {} not NULL."""
    # setup: copy the backend + session fixture from
    # test_submit_persists_row_and_runs_to_succeeded in this file
    job = _run(backend.submit(kind="detect_beep", args={"video_id": "v1"}, stage_number=2))
    row = _fetch_row(session_factory, job.id)  # reuse/mirror this file's row-fetch helper
    assert row.args == {"video_id": "v1"}

    job2 = _run(backend.submit(kind="detect_beep", stage_number=2))
    row2 = _fetch_row(session_factory, job2.id)
    assert row2.args == {}  # None coalesces to {} so NULL means "pre-migration"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH=~/.claude-tmp/bin:$PATH uv run pytest tests/test_postgres_job_backend.py::test_submit_persists_wire_args -v`
Expected: FAIL (`args` attribute/column does not exist).

- [ ] **Step 3: Generate and fill the migration**

Run: `uv run alembic revision -m "add args to compute_jobs"` (this sets `down_revision` to the current head, `0c1dbb2ce678`). Fill it, mirroring `f6acac06499c_add_shooter_slug_to_compute_jobs.py`:

```python
"""add args to compute_jobs

Persists the wire-serialised submit args (job_journal.to_wire_args shape)
so a failed job can be re-enqueued by the retry endpoint. Nullable, no
backfill: rows are ephemeral (boot-swept), and NULL is the retry
endpoint's "predates retry support" sentinel. Plain metadata on an
already-RLS'd table - the tenant_isolation policy keys on user_id only,
so no RLS DDL here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "<generated>"
down_revision: str | Sequence[str] | None = "0c1dbb2ce678"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "compute_jobs",
        sa.Column("args", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("compute_jobs", "args")
```

- [ ] **Step 4: Add the column to ComputeJobRow**

In `src/splitsmith/db/models.py`, inside `ComputeJobRow` next to `timings` (same JSON idiom, line ~486):

```python
    # Wire-serialised submit args (job_journal.to_wire_args shape),
    # persisted so retry can re-enqueue a failed job. NULL only on rows
    # created before the retry migration - retry refuses those. Not part
    # of the wire Job model.
    args: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
```

- [ ] **Step 5: Persist args in PostgresJobBackend.submit**

In `src/splitsmith/db/job_backend.py`, add one kwarg to the `ComputeJobRow(...)` construction inside `submit` (~line 224):

```python
        row = ComputeJobRow(
            id=job_id,
            user_id=self._user_id,
            kind=kind,
            args=_to_wire_args(args or {}),
            stage_number=stage_number,
            ...
        )
```

Do NOT add `args` to `_ROW_TO_JOB_FIELDS` / `_COLUMNS` wire projection - the SPA never sees raw args.

- [ ] **Step 6: Run test to verify it passes**

Run: `PATH=~/.claude-tmp/bin:$PATH uv run pytest tests/test_postgres_job_backend.py -v`
Expected: new test PASS, all existing tests in the file still PASS.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/*_add_args_to_compute_jobs.py src/splitsmith/db/models.py src/splitsmith/db/job_backend.py tests/test_postgres_job_backend.py
git commit -m "feat(db): persist wire submit args on compute_jobs for retry"
```

---

### Task 2: JobNotRetryableError + JobRegistry.retry (local backend)

**Files:**
- Modify: `src/splitsmith/ui/jobs.py` (exception near `UnknownJobKindError`; `JobBackend` protocol ~line 400; `JobRegistry.__init__` ~line 453, `submit` ~line 532, `_trim_retained_locked`)
- Test: `tests/test_jobs.py`

**Interfaces:**
- Produces: `class JobNotRetryableError(Exception)`; protocol method `async def retry(self, job_id: str) -> Job | None` - returns the NEW pending Job, `None` for unknown id, raises `JobNotRetryableError` when the job is not FAILED. The old failed job is acknowledged as a side effect. Tasks 3 and 4 depend on these exact semantics.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_jobs.py`, using the file's `_Sync(JobRegistry(...))` wrapper and terminal-wait idiom (see `test_submit_runs_to_succeeded`, line 93):

```python
def test_retry_failed_job_resubmits_with_original_args() -> None:
    reg = _Sync(JobRegistry(max_concurrent=1))
    calls: list[dict] = []

    def flaky(handle, **kwargs) -> None:
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("boom")

    reg.bodies.register("flaky", flaky)  # mirror this file's body-registration idiom
    job = reg.submit(kind="flaky", args={"x": 1}, stage_number=3, shooter_slug="anna")
    _wait_terminal(reg, job.id)  # reuse this file's wait helper
    assert reg.get(job.id).status is JobStatus.FAILED

    new = reg.retry(job.id)
    assert new is not None and new.id != job.id
    assert new.kind == "flaky" and new.stage_number == 3 and new.shooter_slug == "anna"
    _wait_terminal(reg, new.id)
    assert calls == [{"x": 1}, {"x": 1}]
    assert reg.get(job.id).acknowledged is True  # retry clears it from the failed list


def test_retry_unknown_id_returns_none() -> None:
    reg = _Sync(JobRegistry())
    assert reg.retry("nope") is None


def test_retry_non_failed_job_raises() -> None:
    reg = _Sync(JobRegistry(max_concurrent=1))
    reg.bodies.register("ok", lambda handle, **kw: None)
    job = reg.submit(kind="ok")
    _wait_terminal(reg, job.id)  # succeeded
    with pytest.raises(JobNotRetryableError):
        reg.retry(job.id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_jobs.py -k retry -v`
Expected: FAIL (`JobNotRetryableError` / `retry` not defined).

- [ ] **Step 3: Implement**

In `src/splitsmith/ui/jobs.py`:

Exception (next to the existing job exceptions):

```python
class JobNotRetryableError(Exception):
    """Job exists but cannot be retried (not FAILED, or args unknown)."""
```

Protocol (`JobBackend`, with the other method stubs ~line 406-436):

```python
    async def retry(self, job_id: str) -> Job | None: ...
```

`JobRegistry.__init__`: add

```python
        # Original call_args per retained job, so retry can re-enqueue.
        # Entries die with their job in _trim_retained_locked.
        self._args: dict[str, dict[str, Any]] = {}
```

`JobRegistry.submit`: inside the `with self._lock:` block, right after `self._jobs[job.id] = job`:

```python
            self._args[job.id] = call_args
```

`_trim_retained_locked`: wherever an id is evicted from `self._jobs`, also `self._args.pop(job_id, None)`.

New method on `JobRegistry`:

```python
    async def retry(self, job_id: str) -> Job | None:
        """Re-enqueue a FAILED job with its original args.

        Returns the NEW pending job; the failed job stays in history,
        acknowledged. None for an unknown id. Raises
        JobNotRetryableError when the job is not FAILED.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status is not JobStatus.FAILED:
                raise JobNotRetryableError(f"job is {job.status.value}; only failed jobs can be retried")
            args = dict(self._args.get(job_id) or {})
            kind = job.kind
            stage_number = job.stage_number
            shooter_slug = job.shooter_slug
            video_id = job.video_id
        await self.acknowledge(job_id)
        return await self.submit(
            kind=kind,
            args=args,
            stage_number=stage_number,
            shooter_slug=shooter_slug,
            video_id=video_id,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_jobs.py -v`
Expected: all PASS (retry tests plus no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/jobs.py tests/test_jobs.py
git commit -m "feat: retry for failed jobs in the in-memory job registry"
```

---

### Task 3: PostgresJobBackend.retry (hosted backend)

**Files:**
- Modify: `src/splitsmith/db/job_backend.py`
- Test: `tests/test_postgres_job_backend.py`

**Interfaces:**
- Consumes: `ComputeJobRow.args` (Task 1), `JobNotRetryableError` (Task 2), `rehydrate_args(kind, args)` from `splitsmith.ui.job_journal`.
- Produces: `PostgresJobBackend.retry(job_id) -> Job | None` with identical semantics to Task 2, plus one extra failure mode: `row.args is None` (pre-migration row) raises `JobNotRetryableError`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_postgres_job_backend.py` (same fixture idiom as Task 1's test):

```python
def test_retry_failed_job_reenqueues_with_persisted_args(tmp_path) -> None:
    # setup: backend with a body that fails on first call, succeeds on second
    job = _run(backend.submit(kind="flaky", args={"x": 1}, stage_number=3))
    _wait_failed(backend, job.id)
    new = _run(backend.retry(job.id))
    assert new is not None and new.id != job.id
    assert new.kind == "flaky" and new.stage_number == 3
    old = _run(backend.get(job.id))
    assert old.acknowledged is True


def test_retry_pre_migration_row_raises(tmp_path) -> None:
    job = _run(backend.submit(kind="flaky", args={"x": 1}))
    _wait_failed(backend, job.id)
    _set_row_args_null(session_factory, job.id)  # simulate a pre-migration row
    with pytest.raises(JobNotRetryableError):
        _run(backend.retry(job.id))


def test_retry_wrong_user_returns_none(tmp_path) -> None:
    # two backends over the same DB, bound to different user ids - copy the
    # two-backend construction from this file's existing tenant-isolation test
    job = _run(backend_a.submit(kind="flaky", args={"x": 1}))
    _wait_failed(backend_a, job.id)
    assert _run(backend_b.retry(job.id)) is None  # not-found, never an exception
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PATH=~/.claude-tmp/bin:$PATH uv run pytest tests/test_postgres_job_backend.py -k retry -v`
Expected: FAIL (`retry` not defined).

- [ ] **Step 3: Implement**

In `src/splitsmith/db/job_backend.py`:

Extend the existing journal import (line 45): `from ..ui.job_journal import rehydrate_args, to_wire_args`, and import `JobNotRetryableError` alongside the other `..ui.jobs` imports.

New method on `PostgresJobBackend`, mirroring the row-fetch-with-user-filter pattern the file's `cancel()` uses:

```python
    async def retry(self, job_id: str) -> Job | None:
        """Re-enqueue a FAILED job as a new row using its persisted args.

        Returns the NEW pending job; the failed row stays, acknowledged.
        None when the id is unknown for this user. Raises
        JobNotRetryableError when the row is not FAILED or predates the
        args column (args is NULL).
        """
        async with self._session_factory() as session:
            row = await self._get_row(session, job_id)  # user-filtered fetch, as in cancel()
            if row is None:
                return None
            if row.status != JobStatus.FAILED.value:
                raise JobNotRetryableError(f"job is {row.status}; only failed jobs can be retried")
            if row.args is None:
                raise JobNotRetryableError("job predates retry support; re-run it from its surface")
            row.acknowledged = True
            row.updated_at = datetime.now(UTC)
            kind = row.kind
            wire_args = row.args
            stage_number = row.stage_number
            shooter_slug = row.shooter_slug
            video_id = row.video_id
            await session.commit()
        return await self.submit(
            kind=kind,
            args=rehydrate_args(kind, wire_args),
            stage_number=stage_number,
            shooter_slug=shooter_slug,
            video_id=video_id,
        )
```

Note: `submit` re-wires via `_to_wire_args`, so passing rehydrated (typed) args is required - never pass `row.args` straight through.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PATH=~/.claude-tmp/bin:$PATH uv run pytest tests/test_postgres_job_backend.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/db/job_backend.py tests/test_postgres_job_backend.py
git commit -m "feat(db): retry for failed jobs in the postgres job backend"
```

---

### Task 4: POST /api/me/jobs/{job_id}/retry

**Files:**
- Modify: `src/splitsmith/ui/server.py` (immediately after the cancel route, ~line 9252)
- Test: `tests/test_ui_server.py`

**Interfaces:**
- Consumes: `state.jobs.retry(job_id)` (Tasks 2/3), `JobNotRetryableError`.
- Produces: `POST /api/me/jobs/{job_id}/retry` returning the NEW `Job` (200), 404 for unknown id, 409 for not-retryable. Task 5's `api.retryJob` calls this.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ui_server.py` next to `test_cancel_endpoint_returns_404_for_unknown_job` (line 2750), reusing `_seed_project_with_primary` and `_wait_for_job`:

```python
def test_retry_endpoint_returns_404_for_unknown_job(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.post("/api/me/jobs/does-not-exist/retry")
    assert resp.status_code == 404


def test_retry_endpoint_409_for_non_failed_job(tmp_path: Path, monkeypatch) -> None:
    # submit a job that succeeds (mirror the body-registration monkeypatching
    # used by test_jobs_endpoints_list_and_get, line 4135), wait terminal,
    # then retry it
    resp = client.post(f"/api/me/jobs/{job_id}/retry")
    assert resp.status_code == 409


def test_retry_endpoint_reenqueues_failed_job(tmp_path: Path, monkeypatch) -> None:
    # register a body that raises on first call; submit, wait for FAILED
    resp = client.post(f"/api/me/jobs/{job_id}/retry")
    assert resp.status_code == 200
    new = resp.json()
    assert new["id"] != job_id and new["kind"] == kind
    old = client.get(f"/api/me/jobs/{job_id}").json()
    assert old["acknowledged"] is True
```

Fill the two sketched setups by copying the neighboring jobs-endpoint tests' registration/monkeypatch scaffolding verbatim.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui_server.py -k retry_endpoint -v`
Expected: FAIL with 404/405 (route absent).

- [ ] **Step 3: Implement the route**

In `src/splitsmith/ui/server.py`, directly after the cancel route, mirroring its handler shape (state access, auth, error mapping):

```python
@app.post("/api/me/jobs/{job_id}/retry", response_model=Job)
async def retry_job(job_id: str) -> Job:
    """Re-enqueue a failed job with its original args; returns the new job."""
    try:
        job = await state.jobs.retry(job_id)
    except JobNotRetryableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job
```

Import `JobNotRetryableError` with the other `jobs` imports at the top of server.py.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui_server.py -k "retry_endpoint or cancel_endpoint or jobs_endpoints" -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_ui_server.py
git commit -m "feat: retry endpoint for failed jobs"
```

---

### Task 5: SPA data layer - Job.timings, retryJob, JobsState.retry

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`Job` interface ~line 1202; `api` job methods ~line 2994)
- Modify: `src/splitsmith/ui_static/src/lib/jobs.ts` (`JobsState` ~line 26, hook body)

**Interfaces:**
- Consumes: Task 4's endpoint.
- Produces (Task 6 depends on these exact names):
  - `interface JobPhase { name: string; ms: number }`
  - `interface JobTimings { queue_wait_ms: number | null; total_ms: number; phases: JobPhase[]; meta?: Record<string, unknown> }`
  - `Job.timings: JobTimings | null`
  - `api.retryJob(jobId: string): Promise<Job>`
  - `JobsState.retry(job: Job): Promise<void>`

- [ ] **Step 1: Add the types and API method**

In `api.ts`, above the `Job` interface:

```ts
export interface JobPhase {
  name: string;
  ms: number;
}

/** Per-job observability persisted on completion (observability.py). */
export interface JobTimings {
  queue_wait_ms: number | null;
  total_ms: number;
  phases: JobPhase[];
  meta?: Record<string, unknown>;
}
```

In the `Job` interface, after `result`:

```ts
  /** Phase timings persisted when the job finishes; null while active. */
  timings: JobTimings | null;
```

In the `api` object, next to `cancelJob` (line ~3010): duplicate `cancelJob`'s one-line definition verbatim, rename it `retryJob`, and change only the trailing path segment from `/cancel` to `/retry`. The request-helper name, generic `<Job>` parameter, and options object must be byte-identical to `cancelJob`'s. Result shape:

```ts
  retryJob: (jobId: string) => Promise<Job>   // POST /api/me/jobs/{id}/retry
```

- [ ] **Step 2: Add retry to JobsState**

In `jobs.ts`, extend the interface (after `cancel` at line 35):

```ts
  retry: (job: Job) => Promise<void>;
```

In the hook body, next to the `cancel` callback (line 90), mirroring its error-handling style:

```ts
  const retry = useCallback(async (job: Job) => {
    await api.retryJob(job.id);
    await refresh();
  }, [refresh]);
```

Add `retry` to the returned object (with `acknowledge`, `acknowledgeAll`, `cancel` at ~line 110).

- [ ] **Step 3: Verify**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test`
Expected: typecheck clean; existing tests PASS (any test building a `Job` literal now needs `timings: null` - fix those literals, do not make the field optional).

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/lib/jobs.ts
git commit -m "feat(ui): job timings type and retry action in the jobs data layer"
```

---

### Task 6: Jobs page component

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/Jobs.tsx`
- Modify: `src/splitsmith/ui_static/src/components/Jobs.tsx` (export `KIND_LABEL`, `KIND_ICON`, `jobTarget` - add `export` keywords, lines 53-86)
- Modify: `src/splitsmith/ui_static/src/components/match/MatchShell.tsx` (`MatchShellOutletContext` ~line 63; context provision ~line 621)
- Test: `src/splitsmith/ui_static/src/pages/Jobs.test.tsx`

**Interfaces:**
- Consumes: `JobsState` incl. `retry` (Task 5), `KIND_LABEL`/`KIND_ICON`/`jobTarget` from `@/components/Jobs`, `Kicker` from `@/components/ui`.
- Produces: `export function Jobs()` page component; `MatchShellOutletContext.jobsState?: JobsState`. Task 7 registers the route.

- [ ] **Step 1: Write the failing test**

Create `pages/Jobs.test.tsx`, mirroring `pages/ResultsStage.test.tsx`'s harness (MemoryRouter + `Shell` rendering `<Outlet context={ctx} />`; `beforeAll` stubs for `ResizeObserver`/`matchMedia`):

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { Jobs } from "@/pages/Jobs";
import type { Job } from "@/lib/api";
import type { JobsState } from "@/lib/jobs";

function makeJob(over: Partial<Job>): Job {
  return {
    id: "j1", kind: "detect_beep", stage_number: 3, shooter_slug: "anna",
    video_id: "v1", status: "succeeded", progress: null, message: null,
    error: null, cancel_requested: false, acknowledged: false, result: null,
    timings: null, created_at: "2026-08-10T10:00:00Z",
    updated_at: "2026-08-10T10:01:00Z", started_at: null, finished_at: null,
    ...over,
  };
}

function makeJobsState(jobs: Job[], over: Partial<JobsState> = {}): JobsState {
  return {
    jobs,
    running: jobs.filter((j) => j.status === "running"),
    pending: jobs.filter((j) => j.status === "pending"),
    failed: jobs.filter((j) => j.status === "failed" && !j.acknowledged),
    error: null,
    refresh: vi.fn(), acknowledge: vi.fn(), acknowledgeAll: vi.fn(),
    cancel: vi.fn(), retry: vi.fn(),
    ...over,
  };
}

function renderJobs(jobsState: JobsState) {
  return render(
    <MemoryRouter initialEntries={["/match/m1/jobs"]}>
      <Routes>
        <Route element={<Outlet context={{ jobsState }} />}>
          <Route path="/match/:matchId/jobs" element={<Jobs />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Jobs page", () => {
  it("shows the all-quiet state when nothing is active or failed", () => {
    renderJobs(makeJobsState([]));
    expect(screen.getByText(/all quiet/i)).toBeInTheDocument();
  });

  it("retries a failed job", async () => {
    const failed = makeJob({ id: "jf", status: "failed", error: "boom" });
    const state = makeJobsState([failed]);
    renderJobs(state);
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(state.retry).toHaveBeenCalledWith(failed);
  });

  it("renders phase timings on finished jobs", () => {
    const done = makeJob({
      status: "succeeded",
      timings: { queue_wait_ms: 120, total_ms: 4500, phases: [{ name: "beep_detect", ms: 4380 }] },
    });
    renderJobs(makeJobsState([done]));
    expect(screen.getByText("beep_detect")).toBeInTheDocument();
  });
});
```

(If `@testing-library/user-event` is not already a devDependency, use `fireEvent.click` from `@testing-library/react` instead - do not add a dependency.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm test -- Jobs.test`
Expected: FAIL (module `@/pages/Jobs` not found).

- [ ] **Step 3: Export the shared job presentation helpers**

In `components/Jobs.tsx`, add `export` to the `KIND_LABEL` and `KIND_ICON` const declarations (lines 53-75) and the `jobTarget` function (lines 81-86). No other changes.

- [ ] **Step 4: Extend the outlet context**

In `MatchShell.tsx`: import `type { JobsState } from "@/lib/jobs"`; add to `MatchShellOutletContext`:

```ts
  /** The shell's single jobs poller - pages must use this, never a second useJobs(). */
  jobsState?: JobsState;
```

and add `jobsState` to the context object literal passed to `<Outlet context={...} />` (~line 621; the shell's `useJobs()` result is already in scope as `jobsState`, line ~333 - keep the existing `jobs: jobsState.jobs` entry unchanged).

- [ ] **Step 5: Implement the page**

Create `pages/Jobs.tsx`:

```tsx
import { useOutletContext } from "react-router-dom";
import { Kicker } from "@/components/ui";
import { KIND_ICON, KIND_LABEL, jobTarget } from "@/components/Jobs";
import type { Job, JobTimings } from "@/lib/api";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

function TimingsList({ timings }: { timings: JobTimings }) {
  return (
    <ul className="mt-2 space-y-1 text-xs text-muted">
      {timings.phases.map((p) => (
        <li key={p.name} className="flex justify-between gap-4">
          <span>{p.name}</span>
          <span className="tabular-nums">{fmtMs(p.ms)}</span>
        </li>
      ))}
      <li className="flex justify-between gap-4 border-t border-rule pt-1 text-ink-2">
        <span>total</span>
        <span className="tabular-nums">{fmtMs(timings.total_ms)}</span>
      </li>
    </ul>
  );
}

function JobCard({ job, onCancel, onRetry, onDismiss }: {
  job: Job;
  onCancel?: (job: Job) => void;
  onRetry?: (job: Job) => void;
  onDismiss?: (job: Job) => void;
}) {
  const active = job.status === "running" || job.status === "pending";
  const failed = job.status === "failed";
  return (
    <li className="rounded-md border border-rule bg-surface-2 p-3">
      <div className="flex items-center gap-2">
        <span aria-hidden>{KIND_ICON[job.kind] ?? null}</span>
        <span className="font-medium text-ink">{KIND_LABEL[job.kind] ?? job.kind}</span>
        <span className="text-xs text-muted">{jobTarget(job)}</span>
        <span className="ml-auto flex items-center gap-1.5 text-xs text-ink-2">
          <span
            className={
              failed
                ? "inline-block size-[5px] rounded-full bg-live shadow-[0_0_6px_var(--color-live-glow)]"
                : active
                  ? "inline-block size-[5px] rounded-full bg-led shadow-[0_0_6px_var(--color-led-glow)]"
                  : "inline-block size-[5px] rounded-full bg-surface-3"
            }
            aria-hidden
          />
          {job.status}
        </span>
      </div>
      {job.status === "running" && typeof job.progress === "number" && (
        <div className="mt-2 h-1 overflow-hidden rounded bg-surface-3" role="progressbar"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(job.progress * 100)}>
          <div className="h-full bg-led motion-safe:transition-[width]" style={{ width: `${Math.round(job.progress * 100)}%` }} />
        </div>
      )}
      {job.message && <p className="mt-1 text-xs text-muted">{job.message}</p>}
      {failed && job.error && <p className="mt-1 text-xs text-live">{job.error}</p>}
      {(onCancel || onRetry || onDismiss) && (
        <div className="mt-2 flex gap-2">
          {onCancel && active && (
            <button type="button" className="min-h-11 rounded-md border border-rule px-3 text-sm text-ink-2"
              onClick={() => onCancel(job)} disabled={job.cancel_requested}>
              {job.cancel_requested ? "Cancelling..." : "Cancel"}
            </button>
          )}
          {onRetry && failed && (
            <button type="button" className="min-h-11 rounded-md border border-rule px-3 text-sm text-ink"
              onClick={() => onRetry(job)}>
              Retry
            </button>
          )}
          {onDismiss && failed && (
            <button type="button" className="min-h-11 rounded-md px-3 text-sm text-muted"
              onClick={() => onDismiss(job)}>
              Dismiss
            </button>
          )}
        </div>
      )}
      {!active && job.timings && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-muted">Phase timings</summary>
          <TimingsList timings={job.timings} />
        </details>
      )}
    </li>
  );
}

export function Jobs() {
  const ctx = useOutletContext<MatchShellOutletContext | undefined>();
  const state = ctx?.jobsState;
  if (!state) return null;

  const active = [...state.running, ...state.pending];
  const attention = state.failed;
  const recent = state.jobs
    .filter((j) => !active.includes(j) && !attention.includes(j))
    .slice(0, 20);
  const quiet = active.length === 0 && attention.length === 0;

  return (
    <div className="mx-auto max-w-2xl px-4 py-6">
      <Kicker>Jobs</Kicker>
      {quiet && (
        <p className="mt-4 rounded-md border border-rule bg-surface-2 p-4 text-sm text-muted">
          All quiet - nothing pending.
        </p>
      )}
      {attention.length > 0 && (
        <section className="mt-4" aria-label="Needs attention">
          <h2 className="text-sm font-medium text-ink-2">Needs attention</h2>
          <ul className="mt-2 space-y-2">
            {attention.map((j) => (
              <JobCard key={j.id} job={j} onRetry={state.retry} onDismiss={state.acknowledge} />
            ))}
          </ul>
        </section>
      )}
      {active.length > 0 && (
        <section className="mt-4" aria-label="Active">
          <h2 className="text-sm font-medium text-ink-2">Active</h2>
          <ul className="mt-2 space-y-2">
            {active.map((j) => (
              <JobCard key={j.id} job={j} onCancel={state.cancel} />
            ))}
          </ul>
        </section>
      )}
      {recent.length > 0 && (
        <section className="mt-4" aria-label="Recent">
          <h2 className="text-sm font-medium text-ink-2">Recent</h2>
          <ul className="mt-2 space-y-2">
            {recent.map((j) => (
              <JobCard key={j.id} job={j} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
```

Adapt token/class details to what `components/Jobs.tsx` actually uses if any class above does not exist (verify `min-h-11`, `bg-live`, `bg-led`, `border-rule`, `bg-surface-2/3`, `text-ink/-2`, `text-muted` against that file - all were observed there except `min-h-11`, which is standard Tailwind for the 44 px target).

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm test -- Jobs.test && pnpm typecheck`
Expected: 3 tests PASS, typecheck clean.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Jobs.tsx src/splitsmith/ui_static/src/pages/Jobs.test.tsx src/splitsmith/ui_static/src/components/Jobs.tsx src/splitsmith/ui_static/src/components/match/MatchShell.tsx
git commit -m "feat(ui): mobile-first jobs page with retry and phase timings"
```

---

### Task 7: Route, nav item, badge, breadcrumb

**Files:**
- Modify: `src/splitsmith/ui_static/src/App.tsx` (match subtree, after the `results` route ~line 296)
- Modify: `src/splitsmith/ui_static/src/components/match/navItems.tsx`
- Modify: `src/splitsmith/ui_static/src/components/match/MatchShell.tsx` (`viewLabel` ~line 99; both `matchNavItems` call sites ~552 and the sidebar props ~596)
- Modify: `src/splitsmith/ui_static/src/components/match/MatchSidebar.tsx` (accept + forward the new count prop)
- Test: `src/splitsmith/ui_static/src/components/match/navItems.test.ts`

**Interfaces:**
- Consumes: `Jobs` page (Task 6), `JobsState.failed`.
- Produces: route `/match/:matchId/jobs` (NOT DesktopGated); `matchNavItems` gains required arg `jobsAttentionCount: number` and emits a `jobs` item with `count: jobsAttentionCount, badgeKind: "pending"`.

- [ ] **Step 1: Write the failing test**

Create `components/match/navItems.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { matchNavItems } from "@/components/match/navItems";

describe("matchNavItems jobs entry", () => {
  it("links to the jobs page and badges the failed count", () => {
    const items = matchNavItems({
      base: "/match/m1",
      hasFootage: true,
      beepReviewPendingCount: 0,
      jobsAttentionCount: 2,
    });
    const jobs = items.find((i) => i.key === "jobs");
    expect(jobs).toMatchObject({
      to: "/match/m1/jobs",
      label: "Jobs",
      count: 2,
      badgeKind: "pending",
    });
  });
});
```

(If `matchNavItems`'s args type requires more fields than shown, pass the minimal valid literal - check the interface at navItems.tsx:35.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm test -- navItems.test`
Expected: FAIL (no `jobs` item / missing arg).

- [ ] **Step 3: Implement**

`navItems.tsx`:
- Add `Activity` to the `lucide-react` import (line 7-16).
- Add `jobsAttentionCount: number` to the `matchNavItems` args type.
- Append a jobs item to the returned array between `beep-review` and `export`, mirroring the neighbors' icon className:

```ts
    {
      key: "jobs",
      to: `${base}/jobs`,
      icon: <Activity className="size-4" aria-hidden />,
      label: "Jobs",
      count: jobsAttentionCount,
      badgeKind: "pending",
    },
```

(If the file is `.tsx` with JSX icons like `<Volume2 ... />`, copy the exact className the beep-review item uses.)

`MatchShell.tsx`:
- `viewLabel` (~line 99): add, mirroring the beep-review line exactly:

```ts
    if (pathname.startsWith("/jobs")) return "Jobs";
```

- Pass `jobsAttentionCount: jobsState.failed.length` into the mobile-drawer `matchNavItems({...})` call (~line 552) and forward the same value to `MatchSidebar` (prop next to `beepReviewPendingCount`, ~line 596).

`MatchSidebar.tsx`: accept `jobsAttentionCount: number` as a prop and pass it into its own `matchNavItems({...})` call, exactly parallel to `beepReviewPendingCount`.

`App.tsx`:
- Import with the other page imports (lines 27-53): `import { Jobs } from "@/pages/Jobs";`
- Inside the `<Route element={<MatchShell />}>` block, after the `results` route (line 296):

```tsx
              <Route path="jobs" element={<Jobs />} />
```

No `DesktopGate`, no `ShooterScopedRoute`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm test && pnpm typecheck`
Expected: all PASS (including MatchShell tests - if `MatchShell.test.tsx` builds nav args, update its literals with `jobsAttentionCount`).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/App.tsx src/splitsmith/ui_static/src/components/match/navItems.tsx src/splitsmith/ui_static/src/components/match/navItems.test.ts src/splitsmith/ui_static/src/components/match/MatchShell.tsx src/splitsmith/ui_static/src/components/match/MatchSidebar.tsx
git commit -m "feat(ui): jobs route, nav item and failed-count badge"
```

---

### Task 8: Full gates, visual verification, PR

**Files:**
- No new files; verification and PR only.

- [ ] **Step 1: Backend gates**

Run: `uv run ruff check . && uv run black --check . && uv run pytest`
Expected: all clean/PASS. Fix anything that fails before continuing (no "pre-existing" dismissals - fix or surface).

- [ ] **Step 2: Docker smoke (schema change)**

Run: `PATH=~/.claude-tmp/bin:$PATH uv run pytest -m docker`
Expected: PASS, and confirm in the output that the docker-marked tests actually RAN (not skipped) - a silent skip means docker is not on PATH.

- [ ] **Step 3: Frontend gates**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test && pnpm lint`
Expected: all clean.

- [ ] **Step 4: Dash sweep**

Run: `git diff main... | grep -n $'—\|--' -- || true` on added lines of new copy/comments; confirm no em dashes and no double dashes in prose (code operators like `--flag` in shell snippets are fine).

- [ ] **Step 5: Visual verification at phone width**

Serve the app locally and take a bounded headless screenshot (Playwright MCP navigate hangs on live SSE - use the bounded headless recipe with `domcontentloaded`, viewport 390x844) of `/match/<id>/jobs` in three states if data allows: all-quiet, active job, failed job. Confirm: sections legible, buttons at least 44 px, badge visible in the drawer nav, no horizontal scroll.

- [ ] **Step 6: Open the PR**

```bash
git push -u origin feat/jobs-page
gh pr create --title "feat: mobile-first jobs page with retry" --body "..."
```

PR body: summarize the page, the retry vertical (migration + both backends + endpoint), the spec correction about timings-on-completion, and the verification evidence (gates + docker smoke + screenshots). Do not enable auto-merge without the user (main has no required checks - auto-merge merges immediately).
