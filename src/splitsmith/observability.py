"""Per-job observability primitives: phase timing, structured logging, Sentry.

Why this is a top-level module
------------------------------
It is imported by both the web (``ui.server``) and the worker (``queue``)
and by the job backends. To avoid an import cycle it depends ONLY on the
standard library plus pydantic, and imports NOTHING from ``splitsmith.ui.*``
or ``splitsmith.db.*``. ``server.py`` and ``job_backend.py`` import FROM
it; it imports from nobody in-package.

It is also mode-agnostic. :func:`emit_job_event` always logs one INFO
record carrying structured extras; whether that record is serialised as a
single JSON line or a plain text line is decided by which formatter the
logging config selected (:class:`StructuredJsonFormatter` in hosted mode,
the plain formatter locally). This keeps mode detection out of this module
and out of any ``server.py`` import.

The pieces
----------
- :class:`PhaseTimer` -- pure, no I/O: ``perf_counter`` phase contextmanager
  that records each closed phase EVEN IF its body raises, plus a small
  ``meta`` dict and a ``build()`` that returns the persisted timings shape.
- :func:`_queue_wait_ms` -- derive enqueue->start latency from the row's
  ``created_at`` / ``started_at`` wall-clock timestamps (perf_counter has no
  shared epoch across enqueue and run, especially across processes).
- :class:`StructuredJsonFormatter` -- one JSON line per record; folds in the
  observability extras when present, never absolute paths or other tenants.
- :func:`emit_job_event` -- emit exactly one ``job.completed`` /
  ``job.failed`` record from a timings dict + job metadata.
- :func:`init_sentry` -- no-op when ``SENTRY_DSN`` is unset; PII-scrubbed
  init otherwise.

Timings contract (persisted to ``compute_jobs.timings`` and emitted verbatim
in the job event)::

    {
      "queue_wait_ms": float | null,  # (started - created) ms, null if unknown
      "total_ms": float,              # whole-body perf_counter delta, always present
      "phases": [ {"name": str, "ms": float}, ... ],  # closed phases, in order
      "meta": { ...small scalars... } # job-specific: input_bytes, encoder, ...
      "worker": { ... }               # hosted backend only, see WorkerProbe
    }

``worker`` is the run's machine, not the body's: which worker ran the job
(:func:`worker_name`), its container limits, the process uptime when the
job started (on a one-shot drain's first job that is the startup cost:
image, imports, model warmup), the storage bytes moved and the memory
high-water marks. The byte counts and ``peak_rss_mb`` are process-wide, so
they belong to this job only when ``ran_alone`` is true.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Job-event field names that ride on a LogRecord as ``extra=`` and that the
# JSON formatter folds into its output. Centralised so the formatter and the
# emitter cannot drift.
_EVENT_EXTRA_FIELDS = ("event", "job_kind", "user_id", "status", "timings", "job_error")


def _queue_wait_ms(created_at: datetime | None, started_at: datetime | None) -> float | None:
    """Enqueue->start latency in milliseconds, or ``None`` when unknowable.

    Returns ``None`` when either timestamp is missing or when ``started_at``
    precedes ``created_at`` (a clock-skew guard: a negative queue wait is
    never meaningful, so we report unknown rather than a bogus negative).
    """
    if created_at is None or started_at is None:
        return None
    # Production rows are tz-aware (Postgres TIMESTAMPTZ); SQLite (unit tests)
    # round-trips naive datetimes. Normalise naive values to UTC so the
    # subtraction never raises on a mixed-awareness pair.
    created = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)
    started = started_at if started_at.tzinfo is not None else started_at.replace(tzinfo=UTC)
    delta = (started - created).total_seconds()
    if delta < 0:
        return None
    return delta * 1000.0


class PhaseTimer:
    """Records per-phase and whole-body durations for one job run.

    Pure: no file I/O, no DB, no logging. The backend ``_run`` owns the
    instance (built from the row's ``created_at`` / ``started_at``), the body
    opens phases through it, and the backend persists ``build()`` once and
    emits the event once. The body never persists or logs the timings itself.
    """

    def __init__(
        self,
        *,
        created_at: datetime | None = None,
        started_at: datetime | None = None,
    ) -> None:
        # Wall-clock row timestamps drive queue_wait_ms (perf_counter cannot:
        # it has no epoch shared across enqueue and run, which in hosted mode
        # happen in different processes).
        self._created_at = created_at
        self._started_at = started_at
        # perf_counter origin for total_ms.
        self._t0 = time.perf_counter()
        self._phases: list[dict[str, Any]] = []
        self._meta: dict[str, Any] = {}
        self._worker: dict[str, Any] | None = None
        self._open: str | None = None

    @contextlib.contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Time a phase, recording its duration EVEN IF the body raises.

        The recorded ``{"name", "ms"}`` is appended in a ``finally`` so a
        crash mid-phase still leaves the phases that ran (including this one)
        in ``build()``'s output -- that is what lets ``job.failed`` carry the
        partial timeline. Nesting is allowed but discouraged; each phase
        records independently. The original exception propagates unchanged.
        """
        prev_open = self._open
        self._open = name
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._phases.append({"name": name, "ms": elapsed_ms})
            self._open = prev_open

    def set_meta(self, **kv: object) -> None:
        """Merge small scalars into the per-job ``meta`` dict."""
        self._meta.update(kv)

    def add_meta(self, key: str, value: object) -> None:
        """Set a single ``meta`` scalar."""
        self._meta[key] = value

    def set_worker(self, facts: dict[str, Any]) -> None:
        """Attach the run's :class:`WorkerProbe` facts (backend only)."""
        self._worker = dict(facts)

    def build(self) -> dict[str, Any]:
        """Return the timings dict. ``total_ms`` is computed at call time.

        Safe to call multiple times (e.g. once on the failure path and once
        on success); each call recomputes ``total_ms`` from the perf_counter
        origin and re-derives ``queue_wait_ms`` from the row timestamps.
        """
        total_ms = (time.perf_counter() - self._t0) * 1000.0
        built: dict[str, Any] = {
            "queue_wait_ms": _queue_wait_ms(self._created_at, self._started_at),
            "total_ms": total_ms,
            "phases": list(self._phases),
            "meta": dict(self._meta),
        }
        if self._worker is not None:
            built["worker"] = dict(self._worker)
        return built


# --- Worker facts ---------------------------------------------------------

_CGROUP_ROOT = Path("/sys/fs/cgroup")
# perf origin for the uptime fallback where /proc is absent (macOS tests).
_IMPORT_MONOTONIC = time.monotonic()
_WORKER_NAME: str | None = None


def set_worker_name(name: str) -> None:
    """Name this process's worker (the agent sets ``agent:<worker_id>``)."""
    global _WORKER_NAME
    _WORKER_NAME = name


def worker_name() -> str:
    """The explicit name, else the Cloud Run execution and task, else the host."""
    if _WORKER_NAME is not None:
        return _WORKER_NAME
    execution = os.environ.get("CLOUD_RUN_EXECUTION")
    if execution:
        return f"cloudrun:{execution}#{os.environ.get('CLOUD_RUN_TASK_INDEX', '0')}"
    return f"host:{socket.gethostname()}"


#: The worker's size as deployed. Cloud Run exposes neither cgroup v2 limits
#: nor any env var for a job's --cpu / --memory (its sandbox reports the
#: host's cores), so the deploy states them next to those flags.
WORKER_VCPU_ENV = "SPLITSMITH_WORKER_VCPU"
WORKER_MEMORY_MB_ENV = "SPLITSMITH_WORKER_MEMORY_MB"
# cgroup v1 reports "no limit" as a huge page-aligned number, not "max".
_CGROUP_V1_UNLIMITED = 1 << 60


def _read_ints(path: Path) -> list[int] | None:
    try:
        return [int(v) for v in path.read_text(encoding="utf-8").split() if v != "max"] or None
    except (OSError, ValueError):
        return None


def container_limits(cgroup_root: Path = _CGROUP_ROOT) -> dict[str, Any]:
    """vCPU and memory limits: the deploy's env, else cgroup v2, else v1.

    The vCPU falls back to ``os.cpu_count()`` (the host's cores, not a
    quota) and an unknown memory is ``None``.
    """
    vcpu: float | None = None
    memory_mb: int | None = None
    try:
        vcpu = float(os.environ[WORKER_VCPU_ENV])
    except (KeyError, ValueError):
        pass
    try:
        memory_mb = int(os.environ[WORKER_MEMORY_MB_ENV])
    except (KeyError, ValueError):
        pass
    if vcpu is None:
        v2 = (cgroup_root / "cpu.max").read_text().split() if (cgroup_root / "cpu.max").is_file() else []
        if len(v2) == 2 and v2[0] != "max":
            vcpu = int(v2[0]) / int(v2[1])
        else:
            quota = _read_ints(cgroup_root / "cpu" / "cpu.cfs_quota_us")
            period = _read_ints(cgroup_root / "cpu" / "cpu.cfs_period_us")
            if quota and period and quota[0] > 0:
                vcpu = quota[0] / period[0]
    if memory_mb is None:
        raw = _read_ints(cgroup_root / "memory.max") or _read_ints(
            cgroup_root / "memory" / "memory.limit_in_bytes"
        )
        if raw and raw[0] < _CGROUP_V1_UNLIMITED:
            memory_mb = raw[0] // (1024 * 1024)
    if vcpu is None:
        cores = os.cpu_count()
        vcpu = float(cores) if cores else None
    return {"vcpu": vcpu, "memory_mb": memory_mb}


def _process_uptime_s() -> float:
    """Seconds since this process started (/proc), else since this import."""
    try:
        fields = Path("/proc/self/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        start_ticks = int(fields[19])  # field 22 overall; 2 consumed by the split
        boot_uptime = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
        return max(0.0, boot_uptime - start_ticks / os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError, AttributeError):
        return time.monotonic() - _IMPORT_MONOTONIC


def _peak_rss_mb() -> tuple[float | None, float | None]:
    """Process and largest-child (ffmpeg) memory high-water marks, MB."""
    try:
        import resource
        import sys
    except ImportError:  # Windows
        return None, None
    # ru_maxrss is KiB on Linux and bytes on macOS.
    scale = 1024 * 1024 if sys.platform == "darwin" else 1024
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale
    child = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / scale
    return round(own, 1), round(child, 1)


class StorageIO:
    """Process-wide counters of bytes moved through object storage."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._read = 0
        self._written = 0

    def add_read(self, n: int) -> None:
        with self._lock:
            self._read += n

    def add_written(self, n: int) -> None:
        with self._lock:
            self._written += n

    def snapshot(self) -> tuple[int, int]:
        with self._lock:
            return self._read, self._written


STORAGE_IO = StorageIO()


class _RunningJobs:
    """How many jobs this process runs now, and how many it has started."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.started = 0

    def begin(self) -> tuple[int, int]:
        with self._lock:
            self.active += 1
            self.started += 1
            return self.active, self.started

    def end(self) -> int:
        with self._lock:
            self.active -= 1
            return self.started


_RUNNING = _RunningJobs()


def jobs_running() -> int:
    """How many jobs this process is running right now."""
    return _RUNNING.active


class WorkerProbe:
    """Samples the worker at a job's start and reports the facts at its end.

    A job ran alone when nothing else was running as it started and nothing
    else started before it finished; only then are the process-wide byte
    counts and memory marks this job's own.
    """

    def __init__(self) -> None:
        self._facts: dict[str, Any] = {}
        self._read0 = self._written0 = 0
        self._active_at_start = 0
        self._started_at_start = 0

    @classmethod
    def start(cls) -> WorkerProbe:
        probe = cls()
        probe._active_at_start, probe._started_at_start = _RUNNING.begin()
        probe._read0, probe._written0 = STORAGE_IO.snapshot()
        probe._facts = {
            "name": worker_name(),
            **container_limits(),
            "uptime_at_start_ms": round(_process_uptime_s() * 1000.0, 1),
        }
        return probe

    def finish(self) -> dict[str, Any]:
        started_at_end = _RUNNING.end()
        read, written = STORAGE_IO.snapshot()
        own_rss, child_rss = _peak_rss_mb()
        return {
            **self._facts,
            "storage_read_bytes": read - self._read0,
            "storage_written_bytes": written - self._written0,
            "peak_rss_mb": own_rss,
            "peak_child_rss_mb": child_rss,
            "ran_alone": self._active_at_start == 1 and started_at_end == self._started_at_start,
        }


class StructuredJsonFormatter(logging.Formatter):
    """Render each record as a single JSON line.

    Always emits ``ts`` / ``level`` / ``logger`` / ``message``. When a record
    carries the observability extras (``event`` etc., set by
    :func:`emit_job_event`), those are folded in. Records WITHOUT the extras
    (ordinary log lines) still produce a valid JSON line -- the hosted stdout
    stream stays uniformly JSON for log capture.

    Safety: this never reaches into the record for absolute paths or other
    tenants' identifiers. The only tenant id it emits is ``user_id`` (the
    owning tenant, which the spec marks safe to log); callers are responsible
    for not putting paths or foreign ids into the extras in the first place.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            # Cloud Logging reads a JSON line's severity from "severity" and
            # its text from "message"; without "severity" every line lands
            # as DEFAULT and level filters in the console match nothing.
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            for field in _EVENT_EXTRA_FIELDS:
                if hasattr(record, field):
                    value = getattr(record, field)
                    if field == "job_error" and value is None:
                        continue
                    payload[field] = value
        # Without this a logger.exception() line carries only its message.
        # Error Reporting groups errors from a "stack_trace" field.
        if record.exc_info:
            payload["stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, default=str)


def emit_job_event(
    logger: logging.Logger,
    *,
    event: str,
    kind: str,
    user_id: str,
    status: str,
    timings: dict[str, Any],
    error: str | None = None,
) -> None:
    """Emit exactly one structured job-lifecycle log record.

    ``event`` is ``"job.completed"`` or ``"job.failed"``. The record carries
    the observability extras the :class:`StructuredJsonFormatter` serialises
    in hosted mode; in local mode the same record renders as a plain INFO
    line. The caller emits this once per job from the backend's terminal
    path, so partial timings from a raised body are still logged.

    Never pass absolute paths or another tenant's id in any argument:
    ``user_id`` is the owning tenant (safe), and ``error`` should be a
    path-free ``str(exc)``.
    """
    extra: dict[str, Any] = {
        "event": event,
        "job_kind": kind,
        "user_id": user_id,
        "status": status,
        "timings": timings,
    }
    if error is not None:
        extra["job_error"] = error
    logger.info("%s kind=%s status=%s", event, kind, status, extra=extra)


def init_sentry(*, component: str) -> None:
    """Initialise Sentry for ``component`` (``"web"`` / ``"worker"``).

    No-op when ``SENTRY_DSN`` is unset or empty, and a no-op when the
    ``sentry_sdk`` wheel is absent (slim local installs do not ship it).
    Integrations are imported defensively: a missing or renamed integration
    is skipped, never fatal, so the hosted ``uv lock`` stays unpinned within
    its group.

    PII is scrubbed: ``send_default_pii=False`` plus a ``before_send`` hook
    that drops request cookies, the ``Authorization`` header, and the
    magic-link token query parameter before an event leaves the process.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return

    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - wheel absent in slim local installs
        return

    environment = os.environ.get("SPLITSMITH_ENV") or os.environ.get("SPLITSMITH_MODE") or "unknown"

    try:
        traces_sample_rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0"))
    except ValueError:
        traces_sample_rate = 0.0

    integrations: list[Any] = []
    try:
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        integrations.append(FastApiIntegration())
    except Exception:  # noqa: BLE001 - missing/renamed integration is skipped, not fatal
        pass
    try:
        from sentry_sdk.integrations.logging import LoggingIntegration

        integrations.append(LoggingIntegration(event_level=logging.ERROR))
    except Exception:  # noqa: BLE001
        pass
    try:
        from sentry_sdk.integrations.asyncpg import AsyncPGIntegration

        integrations.append(AsyncPGIntegration())
    except Exception:  # noqa: BLE001
        pass

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        send_default_pii=False,
        traces_sample_rate=traces_sample_rate,
        integrations=integrations,
        before_send=_scrub_event,
    )
    sentry_sdk.set_tag("component", component)


def capture_job_exception(exc: BaseException, *, kind: str, user_id: str) -> None:
    """Report a failed worker job to Sentry, tagged with its kind + owner.

    The worker converts a job-body exception into a ``job.failed`` INFO log
    (not ERROR), so the ``LoggingIntegration`` event floor never picks it up;
    this is the explicit hook that surfaces worker crashes in Sentry the way
    the FastAPI integration already surfaces web errors. No-op when the
    ``sentry_sdk`` wheel is absent (slim installs) or Sentry was never
    initialised (no ``SENTRY_DSN``): ``capture_exception`` is itself a no-op
    against the disabled default hub.
    """
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - wheel absent in slim local installs
        return
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("job_kind", kind)
        scope.set_tag("user_id", user_id)
        sentry_sdk.capture_exception(exc)


def _scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Strip cookies, the Authorization header, and the magic-link token.

    Belt-and-braces on top of ``send_default_pii=False``: we never want a
    session cookie, bearer token, or sign-in token landing in Sentry.
    """
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if key.lower() == "authorization":
                    headers.pop(key)
        query_string = request.get("query_string")
        if isinstance(query_string, str) and "token=" in query_string:
            request["query_string"] = _drop_token_param(query_string)
    return event


def _drop_token_param(query_string: str) -> str:
    """Remove a ``token=...`` pair from a raw query string."""
    parts = [p for p in query_string.split("&") if not p.startswith("token=")]
    return "&".join(parts)
