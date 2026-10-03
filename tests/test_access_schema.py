from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import insert, select

from splitsmith.db import AccessRequest, Base, User, create_engine, sessionmaker


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


def test_user_row_inserted_without_tier_gets_full(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")

    async def go() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Core insert, bypassing the ORM default: the server default must apply.
            await conn.execute(insert(User.__table__).values(id="u1", email="a@x.se", entitlement="free"))
        async with sessionmaker(engine)() as s:
            return (await s.execute(select(User.access_tier).where(User.id == "u1"))).scalar_one()

    assert _run(go()) == "full"


def test_access_request_email_is_unique(tmp_path: Path) -> None:
    import pytest
    from sqlalchemy.exc import IntegrityError

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")

    async def go() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker(engine)() as s:
            s.add(AccessRequest(email="a@x.se", source="form", status="pending"))
            await s.commit()
            s.add(AccessRequest(email="a@x.se", source="login", status="pending"))
            with pytest.raises(IntegrityError):
                await s.commit()

    _run(go())
