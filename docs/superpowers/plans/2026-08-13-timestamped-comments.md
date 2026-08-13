# Timestamped Comments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let anyone holding a comment-capable share link post a public, timestamped comment on a shooter's stage video, anchored to a time or a shot, per `docs/superpowers/specs/2026-08-13-timestamped-comments-design.md`.

**Architecture:** A new `match_comments` table under the existing tenant RLS policy holds comments owned by the *match owner*, not the author. The anonymous write rides the seam #779 left open: `"comment"` joins `_WRITE_CAPABLE_SCOPES` in `db/share_guard.py`, `_share_alias` gains a second allowlist (`_SHARE_WRITE_PATH_RE`) with its own method set, and `ui/capabilities.py` maps the scope to a new `comment_write` capability. Display names are always server-derived, never client-supplied. The SPA adds a comment panel to `ResultsStage` reusing the shipped `lib/moment.ts` clock.

**Tech Stack:** FastAPI + SQLAlchemy 2 async + pydantic v2 + alembic + pytest (backend, `uv`); React 19 + react-router 7 + vitest (`src/splitsmith/ui_static/`, `pnpm`).

## Global Constraints

- Branch: `feat/timestamped-comments`, created from `spec/timestamped-comments` (which holds the design doc at `25aa30a`).
- **No new dependencies** on either side. The dep list is small on purpose (CLAUDE.md).
- Python 3.11+, type hints everywhere, `pathlib.Path` never strings, f-strings, Black line length 110, Ruff clean.
- All user-visible copy and all comments: **ASCII only**, single `-` dash, never an em dash, straight quotes.
- `anchor_t` is seconds after the start beep, may be negative, rounded to 2 decimals, and is **always** stored - including when `anchor_kind == "shot"`.
- `author_handle`, `author_user_id`, `user_id` and `match_id` are **never** read from a request body. The write request model does not declare them.
- The uniform-404 rule: every refusal on the anonymous surface returns `{"detail": "not found"}` with status 404, byte-identical to an unknown token. Only 422 (validation) and 429 (rate limit) may differ, and only for a caller who already passed the scope check.
- `_SHARE_PATH_RE` stays GET-only and keeps its docstring claim. Writes get a separate pattern; the two are never merged.
- Backend tests: `uv run pytest -n0 <file> -q` (serial for a single file - worker startup dominates a focused run).
- **Async tests use the repo's `asyncio.run()` idiom, not an async-test plugin.** 57 test files write sync `def test_...` bodies that call `asyncio.run(coro)`; `tests/test_share_tokens_store.py` is the closest model, including its `_engine_with_users` helper built on `create_engine` / `sessionmaker` from `splitsmith.db`. There is no `pytest-asyncio`, and `anyio` is only a transitive dependency of starlette - reaching for its pytest plugin would take an undeclared dependency on another package's dep tree, which the no-new-dependencies rule forbids.
- Frontend tests: `pnpm vitest run <file>` from `src/splitsmith/ui_static/`.
- Full gates once at the end of the branch (Task 11), not per task.
- Current alembic head is `a1c9e3b7d5f0` (`add_scope_to_share_tokens`). Task 1's migration sets `down_revision = "a1c9e3b7d5f0"`.

---

### Task 1: `match_comments` table + migration

**Files:**
- Modify: `src/splitsmith/db/models.py` (append a `CommentRow` class after `ShareTokenRow`)
- Create: `alembic/versions/b4d8f1a90c27_create_match_comments_table.py`
- Test: `tests/test_comments_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `splitsmith.db.models.CommentRow` with columns `id, user_id, match_id, slug, stage_number, anchor_t, anchor_kind, anchor_shot_id, author_kind, author_user_id, author_handle, author_key_hash, share_token_id, body, created_at, deleted_at`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_comments_schema.py`:

```python
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
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest -n0 tests/test_comments_schema.py -q`
Expected: FAIL with `ImportError: cannot import name 'CommentRow'`.

- [ ] **Step 3: Add the model**

Append to `src/splitsmith/db/models.py`, after `ShareTokenRow`:

```python
class CommentRow(Base):
    """One public, timestamped comment on a shooter's stage video.

    **``user_id`` is the match owner, not the author.** Counterintuitive
    and deliberate: the comment is about the owner's footage, it dies
    with the owner's match through the CASCADE below, and an anonymous
    author has no account for it to belong to. Tenancy therefore stays
    exactly what it is in every other table, and the RLS policy needs no
    special case. ``author_user_id`` is the separate, nullable column
    that records a *signed-in* author.

    **``anchor_t`` is always set, even when ``anchor_kind == "shot"``.**
    The shot id is a label; ``anchor_t`` is the truth. A re-detect, a
    renumber, or a recycled ``cand-<n>`` (#842) therefore degrades a
    shot-anchored comment to a plain time pin -- it is never hidden and
    never silently re-attaches to a different shot, which is the failure
    that would actually mislead a reader.

    **``author_key_hash`` is convenience, not a security boundary.** The
    client mints a random opaque key once and keeps it in localStorage;
    it exists so a commenter can delete their own comment without an
    account. Anyone can mint one, so it must never gate anything whose
    exposure matters.

    **``share_token_id`` is the moderation primitive.** It makes "remove
    everything that came through the link I sent to that guy" one query,
    and it composes with revocation (#788).
    """

    __tablename__ = "match_comments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Paired with user_id the way state_docs pairs them, rather than a
    # single-column FK: matches are keyed (user_id, match_id).
    match_id: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    stage_number: Mapped[int] = mapped_column(Integer, nullable=False)

    anchor_t: Mapped[float] = mapped_column(Float, nullable=False)
    anchor_kind: Mapped[str] = mapped_column(String, nullable=False, default="time")
    anchor_shot_id: Mapped[str | None] = mapped_column(String, nullable=True)

    author_kind: Mapped[str] = mapped_column(String, nullable=False, default="handle")
    author_user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_handle: Mapped[str] = mapped_column(String, nullable=False)
    author_key_hash: Mapped[str] = mapped_column(String, nullable=False)
    share_token_id: Mapped[str] = mapped_column(String, nullable=False)

    body: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<CommentRow id={self.id!r} match_id={self.match_id!r} stage={self.stage_number}>"
```

Add `Float` and `Integer` to the existing `from sqlalchemy import ...` line if they are not already imported, and `ForeignKey` likewise.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest -n0 tests/test_comments_schema.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Write the migration**

Create `alembic/versions/b4d8f1a90c27_create_match_comments_table.py`:

```python
"""create match_comments table

Public timestamped comments on a shooter's stage video. ``user_id`` is
the match owner (see :class:`splitsmith.db.models.CommentRow`), so the
table joins the ``tenant_isolation`` RLS policy family unchanged - the
owner's tenant is what an anonymous write is impersonating by the time
it reaches here.

Two indexes, both driven by real queries: the thread read is
``(user_id, match_id, slug, stage_number)`` and the two bulk-moderation
deletes are by ``share_token_id`` and by ``author_key_hash``.

Revision ID: b4d8f1a90c27
Revises: a1c9e3b7d5f0
Create Date: 2026-08-13 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4d8f1a90c27"
down_revision: str | Sequence[str] | None = "a1c9e3b7d5f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = "tenant_isolation"
_THREAD_INDEX = "ix_match_comments_thread"
_TOKEN_INDEX = "ix_match_comments_share_token_id"
_AUTHOR_INDEX = "ix_match_comments_author_key_hash"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "match_comments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("stage_number", sa.Integer(), nullable=False),
        sa.Column("anchor_t", sa.Float(), nullable=False),
        sa.Column("anchor_kind", sa.String(), nullable=False),
        sa.Column("anchor_shot_id", sa.String(), nullable=True),
        sa.Column("author_kind", sa.String(), nullable=False),
        sa.Column("author_user_id", sa.String(), nullable=True),
        sa.Column("author_handle", sa.String(), nullable=False),
        sa.Column("author_key_hash", sa.String(), nullable=False),
        sa.Column("share_token_id", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        _THREAD_INDEX,
        "match_comments",
        ["user_id", "match_id", "slug", "stage_number"],
        unique=False,
    )
    op.create_index(_TOKEN_INDEX, "match_comments", ["share_token_id"], unique=False)
    op.create_index(_AUTHOR_INDEX, "match_comments", ["author_key_hash"], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        # Same body as d1f7b25c8a3e; each statement issued separately
        # because asyncpg can't run multiple commands in one prepared
        # statement.
        op.execute("ALTER TABLE match_comments ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE match_comments FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {_POLICY} ON match_comments "
            f"FOR ALL "
            f"USING (user_id = current_setting('app.user_id', true)) "
            f"WITH CHECK (user_id = current_setting('app.user_id', true))"
        )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON match_comments")
        op.execute("ALTER TABLE match_comments NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE match_comments DISABLE ROW LEVEL SECURITY")
    op.drop_index(_AUTHOR_INDEX, table_name="match_comments")
    op.drop_index(_TOKEN_INDEX, table_name="match_comments")
    op.drop_index(_THREAD_INDEX, table_name="match_comments")
    op.drop_table("match_comments")
```

- [ ] **Step 6: Verify the migration applies on a clean database**

Run the repo's existing migration smoke test:

`uv run pytest -n0 -k migration -q`

Expected: PASS. If the repo's smoke test names a single head, confirm `b4d8f1a90c27` is now it. If any test asserts a specific head revision string, update it to `b4d8f1a90c27`.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/db/models.py alembic/versions/b4d8f1a90c27_create_match_comments_table.py tests/test_comments_schema.py
git commit -m "feat(db): match_comments table for timestamped share comments"
```

---

### Task 2: Server-derived comment handles

**Files:**
- Create: `src/splitsmith/comment_identity.py`
- Test: `tests/test_comment_identity.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `hash_author_key(author_key: str) -> str`
  - `derive_handle(author_key: str, *, secret: bytes | None = None) -> str`
  - `handle_secret() -> bytes`
  - `MAX_AUTHOR_KEY_LEN: int = 128`

**Why this task exists at all.** The tempting build is a client-side wordlist that posts its chosen handle. That is wrong: if the client supplies `author_handle`, anyone with `curl` signs a comment with the match owner's name. The server owns the name; the client owns only an opaque key.

- [ ] **Step 1: Write the failing test**

Create `tests/test_comment_identity.py`:

```python
"""Server-derived comment handles."""

from __future__ import annotations

from splitsmith.comment_identity import (
    ADJECTIVES,
    NOUNS,
    derive_handle,
    hash_author_key,
)


def test_handle_is_stable_for_a_key() -> None:
    secret = b"test-secret"
    assert derive_handle("abc123", secret=secret) == derive_handle("abc123", secret=secret)


def test_handle_differs_across_keys() -> None:
    secret = b"test-secret"
    handles = {derive_handle(f"key-{i}", secret=secret) for i in range(200)}
    # 200 draws from a ~102k space: collisions are possible but a large
    # cluster means the derivation is not spreading.
    assert len(handles) > 190


def test_handle_shape_is_adjective_noun_number() -> None:
    handle = derive_handle("abc123", secret=b"test-secret")
    adjective, noun, number = handle.split(" ")
    assert adjective in ADJECTIVES
    assert noun in NOUNS
    assert number.isdigit() and len(number) == 2


def test_handle_is_ascii_only() -> None:
    """CLAUDE.md: all user-visible copy is ASCII. A handle is copy."""
    for word in (*ADJECTIVES, *NOUNS):
        assert word.isascii()


def test_secret_changes_the_handle() -> None:
    """A rotated secret must not be reversible from an observed handle,
    so the mapping has to actually depend on it."""
    assert derive_handle("abc123", secret=b"one") != derive_handle("abc123", secret=b"two")


def test_hash_author_key_is_stable_and_not_the_key() -> None:
    assert hash_author_key("abc123") == hash_author_key("abc123")
    assert "abc123" not in hash_author_key("abc123")
    assert len(hash_author_key("abc123")) == 64
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest -n0 tests/test_comment_identity.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'splitsmith.comment_identity'`.

- [ ] **Step 3: Implement**

Create `src/splitsmith/comment_identity.py`:

