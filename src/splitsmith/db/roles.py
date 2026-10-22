"""Least-privilege Postgres roles for the hosted runtime.

Hosted mode connects with two kinds of credential:

- **The owner** (``neondb_owner`` on Neon) owns every table, sequence and
  function, and is the only role Alembic runs as. Its URL is
  ``SPLITSMITH_MIGRATION_DATABASE_URL`` and lives with the deploy job, never
  in the runtime environment.
- **Runtime login roles** -- ``splitsmith_app`` for the API and the Railway
  worker, ``splitsmith_agent`` for self-hosted agents -- read and write rows
  and nothing else. Both are members of the ``splitsmith_runtime`` group,
  which holds the grants, so revoking one box's credential leaves the API
  untouched.

Why plain SQL roles and not Neon's role API: a role made through Neon's API
or console joins ``neon_superuser`` and carries ``BYPASSRLS``, and neither
can be taken away afterwards. A ``BYPASSRLS`` role skips every row-level
security policy, ``FORCE ROW LEVEL SECURITY`` included, so the tenant
policies would be decorative. A role made with ``CREATE ROLE`` by the owner
has neither. The trade-off is that its password is set with ``ALTER ROLE``
rather than Neon's reset endpoint.

:func:`provision_statements` is idempotent: run it after creating a branch,
and again whenever the grant set changes. Default privileges cover objects
later migrations create, as long as the owner runs those migrations.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

RUNTIME_GROUP = "splitsmith_runtime"
APP_ROLE = "splitsmith_app"
AGENT_ROLE = "splitsmith_agent"
LOGIN_ROLES: tuple[str, ...] = (APP_ROLE, AGENT_ROLE)

#: Attributes every runtime role must have. ``NOBYPASSRLS`` is the one RLS
#: depends on; the rest keep a leaked runtime credential from creating
#: databases or roles, or streaming the WAL. ``SUPERUSER`` is not in the
#: list re-applied by ``ALTER ROLE``: on Postgres 16+ only a superuser may
#: touch that attribute, even to clear it, and the owner is not one (nor can
#: it make one, so a role it created never has it).
_RUNTIME_ATTRIBUTES = "NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION"

#: Tables the runtime may read but never write: the migration ledger.
READ_ONLY_TABLES: tuple[str, ...] = ("alembic_version",)


def _ident(name: str) -> str:
    """Quote ``name`` as a Postgres identifier."""
    return '"' + name.replace('"', '""') + '"'


def _literal(value: str) -> str:
    """Quote ``value`` as a Postgres string literal."""
    return "'" + value.replace("'", "''") + "'"


def _ensure_role(name: str, *, login: bool, member_of: str | None) -> str:
    """A ``DO`` block that creates ``name`` if absent and pins its attributes.

    ``ALTER ROLE`` runs on every apply, so a role someone loosened by hand is
    tightened again.
    """
    login_kw = "LOGIN" if login else "NOLOGIN"
    create = f"CREATE ROLE {_ident(name)} {login_kw} NOSUPERUSER {_RUNTIME_ATTRIBUTES}"
    if member_of is not None:
        create += f" IN ROLE {_ident(member_of)}"
    return (
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_literal(name)}) THEN "
        f"{create}; "
        "END IF; "
        f"ALTER ROLE {_ident(name)} {login_kw} {_RUNTIME_ATTRIBUTES}; "
        "END $$"
    )


def provision_statements(
    *,
    owner: str,
    database: str,
    schema: str = "public",
    group: str = RUNTIME_GROUP,
    login_roles: tuple[str, ...] = LOGIN_ROLES,
) -> list[str]:
    """SQL that creates the runtime roles and grants them row access.

    Run as ``owner`` (the role that owns the schema's objects). Passwords are
    not set here: a new login role cannot sign in until
    :func:`password_statement` gives it one.
    """
    g = _ident(group)
    s = _ident(schema)
    o = _ident(owner)
    statements = [_ensure_role(group, login=False, member_of=None)]
    for role in login_roles:
        statements.append(_ensure_role(role, login=True, member_of=group))
        # IN ROLE only applies on CREATE; GRANT covers a role that existed
        # before it joined the group.
        statements.append(f"GRANT {g} TO {_ident(role)}")
    statements += [
        f"GRANT CONNECT ON DATABASE {_ident(database)} TO {g}",
        f"GRANT USAGE ON SCHEMA {s} TO {g}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {s} TO {g}",
        f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA {s} TO {g}",
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {s} TO {g}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA {s} "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {g}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA {s} "
        f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {g}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA {s} GRANT EXECUTE ON FUNCTIONS TO {g}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA {s} GRANT USAGE ON TYPES TO {g}",
    ]
    for table in READ_ONLY_TABLES:
        qualified = f"{s}.{_ident(table)}"
        statements.append(
            "DO $$ BEGIN "
            f"IF to_regclass({_literal(f'{schema}.{table}')}) IS NOT NULL THEN "
            f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {qualified} FROM {g}; "
            "END IF; END $$"
        )
    return statements


def password_statement(role: str, password: str) -> str:
    """``ALTER ROLE`` that sets ``role``'s password.

    The caller generates the password and keeps it out of logs; this only
    quotes it.
    """
    return f"ALTER ROLE {_ident(role)} PASSWORD {_literal(password)}"


#: One row: is the connected role able to skip row-level security?
PRIVILEGED_ROLE_QUERY = (
    "SELECT current_user AS role, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
)

#: Set to ``1`` to let a hosted process run as a role that skips RLS. Meant
#: for the cut-over window only; the process logs an error on every boot
#: while it is set.
ALLOW_PRIVILEGED_ROLE_ENV = "SPLITSMITH_ALLOW_PRIVILEGED_DB_ROLE"


def privileged_role_problem(role: str, *, superuser: bool, bypass_rls: bool) -> str | None:
    """Why ``role`` must not run the hosted runtime, or ``None`` if it may.

    A superuser or ``BYPASSRLS`` role reads every tenant's rows whatever
    ``app.user_id`` says, which turns the RLS policies off without a trace.
    """
    if superuser:
        reason = "is a superuser"
    elif bypass_rls:
        reason = "has BYPASSRLS"
    else:
        return None
    return (
        f"the database role {role!r} {reason}, so row-level security does not apply to it. "
        f"Connect the API and workers as a least-privilege role ({APP_ROLE} / {AGENT_ROLE}, "
        f"see scripts/db_roles.py) and keep the owner for migrations "
        f"(SPLITSMITH_MIGRATION_DATABASE_URL)."
    )


async def assert_runtime_role(engine: AsyncEngine) -> None:
    """Refuse to run the hosted runtime as a role that skips RLS.

    No-op off Postgres (SQLite has no roles and no RLS). Raises
    :class:`RuntimeError` naming the problem; with
    ``SPLITSMITH_ALLOW_PRIVILEGED_DB_ROLE=1`` it logs it instead.
    """
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text

    async with engine.connect() as conn:
        row = (await conn.execute(text(PRIVILEGED_ROLE_QUERY))).one()
    problem = privileged_role_problem(row.role, superuser=row.rolsuper, bypass_rls=row.rolbypassrls)
    if problem is None:
        return
    if os.environ.get(ALLOW_PRIVILEGED_ROLE_ENV, "").strip() == "1":
        logger.error("%s (allowed by %s=1)", problem, ALLOW_PRIVILEGED_ROLE_ENV)
        return
    raise RuntimeError(f"{problem} Set {ALLOW_PRIVILEGED_ROLE_ENV}=1 to start anyway.")
