"""Postgres-backed :class:`RecentProjectsStore` (doc 10, Tier 3).

Hosted-mode counterpart to :class:`splitsmith.user_config.JsonRecentProjectsStore`.
The Json impl writes ``~/.splitsmith/projects.json`` -- per-machine state on
the operator's laptop. This one writes a row per (user, path) to
``recent_projects`` -- per-account state shared across whichever browsers
the same hosted user signs into.

The store is constructed per-request with the resolved user id, mirroring
doc 10's "no AppState singleton; bind on the request" rule. The
``sessionmaker`` is shared (created once at boot); each call opens its
own session so concurrent requests don't share an :class:`AsyncSession`.

The store is engine-agnostic. Tests use ``sqlite+aiosqlite:///:memory:``;
production uses ``postgresql+asyncpg://``. The same SQLAlchemy 2.x query
shapes work on both.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..user_config import RECENT_PROJECTS_LIMIT, RecentProject
from .models import RecentProjectRow


class PostgresRecentProjectsStore:
    """:class:`RecentProjectsStore` backed by the ``recent_projects`` table.

    The class name says ``Postgres`` to match the doc's hosted-mode
    naming, but the implementation is engine-agnostic -- SQLAlchemy
    handles dialect differences. SQLite is what the test suite runs
    against; asyncpg + Postgres is what hosted-mode wires up.

    **Multi-tenant invariant:** every SQL statement issued by this
    store includes ``RecentProjectRow.user_id == self._user_id`` in
    its WHERE clause. Two users opening the same path own disjoint
    rows (the table's ``(user_id, path)`` unique constraint enforces
    the boundary at the DB layer; the per-method filter enforces it
    at the query layer). Tests in ``test_recent_projects_store.py``
    guard the invariant -- if you add a new method here, add an
    isolation test for it too.

    A "recently opened" list is personal state, so this table is
    deliberately per-user and stays that way even when matches
    themselves become shareable. Sharing happens at the (future)
    ``projects`` + ``project_members`` layer that doc 02 plans;
    when that lands, ``path`` here may grow a sibling ``project_id``
    column so a shared project shows up in each member's picker
    without duplicating the underlying record.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        user_id: str,
    ) -> None:
        # Defence-in-depth: a bug elsewhere that lets ``None`` /
        # empty-string through the auth layer would silently yield
        # an empty recent-projects list (the WHERE clause wouldn't
        # match any row) rather than leak across tenants -- but
        # that silence is its own bug. Fail loud at construction.
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "PostgresRecentProjectsStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    async def list(self) -> list[RecentProject]:
        """Return the user's recent projects, newest first, capped at
        :data:`RECENT_PROJECTS_LIMIT` (matches the Json impl)."""
        async with self._session_factory() as session:
            stmt = (
                select(RecentProjectRow)
                .where(RecentProjectRow.user_id == self._user_id)
                .order_by(RecentProjectRow.last_opened_at.desc())
                .limit(RECENT_PROJECTS_LIMIT)
            )
            rows = (await session.execute(stmt)).scalars().all()
        return [
            RecentProject(
                path=row.path,
                name=row.name,
                last_opened_at=row.last_opened_at,
                kind=row.kind,
            )
            for row in rows
        ]

    async def record_open(
        self,
        path: Path,
        name: str,
        *,
        kind: str | None = None,
    ) -> None:
        """Insert or refresh the row for ``(user_id, resolved_path)``.

        Resolving the path here matches the Json impl: two opens via
        different relative paths collapse to one row. After the
        upsert we trim rows beyond :data:`RECENT_PROJECTS_LIMIT` so
        the table doesn't grow unbounded for users who churn through
        projects.
        """
        resolved = str(Path(path).expanduser().resolve())
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(RecentProjectRow).where(
                        RecentProjectRow.user_id == self._user_id,
                        RecentProjectRow.path == resolved,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    RecentProjectRow(
                        user_id=self._user_id,
                        path=resolved,
                        name=name,
                        kind=kind,
                        last_opened_at=now,
                    )
                )
            else:
                existing.name = name
                existing.kind = kind
                existing.last_opened_at = now
            await session.commit()
            await self._trim_to_limit(session)

    async def remove(self, path: Path) -> bool:
        """Drop one entry; return ``True`` if a row was deleted."""
        resolved = str(Path(path).expanduser().resolve())
        async with self._session_factory() as session:
            result = await session.execute(
                delete(RecentProjectRow).where(
                    RecentProjectRow.user_id == self._user_id,
                    RecentProjectRow.path == resolved,
                )
            )
            await session.commit()
            return (result.rowcount or 0) > 0

    async def _trim_to_limit(self, session) -> None:
        # Find ids ranked beyond the limit and delete them. Two
        # statements (select + delete-by-id) instead of a single
        # subquery-delete so SQLite is happy -- it doesn't allow
        # referencing the deletion target inside the subquery.
        overflow_ids = (
            (
                await session.execute(
                    select(RecentProjectRow.id)
                    .where(RecentProjectRow.user_id == self._user_id)
                    .order_by(RecentProjectRow.last_opened_at.desc())
                    .offset(RECENT_PROJECTS_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        if not overflow_ids:
            return
        await session.execute(delete(RecentProjectRow).where(RecentProjectRow.id.in_(overflow_ids)))
        await session.commit()