```python
"""Server-derived display names for anonymous commenters.

The client mints one opaque ``author_key`` and keeps it in
localStorage. It never sends a display name, and the request model never
declares one -- if it did, anyone with ``curl`` could sign a comment with
the match owner's name, which is exactly the impersonation this design
set out to prevent.

The handle is ``HMAC(secret, author_key)`` indexed into a curated IPSC
wordlist, giving ``adjective noun NN`` -- "Prone Popper 47". The HMAC
secret is what stops an attacker grinding keys offline until one hashes
to a handle someone else is already using: without it the search space
is only ~102k and a laptop exhausts it instantly; with it, the only
attack left is posting repeatedly, which the rate limit sees.

Rotating the secret is safe. ``author_handle`` is denormalized onto every
comment row at write time, so existing comments keep the name they were
posted under; only a *new* comment from the same browser gets a new one.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Final

SPLITSMITH_COMMENT_HANDLE_SECRET_ENV: Final = "SPLITSMITH_COMMENT_HANDLE_SECRET"

# Longest client-supplied author key accepted. The client mints 32 random
# bytes hex-encoded (64 chars); the cap is generous headroom that still
# bounds what reaches the HMAC.
MAX_AUTHOR_KEY_LEN: Final = 128

ADJECTIVES: Final[tuple[str, ...]] = (
    "Steady", "Swift", "Silent", "Sharp", "Rapid", "Calm", "Bold", "Brisk",
    "Clean", "Crisp", "Eager", "Fast", "Flat", "Fluid", "Keen", "Level",
    "Lucky", "Nimble", "Precise", "Prone", "Quick", "Ready", "Rolling",
    "Smooth", "Snappy", "Solid", "Spare", "Tight", "Trusty", "Wide",
    "Willing", "Zeroed",
)

NOUNS: Final[tuple[str, ...]] = (
    "Alpha", "Charlie", "Delta", "Mike", "Popper", "Plate", "Star", "Squib",
    "Comstock", "Classifier", "Draw", "Hoser", "Berm", "Papa", "Port",
    "Reload", "Sierra", "Stage", "Steel", "Target", "Transition", "Trigger",
    "Wall", "Zebra", "Fault", "Gong", "Magwell", "Sight", "Holster", "Bay",
    "Squad", "Chrono",
)

# 32 * 32 * 100 = 102,400 distinct handles.
_NUMBERS: Final = 100

# Process-lifetime fallback when the env var is unset (local / dev). A
# random value would change handles on every restart, so this is a fixed
# string: local mode has one operator and no adversary to grind keys.
_DEV_SECRET: Final = b"splitsmith-local-comment-handles"


def handle_secret() -> bytes:
    """HMAC key for handle derivation.

    Hosted deploys set ``SPLITSMITH_COMMENT_HANDLE_SECRET``. An unset var
    falls back to a fixed dev value rather than a random one: a random
    per-process secret would hand every browser a new name on each
    redeploy, which reads as a bug rather than as security.
    """
    raw = os.environ.get(SPLITSMITH_COMMENT_HANDLE_SECRET_ENV, "").strip()
    return raw.encode("utf-8") if raw else _DEV_SECRET


def hash_author_key(author_key: str) -> str:
    """Storage form of the client's opaque key.

    Hashed so a database dump does not hand out the tokens that let
    someone delete other people's comments. Plain SHA-256 (not HMAC) is
    right here: the value it protects is high-entropy client randomness,
    not a guessable identifier, so there is nothing to brute-force.
    """
    return hashlib.sha256(author_key.encode("utf-8")).hexdigest()


def derive_handle(author_key: str, *, secret: bytes | None = None) -> str:
    """Deterministic IPSC-themed display name for an anonymous commenter.

    ``adjective noun NN``, e.g. "Prone Popper 47". Stable for a given
    key + secret, and unguessable in the other direction.
    """
    key = secret if secret is not None else handle_secret()
    digest = hmac.new(key, author_key.encode("utf-8"), hashlib.sha256).digest()
    value = int.from_bytes(digest[:8], "big")
    adjective = ADJECTIVES[value % len(ADJECTIVES)]
    noun = NOUNS[(value // len(ADJECTIVES)) % len(NOUNS)]
    number = (value // (len(ADJECTIVES) * len(NOUNS))) % _NUMBERS
    return f"{adjective} {noun} {number:02d}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -n0 tests/test_comment_identity.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/comment_identity.py tests/test_comment_identity.py
git commit -m "feat(comments): server-derived IPSC handles for anonymous commenters"
```

---

### Task 3: `CommentStore`

**Files:**
- Create: `src/splitsmith/db/comments.py`
- Test: `tests/test_comments_store.py`

**Interfaces:**
- Consumes: `CommentRow` (Task 1), `hash_author_key` / `derive_handle` (Task 2).
- Produces: `splitsmith.db.comments.CommentStore` and the frozen dataclass `Comment`:

```python
@dataclass(frozen=True)
class Comment:
    id: str
    anchor_t: float
    anchor_kind: str
    anchor_shot_id: str | None
    author_kind: str
    author_handle: str
    author_key_hash: str
    share_token_id: str
    body: str
    created_at: datetime
```

  Methods: `list_for_stage(match_id, slug, stage_number) -> list[Comment]`;
  `create(...) -> Comment`; `delete_own(comment_id, *, match_id, author_key_hash) -> bool`;
  `delete_as_owner(comment_id, *, match_id) -> bool`;
  `delete_by_share_token(match_id, share_token_id) -> int`;
  `delete_by_author_key_hash(match_id, author_key_hash) -> int`;
  `count_for_stage(match_id, slug, stage_number) -> int`;
  `purge_match(match_id) -> int`.

**`purge_match` is a hard delete, unlike every other delete here.** It exists
for match deletion, where the point is that the data is gone. Nothing cascades
from the matches registry row - `_delete_hosted` deletes `state_docs`
explicitly at step 6 for exactly this reason - so without it a deleted match
leaves its comments behind. Task 12 wires it in.

- [ ] **Step 1: Write the failing test**

Create `tests/test_comments_store.py`:

```python
"""CommentStore behaviour + the multi-tenant isolation guard.

``test_share_tokens_store.py`` sets the precedent twice over: the
``asyncio.run`` idiom (there is no async-test plugin in this repo), and
one isolation test per store method. A method that forgets its
``user_id`` filter is the single most damaging bug this table can carry,
because the owner tenant is pinned by impersonation on every anonymous
write.
"""

from __future__ import annotations

import asyncio

import pytest

from splitsmith.db import Base, User, create_engine, sessionmaker
from splitsmith.db.comments import CommentStore


def _engine_with_owner_and_other() -> sessionmaker:
    """Fresh in-memory engine seeded with two users: 'owner' and 'other'."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            s.add(User(id="owner", email="owner@example.com"))
            s.add(User(id="other", email="other@example.com"))
            await s.commit()

    asyncio.run(_setup())
    return session_factory


def _store(factory: sessionmaker, user_id: str = "owner") -> CommentStore:
    return CommentStore(factory, user_id=user_id)


def _seed(store: CommentStore, **over) -> str:
    """Create one comment with sensible defaults; return its id."""
    kwargs = dict(
        match_id="m1",
        slug="alice",
        stage_number=3,
        anchor_t=4.32,
        anchor_kind="time",
        anchor_shot_id=None,
        author_kind="handle",
        author_user_id=None,
        author_handle="Prone Popper 47",
        author_key_hash="hash-a",
        share_token_id="tok-1",
        body="reload looks early",
    )
    kwargs.update(over)
    return asyncio.run(store.create(**kwargs)).id


def _bodies(store: CommentStore, match_id: str = "m1", slug: str = "alice", stage: int = 3):
    return [c.body for c in asyncio.run(store.list_for_stage(match_id, slug, stage))]


def test_create_then_list_round_trips() -> None:
    store = _store(_engine_with_owner_and_other())
    _seed(store)
    got = asyncio.run(store.list_for_stage("m1", "alice", 3))
    assert [c.body for c in got] == ["reload looks early"]
    assert got[0].anchor_t == pytest.approx(4.32)


def test_list_is_oldest_first() -> None:
    store = _store(_engine_with_owner_and_other())
    _seed(store, body="first")
    _seed(store, body="second")
    assert _bodies(store) == ["first", "second"]


def test_list_is_scoped_to_stage_and_slug() -> None:
    store = _store(_engine_with_owner_and_other())
    _seed(store, body="alice s3")
    _seed(store, slug="bob", body="bob s3")
    _seed(store, stage_number=4, body="alice s4")
    assert _bodies(store) == ["alice s3"]


def test_list_omits_soft_deleted() -> None:
    store = _store(_engine_with_owner_and_other())
    cid = _seed(store)
    assert asyncio.run(store.delete_as_owner(cid, match_id="m1")) is True
    assert _bodies(store) == []


def test_delete_own_requires_the_matching_author_key() -> None:
    store = _store(_engine_with_owner_and_other())
    cid = _seed(store, author_key_hash="hash-a")
    wrong = asyncio.run(store.delete_own(cid, match_id="m1", author_key_hash="hash-b"))
    assert wrong is False
    assert len(_bodies(store)) == 1
    right = asyncio.run(store.delete_own(cid, match_id="m1", author_key_hash="hash-a"))
    assert right is True
    assert _bodies(store) == []


def test_bulk_delete_by_share_token() -> None:
    store = _store(_engine_with_owner_and_other())
    _seed(store, share_token_id="tok-1", body="from link 1")
    _seed(store, share_token_id="tok-2", body="from link 2")
    assert asyncio.run(store.delete_by_share_token("m1", "tok-1")) == 1
    assert _bodies(store) == ["from link 2"]


def test_bulk_delete_by_author_key_hash() -> None:
    store = _store(_engine_with_owner_and_other())
    _seed(store, author_key_hash="hash-a", body="nuisance")
    _seed(store, author_key_hash="hash-b", body="fine")
    assert asyncio.run(store.delete_by_author_key_hash("m1", "hash-a")) == 1
    assert _bodies(store) == ["fine"]


def test_purge_match_hard_deletes_every_stage() -> None:
    store = _store(_engine_with_owner_and_other())
    _seed(store, stage_number=3)
    _seed(store, stage_number=4, slug="bob")
    _seed(store, match_id="m2", body="other match")
    assert asyncio.run(store.purge_match("m1")) == 2
    assert _bodies(store) == []
    assert len(_bodies(store, match_id="m2")) == 1


def test_purge_match_removes_already_soft_deleted_rows() -> None:
    """Soft-deleted rows still hold a body. A match delete must take them
    too, or 'delete my match' leaves text behind."""
    store = _store(_engine_with_owner_and_other())
    cid = _seed(store)
    asyncio.run(store.delete_as_owner(cid, match_id="m1"))
    assert asyncio.run(store.purge_match("m1")) == 1


def test_count_for_stage_ignores_deleted() -> None:
    store = _store(_engine_with_owner_and_other())
    cid = _seed(store)
    assert asyncio.run(store.count_for_stage("m1", "alice", 3)) == 1
    asyncio.run(store.delete_as_owner(cid, match_id="m1"))
    assert asyncio.run(store.count_for_stage("m1", "alice", 3)) == 0


# --- isolation: one per method -------------------------------------------


def test_list_is_isolated_by_user() -> None:
    sf = _engine_with_owner_and_other()
    _seed(_store(sf, "owner"))
    assert asyncio.run(_store(sf, "other").list_for_stage("m1", "alice", 3)) == []


def test_delete_own_is_isolated_by_user() -> None:
    sf = _engine_with_owner_and_other()
    cid = _seed(_store(sf, "owner"))
    stolen = asyncio.run(
        _store(sf, "other").delete_own(cid, match_id="m1", author_key_hash="hash-a")
    )
    assert stolen is False
    assert len(_bodies(_store(sf, "owner"))) == 1


def test_delete_as_owner_is_isolated_by_user() -> None:
    sf = _engine_with_owner_and_other()
    cid = _seed(_store(sf, "owner"))
    assert asyncio.run(_store(sf, "other").delete_as_owner(cid, match_id="m1")) is False
    assert len(_bodies(_store(sf, "owner"))) == 1


def test_bulk_deletes_are_isolated_by_user() -> None:
    sf = _engine_with_owner_and_other()
    _seed(_store(sf, "owner"))
    other = _store(sf, "other")
    assert asyncio.run(other.delete_by_share_token("m1", "tok-1")) == 0
    assert asyncio.run(other.delete_by_author_key_hash("m1", "hash-a")) == 0
    assert len(_bodies(_store(sf, "owner"))) == 1


def test_purge_match_is_isolated_by_user() -> None:
    sf = _engine_with_owner_and_other()
    _seed(_store(sf, "owner"))
    assert asyncio.run(_store(sf, "other").purge_match("m1")) == 0
    assert len(_bodies(_store(sf, "owner"))) == 1


def test_store_refuses_an_empty_user_id() -> None:
    sf = _engine_with_owner_and_other()
    with pytest.raises(ValueError, match="non-empty user_id"):
        CommentStore(sf, user_id="")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest -n0 tests/test_comments_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'splitsmith.db.comments'`.

- [ ] **Step 3: Implement**

Create `src/splitsmith/db/comments.py`:

