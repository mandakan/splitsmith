"""Postgres-backed :class:`splitsmith.whats_new.WhatsNewStore`: the account's
seen set in ``users.whats_new_seen``.

``users`` is not under RLS; every statement filters on ``User.id ==
self._user_id``. ``tests/test_whats_new.py`` pins per-user isolation.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import User


class PostgresWhatsNewStore:
    def __init__(self, session_factory: async_sessionmaker, *, user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "PostgresWhatsNewStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    async def get(self) -> list[str] | None:
        async with self._session_factory() as session:
            seen = (
                await session.execute(select(User.whats_new_seen).where(User.id == self._user_id))
            ).scalar_one_or_none()
        return list(seen) if seen is not None else None

    async def add(self, ids: list[str]) -> list[str]:
        async with self._session_factory() as session:
            user = (await session.execute(select(User).where(User.id == self._user_id))).scalar_one_or_none()
            if user is None:
                raise LookupError(f"User {self._user_id!r} not found")
            seen = sorted(set(user.whats_new_seen or []) | set(ids))
            user.whats_new_seen = seen
            await session.commit()
        return seen
