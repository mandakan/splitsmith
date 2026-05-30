"""In-house passwordless (magic-link) auth backend.

The hosted-mode replacement for :class:`splitsmith.db.auth.HostedLoopbackAuth`:
real, distinct per-user accounts created on first sign-in. We own the
whole flow (no auth vendor) so self-host stays viable and the dependency
list stays small -- doc 02 already wanted sessions in Postgres, so owning
tokens + sessions too is consistent.

Flow (doc 02):

1. ``begin_login(email)`` mints a high-entropy token, stores only its
   SHA-256 hash with a 15-minute expiry, and e-mails a link carrying the
   raw token via the injected :class:`EmailSender`.
2. The user clicks the link; the callback route calls
   ``complete_login(token)``, which verifies the token is unexpired +
   unconsumed, marks it single-use, upserts the ``users`` row (first
   sign-in creates the account), and issues a 30-day session.
3. The browser carries the session secret in an httpOnly cookie;
   ``authenticate_request`` hashes it, resolves the session, and returns
   the :class:`User`.

Security choices: tokens and session secrets are 256-bit
``secrets.token_urlsafe`` values; only their SHA-256 hashes are persisted,
so a database leak yields no usable links or cookies. Token redemption and
session lookup are constant-work indexed point lookups on the hash.

This backend talks to ``users`` / ``magic_link_tokens`` / ``sessions`` --
none of which are under RLS, because identity must be resolved before the
``app.user_id`` GUC the per-tenant stores key on can exist. It therefore
holds the **raw** (non-tenant) session factory, same as HostedLoopbackAuth.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..auth import User
from .email import EmailSender
from .models import MagicLinkTokenRow, SessionRow
from .models import User as UserRow

# Cookie the browser carries the raw session secret in. httpOnly + Secure
# + SameSite=Lax are set by the route when it writes the cookie (doc 02);
# this module only needs the name to read it back.
SESSION_COOKIE_NAME = "splitsmith_session"

# Token / session lifetimes (doc 02).
MAGIC_LINK_TTL = timedelta(minutes=15)
SESSION_TTL = timedelta(days=30)
# Sliding-expiry throttle: only bump ``last_used_at`` / ``expires_at`` when
# the session hasn't been touched for this long, so a busy session doesn't
# incur a write on every request.
SESSION_TOUCH_INTERVAL = timedelta(hours=1)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _hash(secret: str) -> str:
    """SHA-256 hex of a raw token / session secret. The only form that
    ever touches the database."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