```python
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

from .models import CommentRow


@dataclass(frozen=True)
class Comment:
    id: str
    anchor_t: float
    anchor_kind: str
    anchor_shot_id: str | None
    author_kind: str
    author_handle: str
    author_key_hash: str
    share_token_id: str
    body: str
    created_at: datetime


def _to_comment(row: CommentRow) -> Comment:
    return Comment(
        id=row.id,
        anchor_t=row.anchor_t,
        anchor_kind=row.anchor_kind,
        anchor_shot_id=row.anchor_shot_id,
        author_kind=row.author_kind,
        author_handle=row.author_handle,
        author_key_hash=row.author_key_hash,
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
                    select(CommentRow)
                    .where(
                        CommentRow.user_id == self._user_id,
                        CommentRow.match_id == match_id,
                        CommentRow.slug == slug,
                        CommentRow.stage_number == stage_number,
                        CommentRow.deleted_at.is_(None),
                    )
                    # ULIDs sort by creation, so id alone is a stable
                    # oldest-first order without a second column.
                    .order_by(CommentRow.id.asc())
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
            share_token_id=share_token_id,
            body=body,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_comment(row)

    async def delete_own(self, comment_id: str, *, match_id: str, author_key_hash: str) -> bool:
        return await self._soft_delete_one(
            comment_id, match_id=match_id, author_key_hash=author_key_hash
        )

    async def delete_as_owner(self, comment_id: str, *, match_id: str) -> bool:
        return await self._soft_delete_one(comment_id, match_id=match_id, author_key_hash=None)

    async def _soft_delete_one(
        self, comment_id: str, *, match_id: str, author_key_hash: str | None
    ) -> bool:
        conditions = [
            CommentRow.user_id == self._user_id,
            CommentRow.id == comment_id,
            CommentRow.match_id == match_id,
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
        return await self._soft_delete_many(
            match_id, CommentRow.share_token_id == share_token_id
        )

    async def delete_by_author_key_hash(self, match_id: str, author_key_hash: str) -> int:
        return await self._soft_delete_many(
            match_id, CommentRow.author_key_hash == author_key_hash
        )

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -n0 tests/test_comments_store.py -q`
Expected: PASS (16 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/db/comments.py tests/test_comments_store.py
git commit -m "feat(db): CommentStore with soft delete and bulk moderation"
```

---

### Task 4: The write-capable share scope

**Files:**
- Modify: `src/splitsmith/db/share_guard.py` (`_WRITE_CAPABLE_SCOPES`)
- Modify: `src/splitsmith/ui/capabilities.py` (`COMMENT_WRITE`, `_SHARE_SCOPE_CAPABILITIES`)
- Modify: `src/splitsmith/db/share_tokens.py` (`ShareTokenStore.create` takes `scope`; `ShareToken` carries it)
- Test: `tests/test_share_comment_scope.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `splitsmith.ui.capabilities.COMMENT_WRITE = "comment_write"`
  - `splitsmith.db.share_guard.COMMENT_SCOPE = "comment"`
  - `ShareTokenStore.create(match_id: str, *, scope: str = "read") -> ShareToken`
  - `ShareToken.scope: str`

**The load-bearing test here** is that adding a member to `_WRITE_CAPABLE_SCOPES` did not weaken the read path for everyone. Write that one first.

- [ ] **Step 1: Write the failing test**

Create `tests/test_share_comment_scope.py`:

```python
"""The 'comment' share scope and what it does (and does not) unlock."""

from __future__ import annotations

import pytest

from splitsmith.ui.capabilities import (
    COMMENT_WRITE,
    EDIT,
    REVIEW,
    SHARE_MANAGE,
    share_scope_capabilities,
)

share_guard = pytest.importorskip("splitsmith.db.share_guard")


def _with_scope(scope):  # type: ignore[no-untyped-def]
    token = share_guard.current_share_scope.set(scope)
    try:
        return share_guard.share_request_is_read_only()
    finally:
        share_guard.current_share_scope.reset(token)


def test_read_scope_is_still_read_only() -> None:
    """The regression that matters: _WRITE_CAPABLE_SCOPES gaining a
    member must not turn the check off for the scope every existing
    share link carries."""
    assert _with_scope("read") is True


def test_unknown_scope_still_fails_closed() -> None:
    assert _with_scope("kommentar") is True
    assert _with_scope("") is True


def test_comment_scope_is_write_capable() -> None:
    assert _with_scope("comment") is False


def test_no_scope_at_all_is_not_a_share_request() -> None:
    assert _with_scope(None) is False


def test_comment_scope_grants_only_comment_write() -> None:
    caps = share_scope_capabilities("comment")
    assert COMMENT_WRITE in caps
    assert EDIT not in caps
    assert REVIEW not in caps
    assert SHARE_MANAGE not in caps


def test_read_scope_grants_nothing() -> None:
    assert share_scope_capabilities("read") == frozenset()


def test_unknown_scope_grants_nothing() -> None:
    assert share_scope_capabilities("comment ") == frozenset()
    assert share_scope_capabilities(None) == frozenset()
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest -n0 tests/test_share_comment_scope.py -q`
Expected: FAIL with `ImportError: cannot import name 'COMMENT_WRITE'`.

- [ ] **Step 3: Wire the scope**

In `src/splitsmith/db/share_guard.py`, replace the `_WRITE_CAPABLE_SCOPES` definition:

```python
# The one write-capable scope: a link minted for commenting. Named here
# rather than inline so the routes and the token store agree on the
# spelling.
COMMENT_SCOPE = "comment"

# Scopes allowed to write through share auth. Any scope NOT in this set
# is treated as read-only, so an unknown or mistyped scope fails closed
# instead of silently skipping every defense layer. "read" - which every
# token minted before comments shipped carries - is deliberately absent,
# so turning this feature on cannot retroactively open a link that is
# already in someone's inbox.
_WRITE_CAPABLE_SCOPES: frozenset[str] = frozenset({COMMENT_SCOPE})
```

In `src/splitsmith/ui/capabilities.py`, add the capability and the mapping:

```python
COMMENT_WRITE = "comment_write"
```

(next to `EDIT` / `REVIEW` / `SHARE_MANAGE`), extend the module docstring's capability list with:

```
- ``comment_write``: posting and self-deleting a timestamped comment on
  the anonymous share surface. Granted only by the ``comment`` share
  scope - never by ``capabilities_for_origin``, because an authenticated
  operator editing their own match has no use for it.
```

and replace the scope map:

```python
# Share-token scopes -> capability sets. 'read' grants nothing; 'comment'
# is the first write-capable scope (the one #779 anticipated).
_SHARE_SCOPE_CAPABILITIES: dict[str, frozenset[str]] = {
    "read": frozenset(),
    "comment": frozenset({COMMENT_WRITE}),
}
```

In `src/splitsmith/db/share_tokens.py`, add `scope` to the `ShareToken` dataclass and to `_to_share_token`, and give `create` a keyword-only `scope`:

```python
@dataclass(frozen=True)
class ShareToken:
    id: str
    match_id: str
    token: str
    created_at: datetime
    revoked_at: datetime | None
    scope: str
```

```python
def _to_share_token(row: ShareTokenRow) -> ShareToken:
    return ShareToken(
        id=row.id,
        match_id=row.match_id,
        token=row.token,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        scope=row.scope,
    )
```

```python
    async def create(self, match_id: str, *, scope: str = "read") -> ShareToken:
        """Mint a link. ``scope`` is fixed for the token's whole life -
        there is deliberately no route that changes it, so an owner can
        reason about a link they already sent from the moment they sent
        it."""
        if scope not in ("read", "comment"):
            raise ValueError(f"unknown share scope {scope!r}")
        row = ShareTokenRow(
            user_id=self._user_id,
            match_id=match_id,
            token=secrets.token_urlsafe(32),
            scope=scope,
        )
        ...
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -n0 tests/test_share_comment_scope.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Run the existing share suites for regressions**

Run: `uv run pytest -n0 tests/test_share_tokens_store.py tests/test_ui_server.py -q -k "share"`
Expected: PASS. `ShareToken` gained a field and `create` gained a keyword; if a test constructs `ShareToken(...)` positionally it needs `scope="read"` added.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/db/share_guard.py src/splitsmith/ui/capabilities.py src/splitsmith/db/share_tokens.py tests/test_share_comment_scope.py
git commit -m "feat(share): comment scope joins _WRITE_CAPABLE_SCOPES"
```

---

### Task 5: The write allowlist in `_share_alias`

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`_SHARE_PATH_RE` gains the comments read shape; new `_SHARE_WRITE_PATH_RE`; `_share_alias` method handling)
- Test: `tests/test_share_write_allowlist.py`

**Interfaces:**
- Consumes: `COMMENT_SCOPE` (Task 4).
- Produces: `_SHARE_WRITE_PATH_RE` and the admission rule. No new callables.

- [ ] **Step 1: Write the failing test**

Create `tests/test_share_write_allowlist.py`:

```python
"""The containment boundary for anonymous writes.

These are the tests that matter most on this branch. The player either
works or obviously does not; a hole here is silent.
"""

from __future__ import annotations

import re

from splitsmith.ui.server import _SHARE_PATH_RE, _SHARE_WRITE_PATH_RE

COMMENTS = "shooters/alice/stages/3/comments"


def test_read_pattern_admits_the_comment_thread() -> None:
    assert _SHARE_PATH_RE.fullmatch(COMMENTS)


def test_read_pattern_does_not_admit_a_comment_id() -> None:
    """Reading one comment by id is not a shape we serve; the thread is."""
    assert _SHARE_PATH_RE.fullmatch(f"{COMMENTS}/01J000000000000000000000") is None


def test_write_pattern_admits_post_and_delete_shapes_only() -> None:
    assert _SHARE_WRITE_PATH_RE.fullmatch(COMMENTS)
    assert _SHARE_WRITE_PATH_RE.fullmatch(f"{COMMENTS}/01J000000000000000000000")


def test_write_pattern_admits_nothing_else_from_the_read_surface() -> None:
    """The two patterns are separate on purpose. If someone ever merges
    them, this fails."""
    for shape in (
        "match/shooters",
        "shooters/alice/project",
        "shooters/alice/stages/3/coach",
        "shooters/alice/coach/distributions",
        "shooters/alice/videos/stream",
        "match/stage/3/compare",
        "match/shooters/alice/videos/stream",
        "og.png",
        "og-meta",
    ):
        assert _SHARE_WRITE_PATH_RE.fullmatch(shape) is None, shape


def test_write_pattern_rejects_traversal_and_extra_segments() -> None:
    for shape in (
        "shooters/alice/stages/3/comments/../../../match/shooters",
        "shooters/alice/stages/3/comments/abc/def",
        "shooters/alice/stages/x/comments",
        "shooters/alice/stages/3/comments/",
        "SHOOTERS/alice/stages/3/comments",
    ):
        assert _SHARE_WRITE_PATH_RE.fullmatch(shape) is None, shape


def test_write_pattern_is_anchored_against_a_trailing_newline() -> None:
    """_REVIEW_ROUTES documents why \\Z beats $ on an allow-list: plain $
    also matches before one trailing newline, and the widened form grants
    more than intended."""
    assert _SHARE_WRITE_PATH_RE.fullmatch(f"{COMMENTS}\n") is None
    assert _SHARE_WRITE_PATH_RE.pattern.endswith(r")\Z")
    assert _SHARE_WRITE_PATH_RE.flags & re.IGNORECASE == 0
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest -n0 tests/test_share_write_allowlist.py -q`
Expected: FAIL with `ImportError: cannot import name '_SHARE_WRITE_PATH_RE'`.

- [ ] **Step 3: Add the read shape and the write pattern**

In `src/splitsmith/ui/server.py`, add one alternative to `_SHARE_PATH_RE`, immediately after the `shooters/[^/]+/stages/\d+/coach` line:

```python
    # The comment thread. Readable through ANY scope, including plain
    # "read" - a link already in the wild shows the conversation but
    # cannot join it. Posting is _SHARE_WRITE_PATH_RE's business.
    r"|shooters/[^/]+/stages/\d+/comments"
```

Then, immediately after the `_SHARE_PATH_RE` definition, add:

```python
# The anonymous WRITE surface - deliberately a second pattern rather than
# an extension of _SHARE_PATH_RE, whose docstring calls itself GET-only
# and must stay true. Admission requires all three of: a shape here, a
# method in _SHARE_WRITE_METHODS, and a resolved token whose scope is
# write-capable (db.share_guard._WRITE_CAPABLE_SCOPES). Any one missing
# is the same opaque 404 as an unknown token, so the write surface is not
# discoverable by probing.
#
# ``\A``/``\Z`` rather than ``^``/``$`` for the reason _REVIEW_ROUTES
# documents: plain ``$`` also matches just before a single trailing
# newline, and on an allow-list that direction is the unsafe one.
_SHARE_WRITE_PATH_RE = re.compile(
    r"\A(?:shooters/[^/]+/stages/\d+/comments"
    r"|shooters/[^/]+/stages/\d+/comments/[A-Za-z0-9]+)\Z"
)
_SHARE_WRITE_METHODS = frozenset({"POST", "DELETE"})
```

- [ ] **Step 4: Admit the write in `_share_alias`**

In `_share_alias`, replace the admission line:

```python
        if not sep or not token or request.method != "GET" or not _SHARE_PATH_RE.fullmatch(rest):
            return not_found
```

with:

```python
        if not sep or not token:
            return not_found
        method = request.method
        if method == "GET":
            if not _SHARE_PATH_RE.fullmatch(rest):
                return not_found
            needs_write_scope = False
        elif method in _SHARE_WRITE_METHODS:
            if not _SHARE_WRITE_PATH_RE.fullmatch(rest):
                return not_found
            needs_write_scope = True
        else:
            return not_found
```

and, immediately after `resolved` is checked for `None`, add the scope gate:

