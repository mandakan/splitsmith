"""Migrations must apply to Postgres, agree with the models, and reverse.

Nothing in CI ran a migration before this (#876). ``deploy-app.yml`` only
notes in a comment that ``serve`` runs ``alembic upgrade head`` on boot,
so the first process that ever applied a new revision was the production
deploy, and a broken one booted into a broken schema.

The default pytest suite does not close this and cannot: it is
SQLite-backed and builds its tables with ``Base.metadata.create_all``.
``tests/test_comments_schema.py`` passes whether or not a migration file
exists and whether or not it agrees with the models -- it tests the
models, which its filename does not say.

Four assertions, cheapest first, each naming what broke:

1. one head revision  -- two revisions claiming the same parent
2. ``upgrade head``   -- a migration that is fine on SQLite and not on PG
3. an empty diff      -- the models and the chain having drifted apart
4. ``downgrade base`` -- an unreversible revision

Run against a scratch database, never one with data: assertion 4 drops
every table.

    SPLITSMITH_DATABASE_URL=postgresql+asyncpg://... \\
        uv run python scripts/ci/assert_migrations_match_models.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _load_config() -> Any:
    from alembic.config import Config

    return Config(str(ALEMBIC_INI))


def check_single_head(cfg: Any) -> str | None:
    """Return an error message, or ``None`` when there is exactly one head."""
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) == 1:
        return None
    return (
        f"expected exactly one head revision, found {len(heads)}: "
        f"{', '.join(sorted(heads))}. Two revisions share a down_revision; "
        "`alembic upgrade head` fails on boot with a multiple-heads error."
    )


async def _diff_against_models(url: str) -> list[Any]:
    """Ops Alembic would generate to make the live schema match the models."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from splitsmith.db.models import Base
    from splitsmith.db.schema_diff import include_object

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(
                        sync_conn,
                        opts={"include_object": include_object},
                    ),
                    Base.metadata,
                )
            )
    finally:
        await engine.dispose()


def main() -> int:
    url = os.environ.get("SPLITSMITH_DATABASE_URL", "").strip()
    if not url:
        print(
            "SPLITSMITH_DATABASE_URL is unset. Point it at a SCRATCH database: "
            "this script downgrades to base, which drops every table.",
            file=sys.stderr,
        )
        return 2

    from alembic import command

    cfg = _load_config()

    print("[1/4] revision chain has a single head ...", flush=True)
    if (problem := check_single_head(cfg)) is not None:
        print(f"FAIL: {problem}", file=sys.stderr)
        return 1

    print("[2/4] alembic upgrade head ...", flush=True)
    command.upgrade(cfg, "head")

    print("[3/4] the resulting schema matches Base.metadata ...", flush=True)
    diffs = asyncio.run(_diff_against_models(url))
    if diffs:
        print(
            f"FAIL: the migration chain and the SQLAlchemy models disagree "
            f"({len(diffs)} difference(s)). Alembic would generate:",
            file=sys.stderr,
        )
        for op in diffs:
            print(f"  - {op}", file=sys.stderr)
        print(
            "\nFix by writing the migration that closes the gap "
            "(`alembic revision --autogenerate`), not by widening the filter "
            "in src/splitsmith/db/schema_diff.py.",
            file=sys.stderr,
        )
        return 1

    print("[4/4] downgrade base, then upgrade head again ...", flush=True)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    print("OK: migrations apply, match the models, and reverse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
