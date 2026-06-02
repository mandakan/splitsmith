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
    }
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
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

    def build(self) -> dict[str, Any]:
        """Return the timings dict. ``total_ms`` is computed at call time.

        Safe to call multiple times (e.g. once on the failure path and once
        on success); each call recomputes ``total_ms`` from the perf_counter
        origin and re-derives ``queue_wait_ms`` from the row timestamps.
        """
        total_ms = (time.perf_counter() - self._t0) * 1000.0
        return {
            "queue_wait_ms": _queue_wait_ms(self._created_at, self._started_at),
            "total_ms": total_ms,
            "phases": list(self._phases),
            "meta": dict(self._meta),
        }


class StructuredJsonFormatter(logging.Formatter):
    """Render each record as a single JSON line.

    Always emits ``ts`` / ``level`` / ``logger`` / ``msg``. When a record
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
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "event"):
            for field in _EVENT_EXTRA_FIELDS:
                if hasattr(record, field):
                    value = getattr(record, field)
                    if field == "job_error" and value is None:
                        continue
                    payload[field] = value
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