```python
        if needs_write_scope:
            # Lazy import for the same reason the share_guard import below
            # is lazy: this middleware runs on every request in local mode
            # too, where splitsmith.db may not be installed.
            from ..db.share_guard import _WRITE_CAPABLE_SCOPES

            if resolved.scope not in _WRITE_CAPABLE_SCOPES:
                return not_found
```

Note for the implementer: `_WRITE_CAPABLE_SCOPES` is currently private. Export a public reader alongside it in `share_guard.py` rather than importing the underscore name:

```python
def scope_may_write(scope: str | None) -> bool:
    """Whether a resolved token's scope may pass the write allowlist.

    Fails closed: unknown, empty and None all return False.
    """
    return scope is not None and scope in _WRITE_CAPABLE_SCOPES
```

and use `if not scope_may_write(resolved.scope): return not_found`.

- [ ] **Step 4b: Map the comment routes in the capability table**

Admission is not the last gate. `required_capability` (`ui/capabilities.py`)
returns `EDIT` for any unclassified write, and a `comment`-scoped token grants
only `COMMENT_WRITE` - so without an entry here every admitted comment write is
refused **403** while every other refusal on this surface is an opaque 404.
That breaks the feature *and* tells a prober exactly which write shapes the
allowlist admits.

```python
# The comment routes on the anonymous share surface. Mapped explicitly
# rather than falling through to EDIT: a comment-scoped token grants only
# COMMENT_WRITE, and an unmapped route would refuse with a 403 among
# 404s - a discriminator that enumerates the write allowlist.
_COMMENT_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"\Ashooters/[^/]+/stages/\d+/comments\Z")),
    ("DELETE", re.compile(r"\Ashooters/[^/]+/stages/\d+/comments/[A-Za-z0-9]+\Z")),
)
```

consulted in `required_capability` after the `match/shares` check:

```python
    for allowed_method, pattern in _COMMENT_ROUTES:
        if method == allowed_method and pattern.match(rest) is not None:
            return COMMENT_WRITE
```

`capabilities_for_origin` also gains `COMMENT_WRITE` for the `desktop` and
`hosted` sets - the owner moderates comments on their own match through the
same per-stage DELETE route, and a mirror keeps that for the same reason it
keeps `SHARE_MANAGE`. `local` does not: there is no share surface and so no
comments.

