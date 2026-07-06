"""Operator-scoped worker registry and token exchange (self-hosted workers).

``WorkersStore`` is OPERATOR-scoped, not tenant-scoped: it takes the raw
session_factory and has NO user_id parameter. This is a deliberate departure
from ``share_tokens.py`` (which is per-user) -- workers are infrastructure
managed by the operator, not personal data belonging to any one user. No RLS
policy applies; the unique-token constraint is the only isolation boundary
(same reasoning as ``magic_link_tokens``).

Token lifecycle
---------------
1. Operator creates a self-hosted worker via ``create_self_hosted()``.
   A high-entropy registration token is returned in plaintext (once).
   Its SHA-256 hex digest is stored in ``WorkerRow.registration_token_hash``.

2. The remote box calls ``register()`` with that plaintext token plus a
   metadata dict. If the token exists and has not expired:
   - ``registration_token_hash`` is cleared (set to None) so the token
     cannot match again even if it is replayed.
   - ``registered_at`` is stamped.
   - A fresh worker token is minted; its hash lands in ``worker_token_hash``.
   - The (WorkerRecord, plaintext_worker_token) pair is returned.
   A concurrent or replayed ``register()`` call finds no pending row and
   gets None -- unknown, already-used, and expired tokens are all uniform None.

3. On every request the worker presents its worker token; ``authenticate()``
   hashes it and looks up the row. Returns None if the row is unregistered.

Contrast with ``share_tokens.py``
----------------------------------
``ShareTokenStore`` requires a ``user_id`` and filters every query by it.
``WorkersStore`` requires no ``user_id`` -- there is one fleet shared by the
operator, not a per-user resource.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import WorkerRow


def _aware(value: datetime) -> datetime:
    """Coerce a possibly-naive timestamp (SQLite drops tzinfo) to UTC-aware
    so comparisons against datetime.now(UTC) don't raise."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _mint() -> tuple[str, str]:
    """Return (plaintext, sha256_hex) for a fresh token."""
    plain = secrets.token_urlsafe(32)
    return plain, _hash(plain)


@dataclass(frozen=True)
class WorkerRecord:
    id: str
    name: str
    kind: str
    enabled: bool
    priority: int
    registered: bool  # derived: registered_at is not None
    last_seen_at: datetime | None
    last_wake_at: datetime | None
    version: str | None
    info: dict | None
    created_at: datetime
    token_expires_at: datetime | None


def _to_record(row: WorkerRow) -> WorkerRecord:
    return WorkerRecord(
        id=row.id,
        name=row.name,
        kind=row.kind,
        enabled=row.enabled,
        priority=row.priority,
        registered=row.registered_at is not None,
        last_seen_at=row.last_seen_at,
        last_wake_at=row.last_wake_at,
        version=row.version,
        info=row.info,
        created_at=row.created_at,
        token_expires_at=row.token_expires_at,
    )


