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

from .auth import HOSTED_LOOPBACK_EMAIL, HostedLoopbackAuth
from .engine import create_engine, sessionmaker
from .job_backend import PostgresJobBackend
from .matches import PostgresMatchStore
from .models import Base, ComputeJobRow, MatchRow, RecentProjectRow, User, new_ulid
from .recent_projects import PostgresRecentProjectsStore
from .scoreboard_identity import PostgresScoreboardIdentityStore

__all__ = [
    "Base",
    "ComputeJobRow",
    "HOSTED_LOOPBACK_EMAIL",
    "HostedLoopbackAuth",
    "MatchRow",
    "PostgresJobBackend",
    "PostgresMatchStore",
    "PostgresRecentProjectsStore",
    "PostgresScoreboardIdentityStore",
    "RecentProjectRow",
    "User",
    "create_engine",
    "new_ulid",
    "sessionmaker",
]