Leave `match/comments` (Task 8's bulk moderation) unmapped - it falls through
to `EDIT`, which no share scope grants, and it is not in the write allowlist
either. Two independent gates, deliberately.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -n0 tests/test_share_write_allowlist.py -q`
Expected: PASS (6 tests).

Also run the capability suite - `capabilities_for_origin` changed shape:
`uv run pytest -n0 -k "capabilit" -q`

- [ ] **Step 6: Run the existing share suite for regressions**

Run: `uv run pytest -n0 tests/test_ui_server.py -q -k "share"`
Expected: PASS. Every existing non-GET-on-share test must still 404.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/ui/server.py src/splitsmith/db/share_guard.py tests/test_share_write_allowlist.py
git commit -m "feat(share): separate write allowlist gated on a write-capable scope"
```

---

### Task 6: Anonymous comment routes

**Files:**
- Create: `src/splitsmith/ui/comments.py` (request/response models + the pure helpers)
- Modify: `src/splitsmith/ui/server.py` (three routes, next to the existing share routes; `AppState.comments` property)
- Test: `tests/test_comments_api.py`

**Interfaces:**
- Consumes: `CommentStore`, `Comment` (Task 3); `derive_handle`, `hash_author_key`, `MAX_AUTHOR_KEY_LEN` (Task 2); the allowlist (Task 5).
- Produces:
  - `splitsmith.ui.comments.CommentCreateRequest` -- fields `body: str`, `anchor_t: float`, `anchor_kind: Literal["time", "shot"] = "time"`, `anchor_shot_id: str | None = None`. **No other fields.**
  - `splitsmith.ui.comments.CommentOut` / `CommentListResponse`
  - `splitsmith.ui.comments.AUTHOR_KEY_HEADER = "X-Splitsmith-Author-Key"`
  - `splitsmith.ui.comments.BODY_MAX_CHARS = 1000`, `STAGE_COMMENT_CAP = 500`
  - `splitsmith.ui.comments.to_out(comment, *, author_key_hash, owner_view) -> CommentOut`

- [ ] **Step 1: Write the failing test**

Create `tests/test_comments_api.py`. Follow the existing share-endpoint fixtures in `tests/test_ui_server.py` for building a hosted-mode app with a share token; the tests below name the behaviour, and the implementer wires them to that fixture.

```python
"""Anonymous comment endpoints on the share surface.

Adversarial cases first. The happy path either works or obviously does
not; the containment properties fail silently.
"""

from __future__ import annotations

import pytest

from splitsmith.ui.comments import AUTHOR_KEY_HEADER, BODY_MAX_CHARS

NOT_FOUND = {"detail": "not found"}
KEY = "a" * 64


def _post(client, token, *, key=KEY, **body):
    payload = {"body": "reload looks early", "anchor_t": 4.32, **body}
    return client.post(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        json=payload,
        headers={AUTHOR_KEY_HEADER: key},
    )


# --- containment ---------------------------------------------------------

def test_post_through_a_read_scoped_token_is_the_uniform_404(read_token_client) -> None:
    client, token = read_token_client
    resp = _post(client, token)
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


def test_read_token_404_is_identical_to_an_unknown_token_404(read_token_client) -> None:
    client, token = read_token_client
    denied = _post(client, token)
    unknown = _post(client, "not-a-real-token")
    assert (denied.status_code, denied.json()) == (unknown.status_code, unknown.json())


def test_comment_token_cannot_reach_a_non_allowlisted_write_path(comment_token_client) -> None:
    client, token = comment_token_client
    resp = client.post(
        f"/api/share/{token}/shooters/alice/stages/3/audit/accept",
        json={},
        headers={AUTHOR_KEY_HEADER: KEY},
    )
    assert resp.status_code == 404


def test_comment_token_cannot_use_an_unlisted_method(comment_token_client) -> None:
    client, token = comment_token_client
    resp = client.put(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        json={"body": "x", "anchor_t": 1.0},
        headers={AUTHOR_KEY_HEADER: KEY},
    )
    assert resp.status_code == 404


def test_body_cannot_set_owner_or_author_fields(comment_token_client, other_user_id) -> None:
    """A crafted POST must not choose its own name or move the row into
    another tenant."""
    client, token = comment_token_client
    resp = _post(
        client,
        token,
        author_handle="Mathias Axell",
        author_user_id=other_user_id,
        user_id=other_user_id,
        match_id="some-other-match",
        author_kind="account",
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["author_handle"] != "Mathias Axell"
    assert created["author_kind"] == "handle"


def test_list_never_exposes_author_key_hash_or_share_token(comment_token_client) -> None:
    client, token = comment_token_client
    _post(client, token)
    body = client.get(f"/api/share/{token}/shooters/alice/stages/3/comments").json()
    assert body["comments"]
    for comment in body["comments"]:
        assert "author_key_hash" not in comment
        assert "share_token_id" not in comment
        assert "author_user_id" not in comment


# --- read scope sees the thread but cannot join it -----------------------

def test_read_scoped_token_can_read_the_thread(read_token_client, comment_token_client) -> None:
    writer, write_token = comment_token_client
    _post(writer, write_token)
    reader, read_token = read_token_client
    resp = reader.get(f"/api/share/{read_token}/shooters/alice/stages/3/comments")
    assert resp.status_code == 200
    assert len(resp.json()["comments"]) == 1


# --- happy path + validation --------------------------------------------

def test_post_then_list_round_trips(comment_token_client) -> None:
    client, token = comment_token_client
    created = _post(client, token).json()
    listed = client.get(f"/api/share/{token}/shooters/alice/stages/3/comments").json()
    assert [c["id"] for c in listed["comments"]] == [created["id"]]
    assert listed["comments"][0]["body"] == "reload looks early"


def test_handle_is_stable_across_two_posts_from_one_key(comment_token_client) -> None:
    client, token = comment_token_client
    first = _post(client, token, body="one").json()
    second = _post(client, token, body="two").json()
    assert first["author_handle"] == second["author_handle"]


def test_a_different_key_gets_a_different_handle(comment_token_client) -> None:
    client, token = comment_token_client
    first = _post(client, token, key="a" * 64).json()
    second = _post(client, token, key="b" * 64).json()
    assert first["author_handle"] != second["author_handle"]


def test_shot_anchor_keeps_both_fields(comment_token_client) -> None:
    client, token = comment_token_client
    created = _post(client, token, anchor_kind="shot", anchor_shot_id="cand-7").json()
    assert created["anchor_kind"] == "shot"
    assert created["anchor_shot_id"] == "cand-7"
    assert created["anchor_t"] == pytest.approx(4.32)


def test_shot_kind_without_a_shot_id_is_rejected(comment_token_client) -> None:
    client, token = comment_token_client
    resp = _post(client, token, anchor_kind="shot", anchor_shot_id=None)
    assert resp.status_code == 422


def test_empty_body_is_rejected(comment_token_client) -> None:
    client, token = comment_token_client
    assert _post(client, token, body="   ").status_code == 422


def test_oversized_body_is_rejected(comment_token_client) -> None:
    client, token = comment_token_client
    assert _post(client, token, body="x" * (BODY_MAX_CHARS + 1)).status_code == 422


def test_missing_author_key_header_is_rejected(comment_token_client) -> None:
    client, token = comment_token_client
    resp = client.post(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        json={"body": "hi", "anchor_t": 1.0},
    )
    assert resp.status_code == 422


def test_anchor_t_is_clamped_and_rounded(comment_token_client) -> None:
    client, token = comment_token_client
    created = _post(client, token, anchor_t=9999.999).json()
    assert created["anchor_t"] == pytest.approx(3600.0)
    created = _post(client, token, anchor_t=1.23456).json()
    assert created["anchor_t"] == pytest.approx(1.23)


# --- self delete ---------------------------------------------------------

def test_author_can_delete_their_own_comment(comment_token_client) -> None:
    client, token = comment_token_client
    cid = _post(client, token).json()["id"]
    resp = client.delete(
        f"/api/share/{token}/shooters/alice/stages/3/comments/{cid}",
        headers={AUTHOR_KEY_HEADER: KEY},
    )
    assert resp.status_code == 204
    assert client.get(f"/api/share/{token}/shooters/alice/stages/3/comments").json()["comments"] == []


def test_another_key_cannot_delete_it(comment_token_client) -> None:
    client, token = comment_token_client
    cid = _post(client, token).json()["id"]
    resp = client.delete(
        f"/api/share/{token}/shooters/alice/stages/3/comments/{cid}",
        headers={AUTHOR_KEY_HEADER: "b" * 64},
    )
    assert resp.status_code == 404


def test_mine_is_true_only_for_the_posting_key(comment_token_client) -> None:
    client, token = comment_token_client
    _post(client, token)
    mine = client.get(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        headers={AUTHOR_KEY_HEADER: KEY},
    ).json()["comments"][0]
    theirs = client.get(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        headers={AUTHOR_KEY_HEADER: "b" * 64},
    ).json()["comments"][0]
    anonymous = client.get(
        f"/api/share/{token}/shooters/alice/stages/3/comments"
    ).json()["comments"][0]
    assert mine["mine"] is True
    assert theirs["mine"] is False
    assert anonymous["mine"] is False
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest -n0 tests/test_comments_api.py -q`
Expected: FAIL - `ModuleNotFoundError: No module named 'splitsmith.ui.comments'`. Fixtures `read_token_client`, `comment_token_client`, `other_user_id` do not exist yet either; add them to this file, modelled on the hosted-mode share fixtures already in `tests/test_ui_server.py`.

- [ ] **Step 3: Write the models module**

Create `src/splitsmith/ui/comments.py`:

```python
"""Request/response models and pure helpers for timestamped comments.

The route handlers live in ``server.py`` next to the other share routes
(this codebase declares routes inline; #680 tracks the router split).
What lives here is everything that can be tested without an app: the
request model whose *absent* fields are load-bearing, the clamping rule,
and the response projection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..db.comments import Comment

AUTHOR_KEY_HEADER: Final = "X-Splitsmith-Author-Key"

BODY_MAX_CHARS: Final = 1000
# Refuse further comments on a stage past this many. A blunt backstop
# against one link being used to fill a table, distinct from the rate
# limit which bounds speed rather than total.
STAGE_COMMENT_CAP: Final = 500

# Same bound the frontend's parseMoment enforces, and the same one
# share_og.py clamps a moment card to.
T_LIMIT: Final = 3600.0


class CommentCreateRequest(BaseModel):
    """What an anonymous commenter may say.

    The fields that are NOT here are the point: ``author_handle``,
    ``author_kind``, ``author_user_id``, ``user_id``, ``match_id``,
    ``slug`` and ``stage_number`` are all server-side facts. pydantic
    ignores unknown keys by default, so a crafted body carrying them is
    silently dropped rather than rejected - which is what we want; a 422
    would tell a prober which names exist.
    """

    body: str = Field(min_length=1, max_length=BODY_MAX_CHARS)
    anchor_t: float
    anchor_kind: Literal["time", "shot"] = "time"
    anchor_shot_id: str | None = Field(default=None, max_length=128)

    @field_validator("body")
    @classmethod
    def _body_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("body must not be blank")
        return stripped

    @field_validator("anchor_t")
    @classmethod
    def _clamp_and_round(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("anchor_t must be finite")
        return round(max(-T_LIMIT, min(T_LIMIT, value)), 2)

    @model_validator(mode="after")
    def _shot_anchor_carries_a_shot_id(self) -> "CommentCreateRequest":
        if self.anchor_kind == "shot" and not self.anchor_shot_id:
            raise ValueError("anchor_kind='shot' requires anchor_shot_id")
        if self.anchor_kind == "time" and self.anchor_shot_id:
            raise ValueError("anchor_shot_id is only valid with anchor_kind='shot'")
        return self


class CommentOut(BaseModel):
    id: str
    anchor_t: float
    anchor_kind: str
    anchor_shot_id: str | None
    author_kind: str
    author_handle: str
    body: str
    created_at: datetime
    mine: bool
    # Owner view only - the two bulk-moderation actions need them. Absent
    # (None, excluded on serialization) for anonymous callers.
    share_token_id: str | None = None
    author_key_hash: str | None = None


class CommentListResponse(BaseModel):
    comments: list[CommentOut]


def to_out(comment: Comment, *, author_key_hash: str | None, owner_view: bool) -> CommentOut:
    """Project a stored comment for the wire.

    ``author_key_hash`` is the *caller's*, used only to compute ``mine``;
    a caller who sent no key gets ``mine=False`` everywhere, which is the
    correct answer for a first-time reader.
    """
    return CommentOut(
        id=comment.id,
        anchor_t=comment.anchor_t,
        anchor_kind=comment.anchor_kind,
        anchor_shot_id=comment.anchor_shot_id,
        author_kind=comment.author_kind,
        author_handle=comment.author_handle,
        body=comment.body,
        created_at=comment.created_at,
        mine=author_key_hash is not None and comment.author_key_hash == author_key_hash,
        share_token_id=comment.share_token_id if owner_view else None,
        author_key_hash=comment.author_key_hash if owner_view else None,
    )
```

- [ ] **Step 4: Add the routes**

In `src/splitsmith/ui/server.py`, add a `comments` property to `AppState` mirroring the existing `share_tokens` property (constructing `CommentStore(self._session_factory, user_id=<resolved tenant user id>)`, returning `None` outside hosted mode).

Then, next to the `/api/match/shares` routes, add:

```python
    @app.get(
        "/api/shooters/{slug}/stages/{stage_number}/comments",
        response_model=CommentListResponse,
    )
    async def list_stage_comments(
        slug: str,
        stage_number: int,
        request: Request,
    ) -> CommentListResponse:
        """The comment thread. Readable through any share scope, and by
        the owner on their own routes."""
        store = state.comments
        mid = current_match_id.get()
        if store is None or mid is None:
            raise HTTPException(status_code=404, detail="not found")
        caller_hash = _caller_author_key_hash(request)
        owner_view = not current_share_request.get()
        comments = await store.list_for_stage(mid, slug, stage_number)
        return CommentListResponse(
            comments=[
                to_out(c, author_key_hash=caller_hash, owner_view=owner_view) for c in comments
            ]
        )

    @app.post(
        "/api/shooters/{slug}/stages/{stage_number}/comments",
        response_model=CommentOut,
        status_code=201,
    )
    async def create_stage_comment(
        slug: str,
        stage_number: int,
        req: CommentCreateRequest,
        request: Request,
    ) -> CommentOut:
        """Post a comment. Reachable only through a comment-scoped share
        link: the write allowlist plus the scope gate in ``_share_alias``
        are what admit this, and an owner posting on their own footage is
        deliberately not a use case v1 serves."""
        store = state.comments
        mid = current_match_id.get()
        share_token_id = getattr(request.state, "share_token_id", None)
        if store is None or mid is None or share_token_id is None:
            raise HTTPException(status_code=404, detail="not found")
        author_key = _require_author_key(request)
        if await store.count_for_stage(mid, slug, stage_number) >= STAGE_COMMENT_CAP:
            raise HTTPException(
                status_code=429,
                detail={"code": "comment_stage_cap", "message": "this stage has too many comments"},
            )
        created = await store.create(
            match_id=mid,
            slug=slug,
            stage_number=stage_number,
            anchor_t=req.anchor_t,
            anchor_kind=req.anchor_kind,
            anchor_shot_id=req.anchor_shot_id,
            author_kind="handle",
            author_user_id=None,
            author_handle=derive_handle(author_key),
            author_key_hash=hash_author_key(author_key),
            share_token_id=share_token_id,
            body=req.body,
        )
        return to_out(created, author_key_hash=created.author_key_hash, owner_view=False)

    @app.delete(
        "/api/shooters/{slug}/stages/{stage_number}/comments/{comment_id}",
        status_code=204,
    )
    async def delete_stage_comment(
        slug: str,
        stage_number: int,
        comment_id: str,
        request: Request,
    ) -> Response:
        """Delete one comment. An anonymous caller may delete only their
        own, matched on the hashed author key; the owner may delete any.
        Both refusals are the same 404."""
        store = state.comments
        mid = current_match_id.get()
        if store is None or mid is None:
            raise HTTPException(status_code=404, detail="not found")
        if current_share_request.get():
            author_key = _require_author_key(request)
            ok = await store.delete_own(
                comment_id, match_id=mid, author_key_hash=hash_author_key(author_key)
            )
        else:
            ok = await store.delete_as_owner(comment_id, match_id=mid)
        if not ok:
            raise HTTPException(status_code=404, detail="not found")
        return Response(status_code=204)
```

with these two helpers near the other private request helpers:

```python
def _caller_author_key_hash(request: Request) -> str | None:
    """Hashed author key from the request header, or None.

    Optional on reads: a caller who sends none gets ``mine=False``
    everywhere, which is right for a first-time reader.
    """
    raw = request.headers.get(AUTHOR_KEY_HEADER, "").strip()
    if not raw or len(raw) > MAX_AUTHOR_KEY_LEN:
        return None
    return hash_author_key(raw)


def _require_author_key(request: Request) -> str:
    """Author key for a write. 422 rather than 404 when missing: the
    caller already passed the scope gate, so an honest error is right."""
    raw = request.headers.get(AUTHOR_KEY_HEADER, "").strip()
    if not raw or len(raw) > MAX_AUTHOR_KEY_LEN:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "author_key_required",
                "message": f"{AUTHOR_KEY_HEADER} must carry an opaque client key",
            },
        )
    return raw
```

**`request.state.share_token_id`:** `_share_alias` currently stashes `request.state.share_token` (the raw token). Add the row id alongside it - `ResolvedShare` needs a `share_token_id` field, populated in `resolve_share_token` from `row.id`, and `_share_alias` sets `request.state.share_token_id = resolved.share_token_id`. Update `test_share_tokens_store.py` for the new `ResolvedShare` field.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -n0 tests/test_comments_api.py -q`
Expected: PASS (20 tests).

- [ ] **Step 5b: Prove the scope gate is actually covered**

Task 5 added the scope gate in `_share_alias` but could not test it: with no
route behind the path, an admitted write and a refused one both ended as 404,
so deleting the entire gate left 154 tests green. This task is the first point
where the gate is observable, and these tests are what cover it.

Delete the whole `if needs_write_scope:` block from `_share_alias`, run
`uv run pytest -n0 tests/test_comments_api.py -q`, and confirm
`test_post_through_a_read_scoped_token_is_the_uniform_404` **fails**. Restore
the block. Record the output in your report.

If it still passes, the gate remains uncovered and the task is not done -
say so rather than moving on.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui/comments.py src/splitsmith/ui/server.py src/splitsmith/db/share_tokens.py tests/test_comments_api.py
git commit -m "feat(api): anonymous comment thread on the share surface"
```

---

### Task 7: Rate limiting the anonymous write

**Files:**
- Modify: `src/splitsmith/ui/comments.py` (`CommentRateLimiter`)
- Modify: `src/splitsmith/ui/server.py` (apply it in `create_stage_comment`)
- Test: `tests/test_comment_rate_limit.py`

**Interfaces:**
- Consumes: `AUTHOR_KEY_HEADER` (Task 6).
- Produces: `splitsmith.ui.comments.CommentRateLimiter` with
  `allow(key: str, *, now: float) -> bool`, constructor
  `CommentRateLimiter(*, limit: int = 5, window_s: float = 60.0, max_keys: int = 10_000)`.

In-process and per-replica on purpose: this is a spam speed bump, not a security control, and a shared counter would mean Redis, which is a new dependency.

- [ ] **Step 1: Write the failing test**

Create `tests/test_comment_rate_limit.py`:

```python
"""Per-key comment rate limiting."""

from __future__ import annotations

from splitsmith.ui.comments import CommentRateLimiter


def test_allows_up_to_the_limit_then_refuses() -> None:
    limiter = CommentRateLimiter(limit=3, window_s=60.0)
    assert [limiter.allow("k", now=0.0) for _ in range(3)] == [True, True, True]
    assert limiter.allow("k", now=0.0) is False


def test_window_slides() -> None:
    limiter = CommentRateLimiter(limit=1, window_s=60.0)
    assert limiter.allow("k", now=0.0) is True
    assert limiter.allow("k", now=59.0) is False
    assert limiter.allow("k", now=61.0) is True


def test_keys_are_independent() -> None:
    limiter = CommentRateLimiter(limit=1, window_s=60.0)
    assert limiter.allow("a", now=0.0) is True
    assert limiter.allow("b", now=0.0) is True


def test_key_table_is_bounded() -> None:
    """An attacker rotating author keys must not grow the table without
    bound - that would turn a spam control into a memory leak."""
    limiter = CommentRateLimiter(limit=1, window_s=60.0, max_keys=10)
    for i in range(100):
        limiter.allow(f"key-{i}", now=float(i))
    assert limiter.size() <= 10
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest -n0 tests/test_comment_rate_limit.py -q`
Expected: FAIL with `ImportError: cannot import name 'CommentRateLimiter'`.

- [ ] **Step 3: Implement**

Append to `src/splitsmith/ui/comments.py`:

```python
class CommentRateLimiter:
    """Sliding-window comment limiter keyed by hashed author key.

    In-process and per-replica by design. This is a spam speed bump, not
    a security control - the security properties are the scope gate and
    the allowlist. A shared counter would mean Redis, which is a new
    dependency, and the thing it would buy (exact limits across
    replicas) is not worth that on a personal tool's share surface.

    ``max_keys`` bounds the table so an attacker rotating author keys
    turns a spam control into a bigger table rather than a memory leak;
    the oldest entries are evicted first.
    """

    def __init__(self, *, limit: int = 5, window_s: float = 60.0, max_keys: int = 10_000) -> None:
        self._limit = limit
        self._window_s = window_s
        self._max_keys = max_keys
        self._hits: OrderedDict[str, list[float]] = OrderedDict()

    def allow(self, key: str, *, now: float) -> bool:
        stamps = [t for t in self._hits.get(key, ()) if now - t < self._window_s]
        if len(stamps) >= self._limit:
            self._hits[key] = stamps
            self._hits.move_to_end(key)
            return False
        stamps.append(now)
        self._hits[key] = stamps
        self._hits.move_to_end(key)
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)
        return True

    def size(self) -> int:
        return len(self._hits)
```

with `from collections import OrderedDict` added to the imports.

- [ ] **Step 4: Apply it in the route**

In `server.py`, construct one limiter at app build time (`_comment_limiter = CommentRateLimiter()`) and add to `create_stage_comment`, immediately after `author_key = _require_author_key(request)`:

```python
        if not _comment_limiter.allow(hash_author_key(author_key), now=time.monotonic()):
            raise HTTPException(
                status_code=429,
                detail={"code": "comment_rate_limited", "message": "too many comments, slow down"},
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -n0 tests/test_comment_rate_limit.py tests/test_comments_api.py -q`
Expected: PASS. If a `test_comments_api.py` test posts more than 5 times with one key it will now 429 - give those tests distinct keys rather than raising the limit.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui/comments.py src/splitsmith/ui/server.py tests/test_comment_rate_limit.py
git commit -m "feat(comments): per-key sliding-window rate limit"
```

---

### Task 8: Owner-side moderation routes + minting a comment link

**Files:**
- Modify: `src/splitsmith/ui/server.py` (bulk-delete routes; `POST /api/match/shares` accepts `scope`; `ShareInfo` carries it)
- Test: `tests/test_comments_moderation.py`

**Interfaces:**
- Consumes: `CommentStore` bulk deletes (Task 3), `ShareTokenStore.create(scope=)` (Task 4).
- Produces:
  - `ShareCreateRequest` with `scope: Literal["read", "comment"] = "read"`
  - `ShareInfo.scope: str`
  - `DELETE /api/match/comments?share_token_id=...` and `?author_key_hash=...`, returning `{"deleted": <int>}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_comments_moderation.py`:

```python
"""Owner-side moderation: the release condition for anonymous writes."""

from __future__ import annotations


def test_mint_defaults_to_read_scope(owner_client) -> None:
    created = owner_client.post("/api/match/shares", json={}).json()
    assert created["scope"] == "read"


def test_mint_can_request_the_comment_scope(owner_client) -> None:
    created = owner_client.post("/api/match/shares", json={"scope": "comment"}).json()
    assert created["scope"] == "comment"


def test_mint_rejects_an_unknown_scope(owner_client) -> None:
    assert owner_client.post("/api/match/shares", json={"scope": "admin"}).status_code == 422


def test_owner_sees_the_thread_with_moderation_fields(owner_client, seeded_comment) -> None:
    body = owner_client.get("/api/shooters/alice/stages/3/comments").json()
    comment = body["comments"][0]
    assert comment["share_token_id"]
    assert comment["author_key_hash"]


def test_owner_can_delete_any_comment(owner_client, seeded_comment) -> None:
    resp = owner_client.delete(f"/api/shooters/alice/stages/3/comments/{seeded_comment}")
    assert resp.status_code == 204
    assert owner_client.get("/api/shooters/alice/stages/3/comments").json()["comments"] == []


def test_bulk_delete_by_share_token(owner_client, two_links_two_comments) -> None:
    token_id, _ = two_links_two_comments
    resp = owner_client.delete(f"/api/match/comments?share_token_id={token_id}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1}
    assert len(owner_client.get("/api/shooters/alice/stages/3/comments").json()["comments"]) == 1


def test_bulk_delete_by_author_key_hash(owner_client, two_authors_two_comments) -> None:
    key_hash, _ = two_authors_two_comments
    resp = owner_client.delete(f"/api/match/comments?author_key_hash={key_hash}")
    assert resp.json() == {"deleted": 1}


def test_bulk_delete_requires_exactly_one_selector(owner_client) -> None:
    assert owner_client.delete("/api/match/comments").status_code == 422
    assert owner_client.delete(
        "/api/match/comments?share_token_id=a&author_key_hash=b"
    ).status_code == 422


def test_a_share_request_cannot_reach_the_bulk_delete(comment_token_client) -> None:
    """Not in either allowlist, so it is the uniform 404 - not a 403."""
    client, token = comment_token_client
    resp = client.delete(f"/api/share/{token}/match/comments?share_token_id=x")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "not found"}
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest -n0 tests/test_comments_moderation.py -q`
Expected: FAIL - the `scope` field and the bulk-delete route do not exist.

- [ ] **Step 3: Implement**

Add to the existing share models in `server.py`:

```python
class ShareCreateRequest(BaseModel):
    """Body for minting a link.

    ``scope`` is fixed for the token's whole life. There is deliberately
    no route that changes it: an owner should be able to reason about a
    link from the moment they send it, and a toggle would mean a link
    already in someone's inbox could gain the ability to post.
    """

    scope: Literal["read", "comment"] = "read"
```

Change `_create_match_share` to take `req: ShareCreateRequest = ShareCreateRequest()` and pass `scope=req.scope` to `store.create`. Add `scope=s.scope` to every `ShareInfo(...)` construction in both `_list_match_shares` and `_create_match_share`, and add `scope: str` to `ShareInfo`.

Add the bulk-delete route next to the other match-scoped routes:

```python
    @app.delete("/api/match/comments")
    async def delete_match_comments(
        share_token_id: str | None = None,
        author_key_hash: str | None = None,
    ) -> dict[str, int]:
        """Bulk moderation. Exactly one selector, because the two mean
        very different things and a call with neither would read as
        'delete everything' - which is not an action this offers."""
        if (share_token_id is None) == (author_key_hash is None):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "one_selector_required",
                    "message": "pass exactly one of share_token_id or author_key_hash",
                },
            )
        store = state.comments
        mid = current_match_id.get()
        if store is None or mid is None:
            raise HTTPException(status_code=404, detail="not found")
        if share_token_id is not None:
            deleted = await store.delete_by_share_token(mid, share_token_id)
        else:
            assert author_key_hash is not None
            deleted = await store.delete_by_author_key_hash(mid, author_key_hash)
        return {"deleted": deleted}
