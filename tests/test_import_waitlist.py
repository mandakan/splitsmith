from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from splitsmith.db import AccessRequest, Base, User, create_engine, sessionmaker


def test_import_keeps_timestamps_and_skips_known(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}"
    engine = create_engine(url)

    async def setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker(engine)() as s:
            s.add(User(email="me@x.se", access_tier="full"))
            await s.commit()

    asyncio.run(setup())
    dump = tmp_path / "dump.json"
    dump.write_text(
        json.dumps(
            [
                # The real KV writer stores an ISO 8601 string
                # (`new Date().toISOString()`), not millis -- this is the
                # shape a real dump actually has.
                {"email": "Erik@x.se", "ts": "2025-09-27T19:06:40.000Z"},
                {"email": "me@x.se", "ts": "2025-09-27T19:06:40.000Z"},
                {"email": "erik@x.se", "ts": 1759100000000},
            ]
        )
    )
    out = subprocess.run(
        [sys.executable, "scripts/import_waitlist.py", str(dump)],
        env={"SPLITSMITH_DATABASE_URL": url, "PATH": ""},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "imported 1, skipped 2" in out.stdout

    async def rows() -> list[tuple[str, str, int]]:
        async with sessionmaker(engine)() as s:
            return [
                (r.email, r.source, int(r.requested_at.timestamp()))
                for r in (await s.execute(select(AccessRequest))).scalars()
            ]

    assert asyncio.run(rows()) == [("erik@x.se", "import", 1759000000)]


def test_invalid_entries_are_skipped_and_counted(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}"
    engine = create_engine(url)

    async def setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup())
    dump = tmp_path / "dump.json"
    dump.write_text(
        json.dumps(
            [
                {"email": "ok@x.se", "ts": "2025-09-27T19:06:40.000Z"},
                {"email": "ba d@x.se", "ts": "2025-09-27T19:06:40.000Z"},  # internal space
                {"email": "cr\r@x.se", "ts": "2025-09-27T19:06:40.000Z"},  # control char
                {"email": "a" * 317 + "@x.se", "ts": "2025-09-27T19:06:40.000Z"},  # over 320 chars
                {"email": "missing-ts@x.se"},  # no "ts" key at all
            ]
        )
    )
    out = subprocess.run(
        [sys.executable, "scripts/import_waitlist.py", str(dump)],
        env={"SPLITSMITH_DATABASE_URL": url, "PATH": ""},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "imported 1, skipped 4" in out.stdout
    assert "4 invalid" in out.stdout

    async def rows() -> list[str]:
        async with sessionmaker(engine)() as s:
            return [r.email for r in (await s.execute(select(AccessRequest))).scalars()]

    assert asyncio.run(rows()) == ["ok@x.se"]
