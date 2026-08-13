"""Per-owner store for public timestamped comments (#comments).

Constructed per-request with the *match owner's* user id -- which on an
anonymous write is the tenant ``_share_alias`` impersonated, not the
person typing. That is the whole reason this store looks like every
other one despite serving unauthenticated callers: by the time a request
reaches here, the tenant question has already been answered upstream by
the token row.

Multi-tenant invariant: every statement filters on
``CommentRow.user_id == self._user_id``. Isolation tests in
``test_comments_store.py`` guard it - add one per new method.

Deletion is soft (``deleted_at``): a bulk delete by link is a blunt
instrument and an owner who regrets one should be recoverable by hand.
Nothing purges soft-deleted rows; if that becomes a size problem it is a
retention decision to make then.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..comment_identity import author_code_for
from .models import CommentRow


@dataclass(frozen=True)
class Comment:
    id: str
    anchor_t: float
    anchor_kind: str
    anchor_shot_id: str | None
    author_kind: str
    author_user_id: str | None
    author_handle: str
    author_key_hash: str
    author_code: str | None
    share_token_id: str
    body: str
    created_at: datetime


@dataclass(frozen=True)
class CommentAuthorSummary:
    """One author's footprint on a match, as the owner's moderation view
    reports it."""

    author_code: str
    author_kind: str
    first_comment_at: datetime
    comment_count: int
    handles: tuple[str, ...]


def _to_comment(row: CommentRow) -> Comment:
    return Comment(
        id=row.id,
        anchor_t=row.anchor_t,
        anchor_kind=row.anchor_kind,
        anchor_shot_id=row.anchor_shot_id,
        author_kind=row.author_kind,
        author_user_id=row.author_user_id,
        author_handle=row.author_handle,
        author_key_hash=row.author_key_hash,
        author_code=row.author_code,
        share_token_id=row.share_token_id,
        body=row.body,
        created_at=row.created_at,
    )


class CommentStore:
    def __init__(self, session_factory: async_sessionmaker, *, user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "CommentStore requires a non-empty user_id; "
                f"got {user_id!r}. The share alias or auth layer must "
                "resolve the match owner before constructing the store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    async def list_for_stage(self, match_id: str, slug: str, stage_number: int) -> list[Comment]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(CommentRow).where(
                        CommentRow.user_id == self._user_id,
                        CommentRow.match_id == match_id,
                        CommentRow.slug == slug,
                        CommentRow.stage_number == stage_number,
                        CommentRow.deleted_at.is_(None),
                    )
                    # Oldest first. ULIDs do NOT sort by creation within
                    # a millisecond - the library reseeds randomness
                    # instead of incrementing, and the final review
                    # reproduced a non-monotonic pair at trial 8 of 200
                    # (issue #673). So created_at leads and the id is
                    # only the tie-break: on Postgres its microsecond
                    # resolution settles same-millisecond rows correctly,
                    # and on SQLite (CURRENT_TIMESTAMP, one-second
                    # resolution) it degrades to the id ordering, which
                    # is exact across milliseconds and arbitrary within
                    # one. Both are stable - two calls return the same
                    # order - which is the property the thread needs.
                    .order_by(CommentRow.created_at.asc(), CommentRow.id.asc())
                )
            ).scalars()
            return [_to_comment(r) for r in rows]

    async def count_for_stage(self, match_id: str, slug: str, stage_number: int) -> int:
        async with self._session_factory() as session:
            return int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(CommentRow)
                        .where(
                            CommentRow.user_id == self._user_id,
                            CommentRow.match_id == match_id,
                            CommentRow.slug == slug,
                            CommentRow.stage_number == stage_number,
                            CommentRow.deleted_at.is_(None),
                        )
                    )
                ).scalar_one()
            )

    async def create(
        self,
        *,
        match_id: str,
        slug: str,
        stage_number: int,
        anchor_t: float,
        anchor_kind: str,
        anchor_shot_id: str | None,
        author_kind: str,
        author_user_id: str | None,
        author_handle: str,
        author_key_hash: str,
        author_code: str,
        share_token_id: str,
        body: str,
    ) -> Comment:
        row = CommentRow(
            user_id=self._user_id,
            match_id=match_id,
            slug=slug,
            stage_number=stage_number,
            anchor_t=anchor_t,
            anchor_kind=anchor_kind,
            anchor_shot_id=anchor_shot_id,
            author_kind=author_kind,
            author_user_id=author_user_id,
            author_handle=author_handle,
            author_key_hash=author_key_hash,
            author_code=author_code,
            share_token_id=share_token_id,
            body=body,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_comment(row)

    async def delete_own(
        self, comment_id: str, *, match_id: str, slug: str, stage_number: int, author_key_hash: str
    ) -> bool:
        return await self._soft_delete_one(
            comment_id,
            match_id=match_id,
            slug=slug,
            stage_number=stage_number,
            author_key_hash=author_key_hash,
        )

    async def delete_as_owner(self, comment_id: str, *, match_id: str, slug: str, stage_number: int) -> bool:
        return await self._soft_delete_one(
            comment_id, match_id=match_id, slug=slug, stage_number=stage_number, author_key_hash=None
        )

    async def _soft_delete_one(
        self,
        comment_id: str,
        *,
        match_id: str,
        slug: str,
        stage_number: int,
        author_key_hash: str | None,
    ) -> bool:
        # slug + stage_number pin the delete to the thread the URL names
        # (fix round 1, F3): without them a comment posted at alice/3
        # deletes through any other slug/stage path on the same match,
        # since id + match_id + user_id alone already uniquely identify
        # the row - the two path segments were decorative.
        conditions = [
            CommentRow.user_id == self._user_id,
            CommentRow.id == comment_id,
            CommentRow.match_id == match_id,
            CommentRow.slug == slug,
            CommentRow.stage_number == stage_number,
            CommentRow.deleted_at.is_(None),
        ]
        if author_key_hash is not None:
            conditions.append(CommentRow.author_key_hash == author_key_hash)
        async with self._session_factory() as session:
            result = await session.execute(
                update(CommentRow).where(*conditions).values(deleted_at=datetime.now(UTC))
            )
            await session.commit()
            return bool(result.rowcount)

    async def delete_by_share_token(self, match_id: str, share_token_id: str) -> int:
        return await self._soft_delete_many(match_id, CommentRow.share_token_id == share_token_id)

    async def delete_by_author_key_hash(self, match_id: str, author_key_hash: str) -> int:
        return await self._soft_delete_many(match_id, CommentRow.author_key_hash == author_key_hash)

    async def purge_match(self, match_id: str) -> int:
        """Hard-delete every comment on a match, soft-deleted ones included.

        The one destructive method here, and deliberately so: it serves
        match deletion, where leaving a soft-deleted row would mean
        "delete my match" quietly kept other people's text about it.
        Nothing cascades from the matches registry row - ``_delete_hosted``
        deletes ``state_docs`` explicitly for the same reason.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                delete(CommentRow).where(
                    CommentRow.user_id == self._user_id,
                    CommentRow.match_id == match_id,
                )
            )
            await session.commit()
            return int(result.rowcount)

    async def _soft_delete_many(self, match_id: str, predicate) -> int:  # type: ignore[no-untyped-def]
        async with self._session_factory() as session:
            result = await session.execute(
                update(CommentRow)
                .where(
                    CommentRow.user_id == self._user_id,
                    CommentRow.match_id == match_id,
                    CommentRow.deleted_at.is_(None),
                    predicate,
                )
                .values(deleted_at=datetime.now(UTC))
            )
            await session.commit()
            return int(result.rowcount)

    async def author_summaries(self, match_id: str) -> list[CommentAuthorSummary]:
        """Per-author aggregates across one match, newest activity last.

        Aggregated in Python rather than by a GROUP BY on
        ``author_code``: rows written before #867 have the column NULL,
        so grouping in SQL would split one author into a legacy bucket
        and a current one. Deriving every row's code through
        ``author_code_for`` -- the same function the write path uses --
        makes the two indistinguishable, which is the point.

        The row count this loads is bounded by the per-stage comment cap
        times the number of stages on a match. A match that outgrows
        that wants a GROUP BY plus a one-off backfill of author_code,
        not a paginated version of this.
        """
        async with self._session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(CommentRow)
                        .where(
                            CommentRow.user_id == self._user_id,
                            CommentRow.match_id == match_id,
                            CommentRow.deleted_at.is_(None),
                        )
                        .order_by(CommentRow.created_at.asc(), CommentRow.id.asc())
                    )
                ).scalars()
            )
        grouped: dict[str, list[CommentRow]] = {}
        for row in rows:
            code = row.author_code or author_code_for(
                author_kind=row.author_kind,
                author_user_id=row.author_user_id,
                author_key_hash=row.author_key_hash,
            )
            grouped.setdefault(code, []).append(row)
        summaries = []
        for code, group in grouped.items():
            # dict.fromkeys, not a set: the owner reads this list, and
            # the order names were used in is information a set discards.
            handles = tuple(dict.fromkeys(r.author_handle for r in group))
            summaries.append(
                CommentAuthorSummary(
                    author_code=code,
                    author_kind=group[0].author_kind,
                    first_comment_at=group[0].created_at,
                    comment_count=len(group),
                    handles=handles,
                )
            )
        summaries.sort(key=lambda s: s.first_comment_at)
        return summaries
