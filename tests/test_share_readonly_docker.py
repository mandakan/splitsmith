"""#779: prove the READ ONLY share transaction at a real Postgres.

Runs under `pytest -m docker` against the compose stack, connecting as
the same non-superuser role the API runs under. Unit suites cover the
wiring on sqlite (where the listener is a no-op by design); this file
is the proof the database actually refuses the write.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from splitsmith.db.engine import create_engine, sessionmaker, tenant_session_factory
from splitsmith.db.share_guard import current_share_scope

from .test_sync_docker import HOST_APP_DB_URL

# hosted_stack is provided via conftest.py's re-export (same idiom as
# the other docker suites).

pytestmark = pytest.mark.docker


def _tenant_factory():
    engine = create_engine(HOST_APP_DB_URL, pool_disabled=True)
    base = sessionmaker(engine)
    return tenant_session_factory(base, "u-779-readonly-test")


async def _run_statement(sql: str) -> None:
    factory = _tenant_factory()
    async with factory() as session:
        await session.execute(text(sql))
        await session.commit()


def test_read_scoped_share_transaction_rejects_writes(hosted_stack: None) -> None:
    """A data-modification statement inside a read-scoped share request
    fails with SQLSTATE 25006 (read_only_sql_transaction), even one that
    would touch zero rows - enforcement is per-transaction, not
    per-row."""
    token = current_share_scope.set("read")
    try:
        with pytest.raises(DBAPIError) as excinfo:
            asyncio.run(_run_statement("DELETE FROM matches WHERE false"))
    finally:
        current_share_scope.reset(token)
    assert "read-only" in str(excinfo.value).lower()


def test_read_scoped_share_transaction_still_reads(hosted_stack: None) -> None:
    """The same read-scoped transaction serves SELECTs - the share
    surface itself must keep working, and set_config is legal inside a
    READ ONLY transaction."""
    token = current_share_scope.set("read")
    try:
        asyncio.run(_run_statement("SELECT count(*) FROM matches"))
    finally:
        current_share_scope.reset(token)


def test_unscoped_transaction_still_writes(hosted_stack: None) -> None:
    """Control: without a share scope the identical statement succeeds -
    the defense keys off the scope, not off using the tenant factory."""
    asyncio.run(_run_statement("DELETE FROM matches WHERE false"))
