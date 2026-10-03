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
                {"email": "Erik@x.se", "ts": 1759000000000},
                {"email": "me@x.se", "ts": 1759000000000},
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