```

**Capability check:** `required_capability` returns `EDIT` for any unclassified write, so `DELETE /api/match/comments` requires `edit` by default. That is correct and needs no change - moderating your own match is an owner action. Confirm no `_REVIEW_ROUTES` entry accidentally matches it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -n0 tests/test_comments_moderation.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_comments_moderation.py
git commit -m "feat(comments): owner moderation and comment-scoped link minting"
```

---

### Task 9: Frontend author key + API client

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/authorKey.ts`
- Create: `src/splitsmith/ui_static/src/lib/authorKey.test.ts`
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (types + three calls)

**Interfaces:**
- Consumes: the routes from Tasks 6-8.
- Produces:
  - `authorKey(): string` - reads or mints the per-browser key
  - `AUTHOR_KEY_STORAGE_KEY = "splitsmith.authorKey"`
  - `api.listStageComments(slug, stage) -> Promise<CommentListResponse>`
  - `api.createStageComment(slug, stage, input: CommentCreateInput) -> Promise<Comment>`
  - `api.deleteStageComment(slug, stage, id) -> Promise<void>`
  - types `Comment`, `CommentListResponse`, `CommentCreateInput = { body: string; anchor_t: number; anchor_kind: "time" | "shot"; anchor_shot_id?: string | null }`

- [ ] **Step 1: Write the failing test**

Create `src/splitsmith/ui_static/src/lib/authorKey.test.ts`:

```ts
import { beforeEach, describe, expect, it } from "vitest";

import { AUTHOR_KEY_STORAGE_KEY, authorKey } from "./authorKey";

describe("authorKey", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("mints a key on first use and persists it", () => {
    const first = authorKey();
    expect(first).toMatch(/^[0-9a-f]{64}$/);
    expect(localStorage.getItem(AUTHOR_KEY_STORAGE_KEY)).toBe(first);
  });

  it("returns the same key on subsequent calls", () => {
    expect(authorKey()).toBe(authorKey());
  });

  it("replaces a corrupted stored value", () => {
    localStorage.setItem(AUTHOR_KEY_STORAGE_KEY, "not-a-key");
    expect(authorKey()).toMatch(/^[0-9a-f]{64}$/);
  });

  it("survives a localStorage that throws", () => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new Error("quota");
    };
    try {
      expect(authorKey()).toMatch(/^[0-9a-f]{64}$/);
    } finally {
      Storage.prototype.setItem = original;
    }
  });
});
```

- [ ] **Step 2: Run it to make sure it fails**

Run (from `src/splitsmith/ui_static/`): `pnpm vitest run src/lib/authorKey.test.ts`
Expected: FAIL - cannot resolve `./authorKey`.

- [ ] **Step 3: Implement**

Create `src/splitsmith/ui_static/src/lib/authorKey.ts`:

```ts
/**
 * The per-browser opaque key that lets an anonymous commenter delete
 * their own comment, and that the server derives their display handle
 * from.
 *
 * It is deliberately NOT a display name. The server owns the name - if
 * the client could send one, anyone with curl could sign a comment with
 * the match owner's. All the client holds is 32 bytes of randomness.
 *
 * Not a security boundary: anyone can mint one. It must never gate
 * anything whose exposure matters.
 */

export const AUTHOR_KEY_STORAGE_KEY = "splitsmith.authorKey";

const KEY_PATTERN = /^[0-9a-f]{64}$/;

function mint(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

// Falls back to an in-memory key when localStorage is unavailable
// (private mode, quota, disabled storage). The comment still posts and
// still gets a handle; only "delete my comment" stops surviving a
// reload, which is the right thing to degrade.
let memoryKey: string | null = null;

export function authorKey(): string {
  try {
    const stored = localStorage.getItem(AUTHOR_KEY_STORAGE_KEY);
    if (stored && KEY_PATTERN.test(stored)) return stored;
    const minted = mint();
    localStorage.setItem(AUTHOR_KEY_STORAGE_KEY, minted);
    return minted;
  } catch {
    if (memoryKey == null) memoryKey = mint();
    return memoryKey;
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm vitest run src/lib/authorKey.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Add the API client calls**

In `src/splitsmith/ui_static/src/lib/api.ts`, add the types and three functions. `scopeRequestPath` already rewrites `/api/...` onto `/api/share/{token}/...` on a share mount, so these need no share-specific branch:

```ts
export interface Comment {
  id: string;
  anchor_t: number;
  anchor_kind: "time" | "shot";
  anchor_shot_id: string | null;
  author_kind: "handle" | "account";
  author_handle: string;
  body: string;
  created_at: string;
  mine: boolean;
  /** Owner view only; absent for anonymous callers. */
  share_token_id?: string | null;
  author_key_hash?: string | null;
}

export interface CommentListResponse {
  comments: Comment[];
}

export interface CommentCreateInput {
  body: string;
  anchor_t: number;
  anchor_kind: "time" | "shot";
  anchor_shot_id?: string | null;
}
```

```ts
  listStageComments(slug: string, stage: number): Promise<CommentListResponse> {
    return request<CommentListResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stage}/comments`,
      { headers: { [AUTHOR_KEY_HEADER]: authorKey() } },
    );
  },

  createStageComment(
    slug: string,
    stage: number,
    input: CommentCreateInput,
  ): Promise<Comment> {
    return request<Comment>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stage}/comments`,
      { method: "POST", json: input, headers: { [AUTHOR_KEY_HEADER]: authorKey() } },
    );
  },

  deleteStageComment(slug: string, stage: number, id: string): Promise<void> {
    return request<void>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stage}/comments/${encodeURIComponent(id)}`,
      { method: "DELETE", headers: { [AUTHOR_KEY_HEADER]: authorKey() } },
    );
  },
```

with `const AUTHOR_KEY_HEADER = "X-Splitsmith-Author-Key";` near the top and `import { authorKey } from "./authorKey";`.

- [ ] **Step 6: Typecheck and commit**

```bash
pnpm typecheck
git add src/splitsmith/ui_static/src/lib/authorKey.ts src/splitsmith/ui_static/src/lib/authorKey.test.ts src/splitsmith/ui_static/src/lib/api.ts
git commit -m "feat(ui): author key and comment API client"
```

---

### Task 10: Comment panel + anchor snapping in `ResultsStage`

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/commentAnchor.ts`
- Create: `src/splitsmith/ui_static/src/lib/commentAnchor.test.ts`
- Create: `src/splitsmith/ui_static/src/components/comments/CommentPanel.tsx`
- Create: `src/splitsmith/ui_static/src/components/comments/CommentPanel.test.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.tsx`

**Interfaces:**
- Consumes: `api.listStageComments` / `createStageComment` / `deleteStageComment` (Task 9); `CoachShot` (existing, has `id: string | null` and `time_from_beep: number`).
- Produces:
  - `snapToShot(tAfterBeep: number, shots: readonly CoachShot[], toleranceS?: number) -> { anchor_kind: "time" | "shot"; anchor_shot_id: string | null; shot_number: number | null }`
  - `SNAP_TOLERANCE_S = 0.12`
  - `<CommentPanel slug stage shots beepTime currentTime canComment onSeek />`

**Tolerance rationale for the implementer:** 0.12 s sits below the low end of the Production Optics split range the project treats as typical (0.15-0.40 s), so a snap can never straddle two adjacent shots in a fast string.

- [ ] **Step 1: Write the failing test**

Create `src/splitsmith/ui_static/src/lib/commentAnchor.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { SNAP_TOLERANCE_S, snapToShot } from "./commentAnchor";
import type { CoachShot } from "./api";

function shot(n: number, t: number, id: string | null = `cand-${n}`): CoachShot {
  return {
    id,
    shot_number: n,
    ms_after_beep: t * 1000,
    time_from_beep: t,
    time_absolute: t + 10,
    split: 0.2,
    interval_class: null,
    interval_class_source: null,
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
  };
}

const SHOTS = [shot(1, 1.0), shot(2, 1.2), shot(3, 5.0)];

describe("snapToShot", () => {
  it("snaps when inside the tolerance", () => {
    expect(snapToShot(5.05, SHOTS)).toEqual({
      anchor_kind: "shot",
      anchor_shot_id: "cand-3",
      shot_number: 3,
    });
  });

  it("does not snap outside the tolerance", () => {
    expect(snapToShot(3.0, SHOTS)).toEqual({
      anchor_kind: "time",
      anchor_shot_id: null,
      shot_number: null,
    });
  });

  it("does not snap exactly at the tolerance boundary", () => {
    expect(snapToShot(5.0 + SNAP_TOLERANCE_S, SHOTS).anchor_kind).toBe("time");
  });

  it("snaps just inside the boundary", () => {
    expect(snapToShot(5.0 + SNAP_TOLERANCE_S - 0.001, SHOTS).anchor_kind).toBe("shot");
  });

  it("picks the nearer of two close shots", () => {
    expect(snapToShot(1.19, SHOTS).anchor_shot_id).toBe("cand-2");
    expect(snapToShot(1.02, SHOTS).anchor_shot_id).toBe("cand-1");
  });

  it("falls back to a time anchor when the nearest shot has no id", () => {
    expect(snapToShot(1.0, [shot(1, 1.0, null)])).toEqual({
      anchor_kind: "time",
      anchor_shot_id: null,
      shot_number: null,
    });
  });

  it("handles an empty shot table", () => {
    expect(snapToShot(1.0, []).anchor_kind).toBe("time");
  });

  it("handles a negative t (pre-beep draw)", () => {
    expect(snapToShot(-0.5, SHOTS).anchor_kind).toBe("time");
  });
});
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pnpm vitest run src/lib/commentAnchor.test.ts`
Expected: FAIL - cannot resolve `./commentAnchor`.

