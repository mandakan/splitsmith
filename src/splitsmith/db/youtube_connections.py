"""Per-account YouTube connection store (issue #1000, phase 2).

Hosted-mode counterpart to the ``youtube.json`` accessors in
:mod:`splitsmith.youtube.oauth`. One column, ``users.youtube_connection``,
read and written whole; this module is its single owner (the
``profile.py`` rule: "what writes this?" is answerable by grep).

The column holds two independent blocks::

    {"connection": {...StoredYouTubeConnection...},
     "pending":    {...PendingLogin...}}

``connection`` is the channel plus the refresh token *sealed* under
:data:`splitsmith.youtube.sealed.ENV_TOKEN_KEY`; the plaintext token is
never in the row. ``pending`` is a consent flow in flight: the ``state``
and PKCE verifier ``connect/start`` minted, which the callback checks.
It lives in the row rather than in process memory because the start and
the callback can land on different API replicas.

**Multi-tenant invariant:** every statement filters on
``User.id == self._user_id``. ``tests/test_youtube_connection_store.py``
has an isolation test per method; add one for any new method.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..youtube import sealed
from ..youtube.oauth import SCOPE, YouTubeConnection
from .models import User

logger = logging.getLogger(__name__)

CONNECTION_KEY = "connection"
PENDING_KEY = "pending"


class StoredYouTubeConnection(BaseModel):
    """:class:`YouTubeConnection` as it sits in the row: the refresh token
    sealed, everything else plain."""

    schema_version: int = 1
    refresh_token_sealed: str
    channel_id: str
    channel_title: str
    connected_at: datetime
    scopes: list[str] = Field(default_factory=lambda: [SCOPE])

    @classmethod
    def seal_from(cls, conn: YouTubeConnection) -> StoredYouTubeConnection:
        return cls(
            refresh_token_sealed=sealed.seal(conn.refresh_token),
            channel_id=conn.channel_id,
            channel_title=conn.channel_title,
            connected_at=conn.connected_at,
            scopes=list(conn.scopes),
        )

    def open(self) -> YouTubeConnection:
        """The live connection; raises :class:`sealed.SealedTokenError`
        when the key does not match."""
        return YouTubeConnection(
            refresh_token=sealed.open_sealed(self.refresh_token_sealed),
            channel_id=self.channel_id,
            channel_title=self.channel_title,
            connected_at=self.connected_at,
            scopes=list(self.scopes),
        )


class PendingLogin(BaseModel):
    """A consent flow between ``connect/start`` and the callback."""

    state: str
    code_verifier: str
    started_at: datetime
    error: str | None = None


class PostgresYouTubeConnectionStore:
    """Owner-scoped reads and writes of ``users.youtube_connection``."""

    def __init__(self, session_factory: async_sessionmaker, *, user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "PostgresYouTubeConnectionStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    # -- reads ---------------------------------------------------------

    async def _column(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            raw = (
                await session.execute(select(User.youtube_connection).where(User.id == self._user_id))
            ).scalar_one_or_none()
        return dict(raw) if isinstance(raw, dict) else {}

    async def get(self) -> StoredYouTubeConnection | None:
        """The stored connection, or ``None``. A block that fails to
        validate reads as not connected and is logged, never raised."""
        raw = (await self._column()).get(CONNECTION_KEY)
        if not isinstance(raw, dict):
            return None
        try:
            return StoredYouTubeConnection.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 -- a bad block reads as "not connected"
            logger.warning("Discarding malformed youtube_connection for user %s: %s", self._user_id, exc)
            return None

    async def get_pending(self) -> PendingLogin | None:
        raw = (await self._column()).get(PENDING_KEY)
        if not isinstance(raw, dict):
            return None
        try:
            return PendingLogin.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Discarding malformed pending YouTube login for user %s: %s", self._user_id, exc)
            return None

    # -- writes --------------------------------------------------------

    async def _update(self, key: str, value: dict[str, Any] | None) -> None:
        """Set or (with ``None``) drop one block, leaving the other as is."""
        async with self._session_factory() as session:
            user = (await session.execute(select(User).where(User.id == self._user_id))).scalar_one_or_none()
            if user is None:
                raise LookupError(
                    f"User {self._user_id!r} not found; auth layer must "
                    "materialise the user row before writing its YouTube connection."
                )
            column = dict(user.youtube_connection) if isinstance(user.youtube_connection, dict) else {}
            if value is None:
                column.pop(key, None)
            else:
                column[key] = value
            # A fresh dict every time: SQLAlchemy's JSON type tracks
            # reassignment, not in-place mutation.
            user.youtube_connection = column or None
            await session.commit()

    async def set(self, conn: StoredYouTubeConnection) -> None:
        await self._update(CONNECTION_KEY, conn.model_dump(mode="json"))

    async def clear(self) -> None:
        await self._update(CONNECTION_KEY, None)

    async def set_pending(self, pending: PendingLogin) -> None:
        await self._update(PENDING_KEY, pending.model_dump(mode="json"))

    async def clear_pending(self) -> None:
        await self._update(PENDING_KEY, None)
