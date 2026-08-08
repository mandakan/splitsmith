"""Schema-level tests for the device-flow tables (#719).

Covers the two additive schema changes the device flow needs: the new
``device_authorizations`` table and ``desktop_tokens.scope``. The scope
default is the load-bearing part -- an existing pasted token must keep
working, which means it must NOT read as ``'sync'``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from splitsmith.db import Base, DesktopTokenRow, DeviceAuthorizationRow, User, create_engine, sessionmaker


def _factory(tmp_path: Path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'schema.sqlite'}")

    async def _create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    return sessionmaker(engine)


def test_device_authorization_row_round_trips(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    expires = datetime.now(UTC) + timedelta(minutes=10)

    async def _run() -> DeviceAuthorizationRow:
        async with sf() as s:
            s.add(
                DeviceAuthorizationRow(
                    device_code_hash="hash-a",
                    user_code="ABCD-2345",
                    device_name="mac studio",
                    scope="sync",
                    expires_at=expires,
                )
            )
            await s.commit()
        async with sf() as s:
            return (
                await s.execute(
                    select(DeviceAuthorizationRow).where(DeviceAuthorizationRow.user_code == "ABCD-2345")
                )
            ).scalar_one()

    row = asyncio.run(_run())
    assert row.id
    assert row.status == "pending"
    assert row.user_id is None
    assert row.last_polled_at is None


def test_desktop_token_scope_defaults_to_full_for_legacy_rows(tmp_path: Path) -> None:
    """A row inserted without an explicit scope is a legacy pasted token.

    The gate tests ``token_scope == "sync"``, so 'full' is what keeps an
    install in the field working after this ships.
    """
    sf = _factory(tmp_path)

    async def _run() -> str:
        async with sf() as s:
            user = User(email="owner@example.com")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            uid = user.id
        async with sf() as s:
            s.add(DesktopTokenRow(user_id=uid, name="legacy", token_hash="hash-b"))
            await s.commit()
        async with sf() as s:
            row = (
                await s.execute(select(DesktopTokenRow).where(DesktopTokenRow.token_hash == "hash-b"))
            ).scalar_one()
            return row.scope

    assert asyncio.run(_run()) == "full"


def test_store_create_mints_sync_scope_by_default(tmp_path: Path) -> None:
    """Every token minted from here on is sync-scoped -- device flow and
    the account page's manual button alike."""
    from splitsmith.db.desktop_tokens import DesktopTokenStore

    sf = _factory(tmp_path)

    async def _run() -> str:
        async with sf() as s:
            user = User(email="owner@example.com")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            uid = user.id
        store = DesktopTokenStore(sf, user_id=uid)
        record, _raw = await store.create("mac studio")
        async with sf() as s:
            row = (
                await s.execute(select(DesktopTokenRow).where(DesktopTokenRow.id == record.id))
            ).scalar_one()
            return row.scope

    assert asyncio.run(_run()) == "sync"
