"""Provision the hosted database's least-privilege roles, and rotate their passwords.

Run against one Neon branch at a time, connected as the schema owner through
``SPLITSMITH_MIGRATION_DATABASE_URL`` (the same URL the deploy job migrates
with). See :mod:`splitsmith.db.roles` for the layout and why the runtime
roles are plain SQL roles rather than Neon-managed ones.

    # Create the roles and grants (idempotent; safe to re-run).
    uv run --extra hosted python scripts/db_roles.py apply

    # New password for one runtime role. The connection URLs go to mode-0600
    # files and are never printed; move them into the secret store and
    # delete the files. serve connects through the pooler, the worker
    # directly (it LISTENs), so splitsmith_app gets both, same password.
    uv run --extra hosted python scripts/db_roles.py rotate splitsmith_app \
        --out ~/.claude-tmp/app.url --direct-out ~/.claude-tmp/app-direct.url
    gcloud secrets versions add production-database-url --data-file ~/.claude-tmp/app.url
    gcloud secrets versions add production-worker-database-url --data-file ~/.claude-tmp/app-direct.url
    rm ~/.claude-tmp/app.url ~/.claude-tmp/app-direct.url

    # Agents drain the queue too, so theirs is the direct URL
    # (production-agent-database-url); serve hands it out at registration,
    # so re-register each box after rotating it.
    uv run --extra hosted python scripts/db_roles.py rotate splitsmith_agent --direct \
        --out ~/.claude-tmp/agent.url

``rotate`` takes the host from the owner URL and, unless ``--direct`` is
given, points ``--out`` at Neon's pooler (``-pooler`` after the endpoint id).
The owner's own password is Neon's to reset (console or API); this script
never changes it. Secret Manager mounts are read when an instance starts, so
roll serve after a rotation (any ``gcloud run services update``) and the job
picks the new version up on its next run (``.claude/rules/hosted.md``).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url

from splitsmith.db import create_engine
from splitsmith.db.roles import LOGIN_ROLES, password_statement, provision_statements

MIGRATION_URL_ENV = "SPLITSMITH_MIGRATION_DATABASE_URL"


def _owner_url() -> URL:
    raw = os.environ.get(MIGRATION_URL_ENV, "").strip()
    if not raw:
        sys.exit(f"{MIGRATION_URL_ENV} is not set: it must be the schema owner's URL for this branch.")
    url = make_url(raw)
    if not url.drivername.startswith("postgresql"):
        sys.exit(f"{MIGRATION_URL_ENV} must be a Postgres URL, got driver {url.drivername!r}.")
    return url


def pooled_host(host: str) -> str:
    """``host`` on Neon's pooler: ``ep-x.region...`` becomes ``ep-x-pooler.region...``.

    A host that is already pooled, or is not a Neon endpoint host, is
    returned unchanged.
    """
    first, sep, rest = host.partition(".")
    if not first.startswith("ep-") or first.endswith("-pooler") or not sep:
        return host
    return f"{first}-pooler.{rest}"


def direct_host(host: str) -> str:
    """``host`` off Neon's pooler: the inverse of :func:`pooled_host`."""
    first, sep, rest = host.partition(".")
    if not first.startswith("ep-") or not first.endswith("-pooler") or not sep:
        return host
    return f"{first.removesuffix('-pooler')}.{rest}"


def runtime_url(owner: URL, *, role: str, password: str, pooled: bool) -> URL:
    """The owner's URL with ``role``'s credentials, on the pooler or direct host."""
    host = owner.host or ""
    return owner.set(
        username=role, password=password, host=pooled_host(host) if pooled else direct_host(host)
    )


async def _run(owner: URL, statements: list[str]) -> None:
    engine = create_engine(owner.render_as_string(hide_password=False))
    try:
        async with engine.begin() as conn:
            for statement in statements:
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


def _apply(owner: URL) -> None:
    statements = provision_statements(owner=owner.username or "", database=owner.database or "")
    asyncio.run(_run(owner, statements))
    print(f"applied {len(statements)} statements: roles {', '.join(LOGIN_ROLES)} ready")


def _write_secret(path: Path, value: str) -> None:
    """Create ``path`` 0600 with ``value``; never overwrite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(value)


def _rotate(owner: URL, role: str, out: Path, *, pooled: bool, direct_out: Path | None = None) -> None:
    """New password for ``role``; its URL goes to ``out`` (and the direct-host
    URL to ``direct_out``, for the worker, which LISTENs and so cannot use the
    pooler)."""
    if role not in LOGIN_ROLES:
        sys.exit(f"{role!r} is not a runtime role ({', '.join(LOGIN_ROLES)}).")
    targets = [(out, pooled)] + ([(direct_out, False)] if direct_out is not None else [])
    for path, _ in targets:
        if path.exists():
            sys.exit(f"{path} already exists; refusing to overwrite a credential file.")
    password = secrets.token_urlsafe(32)
    # Write the files before the password is set, so a failure to write one
    # cannot leave a role with a password nobody has.
    written: list[Path] = []
    try:
        for path, use_pooler in targets:
            url = runtime_url(owner, role=role, password=password, pooled=use_pooler)
            _write_secret(path, url.render_as_string(hide_password=False))
            written.append(path)
        asyncio.run(_run(owner, [password_statement(role, password)]))
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    where = " and ".join(str(path) for path, _ in targets)
    print(
        f"rotated {role}; its URL is in {where} (mode 0600). Move it into the secret store, then delete it."
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("apply", help="create the runtime roles and grant them row access")
    rotate = sub.add_parser("rotate", help="set a new password for one runtime role")
    rotate.add_argument("role", choices=LOGIN_ROLES)
    rotate.add_argument("--out", type=Path, required=True, help="file to write the role's URL to (0600)")
    rotate.add_argument("--direct", action="store_true", help="--out gets the direct host (no pooler)")
    rotate.add_argument(
        "--direct-out",
        type=Path,
        help="also write the direct-host URL, same password, here (the worker job's)",
    )
    args = parser.parse_args(argv)

    owner = _owner_url()
    if args.command == "apply":
        _apply(owner)
    else:
        direct_out = args.direct_out.expanduser() if args.direct_out else None
        _rotate(owner, args.role, args.out.expanduser(), pooled=not args.direct, direct_out=direct_out)


if __name__ == "__main__":
    main()
