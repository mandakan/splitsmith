"""Docker-compose proof for the device flow (#719).

Reuses ``test_hosted_docker_smoke.py``'s ``hosted_stack`` fixture (docker
compose up/down) plus its ``_psql`` helpers - the same idiom every other
``@pytest.mark.docker`` test in this repo uses.

Things the in-process SQLite suite cannot prove:

1. The migration applies cleanly on live Postgres: ``device_authorizations``
   exists with its two unique constraints, and every pre-existing
   ``desktop_tokens`` row backfilled to ``scope='full'`` - the property
   that keeps installs in the field working.
2. ``device_authorizations`` carries NO RLS policy, deliberately (the poll
   resolves pre-tenant, exactly like ``desktop_tokens``). Asserted against
   ``pg_policies`` under the non-superuser ``splitsmith_app`` role the
   production API actually runs as.
3. The mint-at-poll-time conditional update holds under genuine
   concurrency. SQLite serializes writes, so the in-process version of
   this test proves only the statement shape; this one runs two polls in
   parallel against real Postgres and asserts exactly one token row --
   and that the loser actually reports ``expired`` rather than merely
   leaving one token behind (a loser that raised a lock error instead of
   losing cleanly would otherwise pass unnoticed).
4. The same conditional-update guard on ``decide``: two concurrent
   decisions on one ``user_code`` (one approve, one deny) must not let
   the second writer's commit silently overwrite the first's under READ
   COMMITTED.

Both concurrency tests build their engine with ``pool_disabled=True`` and
schedule both calls via ``asyncio.create_task`` before awaiting them.
This is load-bearing, confirmed empirically: a pooled engine driving two
bare coroutines through ``asyncio.gather`` resolves the first call's chain
of near-instant localhost round trips to completion before the event loop
ever gives the second coroutine's first ``await`` a turn, so the two never
actually contend for the same row -- the guard being removed still leaves
exactly one token/one winner, because there was never a race to guard
against. Forcing a real connection handshake per call (``NullPool``) plus
eager task scheduling reintroduces genuine interleaving, verified by
mutation in both directions (see the task-5 report).

Run with ``PATH=~/.claude-tmp/bin:$PATH uv run pytest -m docker
tests/test_device_auth_docker.py -n0 -v`` (docker CLI lives outside the
default non-interactive PATH on this host, and -n0 is required because
the compose fixtures use fixed container names).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from .test_hosted_docker_smoke import _psql

# ``hosted_stack`` is re-exported for global fixture discovery via
# conftest.py (same idiom as test_sync_docker.py - importing it directly
# would trigger ruff F811 against the fixture parameter below).

pytestmark = pytest.mark.docker

# The role the container's own SPLITSMITH_DATABASE_URL uses. Connecting as
# it from the host exercises the same non-superuser path the API runs
# under, not the ``splitsmith`` superuser ``_psql`` seeds with.
HOST_APP_DB_URL = "postgresql+asyncpg://splitsmith_app:splitsmith_app@localhost:5432/splitsmith"


def test_migration_creates_device_authorizations(hosted_stack: None) -> None:
    columns = set(
        _psql(
            "SELECT column_name FROM information_schema.columns " "WHERE table_name = 'device_authorizations'"
        ).split()
    )
    assert columns == {
        "id",
        "device_code_hash",
        "user_code",
        "device_name",
        "scope",
        "status",
        "user_id",
        "created_at",
        "expires_at",
        "last_polled_at",
    }


def test_legacy_desktop_tokens_backfill_to_full(hosted_stack: None) -> None:
    """The property that keeps an install in the field working.

    A row inserted without an explicit scope - which is exactly what every
    pre-#719 row is - must read as 'full', because the gate tests
    ``== "sync"``.
    """
    uid = f"user-legacy-{uuid.uuid4().hex[:8]}"
    _psql(f"INSERT INTO users (id, email, entitlement) VALUES ('{uid}', '{uid}@hosted.local', 'free')")
    _psql(
        "INSERT INTO desktop_tokens (id, user_id, name, token_hash) VALUES "
        f"('tok-{uid}', '{uid}', 'legacy paste', 'hash-{uid}')"
    )
    assert _psql(f"SELECT scope FROM desktop_tokens WHERE id = 'tok-{uid}'").strip() == "full"


def test_device_authorizations_has_no_rls_policy(hosted_stack: None) -> None:
    """Deliberately absent, not forgotten.

    The poll resolves from the device code alone, before any
    ``app.user_id`` GUC exists - exactly like ``desktop_tokens``. An RLS
    policy here would make that lookup return zero rows and break
    authentication outright. This asserts the absence so a future
    "enable RLS everywhere" sweep has to argue with a test.
    """
    assert _psql("SELECT policyname FROM pg_policies WHERE tablename = 'device_authorizations'").strip() == ""


def test_concurrent_polls_mint_one_token_on_postgres(hosted_stack: None) -> None:
    """The real proof of mint-at-poll-time.

    SQLite serializes writes, so the in-process version of this test
    (tests/test_device_auth_store.py) proves the statement shape and
    nothing more. This one runs both polls against live Postgres under
    the app role, and asserts the LOSER's outcome specifically -- not
    merely that one token exists. The whole guard rests on
    ``result.rowcount != 1`` in ``DeviceAuthStore.poll``; a loser that
    raised a lock error instead of cleanly falling through to
    ``expired`` would still leave exactly one token row behind, so a
    weaker assertion would pass unnoticed.
    """
    from splitsmith.db import create_engine, sessionmaker
    from splitsmith.db.device_auth import DeviceAuthStore

    uid = f"user-device-{uuid.uuid4().hex[:8]}"
    _psql(f"INSERT INTO users (id, email, entitlement) VALUES ('{uid}', '{uid}@hosted.local', 'free')")
    # ``pool_disabled=True`` is load-bearing, not cosmetic (see module
    # docstring point 3): a pooled connection resolves a trivial localhost
    # query fast enough that the first ``poll()`` runs to completion before
    # the event loop ever gives the second one a turn, so the two calls
    # never actually overlap in the database. NullPool forces each session
    # to pay a fresh connect/handshake, which is enough real async I/O to
    # let both polls interleave for real.
    sf = sessionmaker(create_engine(HOST_APP_DB_URL, pool_disabled=True))
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> list[str]:
        req = await store.authorize(f"device-{uid}")
        assert await store.decide(req.user_code, user_id=uid, approved=True) is True
        # ``create_task`` (not bare coroutines passed to ``gather``) so both
        # polls are scheduled on the loop before either one runs -- see the
        # ``pool_disabled`` note above for why this matters.
        t1 = asyncio.create_task(store.poll(req.device_code))
        t2 = asyncio.create_task(store.poll(req.device_code))
        results = await asyncio.gather(t1, t2, return_exceptions=True)
        return [r.status for r in results if not isinstance(r, BaseException)]

    statuses = asyncio.run(_run())
    minted = _psql(f"SELECT count(*) FROM desktop_tokens WHERE user_id = '{uid}'").strip()
    assert minted == "1", f"expected exactly one token, got {minted} (statuses: {statuses})"
    assert statuses.count("approved") == 1, f"expected exactly one winner, got {statuses}"
    assert statuses.count("expired") == 1, f"expected the loser to report expired, got {statuses}"
    assert _psql(f"SELECT scope FROM desktop_tokens WHERE user_id = '{uid}'").strip() == "sync"


def test_concurrent_decides_resolve_exactly_once_on_postgres(hosted_stack: None) -> None:
    """The same conditional-update guard as ``poll``, on ``decide``.

    Two concurrent decisions on one ``user_code`` - one approve, one deny
    - must not let the second writer's commit silently overwrite the
    first's under READ COMMITTED (an approve stomping a deny, with both
    callers seeing ``True``). Only the writer whose UPDATE matched a
    still-pending row gets ``True``, and the row must land in exactly one
    terminal state.
    """
    from splitsmith.db import create_engine, sessionmaker
    from splitsmith.db.device_auth import DeviceAuthStore

    uid = f"user-decide-{uuid.uuid4().hex[:8]}"
    _psql(f"INSERT INTO users (id, email, entitlement) VALUES ('{uid}', '{uid}@hosted.local', 'free')")
    # ``pool_disabled=True`` + ``create_task`` -- see the identical note on
    # ``test_concurrent_polls_mint_one_token_on_postgres``. Confirmed by
    # mutation: a pooled engine with bare coroutines passed to ``gather``
    # resolves the first ``decide`` call to completion before the second
    # one's first await runs, so the two never actually contend for the row
    # regardless of whether the guard is present.
    sf = sessionmaker(create_engine(HOST_APP_DB_URL, pool_disabled=True))
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> tuple[str, list[bool]]:
        req = await store.authorize(f"device-{uid}")
        t1 = asyncio.create_task(store.decide(req.user_code, user_id=uid, approved=True))
        t2 = asyncio.create_task(store.decide(req.user_code, user_id=uid, approved=False))
        results = await asyncio.gather(t1, t2, return_exceptions=True)
        outcomes = [r for r in results if isinstance(r, bool)]
        return req.user_code, outcomes

    user_code, outcomes = asyncio.run(_run())
    assert outcomes.count(True) == 1, f"expected exactly one winner, got {outcomes}"
    status = _psql(f"SELECT status FROM device_authorizations WHERE user_code = '{user_code}'").strip()
    assert status in ("approved", "denied"), status
