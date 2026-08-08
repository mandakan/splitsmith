"""Browser-assisted device authorization (#719).

``DeviceAuthStore`` owns the whole device-code state machine: mint an
authorization, show it to the approving browser, record the decision, and
mint the scoped ``desktop_tokens`` row on the first poll that collects an
approval.

It takes the RAW (non-tenant) session factory, same rationale as
``DesktopTokenAuth`` and ``WorkersStore``: the polling request
authenticates from the device code alone, before any ``app.user_id`` GUC
exists, so every query here runs pre-tenant. The two session-authenticated
methods (``pending`` / ``decide``) take the approver's ``user_id`` as an
explicit argument rather than relying on a tenant being pinned, so the one
factory serves both halves of the flow.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import DesktopTokenRow, DeviceAuthorizationRow
from .models import User as UserRow
from .workers import _hash, _mint

#: The user-code alphabet: A-Z and 2-9 with I, L, O, U, 0 and 1 removed -
#: the characters people mistype when reading a code off one screen and
#: typing it into another. 30 symbols, 8 characters, ~39 bits. Low on
#: purpose: the code is only usable by a caller who already holds a
#: session and who then has to approve, and it dies in ``ttl_seconds``.
#: The real secret is the device code.
_USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
_USER_CODE_LENGTH = 8

#: How many times ``authorize`` retries on a user-code collision before
#: giving up. Collisions are vanishingly unlikely at this alphabet size
#: with a 10-minute window; the retry exists so a collision is a slow
#: request rather than a 500.
_USER_CODE_ATTEMPTS = 5


def format_user_code(raw: str) -> str:
    """Render 8 raw characters as ``XXXX-XXXX``."""
    return f"{raw[:4]}-{raw[4:]}"


def normalize_user_code(raw: str) -> str:
    """Canonicalize whatever the user typed into the stored form.

    Uppercases, drops everything that is not in the alphabet (spaces, the
    hyphen the user may or may not have typed), and re-inserts the hyphen.
    Anything that does not reduce to exactly 8 alphabet characters is
    returned as-is so the lookup simply misses -- callers treat a miss and
    a malformed code identically (404), so there is nothing to gain from
    distinguishing them here.
    """
    cleaned = "".join(c for c in raw.upper() if c in _USER_CODE_ALPHABET)
    if len(cleaned) != _USER_CODE_LENGTH:
        return raw.strip().upper()
    return format_user_code(cleaned)


class DeviceAuthRequest(BaseModel):
    """What ``authorize`` hands back to the desktop install.

    ``device_code`` appears here and only here - the row keeps its hash.
    """

    device_code: str
    user_code: str
    expires_in: int
    interval: int


class DevicePending(BaseModel):
    """What the approval screen shows about a live authorization."""

    user_code: str
    device_name: str
    scope: str
    created_at: datetime
    expires_at: datetime


class DeviceAccount(BaseModel):
    """The account a poll resolved to. No secrets, no admin flag."""

    id: str
    email: str
    display_name: str | None = None


class DevicePollResult(BaseModel):
    """One poll verdict.

    ``status`` is one of ``pending``, ``slow_down``, ``denied``,
    ``expired`` or ``approved``. Only ``approved`` carries ``token`` /
    ``account`` / ``device_name``, and only once - a second poll on the
    same device code reports ``expired``.
    """

    status: str
    token: str | None = None
    account: DeviceAccount | None = None
    device_name: str | None = None


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat them as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_code_collision(exc: IntegrityError) -> bool:
    """True if ``exc`` looks like the one thing ``authorize`` retries for:
    a unique-constraint collision on ``user_code`` or ``device_code_hash``.

    SQLite reports ``UNIQUE constraint failed: device_authorizations.user_code``;
    Postgres reports the constraint name, which is
    ``uq_device_authorizations_user_code`` /
    ``uq_device_authorizations_device_code_hash`` (migration 0c1dbb2ce678).
    Both forms contain the column name as a substring, so a plain text scan
    is portable across the two backends this project runs against. Anything
    that doesn't match is a real bug, not a collision, and must not be
    retried five times and then reported as a misleading "could not
    allocate a unique code".
    """
    text = str(exc.orig or exc).lower()
    return "user_code" in text or "device_code_hash" in text


class DeviceAuthStore:
    """The device-flow state machine over ``device_authorizations``."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        ttl_seconds: int = 600,
        interval_seconds: int = 5,
    ) -> None:
        self._session_factory = session_factory
        self._ttl_seconds = ttl_seconds
        self._interval_seconds = interval_seconds

    async def authorize(self, device_name: str, *, scope: str = "sync") -> DeviceAuthRequest:
        """Create a pending authorization and return its two codes."""
        expires_at = datetime.now(UTC) + timedelta(seconds=self._ttl_seconds)
        for _ in range(_USER_CODE_ATTEMPTS):
            # Fresh device_code AND user_code on every attempt: the table
            # has two independent unique constraints
            # (uq_device_authorizations_device_code_hash,
            # uq_device_authorizations_user_code -- see migration
            # 0c1dbb2ce678), and only regenerating both makes a retry able
            # to clear either collision. Regenerating just the user_code
            # left a device_code_hash collision unretryable.
            plain, hashed = _mint()
            user_code = format_user_code(
                "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(_USER_CODE_LENGTH))
            )
            row = DeviceAuthorizationRow(
                device_code_hash=hashed,
                user_code=user_code,
                device_name=device_name,
                scope=scope,
                status="pending",
                expires_at=expires_at,
            )
            try:
                async with self._session_factory() as session:
                    session.add(row)
                    await session.commit()
            except IntegrityError as exc:
                if not _is_code_collision(exc):
                    # A NOT NULL violation, a bad FK, or anything else that
                    # isn't the one thing this loop knows how to fix by
                    # retrying with fresh random values. Re-raise instead
                    # of burning all _USER_CODE_ATTEMPTS on an error a
                    # retry cannot clear and then masking it behind the
                    # generic RuntimeError below.
                    raise
                continue
            return DeviceAuthRequest(
                device_code=plain,
                user_code=user_code,
                expires_in=self._ttl_seconds,
                interval=self._interval_seconds,
            )
        raise RuntimeError("could not allocate a unique device user code")

    async def pending(self, user_code: str) -> DevicePending | None:
        """The approval screen's view, or ``None`` if there is nothing to
        approve (unknown code, already decided, or expired)."""
        normalized = normalize_user_code(user_code)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DeviceAuthorizationRow).where(DeviceAuthorizationRow.user_code == normalized)
                )
            ).scalar_one_or_none()
            if row is None or row.status != "pending":
                return None
            if _aware(row.expires_at) <= datetime.now(UTC):
                return None
            return DevicePending(
                user_code=row.user_code,
                device_name=row.device_name,
                scope=row.scope,
                created_at=_aware(row.created_at),
                expires_at=_aware(row.expires_at),
            )

    async def decide(self, user_code: str, *, user_id: str, approved: bool) -> bool:
        """Record the browser's decision. ``False`` when there was nothing
        live to decide on, which the route turns into a 404.

        Symmetric with ``poll``'s conditional ``approved -> consumed``
        update: two concurrent decisions on the same user_code must not
        let the second writer's commit silently overwrite the first's
        under READ COMMITTED (an approve stomping a deny, both callers
        seeing ``True``). The conditional ``pending -> approved|denied``
        update is the guard; only the writer whose UPDATE actually matched
        a still-pending row gets ``True``.
        """
        normalized = normalize_user_code(user_code)
        now = datetime.now(UTC)
        new_status = "approved" if approved else "denied"
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DeviceAuthorizationRow).where(DeviceAuthorizationRow.user_code == normalized)
                )
            ).scalar_one_or_none()
            if row is None or row.status != "pending" or _aware(row.expires_at) <= now:
                return False
            result = await session.execute(
                update(DeviceAuthorizationRow)
                .where(
                    DeviceAuthorizationRow.id == row.id,
                    DeviceAuthorizationRow.status == "pending",
                )
                .values(status=new_status, user_id=user_id)
            )
            if result.rowcount != 1:
                await session.rollback()
                return False
            await session.commit()
            return True

    async def poll(self, device_code: str) -> DevicePollResult:
        """One poll from the desktop install.

        An unknown device code reports ``expired``, identically to a real
        one that ran out - a caller must not be able to probe for which
        codes exist.
        """
        hashed = _hash(device_code)
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DeviceAuthorizationRow).where(DeviceAuthorizationRow.device_code_hash == hashed)
                )
            ).scalar_one_or_none()
            if row is None or row.status == "consumed":
                return DevicePollResult(status="expired")
            if _aware(row.expires_at) <= now:
                return DevicePollResult(status="expired")
            if row.status == "denied":
                return DevicePollResult(status="denied")
            if row.status == "pending":
                last = row.last_polled_at
                too_soon = last is not None and (now - _aware(last)).total_seconds() < self._interval_seconds
                if too_soon:
                    return DevicePollResult(status="slow_down")
                row.last_polled_at = now
                await session.commit()
                return DevicePollResult(status="pending")

            # status == "approved": the conditional update is the whole
            # concurrency story. Two simultaneous polls both read
            # "approved"; only one of them touches a row here, and only
            # that one mints a token. The loser falls through to expired.
            result = await session.execute(
                update(DeviceAuthorizationRow)
                .where(
                    DeviceAuthorizationRow.id == row.id,
                    DeviceAuthorizationRow.status == "approved",
                )
                .values(status="consumed")
            )
            if result.rowcount != 1:
                await session.rollback()
                return DevicePollResult(status="expired")
            user_row = (
                await session.execute(select(UserRow).where(UserRow.id == row.user_id))
            ).scalar_one_or_none()
            if user_row is None or user_row.deleted_at is not None:
                await session.commit()  # keep it consumed; the account is gone
                return DevicePollResult(status="expired")
            plain, token_hash = _mint()
            session.add(
                DesktopTokenRow(
                    user_id=user_row.id,
                    name=row.device_name,
                    token_hash=token_hash,
                    scope=row.scope,
                )
            )
            await session.commit()
            return DevicePollResult(
                status="approved",
                token=plain,
                account=DeviceAccount(
                    id=user_row.id,
                    email=user_row.email,
                    display_name=user_row.display_name,
                ),
                device_name=row.device_name,
            )

    async def revoke_token(self, token: str) -> bool:
        """Revoke the ``desktop_tokens`` row a raw token resolves to.

        Backs ``DELETE /api/device/session`` - the one route a sync-scoped
        token may reach outside ``/api/sync/*``, so the local install can
        sign itself out without holding a session cookie. Returns ``False``
        only for an unknown token; an already-revoked token still returns
        ``True`` (its ``revoked_at`` is left untouched, not re-stamped) -
        the route reports success either way, but ``False`` deliberately
        stays reserved for "this token never existed" rather than doubling
        as an existence oracle for "already revoked".
        """
        hashed = _hash(token)
        async with self._session_factory() as session:
            row = (
                await session.execute(select(DesktopTokenRow).where(DesktopTokenRow.token_hash == hashed))
            ).scalar_one_or_none()
            if row is None:
                return False
            if row.revoked_at is None:
                row.revoked_at = datetime.now(UTC)
                await session.commit()
            return True
