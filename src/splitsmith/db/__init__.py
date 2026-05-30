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

from .email import ConsoleEmailSender, EmailSender, build_email_sender
from .engine import create_engine, sessionmaker, tenant_session_factory
from .job_backend import PostgresJobBackend
from .magic_link import (
    SESSION_COOKIE_NAME,
    InvalidMagicLinkError,
    IssuedSession,
    LoginChallenge,
    MagicLinkAuth,
)
from .matches import PostgresMatchStore
from .models import (
    Base,
    ComputeJobRow,
    MagicLinkTokenRow,
    MatchRow,
    RecentProjectRow,
    SessionRow,
    User,
    new_ulid,
)
from .recent_projects import PostgresRecentProjectsStore
from .scoreboard_identity import PostgresScoreboardIdentityStore

__all__ = [
    "Base",
    "ComputeJobRow",
    "ConsoleEmailSender",
    "EmailSender",
    "InvalidMagicLinkError",
    "IssuedSession",
    "LoginChallenge",
    "MagicLinkAuth",
    "MagicLinkTokenRow",
    "MatchRow",
    "PostgresJobBackend",
    "PostgresMatchStore",
    "PostgresRecentProjectsStore",
    "PostgresScoreboardIdentityStore",
    "RecentProjectRow",
    "SESSION_COOKIE_NAME",
    "SessionRow",
    "User",
    "build_email_sender",
    "create_engine",
    "new_ulid",
    "sessionmaker",
    "tenant_session_factory",
]
