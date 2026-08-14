"""Per-user account-profile writes (#867).

Today the profile is one column: ``users.display_name``. It gets its own
store rather than a method on an existing one because it is the only
place in the codebase that writes it, and #867 exists precisely because
nothing did -- a single named owner makes that answerable by grep.

**Multi-tenant invariant:** every statement filters on
``User.id == self._user_id``. Isolation tests in
``test_profile_store.py`` guard it; add one per new method. See
:mod:`splitsmith.db.scoreboard_identity` for the sibling pattern.

The store takes an already-normalized value. Validation lives in
:mod:`splitsmith.display_name` and runs in the route, so a bad name is a
422 before any session opens.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import User


class PostgresProfileStore:
    """Writes the authenticated user's profile columns."""

    def __init__(self, session_factory: async_sessionmaker, *, user_id: str) -> None:
        # Same fail-loud-on-empty-user_id pattern as
        # PostgresScoreboardIdentityStore: a None/empty user_id would
        # match no row and make a failed write look like a success.
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "PostgresProfileStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    async def set_display_name(self, display_name: str | None) -> None:
        """Set (or with ``None``, clear) the user's display name.

        ``display_name`` must already have passed
        :func:`splitsmith.display_name.normalize_display_name` -- in
        particular a blank name arrives here as ``None``, never ``""``,
        which is what keeps #866's fallback invariant true.
        """
        async with self._session_factory() as session:
            user = (await session.execute(select(User).where(User.id == self._user_id))).scalar_one_or_none()
            if user is None:
                raise LookupError(
                    f"User {self._user_id!r} not found; auth layer "
                    "must materialise the user row before calling set_display_name()."
                )
            user.display_name = display_name
            await session.commit()
