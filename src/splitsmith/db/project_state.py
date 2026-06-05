"""Postgres-backed hosted match-state store with optimistic locking.

Hosted-mode per-match state -- the match doc, per-shooter project docs,
and per-stage audit docs -- used to live as JSON files on ephemeral
container disk mirrored whole to S3. That lost state on redeploy and was
last-writer-wins under concurrent edits. This store moves those small
JSON docs into the ``state_docs`` table (:class:`StateDocRow`) with
optimistic-concurrency versioning, so resolution is stateless across
replicas and concurrent edits are detected instead of lost.

**Local desktop mode stays file-based** -- this store only backs hosted
mode. The seam in ``MatchProject``/``Match`` chooses store vs file at
save time.

Constructed per-request (API) / per-job (worker) with the resolved user
id, mirroring :class:`splitsmith.db.matches.PostgresMatchStore`. The
``session_factory`` is the tenant-scoped one (sets the ``app.user_id``
GUC the RLS policy keys on); every query *also* filters
``WHERE user_id == self._user_id`` as defence-in-depth.

Engine-agnostic: tests use ``sqlite+aiosqlite:///:memory:``; production
uses ``postgresql+asyncpg://``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import StateDocRow


class StateConflictError(Exception):
    """Raised when an optimistic-locking save loses a version race.

    Either an INSERT (``expected_version == 0``) hit the uniqueness guard
    because a doc already exists, or an UPDATE matched 0 rows because the
    stored ``version`` no longer equals the ``expected_version`` the
    caller loaded. Surfaced to the SPA as HTTP 409 ``version_conflict``;
    the worker re-loads + re-merges audit docs on this error.
    """


# The three polymorphic doc kinds. ``slug`` is NULL for ``match``;
# ``stage_number`` is non-NULL only for ``audit``.
_KIND_MATCH = "match"
_KIND_PROJECT = "project"
_KIND_AUDIT = "audit"


class ProjectStateStore:
    """Per-user view of the ``state_docs`` table.

    **Multi-tenant invariant:** every SQL statement issued by this store
    includes ``StateDocRow.user_id == self._user_id`` in its WHERE
    clause. The ``state_docs`` RLS policy enforces the same boundary at
    the DB layer; the per-method filter enforces it at the query layer.
    Tests in ``test_project_state_store.py`` guard the invariant -- if you
    add a method here, add an isolation test for it too.

    Each doc kind gets a thin public ``load_*`` / ``save_*`` wrapper over
    one private ``_load`` / ``_save`` keyed on ``doc_kind`` -- the wrappers
    keep the isolation-test-per-method discipline while the lifecycle
    lives in one place.

    ``load_*`` returns ``(doc | None, version)`` -- ``(None, 0)`` when the
    doc is absent. ``save_*`` takes ``expected_version`` and returns the
    new version:

    - ``expected_version == 0`` -> INSERT a fresh row at version 1. A
      uniqueness collision (someone else inserted first) raises
      :class:`StateConflictError`.
    - ``expected_version > 0`` -> ``UPDATE ... WHERE version ==
      expected_version``. ``rowcount == 0`` (stale read or missing row)
      raises :class:`StateConflictError`.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        user_id: str,
    ) -> None:
        # Fail loud at construction -- a None/empty user_id from a buggy
        # auth layer would otherwise silently scope every query to "no
        # rows", which is its own bug. Same guard as PostgresMatchStore.
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "ProjectStateStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    # -- match doc (slug / stage NULL) --------------------------------

    async def load_match(self, match_id: str) -> tuple[dict | None, int]:
        return await self._load(match_id, _KIND_MATCH, slug=None, stage_number=None)

    async def save_match(self, match_id: str, doc: dict, *, expected_version: int) -> int:
        return await self._save(
            match_id, _KIND_MATCH, doc, expected_version=expected_version, slug=None, stage_number=None
        )

    # -- per-shooter project doc (slug set, stage NULL) ---------------

    async def load_project(self, match_id: str, slug: str) -> tuple[dict | None, int]:
        return await self._load(match_id, _KIND_PROJECT, slug=slug, stage_number=None)

    async def save_project(self, match_id: str, slug: str, doc: dict, *, expected_version: int) -> int:
        return await self._save(
            match_id, _KIND_PROJECT, doc, expected_version=expected_version, slug=slug, stage_number=None
        )

    # -- per-stage audit doc (slug + stage set) -----------------------

    async def load_audit(self, match_id: str, slug: str, stage_number: int) -> tuple[dict | None, int]:
        return await self._load(match_id, _KIND_AUDIT, slug=slug, stage_number=stage_number)

    async def save_audit(
        self, match_id: str, slug: str, stage_number: int, doc: dict, *, expected_version: int
    ) -> int:
        return await self._save(
            match_id,
            _KIND_AUDIT,
            doc,
            expected_version=expected_version,
            slug=slug,
            stage_number=stage_number,
        )

    # -- cascade cleanup ----------------------------------------------

    async def list_project_docs(self, match_id: str) -> list[tuple[str, dict]]:
        """Return ``(slug, doc)`` for every per-shooter project doc.

        Used by the delete cascade to harvest each shooter's attached
        ``raw_videos[].storage_path`` (for the storage cleanup loop and
        the cross-match raw-upload refcount). ``slug`` is non-NULL for
        project docs, so it is always a real string here.
        """
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(StateDocRow).where(
                            StateDocRow.user_id == self._user_id,
                            StateDocRow.match_id == match_id,
                            StateDocRow.doc_kind == _KIND_PROJECT,
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [(row.slug, row.doc) for row in rows]

    async def delete_shooter(self, match_id: str, slug: str) -> int:
        """Delete one shooter's docs within a match; return the row count.

        Sweeps that shooter's project doc and every per-stage audit doc
        (both carry ``slug``); the match doc has ``slug IS NULL`` so it is
        untouched. Called when a single shooter is removed from a match,
        the per-shooter analogue of :meth:`delete_match`. Idempotent.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                delete(StateDocRow).where(
                    StateDocRow.user_id == self._user_id,
                    StateDocRow.match_id == match_id,
                    StateDocRow.slug == slug,
                )
            )
            await session.commit()
            return result.rowcount or 0

    async def delete_match(self, match_id: str) -> int:
        """Delete every doc for ``match_id``; return the row count.

        Sweeps the match doc, every per-shooter project doc, and every
        per-stage audit doc in one statement -- they all share
        ``match_id``, so the WHERE deliberately omits
        ``doc_kind``/``slug``/``stage_number``. This closes the
        long-standing orphaned-``state_docs`` gap left by shooter/match
        removal (see ``remove_match_shooter`` in the UI server). Idempotent.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                delete(StateDocRow).where(
                    StateDocRow.user_id == self._user_id,
                    StateDocRow.match_id == match_id,
                )
            )
            await session.commit()
            return result.rowcount or 0

    # -- shared lifecycle ---------------------------------------------

    def _identity_where(self, match_id: str, doc_kind: str, slug: str | None, stage_number: int | None):
        """Build the WHERE terms uniquely identifying one doc.

        ``slug``/``stage_number`` are NULL for some kinds; comparing a
        column to NULL with ``==`` yields ``NULL`` (never true) in SQL, so
        use ``IS NULL`` for the None case. The leading ``user_id`` term is
        the multi-tenant guard present on every statement.
        """
        terms = [
            StateDocRow.user_id == self._user_id,
            StateDocRow.match_id == match_id,
            StateDocRow.doc_kind == doc_kind,
            StateDocRow.slug.is_(None) if slug is None else StateDocRow.slug == slug,
            (
                StateDocRow.stage_number.is_(None)
                if stage_number is None
                else StateDocRow.stage_number == stage_number
            ),
        ]
        return terms

    async def _load(
        self, match_id: str, doc_kind: str, *, slug: str | None, stage_number: int | None
    ) -> tuple[dict | None, int]:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(StateDocRow).where(*self._identity_where(match_id, doc_kind, slug, stage_number))
                )
            ).scalar_one_or_none()
        if row is None:
            return None, 0
        return row.doc, row.version

    async def _save(
        self,
        match_id: str,
        doc_kind: str,
        doc: dict,
        *,
        expected_version: int,
        slug: str | None,
        stage_number: int | None,
    ) -> int:
        if expected_version == 0:
            return await self._insert(match_id, doc_kind, doc, slug=slug, stage_number=stage_number)
        return await self._update(
            match_id, doc_kind, doc, expected_version=expected_version, slug=slug, stage_number=stage_number
        )

    async def _insert(
        self, match_id: str, doc_kind: str, doc: dict, *, slug: str | None, stage_number: int | None
    ) -> int:
        async with self._session_factory() as session:
            session.add(
                StateDocRow(
                    user_id=self._user_id,
                    match_id=match_id,
                    doc_kind=doc_kind,
                    slug=slug,
                    stage_number=stage_number,
                    doc=doc,
                    version=1,
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                # A concurrent writer inserted the same identity first
                # (the coalesce expression unique index on PG, or the
                # plain unique index on SQLite, rejected our row). The
                # caller thought this was a creation (expected_version 0)
                # but the doc already exists -> a genuine conflict.
                await session.rollback()
                raise StateConflictError(
                    f"insert conflict for {doc_kind} doc "
                    f"(match_id={match_id!r}, slug={slug!r}, stage={stage_number!r}): "
                    "a doc already exists"
                ) from exc
        return 1

    async def _update(
        self,
        match_id: str,
        doc_kind: str,
        doc: dict,
        *,
        expected_version: int,
        slug: str | None,
        stage_number: int | None,
    ) -> int:
        new_version = expected_version + 1
        async with self._session_factory() as session:
            result = await session.execute(
                update(StateDocRow)
                .where(
                    *self._identity_where(match_id, doc_kind, slug, stage_number),
                    StateDocRow.version == expected_version,
                )
                .values(doc=doc, version=new_version, updated_at=datetime.now(UTC))
            )
            if result.rowcount == 0:
                # Either the row is gone or its version moved on since the
                # caller loaded it -- a lost optimistic-locking race.
                await session.rollback()
                raise StateConflictError(
                    f"version conflict for {doc_kind} doc "
                    f"(match_id={match_id!r}, slug={slug!r}, stage={stage_number!r}): "
                    f"expected version {expected_version}, row was changed or removed"
                )
            await session.commit()
        return new_version
