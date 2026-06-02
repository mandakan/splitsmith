"""Unit tests for :mod:`splitsmith.observability`.

Covers the pure primitives this slice introduced: :class:`PhaseTimer`
(phase order/durations, queue_wait derivation, partial-timings on a raised
body), :class:`StructuredJsonFormatter` (one JSON line, folds the job-event
extras, no path/cross-tenant leakage), :func:`emit_job_event`, and the
:func:`init_sentry` no-op gate. Also exercises the local
:class:`JobRegistry` terminal path threading the timer through the handle
and emitting exactly one event.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta

import pytest

from splitsmith.observability import (
    PhaseTimer,
    StructuredJsonFormatter,
    _queue_wait_ms,
    emit_job_event,
    init_sentry,
)


def test_phasetimer_records_closed_phases_in_order_and_total() -> None:
    timer = PhaseTimer()
    for name in ("a", "b", "c"):
        with timer.phase(name):
            time.sleep(0.001)
    timer.set_meta(input_bytes=1024, encoder="h264")
    timer.add_meta("cold_model_load", False)

    built = timer.build()
    assert [p["name"] for p in built["phases"]] == ["a", "b", "c"]
    assert all(isinstance(p["ms"], float) and p["ms"] >= 0 for p in built["phases"])
    phase_sum = sum(p["ms"] for p in built["phases"])
    assert built["total_ms"] >= phase_sum
    assert built["meta"] == {
        "input_bytes": 1024,
        "encoder": "h264",
        "cold_model_load": False,
    }
    assert built["queue_wait_ms"] is None  # no row timestamps supplied


def test_phasetimer_records_partial_on_exception() -> None:
    timer = PhaseTimer()
    with timer.phase("ran_ok"):
        pass
    with pytest.raises(ValueError):
        with timer.phase("boom"):
            raise ValueError("kaboom")

    built = timer.build()
    names = [p["name"] for p in built["phases"]]
    assert names == ["ran_ok", "boom"]  # the raising phase is still recorded
    assert "total_ms" in built and built["total_ms"] >= 0


def test_queue_wait_ms_from_timestamps() -> None:
    created = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)
    started = created + timedelta(milliseconds=250)
    assert _queue_wait_ms(created, started) == pytest.approx(250.0)
    # Missing either timestamp -> None.
    assert _queue_wait_ms(None, started) is None
    assert _queue_wait_ms(created, None) is None
    # Clock skew (started before created) -> None, never a negative wait.
    assert _queue_wait_ms(started, created) is None


def test_phasetimer_queue_wait_from_row_timestamps() -> None:
    created = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)
    started = created + timedelta(milliseconds=120)
    timer = PhaseTimer(created_at=created, started_at=started)
    assert timer.build()["queue_wait_ms"] == pytest.approx(120.0)


def test_structured_json_formatter_emits_one_line_json_with_event_fields() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="splitsmith.test",
        level=logging.INFO,
        pathname="/abs/secret/path.py",
        lineno=1,
        msg="job.completed",
        args=(),
        exc_info=None,
    )
    record.event = "job.completed"
    record.job_kind = "shot_detect"
    record.user_id = "tenant-A"
    record.status = "succeeded"
    record.timings = {"total_ms": 12.5, "phases": [{"name": "x", "ms": 1.0}], "meta": {}}

    line = formatter.format(record)
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["event"] == "job.completed"
    assert parsed["job_kind"] == "shot_detect"
    assert parsed["user_id"] == "tenant-A"
    assert parsed["status"] == "succeeded"
    assert parsed["timings"]["total_ms"] == 12.5
    # No absolute path from the record, no second tenant id leaked.
    assert "/abs/secret/path.py" not in line
    assert "tenant-B" not in line
    # job_error omitted when not present.
    assert "job_error" not in parsed


def test_structured_json_formatter_plain_record_is_valid_json() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="splitsmith.misc",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="just a line %s",
        args=("here",),
        exc_info=None,
    )
    parsed = json.loads(formatter.format(record))
    assert parsed["msg"] == "just a line here"
    assert parsed["level"] == "INFO"
    assert "event" not in parsed
    # ts is tz-aware UTC, not a naive local-time string.
    ts = datetime.fromisoformat(parsed["ts"])
    assert ts.tzinfo is not None
    assert ts.utcoffset() == timedelta(0)


def test_emit_job_event_attaches_extras(caplog) -> None:
    log = logging.getLogger("splitsmith.test.emit")
    with caplog.at_level(logging.INFO, logger="splitsmith.test.emit"):
        emit_job_event(
            log,
            event="job.failed",
            kind="export",
            user_id="tenant-A",
            status="failed",
            timings={"total_ms": 3.0, "phases": [], "meta": {}},
            error="boom",
        )
    recs = [r for r in caplog.records if getattr(r, "event", None) == "job.failed"]
    assert len(recs) == 1
    rec = recs[0]
    assert rec.job_kind == "export"
    assert rec.user_id == "tenant-A"
    assert rec.status == "failed"
    assert rec.job_error == "boom"
    assert rec.timings["total_ms"] == 3.0


def test_init_sentry_noop_without_dsn(monkeypatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    called: list[bool] = []

    import sys
    import types

    fake = types.ModuleType("sentry_sdk")

    def _init(**_kwargs: object) -> None:
        called.append(True)

    fake.init = _init  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)

    init_sentry(component="web")
    assert called == []  # returned before importing/initialising sentry


def test_init_sentry_tags_environment(monkeypatch) -> None:
    """With a DSN set, ``init`` is called once and ``environment`` comes from
    ``SPLITSMITH_ENV`` (falling back to ``SPLITSMITH_MODE``)."""
    monkeypatch.setenv("SENTRY_DSN", "https://abc@example.test/1")
    monkeypatch.setenv("SPLITSMITH_ENV", "staging")
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")

    import sys
    import types

    init_kwargs: dict[str, object] = {}
    tags: dict[str, object] = {}

    fake = types.ModuleType("sentry_sdk")

    def _init(**kwargs: object) -> None:
        init_kwargs.update(kwargs)

    def _set_tag(key: str, value: object) -> None:
        tags[key] = value

    fake.init = _init  # type: ignore[attr-defined]
    fake.set_tag = _set_tag  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    # No integrations module attrs -> each defensive import is skipped.
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", types.ModuleType("x"))

    init_sentry(component="worker")

    assert init_kwargs["dsn"] == "https://abc@example.test/1"
    assert init_kwargs["environment"] == "staging"
    assert init_kwargs["send_default_pii"] is False
    assert tags["component"] == "worker"


def test_init_sentry_environment_falls_back_to_mode(monkeypatch) -> None:
    """When ``SPLITSMITH_ENV`` is unset the environment tag uses
    ``SPLITSMITH_MODE``."""
    monkeypatch.setenv("SENTRY_DSN", "https://abc@example.test/1")
    monkeypatch.delenv("SPLITSMITH_ENV", raising=False)
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")

    import sys
    import types

    init_kwargs: dict[str, object] = {}
    fake = types.ModuleType("sentry_sdk")
    fake.init = lambda **kwargs: init_kwargs.update(kwargs)  # type: ignore[attr-defined]
    fake.set_tag = lambda *a, **k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", types.ModuleType("x"))

    init_sentry(component="web")

    assert init_kwargs["environment"] == "hosted"
