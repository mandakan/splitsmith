"""Boot-time migration connect retry (#559).

The prod failure this guards: one transient asyncpg ``TimeoutError`` on
the startup ``alembic upgrade head`` connect used to fail the entire
deploy, with no code or migration change involved.

Async test style: ``asyncio.run`` inside sync functions, matching
``test_db_foundation.py``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic.config import Config
from asyncpg import exceptions as asyncpg_exc
from sqlalchemy.exc import InterfaceError, ProgrammingError

from alembic import command
from splitsmith.db import migrations
from splitsmith.db.migrations import (
    TRANSIENT_CONNECT_ERRORS,
    connect_with_retry,
    engine_connect_args,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@contextmanager
def _isolated_logging() -> Iterator[None]:
    """Contain ``alembic/env.py``'s ``fileConfig`` side effect.

    Running alembic in-process re-runs ``env.py``, whose
    ``fileConfig(config.config_file_name)`` defaults to
    ``disable_existing_loggers=True``: it flips ``disabled`` on every
    logger created so far and swaps the root handlers for
    ``alembic.ini``'s. Any later test asserting on log records (via
    ``caplog``) then sees nothing. Production keeps that behaviour --
    this snapshots and restores it around the call instead.
    """
    root = logging.root
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_disabled = {
        name: logger.disabled
        for name, logger in root.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        for name, disabled in saved_disabled.items():
            logger = root.manager.loggerDict.get(name)
            if isinstance(logger, logging.Logger):
                logger.disabled = disabled


class _FakeConnection:
    """Stand-in for the started AsyncConnection ``engine.connect()`` returns."""


class _FakeEngine:
    """Engine whose ``connect()`` raises the queued errors, then succeeds."""

    def __init__(self, errors: list[BaseException]) -> None:
        self._errors = list(errors)
        self.connection = _FakeConnection()
        self.calls = 0

    async def connect(self) -> _FakeConnection:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return self.connection


class _RecordingSleep:
    """``asyncio.sleep`` replacement that records delays instead of waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class _FakeClock:
    """Monotonic clock that advances ``step`` seconds per reading.

    Models attempts that hang for the full connect timeout, without the
    test waiting for any of it.
    """

    def __init__(self, step: float) -> None:
        self.step = step
        self.t = -step  # first reading (the start stamp) is 0.0

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def _interface_error() -> InterfaceError:
    return InterfaceError("connect", None, Exception("connection was closed"))


def _programming_error() -> ProgrammingError:
    return ProgrammingError("SELECT 1", None, Exception('relation "nope" does not exist'))


def test_first_attempt_success_does_not_sleep() -> None:
    engine = _FakeEngine([])
    sleep = _RecordingSleep()

    conn = asyncio.run(connect_with_retry(engine, sleep=sleep))

    assert conn is engine.connection
    assert engine.calls == 1
    assert sleep.delays == []


def test_retries_transient_timeout_then_succeeds() -> None:
    """The #559 failure exactly: asyncpg raises TimeoutError on connect."""
    engine = _FakeEngine([TimeoutError("connect timeout"), TimeoutError("connect timeout")])
    sleep = _RecordingSleep()

    conn = asyncio.run(connect_with_retry(engine, sleep=sleep))

    assert conn is engine.connection
    assert engine.calls == 3
    assert sleep.delays == [1.0, 2.0]


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("connect timeout"),
        ConnectionRefusedError("refused"),
        OSError("temporary failure in name resolution"),
        _interface_error(),
        # The asyncpg classes below reach us *un-wrapped*: SQLAlchemy's
        # asyncpg dialect does no error translation on connect, so
        # naming only its own exception types would miss every
        # Postgres-protocol failure -- including the Neon proxy's 08006,
        # which is the #559 shape.
        asyncpg_exc.ConnectionFailureError("couldn't connect to compute node"),
        asyncpg_exc.CannotConnectNowError("the database system is starting up"),
        asyncpg_exc.AdminShutdownError("terminating connection due to administrator command"),
        asyncpg_exc.TooManyConnectionsError("too many clients already"),
    ],
    ids=["timeout", "refused", "dns", "interface", "pg-08006", "pg-57P03", "pg-57P01", "pg-53300"],
)
def test_every_transient_class_is_retried(error: BaseException) -> None:
    engine = _FakeEngine([error])
    sleep = _RecordingSleep()

    asyncio.run(connect_with_retry(engine, sleep=sleep))

    assert engine.calls == 2
    assert sleep.delays == [1.0]


def test_exhausting_attempts_reraises_the_last_error() -> None:
    errors: list[BaseException] = [TimeoutError(f"attempt {n}") for n in range(1, 7)]
    engine = _FakeEngine(errors)
    sleep = _RecordingSleep()

    with pytest.raises(TimeoutError, match="attempt 6"):
        asyncio.run(connect_with_retry(engine, sleep=sleep))

    assert engine.calls == 6
    # Doubling stops at CONNECT_MAX_DELAY so one sleep can't eat the deadline.
    assert sleep.delays == [1.0, 2.0, 4.0, 8.0, 8.0]


