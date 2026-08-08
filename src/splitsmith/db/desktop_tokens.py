"""Desktop-to-hosted sync bearer tokens (doc 2026-08-07, #631).

``DesktopTokenStore`` is the owner-side management surface (create / list /
revoke), constructed per-request with the resolved user id, mirroring
``ShareTokenStore``. ``DesktopTokenAuth`` is the ``AuthBackend`` that
resolves a bearer token on an incoming request to the owning ``User`` -
it takes the RAW (non-tenant) session factory because ``desktop_tokens``
is not under RLS (see ``DesktopTokenRow`` docstring): a sync-push request
authenticates from the bearer token alone, before any ``app.user_id`` GUC
exists.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..auth import User
from .models import DesktopTokenRow
from .models import User as UserRow
from .workers import _hash, _mint


class DesktopTokenRecord(BaseModel):
    """Owner-facing view of a desktop token row. Never carries the hash."""

    id: str
    name: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


def _to_record(row: DesktopTokenRow) -> DesktopTokenRecord:
    return DesktopTokenRecord(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


class DesktopTokenStore:
    """Owner-scoped view of ``desktop_tokens``.

    Multi-tenant invariant: every statement filters on
    ``DesktopTokenRow.user_id == self._user_id``. The table itself is not
    under RLS (resolved pre-tenant by ``DesktopTokenAuth``), so this
    per-method filter is the isolation boundary for the management surface.
    """

    def __init__(self, session_factory: async_sessionmaker, *, user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "DesktopTokenStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    async def create(self, name: str, *, scope: str = "sync") -> tuple[DesktopTokenRecord, str]:
        """Mint a new token. Returns (record, raw_token) - the raw value
        is only ever available here; only its hash is persisted.

        ``scope`` defaults to ``"sync"`` (#719): every token minted after
        that issue lands is scoped to ``/api/sync/*``, whether it came
        from the device flow or the account page's manual button. The
        only ``'full'`` tokens that will ever exist are the ones already
        issued before the column landed.
        """
        plain, hashed = _mint()
        row = DesktopTokenRow(user_id=self._user_id, name=name, token_hash=hashed, scope=scope)
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_record(row), plain

    async def list(self) -> list[DesktopTokenRecord]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(DesktopTokenRow)
                    .where(DesktopTokenRow.user_id == self._user_id)
                    .order_by(DesktopTokenRow.created_at.desc(), DesktopTokenRow.id.desc())
                )
            ).scalars()
            return [_to_record(r) for r in rows]

    async def revoke(self, token_id: str) -> bool:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DesktopTokenRow).where(
                        DesktopTokenRow.user_id == self._user_id,
                        DesktopTokenRow.id == token_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            if row.revoked_at is None:
                row.revoked_at = datetime.now(UTC)
                await session.commit()
            return True


class DesktopTokenAuth:
    """``AuthBackend`` that resolves ``Authorization: Bearer <token>``.

    Holds the RAW (non-tenant) session factory - same rationale as
    ``MagicLinkAuth`` and ``WorkersStore.authenticate`` - identity must be
    resolved before any per-tenant GUC can be set.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def authenticate_request(self, request: Request) -> User | None:
        scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
        token = bearer.strip()
        if scheme.lower() != "bearer" or not token:
            return None
        hashed = _hash(token)
        async with self._session_factory() as session:
            row = (
                await session.execute(select(DesktopTokenRow).where(DesktopTokenRow.token_hash == hashed))
            ).scalar_one_or_none()
            if row is None or row.revoked_at is not None:
                return None
            user_row = (
                await session.execute(select(UserRow).where(UserRow.id == row.user_id))
            ).scalar_one_or_none()
            if user_row is None or user_row.deleted_at is not None:
                return None
            row.last_used_at = datetime.now(UTC)
            await session.commit()
            return User(id=user_row.id, email=user_row.email, display_name=user_row.display_name)
