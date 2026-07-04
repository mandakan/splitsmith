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
    """

    id: str
    email: str
    display_name: str | None = None
    is_admin: bool = False


class AuthBackend(Protocol):
    async def authenticate_request(self, request: Request) -> User | None:
        """Return the authenticated user, or ``None`` for anonymous.

        Middleware decides whether ``None`` is allowed for a given
        route -- this method only reports who the caller is.
        """


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