- [ ] **Step 3: Implement the snapper**

Create `src/splitsmith/ui_static/src/lib/commentAnchor.ts`:

```ts
/**
 * Decide whether a comment being composed at time `t` (seconds after the
 * beep) is about a specific shot or about a moment in time.
 *
 * The stored anchor always carries `anchor_t` regardless - the shot id
 * is a label, `t` is the truth. That is what makes a re-detect degrade a
 * shot-anchored comment to a time pin rather than re-attach it to a
 * different shot.
 */

import type { CoachShot } from "./api";

/**
 * Below the low end of the Production Optics split range the project
 * treats as typical (0.15-0.40 s), so a snap can never straddle two
 * adjacent shots in a fast string.
 */
export const SNAP_TOLERANCE_S = 0.12;

export type CommentAnchor = {
  anchor_kind: "time" | "shot";
  anchor_shot_id: string | null;
  shot_number: number | null;
};

const TIME_ANCHOR: CommentAnchor = {
  anchor_kind: "time",
  anchor_shot_id: null,
  shot_number: null,
};

export function snapToShot(
  tAfterBeep: number,
  shots: readonly CoachShot[],
  toleranceS: number = SNAP_TOLERANCE_S,
): CommentAnchor {
  let best: CoachShot | null = null;
  let bestDelta = Infinity;
  for (const shot of shots) {
    const delta = Math.abs(shot.time_from_beep - tAfterBeep);
    if (delta < bestDelta) {
      bestDelta = delta;
      best = shot;
    }
  }
  // A shot with no stable id cannot be addressed, so it anchors by time.
  // Legacy audit docs that no save boundary has stamped hit this.
  if (best == null || bestDelta >= toleranceS || best.id == null) return TIME_ANCHOR;
  return { anchor_kind: "shot", anchor_shot_id: best.id, shot_number: best.shot_number };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm vitest run src/lib/commentAnchor.test.ts`
Expected: PASS (8 tests).

- [ ] **Step 5: Write the panel test**

Create `src/splitsmith/ui_static/src/components/comments/CommentPanel.test.tsx`. Model the render helper and `api` mocking on `src/splitsmith/ui_static/src/pages/Coach.test.tsx`.

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CommentPanel } from "./CommentPanel";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listStageComments: vi.fn(),
      createStageComment: vi.fn(),
      deleteStageComment: vi.fn(),
    },
  };
});

function comment(over: Partial<import("@/lib/api").Comment> = {}) {
  return {
    id: "c1",
    anchor_t: 4.32,
    anchor_kind: "time" as const,
    anchor_shot_id: null,
    author_kind: "handle" as const,
    author_handle: "Prone Popper 47",
    body: "reload looks early",
    created_at: "2026-08-13T10:00:00Z",
    mine: false,
    ...over,
  };
}

const SHOTS = [
  {
    id: "cand-3",
    shot_number: 3,
    ms_after_beep: 5000,
    time_from_beep: 5.0,
    time_absolute: 15.0,
    split: 0.2,
    interval_class: null,
    interval_class_source: null,
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
  },
];

function renderPanel(over = {}) {
  return render(
    <CommentPanel
      slug="alice"
      stage={3}
      shots={SHOTS}
      beepTime={10}
      currentTime={14.32}
      canComment
      onSeek={vi.fn()}
      {...over}
    />,
  );
}

