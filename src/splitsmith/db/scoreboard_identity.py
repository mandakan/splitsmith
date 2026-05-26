"""Postgres-backed :class:`ScoreboardIdentityStore` (doc 10, Tier 3).

Hosted-mode counterpart to :class:`splitsmith.user_config.JsonScoreboardIdentityStore`.
The Json impl writes ``~/.splitsmith/scoreboard.json``. This one writes
to ``users.scoreboard_identity`` -- a JSON column on the user row.

Why a column on ``users`` instead of its own table: there is exactly
one scoreboard identity per user (you pin yourself once, the picker
prefills the same shooter_id across all projects). A separate table
would mean a join + a UniqueConstraint on user_id to enforce the
one-per-user invariant; a column gives that invariant for free and
saves a migration.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..user_config import ScoreboardIdentity
from .models import User


class PostgresScoreboardIdentityStore:
    """:class:`ScoreboardIdentityStore` backed by ``users.scoreboard_identity``.

    **Multi-tenant invariant:** every SQL statement issued by this
    store includes ``User.id == self._user_id`` in its WHERE clause.
    Two users own disjoint rows by virtue of the users PK; the
    per-method filter still belt-and-braces it (a query for the
    wrong user_id returns ``None`` instead of leaking a row). Tests
    in ``test_scoreboard_identity_store.py`` guard the invariant --
    if you add a new method, add an isolation test.

    See :mod:`splitsmith.db.recent_projects` for the sibling
    implementation pattern; both follow the checklist in the
    ``multitenant-table-invariants`` memory entry.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        user_id: str,
    ) -> None:
        # Same fail-loud-on-empty-user_id pattern as
        # :class:`PostgresRecentProjectsStore`. A None/empty user_id
        # would silently match no user row and return ``None`` --
        # easily mistaken for "not pinned yet" and hide the auth bug.
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "PostgresScoreboardIdentityStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    async def load(self) -> ScoreboardIdentity | None:
        async with self._session_factory() as session:
            payload = (
                await session.execute(select(User.scoreboard_identity).where(User.id == self._user_id))
            ).scalar_one_or_none()
        if payload is None:
            return None
        # ``scalar_one_or_none`` returns ``None`` both when there is
        # no matching user row *and* when the column itself is NULL.
        # Both cases mean "no pinned identity"; the SPA shows the
        # picker either way.
        return ScoreboardIdentity.model_validate(payload)

    async def save(self, identity: ScoreboardIdentity) -> None:
        async with self._session_factory() as session:
            user = (await session.execute(select(User).where(User.id == self._user_id))).scalar_one_or_none()
            if user is None:
                # The auth layer should have minted the row before
                # the request reached this handler. If not, that's
                # an invariant violation worth surfacing.
                raise LookupError(
                    f"User {self._user_id!r} not found; auth layer "
                    "must materialise the user row before calling save()."
                )
            user.scoreboard_identity = identity.model_dump(mode="json")
            await session.commit()

    async def clear(self) -> None:
        async with self._session_factory() as session:
            user = (await session.execute(select(User).where(User.id == self._user_id))).scalar_one_or_none()
            if user is None:
                # Idempotent: clearing for a missing user is a no-op
                # rather than an error. The hosted-mode delete-account
                # flow may race with a stale request that's still
                # trying to clear -- we'd rather it silently succeed
                # than 500 on the user.
                return
            user.scoreboard_identity = None
            await session.commit()
