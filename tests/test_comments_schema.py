"""Schema-level guards for match_comments (timestamped comments).

Sync tests calling ``asyncio.run`` - the repo idiom (see
``tests/test_share_tokens_store.py``). There is no async-test plugin.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from splitsmith.db import Base, User, create_engine, sessionmaker
from splitsmith.db.models import CommentRow


def _session_factory() -> sessionmaker:
    """Fresh in-memory engine with the schema created and one user seeded."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            s.add(User(id="u1", email="owner@example.com"))
            await s.commit()

    asyncio.run(_setup())
    return session_factory


def test_comment_row_persists_all_columns() -> None:
    sf = _session_factory()

    async def _go() -> CommentRow:
        async with sf() as session:
            session.add(
                CommentRow(
                    user_id="u1",
                    match_id="m1",
                    slug="alice",
                    stage_number=3,
                    anchor_t=4.32,
                    anchor_kind="shot",
                    anchor_shot_id="cand-7",
                    author_kind="handle",
                    author_handle="Prone Popper 47",
                    author_key_hash="deadbeef",
                    share_token_id="s1",
                    body="reload looks early here",
                )
            )
            await session.commit()
            return (await session.execute(select(CommentRow))).scalar_one()

    row = asyncio.run(_go())

    assert row.id  # ULID default assigned
    assert row.anchor_t == pytest.approx(4.32)
    assert row.anchor_kind == "shot"
    assert row.anchor_shot_id == "cand-7"
    assert row.author_user_id is None
    assert row.deleted_at is None
    assert row.created_at is not None


def test_anchor_t_is_required_even_for_a_shot_anchor() -> None:
    """The shot id is a label; anchor_t is the truth. A row without it is
    meaningless once a re-detect moves the shot."""
    assert CommentRow.__table__.c.anchor_t.nullable is False
    assert CommentRow.__table__.c.anchor_shot_id.nullable is True
