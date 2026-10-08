"""Postgres-backed shooter book and account profile (spec 2026-10-08).

Hosted counterparts to :class:`splitsmith.shooter_book.JsonShooterBookStore`
and :class:`splitsmith.account_profile.JsonAccountProfileStore`. Rows live in
``shooter_book`` (one per user and SSI shooter id) and ``account_profiles``
(one per user); logo files live in the tenant's own storage, whose prefix is
already ``users/<id>/`` (``S3Storage``'s traversal guard keeps a key inside
it), under ``account/files/`` and ``account/brand/``, and are mirrored to a
per-user cache on this container by content name before a render reads them.

**Multi-tenant invariant:** every statement filters on ``user_id ==
self._user_id``; the primary keys lead with it and the ``tenant_isolation``
RLS policy applies. ``tests/test_account_identity_db.py`` has an isolation
test per method.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..account_profile import AccountProfile
from ..identity import ShooterIdentity, logo_name, sniff_logo
from ..look_brand import BrandError, check_brand_logo
from ..looks import BRAND_FILE_RE, LookBrand
from ..shooter_book import (
    BackfillCandidate,
    BookSnapshot,
    ShooterBookEntry,
    _safe_file,
    backfill,
)
from ..storage import Storage
from .models import AccountProfileRow, ShooterBookRow

logger = logging.getLogger(__name__)

FILES_KEY = "account/files"
BRAND_KEY = "account/brand"

BackfillSource = Callable[[], Awaitable[Sequence[BackfillCandidate]]]


def _require_user(user_id: object, cls: str) -> str:
    if not isinstance(user_id, str) or not user_id:
        raise ValueError(
            f"{cls} requires a non-empty user_id; got {user_id!r}. The auth layer must resolve a "
            "real user before constructing the per-request store."
        )
    return user_id


def _mirror(storage: Storage | None, key: str, cache: Path, name: str) -> Path | None:
    """``cache/name`` on this disk, copied from ``key`` in storage when it is
    not there yet (content-named, so a cached copy is never stale); ``None``
    when storage has no such object or cannot be read."""
    local = _safe_file(cache, name)
    if local is not None and local.stat().st_size > 0:
        return local
    if storage is None:
        return None
    try:
        if not storage.exists(key):
            return None
        data = storage.read_bytes(key)
    except Exception as exc:  # noqa: BLE001 -- a logo is never worth a failed render
        logger.info("account files: could not read %s (%s)", key, exc)
        return None
    cache.mkdir(parents=True, exist_ok=True)
    partial = cache / f".{name}.part"
    partial.write_bytes(data)
    partial.replace(cache / name)
    return cache / name


async def _profile_row(session, user_id: str) -> AccountProfileRow | None:  # type: ignore[no-untyped-def]
    return (
        await session.execute(select(AccountProfileRow).where(AccountProfileRow.user_id == user_id))
    ).scalar_one_or_none()


class PostgresShooterBookStore:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        user_id: str,
        storage: Storage | None,
        cache_dir: Path,
        backfill_source: BackfillSource | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._user_id = _require_user(user_id, "PostgresShooterBookStore")
        self._storage = storage
        self._cache = cache_dir / "account" / self._user_id / "files"
        self._backfill_source = backfill_source

    # -- the one-time fill from the user's matches ---------------------------

    async def _ensure_backfilled(self) -> None:
        if self._backfill_source is None:
            return
        async with self._session_factory() as session:
            row = await _profile_row(session, self._user_id)
            if row is not None and row.backfilled_at is not None:
                return
        try:
            candidates = await self._backfill_source()
            await backfill(self, candidates)
        except Exception as exc:  # noqa: BLE001 -- a fill that fails is retried on the next read
            logger.warning("shooter book: the fill from existing matches failed (%s)", exc)
            return
        async with self._session_factory() as session:
            row = await _profile_row(session, self._user_id)
            if row is None:
                session.add(AccountProfileRow(user_id=self._user_id, backfilled_at=datetime.now(UTC)))
            else:
                row.backfilled_at = datetime.now(UTC)
            await session.commit()

    # -- reads ------------------------------------------------------------------

    async def _rows(self) -> list[ShooterBookRow]:
        async with self._session_factory() as session:
            stmt = select(ShooterBookRow).where(ShooterBookRow.user_id == self._user_id)
            return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    def _entry(row: ShooterBookRow) -> ShooterBookEntry | None:
        try:
            return ShooterBookEntry(
                shooter_id=row.shooter_id,
                identity=ShooterIdentity.model_validate(row.identity),
                label=row.label,
                updated_at=row.updated_at,
            )
        except Exception as exc:  # noqa: BLE001 -- one bad row must not lose the book
            logger.warning("shooter book: skipping a malformed row %s: %s", row.shooter_id, exc)
            return None

    async def list(self) -> list[ShooterBookEntry]:
        await self._ensure_backfilled()
        entries = [e for e in (self._entry(r) for r in await self._rows()) if e is not None]
        return sorted(entries, key=lambda e: e.shooter_id)

    async def get(self, shooter_id: int) -> ShooterBookEntry | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ShooterBookRow).where(
                        ShooterBookRow.user_id == self._user_id, ShooterBookRow.shooter_id == shooter_id
                    )
                )
            ).scalar_one_or_none()
        return self._entry(row) if row is not None else None

    async def snapshot(self) -> BookSnapshot:
        entries = await self.list()
        logos: dict[str, Path] = {}
        for entry in entries:
            name = entry.identity.logo
            if name and name not in logos:
                path = await self.logo_file(name)
                if path is not None:
                    logos[name] = path
        return BookSnapshot(entries={e.shooter_id: e.identity for e in entries}, logos=logos)

    async def logo_file(self, name: str) -> Path | None:
        if not name or "/" in name or name.startswith("."):
            return None
        return _mirror(self._storage, f"{FILES_KEY}/{name}", self._cache, name)

    # -- writes -----------------------------------------------------------------

    async def put(self, entry: ShooterBookEntry) -> None:
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(ShooterBookRow).where(
                        ShooterBookRow.user_id == self._user_id, ShooterBookRow.shooter_id == entry.shooter_id
                    )
                )
            ).scalar_one_or_none()
            identity = entry.identity.model_dump(mode="json")
            if existing is None:
                session.add(
                    ShooterBookRow(
                        user_id=self._user_id,
                        shooter_id=entry.shooter_id,
                        identity=identity,
                        label=entry.label,
                        updated_at=entry.updated_at,
                    )
                )
            else:
                existing.identity = identity
                existing.label = entry.label
                existing.updated_at = entry.updated_at
            await session.commit()

    async def delete(self, shooter_id: int) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(ShooterBookRow).where(
                    ShooterBookRow.user_id == self._user_id, ShooterBookRow.shooter_id == shooter_id
                )
            )
            await session.commit()

    async def put_logo(self, data: bytes) -> str:
        ext = sniff_logo(data)
        if self._storage is None:
            raise ValueError("This server has no file store for logos.")
        name = logo_name(data, ext)
        self._storage.write_bytes(f"{FILES_KEY}/{name}", data)
        return name


class PostgresAccountProfileStore:
    def __init__(
        self, session_factory: async_sessionmaker, *, user_id: str, storage: Storage | None, cache_dir: Path
    ) -> None:
        self._session_factory = session_factory
        self._user_id = _require_user(user_id, "PostgresAccountProfileStore")
        self._storage = storage
        self._cache = cache_dir / "account" / self._user_id / "brand"

    async def load(self) -> AccountProfile:
        async with self._session_factory() as session:
            row = await _profile_row(session, self._user_id)
        if row is None or row.brand is None:
            return AccountProfile()
        try:
            return AccountProfile(brand=LookBrand.model_validate(row.brand))
        except Exception as exc:  # noqa: BLE001 -- a bad row reads as no brand
            logger.warning("account profile: ignoring a malformed brand: %s", exc)
            return AccountProfile()

    async def save(self, profile: AccountProfile) -> None:
        brand = profile.brand.model_dump(mode="json") if profile.brand is not None else None
        async with self._session_factory() as session:
            row = await _profile_row(session, self._user_id)
            if row is None:
                session.add(AccountProfileRow(user_id=self._user_id, brand=brand))
            else:
                row.brand = brand
                row.updated_at = datetime.now(UTC)
            await session.commit()

    async def put_brand_logo(self, data: bytes) -> str:
        name = check_brand_logo(data)
        if self._storage is None:
            raise BrandError("This server has no file store for logos.")
        self._storage.write_bytes(f"{BRAND_KEY}/{name}", data)
        return name

    async def brand_file(self, name: str) -> Path | None:
        if not BRAND_FILE_RE.fullmatch(name or ""):
            return None
        return _mirror(self._storage, f"{BRAND_KEY}/{name}", self._cache, name)


__all__ = ["PostgresAccountProfileStore", "PostgresShooterBookStore"]
