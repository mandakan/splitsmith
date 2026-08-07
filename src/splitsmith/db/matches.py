"""Postgres-backed per-user match registry (PR-delta).

The desktop flow resolves a ``match_id`` to an on-disk path by scanning
the local ``projects.json`` (:class:`splitsmith.match_registry.MatchRegistry`).
A separate hosted worker process has no such file, so it needs a
queryable ``(user_id, match_id) -> storage_prefix`` mapping to find a
match it never opened locally. This store is that mapping.

Constructed per-request (API) / per-process (worker) with the resolved
user id, mirroring doc 10's "no AppState singleton; bind on the request"
rule. The ``sessionmaker`` is shared (created once at boot); each call
opens its own session so concurrent requests don't share an
:class:`AsyncSession`.

Engine-agnostic: tests use ``sqlite+aiosqlite:///:memory:``; production
uses ``postgresql+asyncpg://``. Same SQLAlchemy 2.x query shapes work on
both.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import MatchRow


class MatchRecord(BaseModel):
    """Pydantic view of a :class:`MatchRow` returned across the store boundary.

    ``origin`` distinguishes a natively-created hosted match ("hosted")
    from one mirrored down by a desktop-to-hosted sync push ("desktop",
    doc 2026-08-07). It is set once at INSERT time and ``upsert`` never
    changes it on an existing row - see :meth:`PostgresMatchStore.upsert`.
    """

    model_config = ConfigDict(from_attributes=True)

    match_id: str
    name: str
    storage_prefix: str
    origin: str
    created_at: datetime
    updated_at: datetime


class PostgresMatchStore:
    """Per-user view of the ``matches`` table.

    **Multi-tenant invariant:** every SQL statement issued by this store
    includes ``MatchRow.user_id == self._user_id`` in its WHERE clause.
    Two users registering the same ``match_id`` own disjoint rows (the
    table's ``(user_id, match_id)`` unique constraint enforces the
    boundary at the DB layer; the per-method filter enforces it at the
    query layer). Tests in ``test_matches_store.py`` guard the invariant
    -- if you add a method here, add an isolation test for it too.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        user_id: str,
    ) -> None:
        # Defence-in-depth: a bug elsewhere that lets ``None`` /
        # empty-string through the auth layer would silently scope every
        # query to "no rows" rather than leak across tenants -- but that
        # silence is its own bug. Fail loud at construction, same as
        # :class:`PostgresRecentProjectsStore`.
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "PostgresMatchStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    async def upsert(
        self, match_id: str, name: str, storage_prefix: str, *, origin: str = "hosted"
    ) -> MatchRecord:
        """Insert or refresh the row for ``(user_id, match_id)``.

        Idempotent: re-registering an existing match updates its name +
        storage_prefix and bumps ``updated_at`` instead of inserting a
        duplicate (the unique constraint would reject one anyway).

        ``origin`` is only applied on INSERT. An existing row's ``origin``
        is never changed by a later upsert - a routine re-registration
        (e.g. the hosted UI resaving a match's name) must not reclassify a
        match that a desktop sync push originally created, or vice versa.
        """
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(MatchRow).where(
                        MatchRow.user_id == self._user_id,
                        MatchRow.match_id == match_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return await self._apply_update(session, existing, name, storage_prefix)
            row = MatchRow(
                user_id=self._user_id,
                match_id=match_id,
                name=name,
                storage_prefix=storage_prefix,
                origin=origin,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # A concurrent first-open inserted the same (user_id,
                # match_id) between our SELECT and INSERT (the SELECT-then-
                # INSERT is not atomic; both coroutines saw None). The
                # uq_matches_user_match constraint rejected our row. Roll
                # back and apply as an update so the open path doesn't 500.
                await session.rollback()
                existing = (
                    await session.execute(
                        select(MatchRow).where(
                            MatchRow.user_id == self._user_id,
                            MatchRow.match_id == match_id,
                        )
                    )
                ).scalar_one()
                return await self._apply_update(session, existing, name, storage_prefix)
            await session.refresh(row)
            return MatchRecord.model_validate(row)

    @staticmethod
    async def _apply_update(session, row: MatchRow, name: str, storage_prefix: str) -> MatchRecord:
        row.name = name
        row.storage_prefix = storage_prefix
        row.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(row)
        return MatchRecord.model_validate(row)

    async def get(self, match_id: str) -> MatchRow | None:
        """Return the user's match row for ``match_id``, or ``None``."""
        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(MatchRow).where(
                        MatchRow.user_id == self._user_id,
                        MatchRow.match_id == match_id,
                    )
                )
            ).scalar_one_or_none()

    async def delete(self, match_id: str) -> bool:
        """Drop the user's row for ``match_id``; return ``True`` if one went.

        Used by the project-delete cascade. Idempotent: deleting an
        already-gone match returns ``False`` without error. Mirrors
        :meth:`PostgresRecentProjectsStore.remove`.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                delete(MatchRow).where(
                    MatchRow.user_id == self._user_id,
                    MatchRow.match_id == match_id,
                )
            )
            await session.commit()
            return (result.rowcount or 0) > 0

    async def list(self) -> list[MatchRow]:
        """Return all of the user's matches, newest first."""
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(MatchRow)
                        .where(MatchRow.user_id == self._user_id)
                        .order_by(MatchRow.updated_at.desc())
                    )
                )
                .scalars()
                .all()
            )
        return list(rows)