class WorkersStore:
    """Operator-scoped worker registry.

    See module docstring for the contrast with ``ShareTokenStore`` and the
    two-phase token lifecycle (registration token -> worker token).
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def create_self_hosted(
        self,
        name: str,
        *,
        priority: int = 10,
        ttl_hours: float = 24.0,
    ) -> tuple[WorkerRecord, str]:
        """Create a pending self-hosted worker row.

        Returns (record, plaintext_registration_token). The record has
        registered=False until ``register()`` completes the exchange.
        """
        plain, hashed = _mint()
        expires_at = datetime.now(UTC) + timedelta(hours=ttl_hours)
        row = WorkerRow(
            name=name,
            kind="self_hosted",
            priority=priority,
            enabled=True,
            registration_token_hash=hashed,
            token_expires_at=expires_at,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_record(row), plain

    async def list(self) -> list[WorkerRecord]:
        """All workers ordered by priority asc, name asc."""
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(WorkerRow).order_by(WorkerRow.priority.asc(), WorkerRow.name.asc())
                )
            ).scalars()
            return [_to_record(r) for r in rows]

    async def get(self, worker_id: str) -> WorkerRecord | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(select(WorkerRow).where(WorkerRow.id == worker_id))
            ).scalar_one_or_none()
            return _to_record(row) if row is not None else None

    async def update(
        self,
        worker_id: str,
        *,
        enabled: bool | None = None,
        priority: int | None = None,
        name: str | None = None,
    ) -> WorkerRecord | None:
        """Update mutable fields; returns the updated record or None if not found."""
        async with self._session_factory() as session:
            row = (
                await session.execute(select(WorkerRow).where(WorkerRow.id == worker_id))
            ).scalar_one_or_none()
            if row is None:
                return None
            if enabled is not None:
                row.enabled = enabled
            if priority is not None:
                row.priority = priority
            if name is not None:
                row.name = name
            await session.commit()
            await session.refresh(row)
            return _to_record(row)

    async def delete(self, worker_id: str) -> bool:
        """Delete a worker row. Returns False (without deleting) for kind='railway'."""
        async with self._session_factory() as session:
            row = (
                await session.execute(select(WorkerRow).where(WorkerRow.id == worker_id))
            ).scalar_one_or_none()
            if row is None:
                return False
            if row.kind == "railway":
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def register(self, token: str, info: dict) -> tuple[WorkerRecord, str] | None:
        """Single-use registration token exchange.

        Looks up the row by SHA-256 hash WHERE registered_at IS NULL, then
        checks expiry. On success: clears registration_token_hash (prevents
        replay even at the DB level), stamps registered_at, mints a worker
        token. Returns (record, plaintext_worker_token) or None.

        Unknown, already-used, and expired tokens all return None - callers
        cannot distinguish them (uniform behaviour).
        """
        if not token:
            return None
        hashed = _hash(token)
        async with self._session_factory() as session:
            # Row-lock the pending row: under READ COMMITTED two concurrent
            # register() calls could both mint tokens; FOR UPDATE serializes them so
            # the loser's SELECT re-checks WHERE and returns None. No-op on SQLite.
            row = (
                await session.execute(
                    select(WorkerRow)
                    .where(
                        WorkerRow.registration_token_hash == hashed,
                        WorkerRow.registered_at.is_(None),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            # Check expiry before committing anything
            if row.token_expires_at is not None:
                if _aware(row.token_expires_at) < datetime.now(UTC):
                    return None
            # Mint the long-lived worker token
            worker_plain, worker_hashed = _mint()
            row.registration_token_hash = None  # single-use: clear so it can never match again
            row.registered_at = datetime.now(UTC)
            row.worker_token_hash = worker_hashed
            row.version = info.get("agent_version")
            row.info = info
            await session.commit()
            await session.refresh(row)
            return _to_record(row), worker_plain

    async def authenticate(self, worker_token: str) -> WorkerRecord | None:
        """Authenticate a worker by its long-lived worker token.

        Returns None for unknown tokens or rows that have not been registered.
        """
        if not worker_token:
            return None
        hashed = _hash(worker_token)
        async with self._session_factory() as session:
            row = (
                await session.execute(select(WorkerRow).where(WorkerRow.worker_token_hash == hashed))
            ).scalar_one_or_none()
        if row is None:
            return None
        if row.registered_at is None:
            return None
        return _to_record(row)

    async def touch_seen(self, worker_id: str, *, version: str | None = None) -> None:
        """Stamp last_seen_at to now for the given worker.

        When ``version`` is given (a reconnecting self-hosted agent reports it
        via header on every channel connect) the ``version`` column is
        refreshed too, so an in-place upgrade shows up without re-registration.
        A ``None`` version leaves the stored value untouched.
        """
        values: dict = {"last_seen_at": datetime.now(UTC)}
        if version is not None:
            values["version"] = version
        async with self._session_factory() as session:
            await session.execute(update(WorkerRow).where(WorkerRow.id == worker_id).values(**values))
            await session.commit()

    async def touch_wake(self, worker_ids: list[str]) -> None:
        """Stamp last_wake_at to now for each worker id."""
        if not worker_ids:
            return
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            await session.execute(
                update(WorkerRow).where(WorkerRow.id.in_(worker_ids)).values(last_wake_at=now)
            )
            await session.commit()

    async def ensure_railway_row(self, *, version: str | None = None) -> None:
        """Idempotent upsert of the single kind='railway' row.

        Creates name='railway', kind='railway', priority=100, enabled=True,
        registered_at=now if no kind='railway' row exists. If one already
        exists, the operator's enabled/priority settings are preserved - but
        ``version`` is always refreshed to the running deploy (web and worker
        ship from the same image, so serve boot knows the worker's version).

        The row is seeded as registered (registered_at stamped) so
        ``list_enabled()`` includes it without requiring a registration flow.
        """
        async with self._session_factory() as session:
            existing = (
                await session.execute(select(WorkerRow).where(WorkerRow.kind == "railway"))
            ).scalar_one_or_none()
            if existing is not None:
                if version is not None and existing.version != version:
                    existing.version = version
                    await session.commit()
                return
            row = WorkerRow(
                name="railway",
                kind="railway",
                priority=100,
                enabled=True,
                registered_at=datetime.now(UTC),
                version=version,
            )
            session.add(row)
            await session.commit()

    async def list_enabled(self) -> list[WorkerRecord]:
        """Workers that are enabled AND either registered or railway-kind.

        Ordered by priority asc.
        """
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(WorkerRow)
                    .where(
                        WorkerRow.enabled.is_(True),
                        (WorkerRow.registered_at.is_not(None)) | (WorkerRow.kind == "railway"),
                    )
                    .order_by(WorkerRow.priority.asc())
                )
            ).scalars()
            return [_to_record(r) for r in rows]
