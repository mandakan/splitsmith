"""Postgres-backed :class:`splitsmith.look_store.LookStore` (issue #1263).

Hosted-mode counterpart to :class:`splitsmith.look_store.FolderLookStore`:
one row per (user, name) in ``user_looks`` holding a
:class:`~splitsmith.look_store.StoredLookBody`, never a template. A hosted
Look may not take a shipped Look's name, and its base must be a shipped
Look, so a shipped name always means the same thing on the server.

**Multi-tenant invariant:** every statement filters on
``UserLookRow.user_id == self._user_id``; the composite primary key and
the ``tenant_isolation`` RLS policy hold the same line at the DB layer.
``tests/test_look_store_db.py`` has an isolation test per method.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..async_bridge import run_sync
from ..fonts import own_file
from ..look_store import (
    LookStoreError,
    StoredLook,
    StoredLookBody,
    check_name,
    is_shipped_name,
    materialize,
)
from ..looks import DEFAULT_LOOK, load_look, variants_for
from .models import UserLookRow


def _check_hosted(name: str, body: StoredLookBody) -> None:
    check_name(name)
    if is_shipped_name(name):
        raise LookStoreError(f"{name!r} is a shipped Look; pick another name")
    if body.base is not None and not is_shipped_name(body.base):
        raise LookStoreError(f"base {body.base!r} is not a shipped Look")
    if body.brand is not None and body.brand.logo:
        raise LookStoreError("brand: a Look's own logo file is desktop only for now; the brand line is fine")
    if any(own_file(value) is not None for value in body.fonts.values()):
        raise LookStoreError("fonts: a Look's own font file is desktop only for now; pick a bundled face")
    base = load_look(body.base or DEFAULT_LOOK)
    for slot, variant in body.styles.items():
        if variant not in variants_for(base, slot):
            raise LookStoreError(
                f"styles: {slot} has no {variant!r} style; it has {', '.join(variants_for(base, slot))}"
            )


class PostgresLookStore:
    def __init__(self, session_factory: async_sessionmaker, *, user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "PostgresLookStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id
        self._materialized: Path | None = None

    @staticmethod
    def _stored(row: UserLookRow) -> StoredLook:
        return StoredLook(
            name=row.name, updated_at=row.updated_at, body=StoredLookBody.model_validate(row.body)
        )

    async def list(self) -> list[StoredLook]:
        async with self._session_factory() as session:
            stmt = select(UserLookRow).where(UserLookRow.user_id == self._user_id)
            rows = (await session.execute(stmt)).scalars().all()
        return sorted((self._stored(row) for row in rows), key=lambda s: s.name)

    async def get(self, name: str) -> StoredLook | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(UserLookRow).where(UserLookRow.user_id == self._user_id, UserLookRow.name == name)
                )
            ).scalar_one_or_none()
        return None if row is None else self._stored(row)

    async def put(self, name: str, body: StoredLookBody) -> StoredLook:
        _check_hosted(name, body)
        now = datetime.now(UTC)
        data = body.model_dump(mode="json")
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(UserLookRow).where(UserLookRow.user_id == self._user_id, UserLookRow.name == name)
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(UserLookRow(user_id=self._user_id, name=name, body=data, updated_at=now))
            else:
                existing.body = data
                existing.updated_at = now
            await session.commit()
        self._materialized = None
        return StoredLook(name=name, updated_at=now, body=body)

    async def delete(self, name: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(UserLookRow).where(UserLookRow.user_id == self._user_id, UserLookRow.name == name)
            )
            await session.commit()
        self._materialized = None

    def materialized_dir(self, cache_root: Path) -> Path:
        """This account's Looks as a Looks folder under
        ``cache_root/<user_id>/`` (see :func:`look_store.materialize`), for
        ``looks.set_user_looks_provider``. Read once per store (one request
        or job); a write through this store reads again."""
        if self._materialized is None:
            self._materialized = materialize(run_sync(self.list()), cache_root / self._user_id)
        return self._materialized
