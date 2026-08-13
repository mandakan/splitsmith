"""Schema-level guards for match_comments (timestamped comments)."""

from __future__ import annotations

import pytest

sa = pytest.importorskip("sqlalchemy")
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from splitsmith.db.models import Base, CommentRow, User  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_comment_row_persists_all_columns(session_factory) -> None:
    async with session_factory() as session:
        session.add(User(id="u1", email="owner@example.com"))
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
        row = (await session.execute(sa.select(CommentRow))).scalar_one()

    assert row.id  # ULID default assigned
    assert row.anchor_t == pytest.approx(4.32)
    assert row.anchor_kind == "shot"
    assert row.anchor_shot_id == "cand-7"
    assert row.author_user_id is None
    assert row.deleted_at is None
    assert row.created_at is not None


async def test_anchor_t_is_required_even_for_a_shot_anchor() -> None:
    """The shot id is a label; anchor_t is the truth. A row without it is
    meaningless once a re-detect moves the shot."""
    assert CommentRow.__table__.c.anchor_t.nullable is False
    assert CommentRow.__table__.c.anchor_shot_id.nullable is True
