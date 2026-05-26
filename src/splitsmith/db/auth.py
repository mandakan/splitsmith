"""Hosted-mode auth backends backed by the ``users`` table.

Today only :class:`HostedLoopbackAuth` lives here -- the same
"single operator, no real identity check" semantics as
:class:`splitsmith.auth.LoopbackAuth`, but the resolved user is
materialised in the database so per-user tables
(``recent_projects``, ``compute_jobs``, etc.) have a real FK target
to point at. :class:`MagicLinkAuth` lands here when the auth-vendor
decision is made and we wire up real sign-in.
"""

from __future__ import annotations

import asyncio

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..auth import User
from .models import User as UserRow

# Stable e-mail for the hosted loopback user. The auth backend will
# upsert (insert-if-missing) a row with this address on every boot;
# the resolved ``users.id`` is what stores get bound to. Hardcoded
# rather than env-configurable because in this mode there is only
# ever one operator -- the deployment is single-tenant by design,
# pending MagicLinkAuth.
HOSTED_LOOPBACK_EMAIL = "loopback@hosted.local"


class HostedLoopbackAuth:
    """Single-operator auth for the hosted stack (pre-MagicLinkAuth).

    Why this exists: the ``LoopbackAuth`` in :mod:`splitsmith.auth`
    returns a hard-coded sentinel id ``"local"``. That's fine for the
    desktop app (nothing persists user-scoped rows in a shared DB),
    but the hosted-mode Postgres stores need a real
    ``users.id`` FK target. This backend bootstraps that target on
    init: a single ``users`` row keyed by
    :data:`HOSTED_LOOPBACK_EMAIL` is upserted, and the resolved ULID
    becomes the user_id every store binds to.

    Once :class:`MagicLinkAuth` lands, this class is retired -- the
    real auth flow mints per-user rows on first sign-in instead.

    The bootstrap is idempotent across server restarts: a second boot
    finds the existing row and reuses its id.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory
        # Bootstrap synchronously at init: this runs in ``create_app``
        # outside any event loop, so ``asyncio.run`` is safe.
        self._user = asyncio.run(self._bootstrap_user())

    @property
    def user_id(self) -> str:
        """The bootstrapped ``users.id`` ULID. Stores bind to this id
        at boot so every request lands rows under the same user."""
        return self._user.id

    @property
    def user(self) -> User:
        return self._user

    async def authenticate_request(self, request: Request) -> User:
        return self._user

    async def _bootstrap_user(self) -> User:
        async with self._session_factory() as session:
            existing = (
                await session.execute(select(UserRow).where(UserRow.email == HOSTED_LOOPBACK_EMAIL))
            ).scalar_one_or_none()
            if existing is None:
                row = UserRow(email=HOSTED_LOOPBACK_EMAIL, display_name="Hosted Operator")
                session.add(row)
                await session.commit()
                await session.refresh(row)
            else:
                row = existing
        return User(
            id=row.id,
            email=row.email,
            display_name=row.display_name,
        )
