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

from .access_requests import (
    AccessRequestStore,
    AccessRequestView,
    AccountAccessStore,
    AccountView,
    AlreadyDecidedError,
    NotFoundError,
)
from .email import ConsoleEmailSender, EmailSender, LettermintEmailSender, build_email_sender
from .engine import LoopEngines, create_engine, loop_sessionmaker, sessionmaker, tenant_session_factory
from .export_presets import PostgresExportPresetStore
from .job_backend import PostgresJobBackend
from .looks import PostgresLookStore
from .magic_link import (
    SESSION_COOKIE_NAME,
    InvalidMagicLinkError,
    IssuedSession,
    LoginChallenge,
    MagicLinkAuth,
)
from .matches import PostgresMatchStore
from .models import (
    AccessRequest,
    Base,
    ComputeJobRow,
    DesktopTokenRow,
    DeviceAuthorizationRow,
    MagicLinkTokenRow,
    MatchRow,
    RecentProjectRow,
    SessionRow,
    ShareTokenRow,
    StateDocRow,
    User,
    new_ulid,
)
from .profile import PostgresProfileStore
from .project_state import ProjectStateStore, StateConflictError
from .recent_projects import PostgresRecentProjectsStore
from .scoreboard_identity import PostgresScoreboardIdentityStore
from .signup_policy import SignupPolicy, build_signup_policy
from .whats_new import PostgresWhatsNewStore
from .youtube_connections import PostgresYouTubeConnectionStore

__all__ = [
    "AccessRequest",
    "AccessRequestStore",
    "AccessRequestView",
    "AccountAccessStore",
    "AccountView",
    "AlreadyDecidedError",
    "Base",
    "ComputeJobRow",
    "ConsoleEmailSender",
    "DesktopTokenRow",
    "DeviceAuthorizationRow",
    "EmailSender",
    "InvalidMagicLinkError",
    "IssuedSession",
    "LettermintEmailSender",
    "LoginChallenge",
    "LoopEngines",
    "MagicLinkAuth",
    "MagicLinkTokenRow",
    "MatchRow",
    "NotFoundError",
    "PostgresExportPresetStore",
    "PostgresLookStore",
    "PostgresWhatsNewStore",
    "PostgresJobBackend",
    "PostgresMatchStore",
    "PostgresProfileStore",
    "PostgresRecentProjectsStore",
    "PostgresScoreboardIdentityStore",
    "PostgresYouTubeConnectionStore",
    "ProjectStateStore",
    "RecentProjectRow",
    "SESSION_COOKIE_NAME",
    "SessionRow",
    "ShareTokenRow",
    "SignupPolicy",
    "StateConflictError",
    "StateDocRow",
    "User",
    "build_email_sender",
    "build_signup_policy",
    "create_engine",
    "loop_sessionmaker",
    "new_ulid",
    "sessionmaker",
    "tenant_session_factory",
]