describe("CommentPanel", () => {
  beforeEach(() => {
    vi.mocked(api.listStageComments).mockResolvedValue({ comments: [comment()] });
    vi.mocked(api.createStageComment).mockResolvedValue(comment({ id: "c2", mine: true }));
  });

  it("renders the handle and body", async () => {
    renderPanel();
    expect(await screen.findByText("Prone Popper 47")).toBeInTheDocument();
    expect(screen.getByText("reload looks early")).toBeInTheDocument();
  });

  it("labels a time anchor with seconds and a shot anchor with the shot", async () => {
    vi.mocked(api.listStageComments).mockResolvedValue({
      comments: [
        comment({ id: "a", anchor_t: 4.32 }),
        comment({ id: "b", anchor_kind: "shot", anchor_shot_id: "cand-3", anchor_t: 5.0 }),
      ],
    });
    renderPanel();
    expect(await screen.findByText("4.32 s")).toBeInTheDocument();
    expect(screen.getByText("Shot 3")).toBeInTheDocument();
  });

  it("renders a shot anchor whose shot no longer resolves as a time pin", async () => {
    vi.mocked(api.listStageComments).mockResolvedValue({
      comments: [comment({ anchor_kind: "shot", anchor_shot_id: "cand-99", anchor_t: 7.5 })],
    });
    renderPanel();
    expect(await screen.findByText("7.50 s")).toBeInTheDocument();
    expect(screen.queryByText(/Shot/)).not.toBeInTheDocument();
  });

  it("posts with the snapped anchor when the playhead is on a shot", async () => {
    renderPanel({ currentTime: 15.02 });
    await screen.findByText("Prone Popper 47");
    await userEvent.type(screen.getByRole("textbox", { name: /comment/i }), "nice");
    await userEvent.click(screen.getByRole("button", { name: /post/i }));
    await waitFor(() =>
      expect(api.createStageComment).toHaveBeenCalledWith("alice", 3, {
        body: "nice",
        anchor_t: 5.02,
        anchor_kind: "shot",
        anchor_shot_id: "cand-3",
      }),
    );
  });

  it("posts a time anchor when the playhead is between shots", async () => {
    renderPanel({ currentTime: 12.5 });
    await screen.findByText("Prone Popper 47");
    await userEvent.type(screen.getByRole("textbox", { name: /comment/i }), "nice");
    await userEvent.click(screen.getByRole("button", { name: /post/i }));
    await waitFor(() =>
      expect(api.createStageComment).toHaveBeenCalledWith("alice", 3, {
        body: "nice",
        anchor_t: 2.5,
        anchor_kind: "time",
        anchor_shot_id: null,
      }),
    );
  });

  it("hides the compose box when commenting is not permitted", async () => {
    renderPanel({ canComment: false });
    await screen.findByText("Prone Popper 47");
    expect(screen.queryByRole("button", { name: /post/i })).not.toBeInTheDocument();
  });

  it("offers delete only on your own comment", async () => {
    vi.mocked(api.listStageComments).mockResolvedValue({
      comments: [comment({ id: "a", mine: false }), comment({ id: "b", mine: true })],
    });
    renderPanel();
    await screen.findAllByText("Prone Popper 47");
    expect(screen.getAllByRole("button", { name: /delete/i })).toHaveLength(1);
  });

  it("seeks when a comment is activated", async () => {
    const onSeek = vi.fn();
    renderPanel({ onSeek });
    await userEvent.click(await screen.findByText("reload looks early"));
    expect(onSeek).toHaveBeenCalledWith(14.32);
  });

  it("degrades to an inline retry when the thread fails to load", async () => {
    vi.mocked(api.listStageComments).mockRejectedValue(new Error("boom"));
    renderPanel();
    expect(await screen.findByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 6: Run it to make sure it fails**

Run: `pnpm vitest run src/components/comments/CommentPanel.test.tsx`
Expected: FAIL - cannot resolve `./CommentPanel`.

- [ ] **Step 7: Build the panel**

Create `src/splitsmith/ui_static/src/components/comments/CommentPanel.tsx`. Requirements the tests above pin, restated so the implementer does not have to reverse them out of assertions:

- Loads the thread on mount via `api.listStageComments(slug, stage)`; a rejection renders an inline "Retry" button, never an error page. The player must keep working.
- Each comment shows `author_handle`, `body`, and an anchor label: `Shot <n>` when `anchor_kind === "shot"` **and** `anchor_shot_id` resolves against `shots`, otherwise `<anchor_t.toFixed(2)> s`. That fallback is the whole point of storing both.
- Activating a comment calls `onSeek(beepTime + anchor_t)` - clip seconds, the same conversion `ResultsStage` already uses for `momentTime`.
- The compose box renders only when `canComment`. On submit it computes `t = currentTime - beepTime`, rounds to 2 decimals, calls `snapToShot(t, shots)`, and posts `{ body, anchor_t: t, ...anchor }` - dropping `shot_number`, which is display-only and not a wire field.
- A comment with `mine === true` shows a Delete button calling `api.deleteStageComment`; others do not.
- Anchor labels must not be colour-only (WCAG stance carried over from the moment work): the shot pin uses a distinct glyph plus its text label.

- [ ] **Step 8: Run the panel tests to verify they pass**

Run: `pnpm vitest run src/components/comments/CommentPanel.test.tsx`
Expected: PASS (10 tests).

- [ ] **Step 9: Wire it into `ResultsStage`**

In `src/splitsmith/ui_static/src/pages/ResultsStage.tsx`:

- Import `CommentPanel` and render it below the shot table, passing `slug`, `stage`, `shots`, `beepTime={coach.beep_time}`, `currentTime`, and `onSeek` (the same seek callback the shot-table rows already use).
- `canComment` comes from the capability set the match payload already serializes (`comment_write`), **not** from `isShareView` - the server gates on scope, and the SPA must gate on the same fact rather than on the URL shape. Follow the pattern in `Home.capabilities.test.tsx` / `Shooters.capabilities.test.tsx` for reading the set.
- Render a pin per comment on the existing scrub bar at `beepTime + anchor_t`, reusing the moment-marker styling.

- [ ] **Step 10: Run the page tests**

Run: `pnpm vitest run src/pages/ResultsStage.test.tsx src/pages/ResultsStage.trimstale.test.tsx`
Expected: PASS. Existing tests may need `listStageComments` added to their `api` mock; add it returning `{ comments: [] }`.

- [ ] **Step 11: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/commentAnchor.ts src/splitsmith/ui_static/src/lib/commentAnchor.test.ts src/splitsmith/ui_static/src/components/comments/ src/splitsmith/ui_static/src/pages/ResultsStage.tsx
git commit -m "feat(ui): comment panel with shot-or-time anchoring on ResultsStage"
```

---

### Task 11: Signed-in commenters keep their account name

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`_share_alias` resolves an optional session; `create_stage_comment` branches on it)
- Test: `tests/test_comments_signed_in.py`

**Interfaces:**
- Consumes: `state.auth.authenticate_request` (existing), `CommentStore.create` (Task 3).
- Produces: `current_share_viewer: ContextVar[User | None]` in `server.py`, set by `_share_alias`, `None` for an anonymous visitor.

**Why this is its own task.** The spec commits to "a signed-in visitor comments under their `display_name`", but `_auth_gate` deliberately hands `/api/share/` straight through without consulting a session - the token *is* the authorization. Resolving a session on a share path is therefore a genuine change to that surface's posture, and a reviewer could reasonably accept anonymous comments while rejecting this. Keep them separable.

**The rule that keeps it safe:** a resolved session on a share request grants **nothing**. It does not change the tenant (`_share_alias` still pins the *owner*), does not widen the allowlist, and does not affect the scope gate. It is read for one purpose - what name to put on the comment - and a failure to resolve is never an error.

- [ ] **Step 1: Write the failing test**

Create `tests/test_comments_signed_in.py`:

```python
"""A signed-in visitor comments under their account name."""

from __future__ import annotations

from splitsmith.ui.comments import AUTHOR_KEY_HEADER

KEY = "c" * 64


def _post(client, token, **headers):
    return client.post(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        json={"body": "nice draw", "anchor_t": 1.0},
        headers={AUTHOR_KEY_HEADER: KEY, **headers},
    )


def test_anonymous_visitor_gets_a_generated_handle(comment_token_client) -> None:
    client, token = comment_token_client
    created = _post(client, token).json()
    assert created["author_kind"] == "handle"
    assert created["author_handle"].split(" ")[-1].isdigit()


def test_signed_in_visitor_uses_their_display_name(comment_token_client, signed_in_headers) -> None:
    client, token = comment_token_client
    created = _post(client, token, **signed_in_headers).json()
    assert created["author_kind"] == "account"
    assert created["author_handle"] == "Anders Berg"


def test_signed_in_visitor_without_a_display_name_falls_back_to_a_handle(
    comment_token_client, nameless_signed_in_headers
) -> None:
    """display_name is nullable. An account with none must not post as
    an empty string or as their email address."""
    client, token = comment_token_client
    created = _post(client, token, **nameless_signed_in_headers).json()
    assert created["author_kind"] == "handle"
    assert created["author_handle"].split(" ")[-1].isdigit()


def test_a_session_does_not_change_the_tenant(
    comment_token_client, signed_in_headers, owner_client
) -> None:
    """The row must land in the OWNER's tenant, not the commenter's. If
    a session ever started driving current_tenant on a share path, this
    is what would catch it."""
    client, token = comment_token_client
    _post(client, token, **signed_in_headers)
    listed = owner_client.get("/api/shooters/alice/stages/3/comments").json()
    assert len(listed["comments"]) == 1


def test_a_session_does_not_widen_the_allowlist(comment_token_client, signed_in_headers) -> None:
    client, token = comment_token_client
    resp = client.post(
        f"/api/share/{token}/shooters/alice/stages/3/audit/accept",
        json={},
        headers={AUTHOR_KEY_HEADER: KEY, **signed_in_headers},
    )
    assert resp.status_code == 404


def test_a_session_does_not_bypass_the_scope_gate(read_token_client, signed_in_headers) -> None:
    """Being signed in must not make a read-scoped link postable."""
    client, token = read_token_client
    assert _post(client, token, **signed_in_headers).status_code == 404


def test_an_invalid_session_degrades_to_anonymous(comment_token_client) -> None:
    client, token = comment_token_client
    created = _post(client, token, Cookie="session=garbage").json()
    assert created["author_kind"] == "handle"
```

Add the three fixtures to this file, modelled on the authenticated-client fixtures already in `tests/test_ui_server.py`: `signed_in_headers` carries a valid session for a user with `display_name="Anders Berg"` who is **not** the match owner, and `nameless_signed_in_headers` the same for a user with `display_name=None`.

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest -n0 tests/test_comments_signed_in.py -q`
Expected: FAIL - `test_signed_in_visitor_uses_their_display_name` gets `author_kind == "handle"`, because Task 6 hardcoded it.

- [ ] **Step 3: Resolve an optional viewer in `_share_alias`**

Add the ContextVar next to `current_share_request`:

```python
# The signed-in user behind a share request, or None. Set by
# _share_alias purely so a comment can carry an account name instead of
# a generated handle.
#
# It grants NOTHING. The tenant stays the match owner's, the allowlist
# does not widen, and the scope gate does not soften. If a future change
# makes this value authorize anything, that is the bug - the token is
# the authorization on this surface, and a second one would mean two
# answers to "who may do this".
current_share_viewer: ContextVar[object | None] = ContextVar(
    "splitsmith_current_share_viewer", default=None
)
```

In `_share_alias`, after the scope gate and before `current_tenant.set(...)`:

```python
        # Best-effort only: an absent, expired or malformed session is an
        # anonymous visitor, never an error. Wrapped because the auth
        # backend may raise on a garbage cookie and a share page must not
        # 500 for it.
        viewer = None
        if needs_write_scope:
            try:
                viewer = await state.auth.authenticate_request(request)
            except Exception:  # noqa: BLE001
                viewer = None
        viewer_token = current_share_viewer.set(viewer)
```

resetting it in the same `finally` block as the others. Resolve it only on writes - a read has no use for it and the extra session lookup would land on every anonymous card fetch.

- [ ] **Step 4: Branch on it in the route**

In `create_stage_comment`, replace the hardcoded author fields:

```python
        viewer = current_share_viewer.get()
        display_name = getattr(viewer, "display_name", None) if viewer is not None else None
        if isinstance(display_name, str) and display_name.strip():
            author_kind = "account"
            author_user_id = viewer.id  # type: ignore[union-attr]
            author_handle = display_name.strip()
        else:
            # Includes a signed-in account that never set a display name:
            # an email address is not a name they chose to publish, and an
            # empty string is not a name at all.
            author_kind = "handle"
            author_user_id = None
            author_handle = derive_handle(author_key)
```

and pass those three into `store.create(...)`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -n0 tests/test_comments_signed_in.py tests/test_comments_api.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_comments_signed_in.py
git commit -m "feat(comments): signed-in visitors comment under their account name"
```

---

### Task 12: Whole-branch pass and full gates

**Files:**
- Modify: `src/splitsmith/ui/match_delete.py` (purge comments in the cascade), `src/splitsmith/ui/server.py` (`DeletionSummary` field)
- Modify: `CLAUDE.md` (share-surface paragraph), `SPEC.md` (module responsibilities)
- Test: `tests/test_comments_seams.py`

**Why a whole-branch task exists.** CLAUDE.md's review practice: "One defect lived in a seam no single task owned; only a cross-cutting read found it." The tasks above each own one layer. These are the questions none of them own.

- [ ] **Step 1: Write the seam tests**

Create `tests/test_comments_seams.py`:

```python
"""Cross-cutting checks no single task owns."""

from __future__ import annotations

from splitsmith.ui.server import _SHARE_PATH_RE, _SHARE_WRITE_PATH_RE


def test_the_two_allowlists_are_distinct_objects() -> None:
    """They are separate so _SHARE_PATH_RE's GET-only docstring stays
    true. If a future edit merges them, this fails."""
    assert _SHARE_PATH_RE is not _SHARE_WRITE_PATH_RE
    assert _SHARE_PATH_RE.pattern != _SHARE_WRITE_PATH_RE.pattern


def test_no_shape_is_admitted_by_both_patterns_except_the_thread() -> None:
    """The comment thread is the one path that is both readable and
    writable. Anything else appearing in both means a read shape leaked
    into the write surface or vice versa."""
    shapes = [
        "match/shooters",
        "shooters/alice/project",
        "shooters/alice/stages/3/coach",
        "shooters/alice/coach/distributions",
        "shooters/alice/videos/stream",
        "match/stage/3/compare",
        "match/shooters/alice/videos/stream",
        "og.png",
        "og-meta",
        "shooters/alice/stages/3/comments",
    ]
    both = [
        s
        for s in shapes
        if _SHARE_PATH_RE.fullmatch(s) and _SHARE_WRITE_PATH_RE.fullmatch(s)
    ]
    assert both == ["shooters/alice/stages/3/comments"]


def test_deleting_a_match_purges_its_comments(owner_client, seeded_comment) -> None:
    """Nothing cascades from the matches registry row - _delete_hosted
    deletes state_docs explicitly for that reason. Comments need the same
    step, or 'delete my match' leaves other people's text behind."""
    before = owner_client.get("/api/shooters/alice/stages/3/comments").json()
    assert len(before["comments"]) == 1

    resp = owner_client.delete("/api/matches/<match_id>")  # the existing delete route
    assert resp.status_code == 200
    assert resp.json()["comments_removed"] == 1


def test_match_delete_reports_comments_in_its_summary(owner_client, seeded_comment) -> None:
    """The summary is the audit trail for a destructive action (CLAUDE.md:
    optimize for the audit trail). A silent purge is worse than none."""
    summary = owner_client.delete("/api/matches/<match_id>").json()
    assert "comments_removed" in summary
```

The implementer fills `<match_id>` from the fixture and confirms the exact delete route path and response shape against `server.py` (search for `delete_match_cascade`).

- [ ] **Step 2: Wire the purge into the cascade**

In `src/splitsmith/ui/match_delete.py`, add a step between the existing steps 6 and 7 of `_delete_hosted` - after the state docs, before the registry row, so a failure leaves the match still resolvable and the operation retryable:

```python
    # 6b. Delete the match's comments. Nothing cascades from the registry
    #     row, so this is explicit for the same reason state docs are.
    #     A match delete that left other people's comments behind would be
    #     a data-retention surprise, not a tidiness one.
    if state.comments is not None:
        try:
            summary.comments_removed = await state.comments.purge_match(match_id)
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"delete comments: {exc}")
```

Add `comments_removed: int = 0` to `DeletionSummary` and to the serialisable view in `server.py` (search for the class documented as "Serialisable view of DeletionSummary").

Check `_delete_local` too: local mode has no share surface and therefore no comments, so it needs no step - confirm that by reading it rather than assuming, and add a one-line comment there saying so.

- [ ] **Step 3: Run the seam tests**

Run: `uv run pytest -n0 tests/test_comments_seams.py tests/test_match_delete.py -q`
Expected: PASS.

- [ ] **Step 4: Run the full backend suite**

Run: `uv run pytest -q`
Expected: PASS. This is the first full-suite run on the branch (~222 s).

- [ ] **Step 5: Run lint and format gates**

```bash
uv run ruff check src tests
uv run black --check src tests
```

Expected: clean. **Do not run `ruff --fix` blindly** - it has broken green CI on this repo before; read each change.

- [ ] **Step 6: Run the full frontend gates**

From `src/splitsmith/ui_static/`:

```bash
pnpm typecheck
pnpm test
pnpm eslint src/lib/authorKey.ts src/lib/commentAnchor.ts src/components/comments
```

Expected: clean.

- [ ] **Step 7: Prove the new tests would have caught the bugs they claim**

For each of these, delete the guard, watch the named test fail, restore it. CLAUDE.md: "Deleting the fix and watching the test fail takes a minute and is the only real proof."

| Guard to delete | Test that must fail |
| --- | --- |
| The `scope_may_write` check in `_share_alias` | `test_post_through_a_read_scoped_token_is_the_uniform_404` |
| `COMMENT_SCOPE` from `_WRITE_CAPABLE_SCOPES` | `test_comment_scope_is_write_capable` |
| `deleted_at.is_(None)` from `list_for_stage` | `test_list_omits_soft_deleted` |
| The `author_key_hash` condition in `_soft_delete_one` | `test_another_key_cannot_delete_it` |
| `owner_view` gating in `to_out` | `test_list_never_exposes_author_key_hash_or_share_token` |
| `best.id == null` in `snapToShot` | `falls back to a time anchor when the nearest shot has no id` |
| Step 6b from `_delete_hosted` | `test_deleting_a_match_purges_its_comments` |
| The `display_name.strip()` truthiness check | `test_signed_in_visitor_without_a_display_name_falls_back_to_a_handle` |

Record the result. A guard whose test still passes without it is a test that does not test what it says.

- [ ] **Step 8: Manual verification on a real server**

Per CLAUDE.md, a green suite is not evidence the feature works. Using the two-server harness from the "verify unreleased hosted features locally" memory (staging runs released code, so it cannot serve this):

1. Mint a `comment`-scoped link and a `read`-scoped link on the same match.
2. Post through the comment link in a browser. Confirm the handle looks like "Prone Popper 47" and **read the rendered output** - do not infer it from the API response.
3. Open the read link. Confirm the thread renders and no compose box appears.
4. Delete your own comment; reload; confirm it is gone.
5. Sign in as a second account in another browser profile, open the same comment link, and post. Confirm the comment carries that account's display name, and that the first browser's pseudonymous comment is unchanged beside it.
6. Revoke the comment link; confirm posting stops and the read link still renders.
7. As owner, bulk-delete by that link's id and confirm the count.
8. Delete the whole match and confirm the summary reports `comments_removed`.

- [ ] **Step 9: Update the docs**

In `CLAUDE.md`, extend the share-link section: the anonymous surface is no longer categorically read-only, `_SHARE_PATH_RE` and `_SHARE_WRITE_PATH_RE` are separate on purpose, and `comment` is the only write-capable scope. State that `author_handle` is server-derived and never client-supplied - that is the invariant a future contributor is most likely to break by "simplifying" the compose box.

In `SPEC.md` under "Module responsibilities", add `comment_identity.py`, `db/comments.py` and `ui/comments.py`.

- [ ] **Step 10: Commit and open the PR**

```bash
git add CLAUDE.md SPEC.md tests/test_comments_seams.py
git commit -m "docs: comments on the share surface, and the write-scope seam"
```

Open a PR against `main`. Per the repo's squash-merge convention, keep the PR body short - a many-commit squash body breaks the release-please parser and the change vanishes from the changelog while CI stays green.

---

## Notes for the reviewer

Ask these specifically rather than "review this diff":

1. **Is `test_read_scope_is_still_read_only` real?** The one change with blast radius beyond this feature is `_WRITE_CAPABLE_SCOPES` gaining a member. Verify by deleting `COMMENT_SCOPE` from the set and confirming the read test still passes (it should - it tests `"read"`), then by adding `"read"` to the set and confirming it fails.
2. **Can any anonymous request reach a write it should not?** Try `PUT`, `PATCH`, `HEAD` and `OPTIONS` on the comments path; try the comments path with a `read` token; try another match's id.
3. **Does every new test fail against the pre-change code?** Step 7 of Task 12 lists the ablations. Several tests on past branches passed against the bug they claimed to cover.
4. **Does a session on a share path grant anything?** Task 11 resolves one purely to read a display name. Check that it does not reach `current_tenant`, the allowlist, or the scope gate - the four `test_a_session_does_not_*` tests are the claim, so verify they would actually fail if it did.
5. **Is `purge_match` reachable on every delete path?** Task 12 adds it to `_delete_hosted`. Confirm by reading `_delete_local` that local mode genuinely has no comments to purge rather than assuming it.
