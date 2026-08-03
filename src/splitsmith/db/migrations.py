"""Connect-with-retry for the boot-time Alembic migration.

``splitsmith serve`` runs ``alembic upgrade head`` before uvicorn binds,
so the migration's DB connect sits inside the deploy's healthcheck
window. Neon (prod) runs with ``suspend_timeout_seconds: 0``, and a
single transient asyncpg ``TimeoutError`` on that one connect used to
fail the whole deploy (#559) -- the image was fine, the migration was a
no-op, and an identical retry minutes later succeeded.

This module retries *only* the connect. Once a connection is open, a
failure is the migration's own and propagates on the first attempt: a
broken revision should fail fast and loudly, not six times slowly.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Awaitable, Callable

from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import ArgumentError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

# SQLAlchemy's asyncpg dialect does *no* error translation on connect:
# ``AsyncAdapt_asyncpg_dbapi.connect`` awaits ``asyncpg.connect`` outside
# any ``try``, and ``Connection.__init__`` only catches
# ``dialect.loaded_dbapi.Error``, which raw ``asyncpg.exceptions.*`` are
# not. So a Postgres-protocol connect failure arrives here as the
# asyncpg class itself and has to be named explicitly -- listing only
# SQLAlchemy's wrappers would silently miss every one of them.
#
# asyncpg lives in the ``hosted`` extra; a local-mode install has no
# Postgres driver (and no alembic either, so this module is unreachable
# there), hence the guard.
try:
    from asyncpg import exceptions as _asyncpg_exc  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover - local-mode install
    _ASYNCPG_TRANSIENT: tuple[type[BaseException], ...] = ()
else:
    _ASYNCPG_TRANSIENT = (
        # Class 08 -- connection exception. Neon's proxy returns 08006
        # ("couldn't connect to compute node") when it fails to wake a
        # suspended compute, which is precisely the #559 shape.
        _asyncpg_exc.PostgresConnectionError,
        # 57P03 -- "the database system is starting up".
        _asyncpg_exc.CannotConnectNowError,
        # 57P01 / 57P02 -- the server is shutting down or recovering.
        _asyncpg_exc.AdminShutdownError,
        _asyncpg_exc.CrashShutdownError,
        # 53300 -- pooler saturated; the next attempt usually gets a slot.
        _asyncpg_exc.TooManyConnectionsError,
    )

#: Failures treated as "the database wasn't reachable *yet*".
#:
#: - :class:`TimeoutError` -- what asyncpg raises when its connect
#:   timeout expires (the exact error from #559; since 3.11
#:   ``asyncio.TimeoutError`` is an alias of the builtin, and it is
#:   itself an :class:`OSError` subclass -- listed for the reader).
#: - :class:`OSError` -- ``ConnectionRefusedError``,
#:   ``ConnectionResetError``, ``socket.gaierror``, TLS errors.
#: - the asyncpg classes above -- server-side "not yet" answers.
#: - :class:`~sqlalchemy.exc.InterfaceError` /
#:   :class:`~sqlalchemy.exc.OperationalError` -- what a *non*-asyncpg
#:   driver (psycopg, aiosqlite) surfaces for the same class of failure.
#:
#: Deliberately absent: ``InvalidPasswordError``,
#: ``InvalidCatalogNameError``, ``ProgrammingError``, asyncpg's own
#: ``InterfaceError``. Those are configuration or code errors that no
#: amount of retrying fixes.
#:
#: Note the honest limit of this set: because ``OSError`` is in it, a
#: permanently wrong host, port or TLS config is also retried, and
#: spends the full budget below before failing. That is the price of
#: retrying a DNS blip in a freshly-started container, and it is paid
#: only on a deploy that was going to fail anyway.
TRANSIENT_CONNECT_ERRORS: tuple[type[BaseException], ...] = (
    TimeoutError,
    OSError,
    InterfaceError,
    OperationalError,
    *_ASYNCPG_TRANSIENT,
)

#: 6 attempts with 1/2/4/8/16s backoff.
CONNECT_ATTEMPTS = 6
CONNECT_BASE_DELAY = 1.0

#: Per-attempt connect timeout handed to asyncpg, replacing its 60s
#: default. Kept comfortably above a Neon cold resume: too low and a
#: slow-but-succeeding wake gets chopped into a failure, which would
#: make this change worse than no change for that input.
CONNECT_TIMEOUT_SECONDS = 15.0

#: Wall-clock ceiling on retrying, checked before each sleep. Attempt
#: count alone doesn't bound the time -- six attempts that each hang for
#: the full connect timeout would be 6 x 15s + 31s of sleeps. With the
#: deadline the worst case is ~75s (deadline + one in-flight attempt),
#: which fits the 130s healthcheck budget in ``docker-compose.yml`` with
#: room for image start and uvicorn boot.
CONNECT_DEADLINE_SECONDS = 60.0

#: Backoff stops doubling here, so a long deadline can't be consumed by
#: one enormous sleep.
CONNECT_MAX_DELAY = 8.0


def engine_connect_args(url: str) -> dict[str, float]:
    """``connect_args`` for the migration engine, given its URL.

    Only asyncpg gets a ``timeout``. The keyword is driver-specific:
    psycopg spells it ``connect_timeout``, and sqlite3 would accept it
    silently as the *busy-lock* timeout (default 5s) -- changing lock
    behaviour on the SQLite path rather than erroring, which is the
    quiet kind of wrong.

    Matches on the parsed driver name rather than a substring, so a
    psycopg URL pointing at a host that happens to contain "asyncpg"
    doesn't get an argument its driver can't use.
    """
    try:
        driver = make_url(url).get_driver_name()
    except (ArgumentError, ValueError):
        # Malformed URL: let engine creation raise its own better error.
        return {}
    if driver == "asyncpg":
        return {"timeout": CONNECT_TIMEOUT_SECONDS}
    return {}


async def connect_with_retry(
    engine: AsyncEngine,
    *,
    attempts: int = CONNECT_ATTEMPTS,
    base_delay: float = CONNECT_BASE_DELAY,
    max_delay: float = CONNECT_MAX_DELAY,
    deadline: float = CONNECT_DEADLINE_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], float] = time.monotonic,
) -> AsyncConnection:
    """Open a connection on ``engine``, retrying transient failures.

    Returns a *started* :class:`~sqlalchemy.ext.asyncio.AsyncConnection`
    -- the caller owns it and must close it (don't re-enter it with
    ``async with``; it is already started and would raise).

    Retries only on :data:`TRANSIENT_CONNECT_ERRORS`, backing off
    ``base_delay * 2 ** (attempt - 1)`` capped at ``max_delay``, and
    gives up at whichever of ``attempts`` or ``deadline`` seconds comes
    first. The final attempt's exception propagates unchanged, so a
    database that is genuinely down still surfaces its real error rather
    than a wrapper.

    Each retry logs to stderr: a deploy that took three tries should say
    so in the Railway log, otherwise the flake is invisible and #559
    reads as "it just works now".

    ``sleep`` and ``now`` are injectable so tests don't spend the real
    budget proving the ladder.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")

    started = now()
    delay = base_delay
    for attempt in range(1, attempts + 1):
        try:
            return await engine.connect()
        except TRANSIENT_CONNECT_ERRORS as exc:
            elapsed = now() - started
            last = attempt == attempts or elapsed + delay >= deadline
            if last:
                print(
                    f"[splitsmith] database connect failed after {attempt} attempts "
                    f"({elapsed:.1f}s): {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                raise
            print(
                f"[splitsmith] database connect attempt {attempt}/{attempts} failed "
                f"({type(exc).__name__}: {exc}); retrying in {delay:g}s",
                file=sys.stderr,
                flush=True,
            )
            await sleep(delay)
            delay = min(delay * 2, max_delay)

    raise AssertionError("unreachable: loop returns or raises on every path")
