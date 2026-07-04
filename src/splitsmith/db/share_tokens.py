"""Per-user share-token store + anonymous resolver (public share links, #349).

``ShareTokenStore`` is the owner-side management surface, constructed
per-request with the resolved user id like ``PostgresMatchStore``.
``resolve_share_token`` is the anonymous path: it takes the raw
(non-tenant) session factory because share_tokens is not under RLS -
the unique-token lookup is the isolation boundary, resolved before any
``app.user_id`` GUC exists (sessions precedent).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import ShareTokenRow


def _aware(value: datetime) -> datetime:
    """Coerce a possibly-naive timestamp (SQLite drops tzinfo) to UTC-aware
    so comparisons against datetime.now(UTC) don't raise."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@dataclass(frozen=True)
class ShareToken:
    id: str
    match_id: str
    token: str
    created_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class ResolvedShare:
    owner_user_id: str
    match_id: str


def _to_share_token(row: ShareTokenRow) -> ShareToken:
    return ShareToken(
        id=row.id,
        match_id=row.match_id,
        token=row.token,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


class ShareTokenStore:
    """Owner-scoped view of ``share_tokens``.

    Multi-tenant invariant: every statement filters on
    ``ShareTokenRow.user_id == self._user_id``. Isolation tests in
    ``test_share_tokens_store.py`` guard it - add one per new method.
    """

    def __init__(self, session_factory: async_sessionmaker, *, user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "ShareTokenStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    async def create(self, match_id: str) -> ShareToken:
        row = ShareTokenRow(
            user_id=self._user_id,
            match_id=match_id,
            token=secrets.token_urlsafe(32),
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_share_token(row)

    async def list_for_match(self, match_id: str) -> list[ShareToken]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(ShareTokenRow)
                    .where(
                        ShareTokenRow.user_id == self._user_id,
                        ShareTokenRow.match_id == match_id,
                    )
                    .order_by(ShareTokenRow.created_at.desc(), ShareTokenRow.id.desc())
                )
            ).scalars()
            return [_to_share_token(r) for r in rows]

    async def revoke(self, share_id: str, *, match_id: str) -> bool:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ShareTokenRow).where(
                        ShareTokenRow.user_id == self._user_id,
                        ShareTokenRow.id == share_id,
                        ShareTokenRow.match_id == match_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            if row.revoked_at is None:
                row.revoked_at = datetime.now(UTC)
                await session.commit()
            return True


async def resolve_share_token(session_factory: async_sessionmaker, token: str) -> ResolvedShare | None:
    """Resolve a raw share token to its owner + match, or None.

    None covers missing, revoked, and expired alike - callers must not be
    able to distinguish them (uniform 404 at the HTTP layer).
    """
    if not token:
        return None
    async with session_factory() as session:
        row = (
            await session.execute(select(ShareTokenRow).where(ShareTokenRow.token == token))
        ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at is not None:
        if _aware(row.expires_at) < datetime.now(UTC):
            return None
    return ResolvedShare(owner_user_id=row.user_id, match_id=row.match_id)
