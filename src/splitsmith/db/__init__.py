"""SaaS-foundation DB layer (doc 02).

SQLAlchemy 2.x async + Alembic for migrations. Tests run against
SQLite in-memory via aiosqlite; the hosted backend will use
asyncpg against Postgres. The same model code generates schema
for both engines.

Why SQLAlchemy + Alembic instead of raw asyncpg:

- **Engine-agnostic queries.** SQLite for tests (microsecond
  in-memory setup), Postgres for prod, MySQL if we ever need to.
  Same model code; the engine swaps with a connection string.
- **Alembic** is the de-facto Postgres migration tool. Anything
  else here is roll-your-own.
- **Pydantic-friendly** declarative models via the 2.0 API.

The Protocols in :mod:`splitsmith.ui.jobs` /
:mod:`splitsmith.user_config` / :mod:`splitsmith.auth` are still
what handlers depend on; the future ``PostgresJobBackend`` /
``PostgresRecentProjectsStore`` / ``MagicLinkAuth`` impls use
this DB layer internally without leaking SQL into handler code.
"""

from .engine import create_engine, sessionmaker
from .models import Base, User, new_ulid

__all__ = ["Base", "User", "create_engine", "new_ulid", "sessionmaker"]
