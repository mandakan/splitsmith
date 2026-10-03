"""Access-request intake and account-tier admin stores (spec 2026-10-03).

Two small stores that sit on top of the ``access_requests`` / ``users``
tables (Task 2's models):

- :class:`AccessRequestStore` is the write path for the public intake
  form and the magic-link sign-in attempt of an email with no account,
  plus the admin actions that decide a pending request.
- :class:`AccountAccessStore` is the admin read/write path over existing
  accounts' ``access_tier`` column.

Both are DB-layer only -- no server.py wiring here. The race-safe
patterns (conditional ``UPDATE`` + ``rowcount``; ``begin_nested`` +
``IntegrityError`` re-select) mirror ``splitsmith.db.magic_link``'s
``complete_login``, for the same reason: two concurrent callers can both
pass a read-then-check, and only the DB's row lock on the UPDATE/INSERT
can serialise them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import AccessRequest, User

# Notes longer than this are truncated before storing -- an admin reads
# these in a list view, not a document.
_NOTE_MAX_LENGTH = 500


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


class AlreadyDecidedError(Exception):
    """The request is no longer pending-or-declined (it was approved)."""


class NotFoundError(Exception):
    """No row exists for the given id."""


class AccessRequestView(BaseModel):
    id: str
    email: str
    note: str | None
    source: str
    status: str
    requested_at: datetime
    last_requested_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    tier_granted: str | None
    email_sent_at: datetime | None


class AccountView(BaseModel):
    id: str
    email: str
    display_name: str | None
    access_tier: str
    created_at: datetime


def _request_view(row: AccessRequest) -> AccessRequestView:
    return AccessRequestView(
        id=row.id,
        email=row.email,
        note=row.note,
        source=row.source,
        status=row.status,
        requested_at=row.requested_at,
        last_requested_at=row.last_requested_at,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
        tier_granted=row.tier_granted,
        email_sent_at=row.email_sent_at,
    )


def _account_view(row: User) -> AccountView:
    return AccountView(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        access_tier=row.access_tier,
        created_at=row.created_at,
    )


class AccessRequestStore:
    """Intake + admin-decision store over ``access_requests``."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._now = now

    async def record(self, email: str, *, source: str, note: str | None = None) -> bool:
        """Note that ``email`` wants access. Returns ``True`` only when a
        new pending row was created.

        An email that already has a ``User`` account writes nothing --
        it isn't missing access, so there is nothing to request.
        """
        normalized = _normalize_email(email)
        trimmed_note = note[:_NOTE_MAX_LENGTH] if note is not None else None
        now = self._now()

        async with self._session_factory() as session:
            has_account = (
                await session.execute(select(User.id).where(User.email == normalized))
            ).scalar_one_or_none()
            if has_account is not None:
                return False

            existing = (
                await session.execute(select(AccessRequest).where(AccessRequest.email == normalized))
            ).scalar_one_or_none()
            if existing is None:
                # Race-safe insert: two concurrent first requests for the
                # same new email can both reach here having seen no row.
                # The loser's INSERT hits the unique constraint on
                # ``email``; catch it and fall through to the bump path
                # below instead of raising.
                try:
                    async with session.begin_nested():
                        row = AccessRequest(
                            email=normalized,
                            note=trimmed_note,
                            source=source,
                            status="pending",
                            requested_at=now,
                            last_requested_at=now,
                        )
                        session.add(row)
                        await session.flush()
                    await session.commit()
                    return True
                except IntegrityError:
                    existing = (
                        await session.execute(select(AccessRequest).where(AccessRequest.email == normalized))
                    ).scalar_one()

            # A row already existed (or we lost the insert race against
            # it): bump the repeat-request timestamp, fill the note only
            # if it was never set, and never touch status/source -- the
            # original request's provenance and any decision stand.
            existing.last_requested_at = now
            if existing.note is None and trimmed_note is not None:
                existing.note = trimmed_note
            await session.commit()
            return False

    async def list(self, status: str | None = None) -> list[AccessRequestView]:
        async with self._session_factory() as session:
            stmt = select(AccessRequest)
            if status is not None:
                stmt = stmt.where(AccessRequest.status == status)
            rows = (await session.execute(stmt)).scalars().all()

        # Pending first, then most-recently-requested first. Two stable
        # sorts rather than one composite key: the first orders every row
        # newest-first, the second (stable) sort then pulls pending rows
        # to the front without disturbing that order within each group.
        ordered = sorted(rows, key=lambda r: r.last_requested_at, reverse=True)
        ordered = sorted(ordered, key=lambda r: 0 if r.status == "pending" else 1)
        return [_request_view(row) for row in ordered]

    async def get(self, request_id: str) -> AccessRequestView:
        async with self._session_factory() as session:
            row = (
                await session.execute(select(AccessRequest).where(AccessRequest.id == request_id))
            ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(request_id)
        return _request_view(row)

    async def approve(self, request_id: str, *, tier: str, admin_email: str) -> AccessRequestView:
        return await self._decide(
            request_id,
            admin_email=admin_email,
            tier_granted=tier,
            new_status="approved",
        )

    async def decline(self, request_id: str, *, admin_email: str) -> AccessRequestView:
        return await self._decide(
            request_id,
            admin_email=admin_email,
            tier_granted=None,
            new_status="declined",
            allowed_from=("pending",),
        )

    async def _decide(
        self,
        request_id: str,
        *,
        admin_email: str,
        tier_granted: str | None,
        new_status: str,
        allowed_from: tuple[str, ...] = ("pending", "declined"),
    ) -> AccessRequestView:
        now = self._now()
        async with self._session_factory() as session:
            row = (
                await session.execute(select(AccessRequest).where(AccessRequest.id == request_id))
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError(request_id)

            # Race-safe status flip: a conditional UPDATE guarded on the
            # row still being in an allowed status, checked by rowcount --
            # same pattern as ``complete_login``'s token consumption, so
            # two concurrent decisions on the same request can't both win.
            result = await session.execute(
                update(AccessRequest)
                .where(
                    AccessRequest.id == request_id,
                    AccessRequest.status.in_(allowed_from),
                )
                .values(
                    status=new_status,
                    decided_at=now,
                    decided_by=admin_email,
                    tier_granted=tier_granted,
                )
            )
            if result.rowcount == 0:
                raise AlreadyDecidedError(request_id)

            if new_status == "approved":
                user_row = (
                    await session.execute(select(User).where(User.email == row.email))
                ).scalar_one_or_none()
                if user_row is None:
                    # Same race-safe create as ``complete_login``'s first
                    # sign-in: a concurrent sign-in could create the user
                    # between our check and our insert.
                    try:
                        async with session.begin_nested():
                            user_row = User(email=row.email, access_tier=tier_granted)
                            session.add(user_row)
                            await session.flush()
                    except IntegrityError:
                        user_row = (
                            await session.execute(select(User).where(User.email == row.email))
                        ).scalar_one()
                        user_row.access_tier = tier_granted
                else:
                    user_row.access_tier = tier_granted

            await session.commit()
            refreshed = (
                await session.execute(select(AccessRequest).where(AccessRequest.id == request_id))
            ).scalar_one()
            return _request_view(refreshed)

    async def mark_email_sent(self, request_id: str) -> None:
        now = self._now()
        async with self._session_factory() as session:
            result = await session.execute(
                update(AccessRequest).where(AccessRequest.id == request_id).values(email_sent_at=now)
            )
            if result.rowcount == 0:
                raise NotFoundError(request_id)
            await session.commit()


class AccountAccessStore:
    """Admin read/write store over accounts' ``access_tier``."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def list(self) -> list[AccountView]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(User).where(User.deleted_at.is_(None)).order_by(User.created_at)
                    )
                )
                .scalars()
                .all()
            )
        return [_account_view(row) for row in rows]

    async def set_tier(self, user_id: str, tier: str) -> AccountView:
        async with self._session_factory() as session:
            row = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if row is None:
                raise NotFoundError(user_id)
            row.access_tier = tier
            await session.commit()
            await session.refresh(row)
            return _account_view(row)