def test_deadline_stops_retrying_before_the_attempt_count_does() -> None:
    """Six attempts that each hang for the connect timeout would run
    6 x 15s + backoff. The wall-clock deadline is what actually bounds
    the boot, so it has to win over ``attempts``."""
    engine = _FakeEngine([TimeoutError("hung") for _ in range(6)])
    sleep = _RecordingSleep()
    clock = _FakeClock(step=15.0)  # every attempt burns the connect timeout

    with pytest.raises(TimeoutError):
        asyncio.run(connect_with_retry(engine, sleep=sleep, now=clock))

    # 0s -> 15s -> 30s -> 45s: the next sleep would cross the 60s
    # deadline, so it gives up at attempt 4 of 6.
    assert engine.calls == 4
    assert sleep.delays == [1.0, 2.0, 4.0]


@pytest.mark.parametrize(
    "error",
    [
        _programming_error(),
        # A wrong password or database name is a config error; retrying
        # it just burns the healthcheck window.
        asyncpg_exc.InvalidPasswordError("password authentication failed"),
        asyncpg_exc.InvalidCatalogNameError('database "typo" does not exist'),
    ],
    ids=["programming", "bad-password", "bad-database"],
)
def test_non_transient_error_fails_on_the_first_attempt(error: BaseException) -> None:
    """A broken migration or a bad config must fail fast, not burn the
    healthcheck window."""
    engine = _FakeEngine([error])
    sleep = _RecordingSleep()

    with pytest.raises(type(error)):
        asyncio.run(connect_with_retry(engine, sleep=sleep))

    assert engine.calls == 1
    assert sleep.delays == []


def test_attempts_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        asyncio.run(connect_with_retry(_FakeEngine([]), attempts=0))


def test_asyncpg_url_gets_an_explicit_connect_timeout() -> None:
    assert engine_connect_args("postgresql+asyncpg://u:p@host/db") == {"timeout": 15.0}


@pytest.mark.parametrize(
    "url",
    [
        "sqlite+aiosqlite:///tmp/smoke.sqlite",
        # Substring matching would hand psycopg a kwarg it doesn't take
        # (it spells the option ``connect_timeout``), and sqlite3 would
        # accept ``timeout`` silently as its busy-lock timeout.
        "postgresql+psycopg://u:p@asyncpg-proxy.internal/db",
        "sqlite+aiosqlite:////data/asyncpg-scratch.sqlite",
    ],
    ids=["sqlite", "psycopg-host-named-asyncpg", "sqlite-path-named-asyncpg"],
)
def test_non_asyncpg_urls_get_no_connect_args(url: str) -> None:
    assert engine_connect_args(url) == {}


def test_malformed_url_returns_no_connect_args() -> None:
    """Let engine creation raise its own error rather than a parse error here."""
    assert engine_connect_args("not a url at all") == {}


def test_programming_error_is_not_in_the_transient_set() -> None:
    """Guards the tuple itself: OperationalError is transient, its sibling isn't."""
    assert not isinstance(_programming_error(), TRANSIENT_CONNECT_ERRORS)


def test_retry_logs_each_attempt_to_stderr(capsys) -> None:
    """The flake has to be visible in the Railway deploy log.

    Without this, both ``print`` calls can be deleted and every other
    test still passes -- and a deploy that survived on the third try
    looks identical to one that connected first time.
    """
    engine = _FakeEngine([TimeoutError("connect timeout")])

    asyncio.run(connect_with_retry(engine, sleep=_RecordingSleep()))

    err = capsys.readouterr().err
    assert "attempt 1/6 failed" in err
    assert "TimeoutError: connect timeout" in err
    assert "retrying in 1s" in err


def test_exhaustion_logs_the_final_failure(capsys) -> None:
    engine = _FakeEngine([TimeoutError("nope") for _ in range(6)])

    with pytest.raises(TimeoutError):
        asyncio.run(connect_with_retry(engine, sleep=_RecordingSleep()))

    assert "failed after 6 attempts" in capsys.readouterr().err


def test_alembic_upgrade_routes_its_connect_through_the_retry(tmp_path, monkeypatch) -> None:
    """The wiring itself, not just the helper.

    Every other test in this module exercises ``connect_with_retry``
    directly, so all of them pass just as happily against an
    ``alembic/env.py`` that never calls it -- which is the state that
    shipped the #559 outage. This test drives the real
    ``alembic upgrade head`` (in-process, sqlite, no docker) and asserts
    the connect went through the retry and that the *live* URL reached
    :func:`engine_connect_args` -- if ``sqlalchemy.url`` ever resolves
    empty, asyncpg silently loses its connect timeout.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wired.sqlite'}"
    connects: list[object] = []
    urls: list[str] = []
    real_connect = migrations.connect_with_retry
    real_args = migrations.engine_connect_args

    async def spy_connect(engine, **kwargs):
        connects.append(engine)
        return await real_connect(engine, **kwargs)

    def spy_args(url: str) -> dict[str, float]:
        urls.append(url)
        return real_args(url)

    monkeypatch.setattr(migrations, "connect_with_retry", spy_connect)
    monkeypatch.setattr(migrations, "engine_connect_args", spy_args)
    monkeypatch.setenv("SPLITSMITH_DATABASE_URL", db_url)
    monkeypatch.chdir(REPO_ROOT)

    with _isolated_logging():
        command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")

    assert connects, "alembic/env.py did not route its connect through connect_with_retry"
    assert urls == [db_url], f"engine_connect_args saw {urls!r}, not the live URL"