class InvalidMagicLinkError(Exception):
    """A presented magic-link token is unknown, expired, or already used.

    Carries a coarse ``reason`` for logging/telemetry; the route maps all
    cases to one generic 400 so it never reveals which links exist.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class LoginChallenge:
    """Handle returned by :meth:`MagicLinkAuth.begin_login`. The raw token
    is **not** here -- it only exists in the e-mailed link."""

    id: str
    email: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedSession:
    """Result of a successful :meth:`MagicLinkAuth.complete_login`.

    ``secret`` is the raw value the route writes into the session cookie;
    it is never persisted (only its hash is). ``user`` is the resolved
    account; ``expires_at`` drives the cookie's Max-Age.
    """

    secret: str
    expires_at: datetime
    user: User


class MagicLinkAuth:
    """Passwordless auth backend backed by Postgres (doc 02)."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        email_sender: EmailSender,
        *,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        # Raw (non-tenant) factory: this backend writes users / sessions /
        # magic_link_tokens, none under RLS, and runs before any GUC.
        self._session_factory = session_factory
        self._email = email_sender
        self._now = now

    # ------------------------------------------------------------------
    # Sign-in flow
    # ------------------------------------------------------------------

    async def begin_login(self, email: str, *, base_url: str) -> LoginChallenge:
        """Mint + e-mail a magic link for ``email`` and return its handle.

        ``base_url`` is the public origin the callback lives under (e.g.
        ``https://splitsmith.app``); the caller supplies it from config or
        the request so this backend stays decoupled from the web layer. We
        send the link regardless of whether ``email`` has an account -- the
        account is created on redemption, and not revealing account
        existence is deliberate.
        """
        normalized = _normalize_email(email)
        token = secrets.token_urlsafe(32)
        now = self._now()
        row = MagicLinkTokenRow(
            email=normalized,
            token_hash=_hash(token),
            expires_at=now + MAGIC_LINK_TTL,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            challenge = LoginChallenge(id=row.id, email=normalized, expires_at=row.expires_at)

        link = f"{base_url.rstrip('/')}/auth/callback?token={token}"
        await self._email.send_magic_link(to=normalized, link=link)
        return challenge

    async def complete_login(
        self,
        token: str,
        *,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> IssuedSession:
        """Redeem ``token``: verify it, mark it single-use, upsert the user,
        and issue a session. Raises :class:`InvalidMagicLinkError` if the
        token is unknown / expired / already consumed."""
        token_hash = _hash(token)
        now = self._now()
        async with self._session_factory() as session:
            link_row = (
                await session.execute(
                    select(MagicLinkTokenRow).where(MagicLinkTokenRow.token_hash == token_hash)
                )
            ).scalar_one_or_none()
            if link_row is None:
                raise InvalidMagicLinkError("not_found")
            if link_row.consumed_at is not None:
                raise InvalidMagicLinkError("consumed")
            if _aware(link_row.expires_at) < now:
                raise InvalidMagicLinkError("expired")

            # Single-use: stamp before issuing so a concurrent second
            # redemption of the same token loses the race on commit.
            link_row.consumed_at = now

            user_row = (
                await session.execute(select(UserRow).where(UserRow.email == link_row.email))
            ).scalar_one_or_none()
            if user_row is None:
                user_row = UserRow(email=link_row.email, email_verified_at=now)
                session.add(user_row)
                await session.flush()
            elif user_row.email_verified_at is None:
                user_row.email_verified_at = now

            secret = secrets.token_urlsafe(32)
            session_row = SessionRow(
                token_hash=_hash(secret),
                user_id=user_row.id,
                expires_at=now + SESSION_TTL,
                last_used_at=now,
                user_agent=user_agent,
                ip=ip,
            )
            session.add(session_row)
            await session.commit()
            user = User(id=user_row.id, email=user_row.email, display_name=user_row.display_name)
            expires_at = now + SESSION_TTL

        return IssuedSession(secret=secret, expires_at=expires_at, user=user)

    # ------------------------------------------------------------------
    # Per-request resolution
    # ------------------------------------------------------------------

    async def authenticate_request(self, request: Request) -> User | None:
        """Resolve the session cookie to a :class:`User`, or ``None``.

        ``None`` (anonymous) is returned for a missing / unknown / expired
        cookie and for a soft-deleted account; the auth gate turns that
        into a 401. Extends the session's sliding expiry lazily.
        """
        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        if not cookie:
            return None
        token_hash = _hash(cookie)
        now = self._now()
        async with self._session_factory() as session:
            session_row = (
                await session.execute(select(SessionRow).where(SessionRow.token_hash == token_hash))
            ).scalar_one_or_none()
            if session_row is None:
                return None
            if _aware(session_row.expires_at) < now:
                return None
            user_row = (
                await session.execute(select(UserRow).where(UserRow.id == session_row.user_id))
            ).scalar_one_or_none()
            if user_row is None or user_row.deleted_at is not None:
                return None

            # Sliding expiry, throttled so a busy session isn't a write per
            # request.
            if now - _aware(session_row.last_used_at) > SESSION_TOUCH_INTERVAL:
                session_row.last_used_at = now
                session_row.expires_at = now + SESSION_TTL
                await session.commit()

            return User(id=user_row.id, email=user_row.email, display_name=user_row.display_name)

    async def end_session(self, session_secret: str) -> None:
        """Revoke the session identified by ``session_secret`` (logout).

        Idempotent: an unknown / already-revoked secret is a no-op.
        """
        token_hash = _hash(session_secret)
        async with self._session_factory() as session:
            session_row = (
                await session.execute(select(SessionRow).where(SessionRow.token_hash == token_hash))
            ).scalar_one_or_none()
            if session_row is None:
                return
            await session.delete(session_row)
            await session.commit()


def _aware(value: datetime) -> datetime:
    """Coerce a possibly-naive timestamp (SQLite drops tzinfo) to UTC-aware
    so comparisons against :func:`_utcnow` don't raise."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
