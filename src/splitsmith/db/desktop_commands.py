"""Postgres-backed store for the desktop command queue (#1100, spec
2026-09-28).

The phone side requests, lists and cancels; the desktop side claims,
heartbeats and completes. One row per request in ``desktop_commands``.

**Multi-tenant invariant:** every statement filters on
``DesktopCommandRow.user_id == self._user_id`` (and the table is under
the ``tenant_isolation`` RLS policy). ``tests/test_desktop_commands_store.py``
has an isolation test per method; add one for any new method.

Leases: a claim holds a command until ``lease_expires_at``; a heartbeat
extends it. A claimed command whose lease lapsed (the desktop quit or
crashed) is claimable again, so no request is stranded by a dead desktop.
Completing is idempotent: a terminal command stays as it is, so a desktop
that retries a completion it already made is harmless.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import DesktopCommandRow

#: Command kinds a phone may request. A new kind is added here and in the
#: desktop runner; the request route refuses anything else.
COMMAND_KINDS = frozenset({"shot_detect"})

ACTIVE_STATUSES = ("pending", "claimed")
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
LEASE = timedelta(minutes=10)

CommandStatus = Literal["pending", "claimed", "succeeded", "failed", "cancelled"]


class DesktopCommand(BaseModel):
    """Wire shape shared by the phone and desktop routes."""

    id: str
    match_id: str
    kind: str
    slug: str | None
    stage_number: int | None
    args: dict[str, Any]
    expected_revision: str | None
    status: CommandStatus
    cancel_requested: bool
    progress_message: str | None
    error: str | None
    result: dict[str, Any] | None
    requested_at: datetime
    claimed_at: datetime | None
    lease_expires_at: datetime | None
    finished_at: datetime | None


def _to_command(row: DesktopCommandRow) -> DesktopCommand:
    return DesktopCommand(
        id=row.id,
        match_id=row.match_id,
        kind=row.kind,
        slug=row.slug,
        stage_number=row.stage_number,
        args=dict(row.args or {}),
        expected_revision=row.expected_revision,
        status=row.status,  # type: ignore[arg-type]
        cancel_requested=bool(row.cancel_requested),
        progress_message=row.progress_message,
        error=row.error,
        result=row.result,
        requested_at=_aware(row.requested_at),
        claimed_at=_aware(row.claimed_at),
        lease_expires_at=_aware(row.lease_expires_at),
        finished_at=_aware(row.finished_at),
    )


def _aware(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on the way back; Postgres keeps it. Every value
    this table stores is UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class DesktopCommandStore:
    def __init__(self, session_factory: async_sessionmaker, *, user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "DesktopCommandStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    def _mine(self) -> Any:
        return DesktopCommandRow.user_id == self._user_id

    # -- phone side ------------------------------------------------------

    async def request(
        self,
        *,
        match_id: str,
        kind: str,
        slug: str | None,
        stage_number: int | None,
        args: dict[str, Any],
        expected_revision: str | None,
    ) -> tuple[DesktopCommand, bool]:
        """Queue a command; returns ``(command, created)``. An active
        command for the same target is returned instead of a second one."""
        if kind not in COMMAND_KINDS:
            raise ValueError(f"unknown command kind {kind!r}")
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(DesktopCommandRow)
                    .where(
                        self._mine(),
                        DesktopCommandRow.match_id == match_id,
                        DesktopCommandRow.kind == kind,
                        DesktopCommandRow.slug.is_(None) if slug is None else DesktopCommandRow.slug == slug,
                        (
                            DesktopCommandRow.stage_number.is_(None)
                            if stage_number is None
                            else DesktopCommandRow.stage_number == stage_number
                        ),
                        DesktopCommandRow.status.in_(ACTIVE_STATUSES),
                    )
                    .order_by(DesktopCommandRow.requested_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return _to_command(existing), False
            row = DesktopCommandRow(
                user_id=self._user_id,
                match_id=match_id,
                kind=kind,
                slug=slug,
                stage_number=stage_number,
                args=args,
                expected_revision=expected_revision,
                status="pending",
                cancel_requested=False,
                requested_at=datetime.now(UTC),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_command(row), True

    async def list_for_match(self, match_id: str, *, limit: int = 20) -> list[DesktopCommand]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(DesktopCommandRow)
                        .where(self._mine(), DesktopCommandRow.match_id == match_id)
                        .order_by(DesktopCommandRow.requested_at.desc(), DesktopCommandRow.id.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return [_to_command(r) for r in rows]

    async def cancel(self, command_id: str, *, match_id: str) -> DesktopCommand | None:
        """Pending -> cancelled at once; claimed -> ``cancel_requested``
        (the desktop stops at its next heartbeat). Terminal: unchanged.
        None when the command is not this user's, or not in ``match_id``."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DesktopCommandRow).where(
                        self._mine(),
                        DesktopCommandRow.id == command_id,
                        DesktopCommandRow.match_id == match_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.status == "pending":
                row.status = "cancelled"
                row.finished_at = datetime.now(UTC)
            elif row.status == "claimed":
                row.cancel_requested = True
            await session.commit()
            await session.refresh(row)
            return _to_command(row)

    async def delete_for_match(self, match_id: str) -> int:
        """The hosted match delete cascade's sweep."""
        async with self._session_factory() as session:
            result = await session.execute(
                delete(DesktopCommandRow).where(self._mine(), DesktopCommandRow.match_id == match_id)
            )
            await session.commit()
            return int(result.rowcount or 0)

    # -- desktop side ----------------------------------------------------

    def _claimable(self, now: datetime) -> Any:
        return or_(
            DesktopCommandRow.status == "pending",
            and_(DesktopCommandRow.status == "claimed", DesktopCommandRow.lease_expires_at < now),
        )

    async def pending_counts(self, *, now: datetime | None = None) -> dict[str, int]:
        """``match_id -> commands a desktop could claim now``, for the
        fingerprint poll. Matches with none are absent."""
        now = now or datetime.now(UTC)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(DesktopCommandRow.match_id, func.count(DesktopCommandRow.id))
                    .where(self._mine(), self._claimable(now))
                    .group_by(DesktopCommandRow.match_id)
                )
            ).all()
        return {str(m): int(c) for m, c in rows}

    async def claim(
        self,
        match_ids: Collection[str],
        *,
        token_id: str | None,
        limit: int = 5,
        now: datetime | None = None,
    ) -> list[DesktopCommand]:
        """Claim up to ``limit`` claimable commands for ``match_ids``,
        oldest first. The UPDATE re-checks claimability per row, so two
        desktops racing for the same command cannot both get it."""
        if not match_ids:
            return []
        now = now or datetime.now(UTC)
        claimed: list[DesktopCommand] = []
        async with self._session_factory() as session:
            candidates = (
                (
                    await session.execute(
                        select(DesktopCommandRow.id)
                        .where(
                            self._mine(),
                            DesktopCommandRow.match_id.in_(list(match_ids)),
                            self._claimable(now),
                        )
                        .order_by(DesktopCommandRow.requested_at, DesktopCommandRow.id)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            for command_id in candidates:
                result = await session.execute(
                    update(DesktopCommandRow)
                    .where(self._mine(), DesktopCommandRow.id == command_id, self._claimable(now))
                    .values(
                        status="claimed",
                        claimed_by=token_id,
                        claimed_at=now,
                        lease_expires_at=now + LEASE,
                        progress_message=None,
                    )
                )
                if result.rowcount:
                    row = (
                        await session.execute(
                            select(DesktopCommandRow).where(DesktopCommandRow.id == command_id)
                        )
                    ).scalar_one()
                    claimed.append(_to_command(row))
            await session.commit()
        return claimed

    async def heartbeat(
        self, command_id: str, *, message: str | None, now: datetime | None = None
    ) -> DesktopCommand | None:
        """Extend a claimed command's lease and record progress. None when
        the command is unknown or no longer claimed (terminal, or its lease
        lapsed and another desktop took it: the caller should stop)."""
        now = now or datetime.now(UTC)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DesktopCommandRow).where(self._mine(), DesktopCommandRow.id == command_id)
                )
            ).scalar_one_or_none()
            if row is None or row.status != "claimed":
                return None
            row.lease_expires_at = now + LEASE
            if message is not None:
                row.progress_message = message
            await session.commit()
            await session.refresh(row)
            return _to_command(row)

    async def complete(
        self,
        command_id: str,
        *,
        status: Literal["succeeded", "failed", "cancelled"],
        error: str | None = None,
        result: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> DesktopCommand | None:
        """Finish a command. Idempotent: an already-terminal command is
        returned unchanged. None when unknown."""
        now = now or datetime.now(UTC)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(DesktopCommandRow).where(self._mine(), DesktopCommandRow.id == command_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.status not in TERMINAL_STATUSES:
                row.status = status
                row.error = error
                row.result = result
                row.finished_at = now
                row.lease_expires_at = None
                await session.commit()
                await session.refresh(row)
            return _to_command(row)
