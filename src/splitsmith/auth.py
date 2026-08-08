"""Auth abstraction for the FastAPI server.

The interface lets the same handlers run in two modes:

- **Local mode** -- single-operator desktop app. ``LoopbackAuth``
  resolves every request to a fixed sentinel user.
- **Hosted mode** -- multi-tenant SaaS. A future ``MagicLinkAuth``
  resolves cookies/headers against the picked auth provider.

The hosted-mode methods (``begin_login`` / ``complete_login`` /
``end_session``) are not in the Protocol yet; they get added when the
hosted backend lands so we don't ship an interface no caller exercises.
See ``docs/saas-readiness/02-tenancy-and-identity.md``.
"""

from __future__ import annotations

from typing import Protocol

from fastapi import Request
from pydantic import BaseModel

LOOPBACK_USER_ID = "local"
LOOPBACK_USER_EMAIL = "local@splitsmith"


class User(BaseModel):
    """Identity of the caller behind a request.

    ``id`` is the stable foreign key used everywhere user identity is
    embedded (project ownership, ACL rows, sync sentinels). In local
    mode it is the literal string ``"local"``; in hosted mode it is
    the database ULID.

    ``token_scope`` is the credential's reach, not the user's (#719).
    ``None`` means unrestricted - a session cookie or the loopback
    user. ``"sync"`` is a device-flow desktop token, which the gate in
    ``_auth_gate`` confines to ``/api/sync/*`` plus its own sign-out
    route. ``"full"`` is a legacy pasted desktop token, unrestricted by
    design so installs in the field keep working.
    """

    id: str
    email: str
    display_name: str | None = None
    is_admin: bool = False
    token_scope: str | None = None


class AuthBackend(Protocol):
    async def authenticate_request(self, request: Request) -> User | None:
        """Return the authenticated user, or ``None`` for anonymous.

        Middleware decides whether ``None`` is allowed for a given
        route -- this method only reports who the caller is.
        """


class CompositeAuth:
    """Tries each backend in order; the first non-``None`` result wins.

    Hosted mode authenticates two ways: a magic-link session cookie (the
    browser) or a desktop bearer token (the sync push from the desktop
    app). Both resolve to a normal :class:`User`, so ``current_tenant``
    and RLS treat them identically.

    One thing downstream DOES distinguish (#719): ``User.token_scope``.
    ``DesktopTokenAuth`` sets it from the token row; the cookie and
    loopback backends leave it ``None``. ``_auth_gate`` reads it to
    confine a sync-scoped token to the sync surface. Tenancy is still
    backend-agnostic; only reach is not.
    """

    def __init__(self, *backends: AuthBackend) -> None:
        self.backends = backends

    async def authenticate_request(self, request: Request) -> User | None:
        for backend in self.backends:
            user = await backend.authenticate_request(request)
            if user is not None:
                return user
        return None


class LoopbackAuth:
    """Local-mode backend. Every request resolves to the same user.

    The request object is ignored on purpose: there are no cookies, no
    bearer tokens, no headers to parse. The desktop process is the
    operator; one process = one user.
    """

    def __init__(self) -> None:
        self._user = User(id=LOOPBACK_USER_ID, email=LOOPBACK_USER_EMAIL)

    async def authenticate_request(self, request: Request) -> User:
        return self._user
