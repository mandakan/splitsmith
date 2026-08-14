# Account Display Name and Author Codes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a hosted account a display name it can set, so #866's `author_kind="account"` comment-attribution branch is reachable in production, and add a stable per-author code so two commenters posting under the same or a similar name are distinguishable.

**Architecture:** Three layers, back to front. A pure normalizer plus a per-user store and a `PATCH /api/me` route make `users.display_name` writable. A second pure derivation (`derive_author_code`) plus one new denormalized column make every comment author carry a stable public code. The SPA gains an `/account` page that owns the display-name field and absorbs desktop-token management, and `CommentPanel` surfaces the code when two authors' names collide.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, pytest. React 19 + TypeScript, react-router-dom, Tailwind, vitest + @testing-library/react.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-13-account-display-name-design.md`. Read it before Task 1.
- Python 3.11+, type hints everywhere. `uv` for dependency management, never `pip`.
- Black formatting, line length 110. Ruff for linting.
- `pathlib.Path` for paths, f-strings for formatting.
- Imports grouped stdlib / third-party / local, separated by blank lines. No relative imports beyond a single dot.
- **Add no new dependencies.** Every import in this plan already exists in the project.
- Pydantic models for all data crossing module boundaries.
- Pure functions where possible: detection and derivation logic takes data and returns data, no file I/O.
- The pytest suite runs in parallel by default. **Every focused test command in this plan passes `-n0`** -- worker startup dominates a single-file run and tracebacks are cleaner. Run the full suite without `-n0` before the final commit.
- New tests must not depend on execution order or share mutable state outside `tmp_path`.
- ASCII punctuation only in code comments and copy: `--` not an em dash, `...` not an ellipsis character, straight quotes.
- The display-name cap is **60 characters**, measured after normalizing. The author code is **6 characters**. Both are named constants, never inline literals.
- **The #866 fallback invariant is not negotiable:** a blank or whitespace-only display name must publish a server-derived handle, never an empty string. `tests/test_comments_signed_in.py` pins it. If a change makes one of those tests fail, the change is wrong.
- Every new test must be checked against the pre-change code before it counts -- see the "Mutation check" step that ends each task. A test that passes without the fix is not evidence.

---

## File Structure

**Create:**

| File | Responsibility |
|---|---|
| `src/splitsmith/display_name.py` | Pure normalization + validation of an account display name |
| `src/splitsmith/db/profile.py` | `PostgresProfileStore` -- the only writer of `users.display_name` |
| `alembic/versions/<rev>_add_author_code_to_match_comments.py` | Adds the `author_code` column |
| `tests/test_display_name.py` | Normalizer units |
| `tests/test_profile_store.py` | Store round-trip + tenant isolation |
| `tests/test_account_display_name_api.py` | `PATCH /api/me` behaviour, mode + scope gates |
| `tests/test_comment_author_codes.py` | Code derivation, denormalization, read-time fallback |
| `tests/test_comment_author_summaries.py` | Owner aggregate endpoint |
| `src/splitsmith/ui_static/src/lib/authorAmbiguity.ts` | Name-collision detection for a comment thread |
| `src/splitsmith/ui_static/src/lib/authorAmbiguity.test.ts` | Its units |
| `src/splitsmith/ui_static/src/components/account/DesktopTokensSection.tsx` | Desktop tokens, rendered inline on a page |
| `src/splitsmith/ui_static/src/components/account/DesktopTokensSection.test.tsx` | Migrated from the dialog's tests |
| `src/splitsmith/ui_static/src/pages/Account.tsx` | The `/account` page |
| `src/splitsmith/ui_static/src/pages/Account.test.tsx` | Page behaviour |

**Modify:**

| File | Change |
|---|---|
| `src/splitsmith/comment_identity.py` | `derive_author_code` + `author_code_for` |
| `src/splitsmith/db/models.py` | `CommentRow.author_code` column |
| `src/splitsmith/db/comments.py` | `author_code` + `author_user_id` on `Comment`; `create` takes a code; `author_summaries` |
| `src/splitsmith/db/__init__.py` | Export `PostgresProfileStore` |
| `src/splitsmith/ui/comments.py` | `author_code` on `CommentOut`; author-summary models; read-time fallback in `to_out` |
| `src/splitsmith/ui/server.py` | `PATCH /api/me`; `TenantContext.profile` + `AppState.profile`; author code on the write path; `GET /api/match/comment-authors` |
| `tests/test_comments_signed_in.py` | Replace direct-column writes with the real route |
| `src/splitsmith/ui_static/src/lib/api.ts` | `updateMe`, `Comment.author_code`, comment-author types + call |
| `src/splitsmith/ui_static/src/components/comments/CommentPanel.tsx` | Author code rendering + owner detail |
| `src/splitsmith/ui_static/src/components/AccountChip.tsx` | Key icon becomes a link to `/account` |
| `src/splitsmith/ui_static/src/App.tsx` | `/account` route |
| `SPEC.md`, `CLAUDE.md` | Document the display name and the author code |

**Delete:** `src/splitsmith/ui_static/src/components/account/DesktopTokensDialog.tsx` and `DesktopTokensDialog.test.tsx` (contents migrate to the section in Task 12).

---

## Task 1: Display-name normalizer

**Files:**
- Create: `src/splitsmith/display_name.py`
- Test: `tests/test_display_name.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MAX_DISPLAY_NAME_LEN: Final[int] = 60`, `normalize_display_name(raw: str | None) -> str | None` raising `ValueError`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_display_name.py`:

```python
"""Units for the account display-name normalizer (#867).

The blank-to-None rule is the load-bearing one: #866's attribution
branch publishes ``display_name`` when it is non-blank and falls back to
a generated handle otherwise, so storing ``""`` would publish an empty
author. Storing ``None`` makes the branch's ``isinstance(str)`` guard
and its ``.strip()`` guard agree.
"""

from __future__ import annotations

import pytest

from splitsmith.display_name import MAX_DISPLAY_NAME_LEN, normalize_display_name


# The last case is a non-breaking space, written as an escape because
# it is indistinguishable from a plain space in a source file.
@pytest.mark.parametrize("raw", [None, "", "   ", "\t\t", " \u00a0 "])
def test_blank_becomes_none(raw: str | None) -> None:
    assert normalize_display_name(raw) is None


def test_surrounding_whitespace_is_stripped() -> None:
    assert normalize_display_name("  Anders Berg  ") == "Anders Berg"


def test_internal_whitespace_runs_collapse() -> None:
    assert normalize_display_name("Anders    Berg") == "Anders Berg"
    assert normalize_display_name("Anders \t Berg") == "Anders Berg"


def test_unicode_is_nfc_normalized() -> None:
    """Escape sequences, not literal characters: a decomposed and a
    composed name look identical in a source file, so a literal-vs-literal
    assertion would be trivially true and prove nothing."""
    decomposed = "Ma\u030athias"  # "Ma" + COMBINING RING ABOVE + "thias"
    composed = "M\u00e5thias"  # LATIN SMALL LETTER A WITH RING ABOVE
    assert decomposed != composed  # guard: the two inputs really do differ
    assert normalize_display_name(decomposed) == composed


def test_non_ascii_names_are_allowed() -> None:
    assert normalize_display_name("M\u00e5thias Axell") == "M\u00e5thias Axell"


def test_at_the_length_cap_is_accepted() -> None:
    name = "a" * MAX_DISPLAY_NAME_LEN
    assert normalize_display_name(name) == name


def test_one_over_the_length_cap_is_rejected() -> None:
    with pytest.raises(ValueError, match="60"):
        normalize_display_name("a" * (MAX_DISPLAY_NAME_LEN + 1))


def test_length_is_measured_after_normalizing() -> None:
    """Padding must not count against the cap -- it is removed first."""
    name = "  " + "a" * MAX_DISPLAY_NAME_LEN + "  "
    assert normalize_display_name(name) == "a" * MAX_DISPLAY_NAME_LEN


@pytest.mark.parametrize("bad", ["Anders\nBerg", "Anders\rBerg", "Anders\x00Berg", "Anders\x1bBerg"])
def test_control_characters_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="control"):
        normalize_display_name(bad)


def test_c1_control_characters_are_rejected() -> None:
    """U+0085 NEXT LINE. Invisible in a source file, which is exactly why
    it is written as an escape."""
    with pytest.raises(ValueError, match="control"):
        normalize_display_name("Anders\u0085Berg")


def test_zero_width_joiners_are_rejected() -> None:
    """U+200D is category Cf: invisible, and a way to make two
    identical-looking names compare unequal."""
    with pytest.raises(ValueError, match="control"):
        normalize_display_name("Anders\u200dBerg")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_display_name.py -n0 -q
```

Expected: collection error, `ModuleNotFoundError: No module named 'splitsmith.display_name'`.

- [ ] **Step 3: Write the implementation**

Create `src/splitsmith/display_name.py`:

```python
"""Normalization and validation for an account's display name (#867).

``users.display_name`` is published under a public share link the moment
a signed-in visitor comments (#866), so what goes into the column is a
publishing decision, not a formatting preference. Three rules carry
weight:

**Blank becomes ``None``, never ``""``.** #866's attribution branch
falls back to a server-derived handle when the name is blank, and it
tests ``isinstance(str)`` *and* ``.strip()`` because it did not trust
the column to be clean. Storing ``None`` makes both guards agree and
keeps the fallback invariant true from the write side.

**NFC, not NFKC.** This preserves the name the user typed. The
comparison used to detect two authors with confusingly similar names is
a different function with different rules, and lives in the frontend
(``lib/authorAmbiguity.ts``) -- it folds compatibility forms because it
is trying to defeat someone choosing one on purpose. Do not reuse
either normalizer for the other's job.

**Control characters are refused outright.** A newline inside an author
name breaks the single-line rendering the comment thread assumes, and
C1 codepoints are invisible padding that would let two visually
identical names differ.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

# Longest display name accepted, measured after normalizing so leading
# padding cannot consume the budget. Sized against the two surfaces that
# render it: the comment thread's author line and the account chip,
# which truncates at 16rem.
MAX_DISPLAY_NAME_LEN: Final = 60

_WHITESPACE_RUN: Final = re.compile(r"\s+")


def normalize_display_name(raw: str | None) -> str | None:
    """Canonical storage form of a user-supplied display name.

    Returns ``None`` for anything blank. Raises ``ValueError`` for a
    name carrying control characters or exceeding
    :data:`MAX_DISPLAY_NAME_LEN`; the route turns that into a 422.
    """
    if raw is None:
        return None
    # NFC first: a decomposed name must be measured and compared in the
    # form it will be stored in, not the form it arrived in.
    value = unicodedata.normalize("NFC", raw)
    # ``Cc`` is C0 + C1; ``Cf`` is the invisible formatting class
    # (zero-width joiners, bidi overrides) that would let two identical-
    # looking names differ. Checked before the whitespace collapse so a
    # newline is a refusal rather than being quietly turned into a space.
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in value):
        raise ValueError("display name may not contain control characters")
    value = _WHITESPACE_RUN.sub(" ", value).strip()
    if not value:
        return None
    if len(value) > MAX_DISPLAY_NAME_LEN:
        raise ValueError(f"display name may be at most {MAX_DISPLAY_NAME_LEN} characters")
    return value
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/test_display_name.py -n0 -q
```

Expected: all tests pass.

- [ ] **Step 5: Mutation check -- prove the tests can fail**

Temporarily change `if not value: return None` to `if not value: return value`, re-run, and confirm `test_blank_becomes_none` fails for the `""` and `"   "` cases. Then restore the line. If the test still passes, the test is wrong, not the code.

- [ ] **Step 6: Format, lint, commit**

```bash
uv run black src/splitsmith/display_name.py tests/test_display_name.py
uv run ruff check src/splitsmith/display_name.py tests/test_display_name.py
git add src/splitsmith/display_name.py tests/test_display_name.py
git commit -m "feat(account): normalize and validate account display names (#867)"
```

---

## Task 2: Profile store

**Files:**
- Create: `src/splitsmith/db/profile.py`
- Modify: `src/splitsmith/db/__init__.py`
- Modify: `src/splitsmith/ui/server.py` (`TenantContext`, `AppState.profile`, `_build_tenant`)
- Test: `tests/test_profile_store.py`

**Interfaces:**
- Consumes: `normalize_display_name` is *not* used here -- the store takes an already-normalized value. Normalization is the route's job so the 422 happens before any session opens.
- Produces: `PostgresProfileStore(session_factory, *, user_id: str)` with `async def set_display_name(self, display_name: str | None) -> None`. `AppState.profile -> PostgresProfileStore | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_profile_store.py`:

```python
"""PostgresProfileStore -- the only writer of users.display_name (#867).

Follows tests/test_scoreboard_identity_store.py exactly: SQLite
in-memory via aiosqlite, sync test functions driving ``asyncio.run``.
**There is no pytest-asyncio in this project** -- ``pyproject.toml``
configures no ``asyncio_mode``, so an ``@pytest.mark.asyncio`` test
would be collected and silently skipped as an un-awaited coroutine.

The multi-tenant invariant is that every statement filters on this
store's user_id, and the isolation test is what guards it. Add one per
new method.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from splitsmith.db import Base, User, create_engine, sessionmaker
from splitsmith.db.profile import PostgresProfileStore


def _build_store_for_new_users(*emails: str) -> tuple[list[str], sessionmaker]:
    """Fresh in-memory engine + one user row per email. Returns the ids
    in the order given, plus the session factory. Mirrors
    ``_build_store_for_new_user`` in test_scoreboard_identity_store.py.
    """
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> list[str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        ids = []
        async with session_factory() as s:
            for email in emails:
                user = User(email=email)
                s.add(user)
                await s.commit()
                await s.refresh(user)
                ids.append(user.id)
        return ids

    return asyncio.run(_setup()), session_factory


def _read_display_name(session_factory: sessionmaker, user_id: str) -> str | None:
    async def _read() -> str | None:
        async with session_factory() as s:
            return (
                await s.execute(select(User.display_name).where(User.id == user_id))
            ).scalar_one()

    return asyncio.run(_read())


@pytest.mark.parametrize("bad", ["", None, 0, b"abc"])
def test_construction_rejects_empty_or_non_string_user_id(bad) -> None:
    """Same defence-in-depth pattern as the scoreboard-identity store:
    a silent empty query hides an auth bug."""
    with pytest.raises(ValueError, match="non-empty user_id"):
        PostgresProfileStore(object(), user_id=bad)


def test_set_and_read_back() -> None:
    (user_id,), sf = _build_store_for_new_users("a@example.com")
    store = PostgresProfileStore(sf, user_id=user_id)

    asyncio.run(store.set_display_name("Anders Berg"))

    assert _read_display_name(sf, user_id) == "Anders Berg"


def test_clearing_writes_null() -> None:
    (user_id,), sf = _build_store_for_new_users("b@example.com")
    store = PostgresProfileStore(sf, user_id=user_id)
    asyncio.run(store.set_display_name("Anders Berg"))

    asyncio.run(store.set_display_name(None))

    assert _read_display_name(sf, user_id) is None


def test_a_write_never_touches_another_users_row() -> None:
    """The multi-tenant invariant. Two stores, two users, one write."""
    (alice, bob), sf = _build_store_for_new_users("alice@example.com", "bob@example.com")
    asyncio.run(PostgresProfileStore(sf, user_id=bob).set_display_name("Bob"))

    asyncio.run(PostgresProfileStore(sf, user_id=alice).set_display_name("Alice"))

    assert _read_display_name(sf, alice) == "Alice"
    assert _read_display_name(sf, bob) == "Bob"


def test_a_missing_user_raises() -> None:
    """The auth layer materialises the row before the handler runs. If it
    did not, that is an invariant violation worth surfacing rather than a
    silent no-op that reads as a successful save."""
    _ids, sf = _build_store_for_new_users("present@example.com")
    store = PostgresProfileStore(sf, user_id="01JNOTAREALUSERID000000000")

    with pytest.raises(LookupError, match="not found"):
        asyncio.run(store.set_display_name("Ghost"))
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_profile_store.py -n0 -q
```

Expected: `ModuleNotFoundError: No module named 'splitsmith.db.profile'`.

If `Base` is not exported from `splitsmith.db`, import it from `splitsmith.db.models` -- check what `tests/test_scoreboard_identity_store.py` does and match it rather than inventing a new import path.

- [ ] **Step 3: Write the store**

Create `src/splitsmith/db/profile.py`:

```python
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
            user = (
                await session.execute(select(User).where(User.id == self._user_id))
            ).scalar_one_or_none()
            if user is None:
                raise LookupError(
                    f"User {self._user_id!r} not found; auth layer "
                    "must materialise the user row before calling set_display_name()."
                )
            user.display_name = display_name
            await session.commit()
```

- [ ] **Step 4: Export it**

In `src/splitsmith/db/__init__.py`, add the import next to the `scoreboard_identity` one and the name to `__all__`:

```python
from .profile import PostgresProfileStore
```

```python
    "PostgresProfileStore",
```

- [ ] **Step 5: Wire it into the tenant context**

In `src/splitsmith/ui/server.py`:

1. Import it alongside the other db stores (find the existing `PostgresScoreboardIdentityStore` import and add `PostgresProfileStore` to the same import list).

2. Add the field to `TenantContext`, directly after `scoreboard_identity`:

```python
    # Per-user account-profile writer (#867). None in local mode: the
    # loopback sentinel has no user row, and the PATCH route that uses
    # this 404s there.
    profile: PostgresProfileStore | None = None
```

3. Add the property to `AppState`, next to the `comments` property:

```python
    @property
    def profile(self) -> PostgresProfileStore | None:
        # Local mode has no per-user profile store - display names are a
        # hosted-account concept. Returns None when no tenant is pinned.
        tenant = current_tenant.get()
        return tenant.profile if tenant is not None else None
```

4. In `_build_tenant`, add the constructor call next to `scoreboard_identity=`:

```python
            profile=PostgresProfileStore(tenant_factory, user_id=user_id),
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
uv run pytest tests/test_profile_store.py -n0 -q
```

Expected: all tests pass.

- [ ] **Step 7: Confirm nothing else broke**

```bash
uv run pytest tests/test_scoreboard_identity_store.py tests/test_db_foundation.py -n0 -q
```

Expected: all pass.

- [ ] **Step 8: Mutation check**

Temporarily drop `.where(User.id == self._user_id)` from the select (leaving `select(User)`), re-run `tests/test_profile_store.py`, and confirm `test_a_write_never_touches_another_users_row` fails. Restore it.

- [ ] **Step 9: Format, lint, commit**

```bash
uv run black src/splitsmith/db/profile.py tests/test_profile_store.py src/splitsmith/ui/server.py src/splitsmith/db/__init__.py
uv run ruff check src/splitsmith/db/profile.py tests/test_profile_store.py
git add src/splitsmith/db/profile.py tests/test_profile_store.py src/splitsmith/db/__init__.py src/splitsmith/ui/server.py
git commit -m "feat(account): per-user profile store for display names (#867)"
```

---

## Task 3: `PATCH /api/me`

**Files:**
- Modify: `src/splitsmith/ui/server.py` (next to `get_me`, around line 10068)
- Test: `tests/test_account_display_name_api.py`

**Interfaces:**
- Consumes: `normalize_display_name` (Task 1), `state.profile` (Task 2).
- Produces: `PATCH /api/me` accepting `{"display_name": str | None}` and returning the updated `User`. Request model `UpdateMeRequest` with a **required, nullable** `display_name` field.

- [ ] **Step 1: Write the failing test**

Create `tests/test_account_display_name_api.py`:

```python
"""PATCH /api/me -- the route that makes users.display_name writable (#867).

Before this route existed the column was NULL for every real account, so
#866's ``author_kind="account"`` branch was unreachable in production.
The end-to-end proof of reachability lives in
tests/test_comments_signed_in.py; this file covers the route itself.

Fixture conventions mirror the other hosted API tests: ``hosted_app``
yields a (TestClient, email sender) pair and ``login`` drives the
magic-link flow.
"""

from __future__ import annotations

import pytest

from splitsmith.display_name import MAX_DISPLAY_NAME_LEN
from tests.hosted_helpers import login


def test_patch_sets_the_display_name(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "namer@example.com")

    resp = client.patch("/api/me", json={"display_name": "Anders Berg"})

    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Anders Berg"
    assert client.get("/api/me").json()["display_name"] == "Anders Berg"


def test_patch_normalizes_before_storing(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "messy@example.com")

    resp = client.patch("/api/me", json={"display_name": "  Anders    Berg  "})

    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Anders Berg"


def test_null_clears_the_display_name(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "clearer@example.com")
    client.patch("/api/me", json={"display_name": "Anders Berg"})

    resp = client.patch("/api/me", json={"display_name": None})

    assert resp.status_code == 200
    assert resp.json()["display_name"] is None


def test_blank_stores_null_not_empty_string(hosted_app) -> None:
    """The #866 fallback invariant, enforced at the write boundary: an
    account with a blank name must never publish an empty author."""
    client, sender = hosted_app
    login(client, sender, "blank@example.com")

    resp = client.patch("/api/me", json={"display_name": "   "})

    assert resp.status_code == 200
    assert resp.json()["display_name"] is None


@pytest.mark.parametrize(
    "bad",
    ["a" * (MAX_DISPLAY_NAME_LEN + 1), "Anders\nBerg", "Anders\x00Berg"],
)
def test_invalid_names_are_422(hosted_app, bad: str) -> None:
    client, sender = hosted_app
    login(client, sender, "invalid@example.com")

    resp = client.patch("/api/me", json={"display_name": bad})

    assert resp.status_code == 422


def test_a_rejected_name_is_not_persisted(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "rejected@example.com")
    client.patch("/api/me", json={"display_name": "Anders Berg"})

    client.patch("/api/me", json={"display_name": "a" * (MAX_DISPLAY_NAME_LEN + 1)})

    assert client.get("/api/me").json()["display_name"] == "Anders Berg"


def test_a_missing_field_is_422(hosted_app) -> None:
    """display_name is required-but-nullable, so an empty body cannot be
    read as 'clear it' by accident."""
    client, sender = hosted_app
    login(client, sender, "empty@example.com")

    assert client.patch("/api/me", json={}).status_code == 422


def test_anonymous_is_401(hosted_app) -> None:
    client, _ = hosted_app
    client.cookies.clear()

    assert client.patch("/api/me", json={"display_name": "Nobody"}).status_code == 401


def test_local_mode_404s(client) -> None:
    """LoopbackAuth's sentinel user has no row to write. The magic-link
    routes 404 in local mode for the same reason."""
    assert client.patch("/api/me", json={"display_name": "Local"}).status_code == 404
```

Add this test to the same file, using the desktop-token minting helper pattern from `tests/test_comments_signed_in.py`:

```python
def test_a_sync_scoped_desktop_token_cannot_set_a_name(hosted_env: str, hosted_app) -> None:
    """A sync-scoped token is confined to /api/sync/* by _auth_gate. #866
    already refuses it a name on a comment; it must not be able to set
    one on the account either. Inherited containment, pinned so a change
    to the gate surfaces here."""
    import asyncio

    from sqlalchemy import select as _select

    from splitsmith.db import User, create_engine, sessionmaker
    from splitsmith.db.desktop_tokens import DesktopTokenStore

    client, sender = hosted_app
    login(client, sender, "tokened@example.com")
    client.cookies.clear()

    async def _mint() -> str:
        engine = create_engine(hosted_env)
        sf = sessionmaker(engine)
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == "tokened@example.com"))).scalar_one()
        store = DesktopTokenStore(sf, user_id=row.id)
        _record, raw = await store.create("test device", scope="sync")
        return raw

    raw = asyncio.run(_mint())

    resp = client.patch(
        "/api/me",
        json={"display_name": "Impostor"},
        headers={"Authorization": f"Bearer {raw}"},
    )

    assert resp.status_code in (401, 404)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_account_display_name_api.py -n0 -q
```

Expected: the PATCH tests fail with 405 Method Not Allowed (the path exists for GET only).

If `hosted_app` / `hosted_env` / `client` are not the fixture names this repo uses, read `tests/conftest.py` and `tests/hosted_helpers.py` and use the real ones. Do not create new fixtures that duplicate existing ones.

- [ ] **Step 3: Write the route**

In `src/splitsmith/ui/server.py`, add the request model next to the other `/api/me` bodies (near `RecentProjectDeleteRequest`, around line 4600):

```python
class UpdateMeRequest(BaseModel):
    """Body for PATCH /api/me (#867).

    ``display_name`` is required *and* nullable: an explicit ``null``
    clears the name, and a body that omits the field is a 422 rather
    than being read as a clear. With one field on the model the
    distinction would otherwise be invisible.
    """

    display_name: str | None
```

Add the import at the top of the module, in the local-import group:

```python
from ..display_name import normalize_display_name
```

Add the route directly below `get_me`:

```python
    @app.patch("/api/me", response_model=User)
    async def patch_me(req: UpdateMeRequest, user: User = Depends(get_current_user)) -> User:
        """Update the signed-in account's profile. Hosted mode only.

        Local mode 404s: ``LoopbackAuth``'s sentinel user has no
        database row, and the magic-link routes 404 there for the same
        reason. A sync-scoped desktop token never arrives here at all --
        ``_auth_gate`` confines it to ``/api/sync/*``.

        Normalization runs before the store opens a session, so an
        invalid name is a 422 that touches nothing. A blank name stores
        ``None``, which is what keeps #866's fallback invariant true:
        an account with no name publishes a generated handle, never an
        empty string.
        """
        store = state.profile
        if store is None:
            raise HTTPException(status_code=404, detail="not found")
        try:
            display_name = normalize_display_name(req.display_name)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_display_name", "message": str(exc)},
            ) from exc
        await store.set_display_name(display_name)
        return user.model_copy(
            update={
                "display_name": display_name,
                "is_admin": user.email.lower() in state.admin_emails,
            }
        )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/test_account_display_name_api.py -n0 -q
```

Expected: all tests pass.

- [ ] **Step 5: Mutation check**

Temporarily delete the `try/except ValueError` and call `store.set_display_name(req.display_name)` with the raw value. Re-run and confirm `test_patch_normalizes_before_storing`, `test_blank_stores_null_not_empty_string`, and the three `test_invalid_names_are_422` cases all fail. Restore.

- [ ] **Step 6: Format, lint, commit**

```bash
uv run black src/splitsmith/ui/server.py tests/test_account_display_name_api.py
uv run ruff check src/splitsmith/ui/server.py tests/test_account_display_name_api.py
git add src/splitsmith/ui/server.py tests/test_account_display_name_api.py
git commit -m "feat(account): PATCH /api/me sets the account display name (#867)"
```

---

## Task 4: Author-code derivation

**Files:**
- Modify: `src/splitsmith/comment_identity.py`
- Test: `tests/test_comment_author_codes.py`

**Interfaces:**
- Consumes: `handle_secret()` (already in the module).
- Produces:
  - `AUTHOR_CODE_ALPHABET: Final[str]`, `AUTHOR_CODE_LEN: Final[int] = 6`
  - `derive_author_code(key: str, *, secret: bytes | None = None) -> str`
  - `author_code_for(*, author_kind: str, author_user_id: str | None, author_key_hash: str, secret: bytes | None = None) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_comment_author_codes.py`:

```python
"""Author-code derivation (#867).

The code is the disambiguator: two commenters posting under the same or
a similar name are told apart by it. That makes three properties
load-bearing -- it is stable for a given key, it is not the raw user id,
and the account and pseudonym branches feed the HMAC different keys but
produce codes from the same alphabet so neither is identifiable by shape.
"""

from __future__ import annotations

from splitsmith.comment_identity import (
    AUTHOR_CODE_ALPHABET,
    AUTHOR_CODE_LEN,
    author_code_for,
    derive_author_code,
)

SECRET = b"test-secret"


def test_code_is_the_declared_length_and_alphabet() -> None:
    code = derive_author_code("01JABCDEFGHJKMNPQRSTVWXYZ0", secret=SECRET)
    assert len(code) == AUTHOR_CODE_LEN
    assert set(code) <= set(AUTHOR_CODE_ALPHABET)


def test_the_alphabet_omits_lookalike_characters() -> None:
    """Crockford base32. I, L, O and U are absent so a code read aloud or
    copied by eye does not collide with a neighbour."""
    for ch in "ILOU":
        assert ch not in AUTHOR_CODE_ALPHABET


def test_code_is_stable_for_a_key() -> None:
    assert derive_author_code("key-a", secret=SECRET) == derive_author_code("key-a", secret=SECRET)


def test_different_keys_give_different_codes() -> None:
    assert derive_author_code("key-a", secret=SECRET) != derive_author_code("key-b", secret=SECRET)


def test_code_is_not_the_raw_key() -> None:
    """A ULID encodes its creation time, so publishing one leaks account
    age. The code must not contain it."""
    user_id = "01JABCDEFGHJKMNPQRSTVWXYZ0"
    assert derive_author_code(user_id, secret=SECRET) not in user_id


def test_the_secret_changes_the_code() -> None:
    assert derive_author_code("key-a", secret=b"one") != derive_author_code("key-a", secret=b"two")


def test_account_authors_key_off_the_user_id() -> None:
    code = author_code_for(
        author_kind="account",
        author_user_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
        author_key_hash="deadbeef",
        secret=SECRET,
    )
    assert code == derive_author_code("01JABCDEFGHJKMNPQRSTVWXYZ0", secret=SECRET)


def test_handle_authors_key_off_the_author_key_hash() -> None:
    code = author_code_for(
        author_kind="handle",
        author_user_id=None,
        author_key_hash="deadbeef",
        secret=SECRET,
    )
    assert code == derive_author_code("deadbeef", secret=SECRET)


def test_an_account_row_with_no_user_id_falls_back_to_the_key_hash() -> None:
    """author_user_id is ON DELETE SET NULL, so an account author whose
    account was deleted keeps author_kind='account' with a NULL id. It
    must still get a code rather than raising."""
    code = author_code_for(
        author_kind="account",
        author_user_id=None,
        author_key_hash="deadbeef",
        secret=SECRET,
    )
    assert code == derive_author_code("deadbeef", secret=SECRET)


def test_one_browser_posting_signed_in_and_signed_out_gets_two_codes() -> None:
    """The code identifies the author, not the browser. Posting under an
    account and posting anonymously from the same browser are two
    different authors and must read as two."""
    signed_in = author_code_for(
        author_kind="account",
        author_user_id="01JABCDEFGHJKMNPQRSTVWXYZ0",
        author_key_hash="deadbeef",
        secret=SECRET,
    )
    anonymous = author_code_for(
        author_kind="handle",
        author_user_id=None,
        author_key_hash="deadbeef",
        secret=SECRET,
    )
    assert signed_in != anonymous
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_comment_author_codes.py -n0 -q
```

Expected: `ImportError: cannot import name 'AUTHOR_CODE_ALPHABET'`.

- [ ] **Step 3: Write the implementation**

Append to `src/splitsmith/comment_identity.py`, after `derive_handle`:

```python
# Crockford base32: the digits plus the consonant-heavy letter set that
# omits I, L, O and U. Chosen over plain base32 so a code read aloud, or
# copied by eye off a comment thread, cannot be confused with a
# neighbouring one - which is the whole job of the code.
AUTHOR_CODE_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# 6 characters over a 32-symbol alphabet is 30 bits, ~1.07e9 codes.
AUTHOR_CODE_LEN: Final = 6

# Domain separation. The handle and the code are derived from the same
# secret and, for a pseudonymous author, from the same key -- without a
# prefix the two HMACs would be the same computation, and a change to
# one derivation could silently move the other.
_AUTHOR_CODE_DOMAIN: Final = b"author-code:"


def derive_author_code(key: str, *, secret: bytes | None = None) -> str:
    """Stable public identifier for a comment author.

    Six Crockford-base32 characters, deterministic for a given key +
    secret and not reversible to the key. Callers should prefer
    :func:`author_code_for`, which decides *which* key an author's code
    derives from; this function is the raw derivation.
    """
    material = secret if secret is not None else handle_secret()
    digest = hmac.new(material, _AUTHOR_CODE_DOMAIN + key.encode("utf-8"), hashlib.sha256).digest()
    value = int.from_bytes(digest[:8], "big")
    out = []
    for _ in range(AUTHOR_CODE_LEN):
        out.append(AUTHOR_CODE_ALPHABET[value % len(AUTHOR_CODE_ALPHABET)])
        value //= len(AUTHOR_CODE_ALPHABET)
    return "".join(out)


def author_code_for(
    *,
    author_kind: str,
    author_user_id: str | None,
    author_key_hash: str,
    secret: bytes | None = None,
) -> str:
    """The author code for one comment's author.

    An account author's code derives from their user id, so it is the
    same code across every browser they post from. A pseudonymous
    author's derives from the hashed browser key, which is the only
    identity they have. **Never the raw user id itself** -- it is the
    internal foreign key and a ULID encodes its creation time, so
    publishing it on an anonymous surface would leak account age.

    The single decision point for which key feeds the HMAC. The write
    path and the read-time fallback in ``ui/comments.to_out`` both call
    this, which is what makes a legacy row's computed code identical to
    the one the write path would have stored.

    ``author_user_id`` is ``ON DELETE SET NULL``, so an account author
    whose account was deleted arrives here as ``author_kind="account"``
    with no id. That falls back to the key hash rather than raising: the
    comment still needs a code, and the one thing it must not do is
    collide with another author's.
    """
    if author_kind == "account" and author_user_id:
        return derive_author_code(author_user_id, secret=secret)
    return derive_author_code(author_key_hash, secret=secret)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/test_comment_author_codes.py -n0 -q
```

Expected: all tests pass.

- [ ] **Step 5: Confirm the existing handle derivation is untouched**

```bash
uv run pytest tests/test_comment_identity.py -n0 -q
```

Expected: all pass. If any fail, the domain-separation prefix was applied to `derive_handle` by mistake -- it must not be.

- [ ] **Step 6: Mutation check**

Temporarily remove `_AUTHOR_CODE_DOMAIN +` from the HMAC input, re-run `tests/test_comment_identity.py` and `tests/test_comment_author_codes.py`, and confirm the author-code tests still pass but the codes change (add a scratch assertion comparing against a hard-coded expected value if you want to see it, then remove it). This one is informational: the domain prefix protects against future drift, not a currently-tested behaviour. Restore.

- [ ] **Step 7: Format, lint, commit**

```bash
uv run black src/splitsmith/comment_identity.py tests/test_comment_author_codes.py
uv run ruff check src/splitsmith/comment_identity.py tests/test_comment_author_codes.py
git add src/splitsmith/comment_identity.py tests/test_comment_author_codes.py
git commit -m "feat(comments): derive a stable public author code (#867)"
```

---

## Task 5: Persist and expose the author code

**Files:**
- Create: `alembic/versions/<rev>_add_author_code_to_match_comments.py`
- Modify: `src/splitsmith/db/models.py` (`CommentRow`)
- Modify: `src/splitsmith/db/comments.py` (`Comment`, `_to_comment`, `create`)
- Modify: `src/splitsmith/ui/comments.py` (`CommentOut`, `to_out`)
- Modify: `src/splitsmith/ui/server.py` (`create_stage_comment`)
- Test: `tests/test_comment_author_codes.py` (append), `tests/test_comments_store.py`, `tests/test_comments_api.py`

**Interfaces:**
- Consumes: `author_code_for` (Task 4).
- Produces: `CommentRow.author_code: Mapped[str | None]`; `Comment.author_code: str | None` and `Comment.author_user_id: str | None`; `CommentStore.create(..., author_code: str, ...)`; `CommentOut.author_code: str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_comment_author_codes.py`:

```python
def test_to_out_emits_the_stored_code() -> None:
    from datetime import UTC, datetime

    from splitsmith.db.comments import Comment
    from splitsmith.ui.comments import to_out

    comment = Comment(
        id="c1",
        anchor_t=1.0,
        anchor_kind="time",
        anchor_shot_id=None,
        author_kind="handle",
        author_user_id=None,
        author_handle="Prone Popper 47",
        author_key_hash="deadbeef",
        author_code="ABC123",
        share_token_id="s1",
        body="nice draw",
        created_at=datetime.now(UTC),
    )

    out = to_out(comment, author_key_hash=None, owner_view=False)

    assert out.author_code == "ABC123"


def test_to_out_computes_the_code_for_a_legacy_row() -> None:
    """Rows written before #867 have author_code NULL. The read-time
    fallback must produce exactly the code the write path would have
    stored, or a legacy comment and a new one from the same author would
    read as two different people."""
    from datetime import UTC, datetime

    from splitsmith.db.comments import Comment
    from splitsmith.ui.comments import to_out

    comment = Comment(
        id="c1",
        anchor_t=1.0,
        anchor_kind="time",
        anchor_shot_id=None,
        author_kind="handle",
        author_user_id=None,
        author_handle="Prone Popper 47",
        author_key_hash="deadbeef",
        author_code=None,
        share_token_id="s1",
        body="nice draw",
        created_at=datetime.now(UTC),
    )

    out = to_out(comment, author_key_hash=None, owner_view=False)

    assert out.author_code == author_code_for(
        author_kind="handle", author_user_id=None, author_key_hash="deadbeef"
    )
```

Append to `tests/test_comments_api.py` (match the file's existing fixture names):

```python
def test_a_posted_comment_carries_an_author_code(comment_token_client) -> None:
    client, token = comment_token_client
    created = _post(client, token).json()
    assert len(created["author_code"]) == 6


def test_two_browsers_get_two_codes(comment_token_client) -> None:
    client, token = comment_token_client
    first = _post(client, token, key="a" * 64).json()
    second = _post(client, token, key="b" * 64).json()
    assert first["author_code"] != second["author_code"]


def test_the_same_browser_keeps_one_code(comment_token_client) -> None:
    client, token = comment_token_client
    first = _post(client, token, key="a" * 64).json()
    second = _post(client, token, key="a" * 64).json()
    assert first["author_code"] == second["author_code"]


def test_author_code_survives_a_handle_secret_rotation(
    comment_token_client, monkeypatch
) -> None:
    """The code is denormalized at write time for the same reason
    author_handle is: rotating the secret must not re-identify history."""
    from splitsmith.comment_identity import SPLITSMITH_COMMENT_HANDLE_SECRET_ENV

    client, token = comment_token_client
    created = _post(client, token).json()

    monkeypatch.setenv(SPLITSMITH_COMMENT_HANDLE_SECRET_ENV, "a-rotated-secret")
    listed = client.get(f"/api/share/{token}/shooters/alice/stages/3/comments").json()

    assert listed["comments"][0]["author_code"] == created["author_code"]
```

`_post` in `tests/test_comments_api.py` may not take a `key` argument today. If it does not, add one with the file's existing default so the three tests above can vary it:

```python
def _post(client, token, key=KEY, **headers):
    return client.post(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        json={"body": "nice draw", "anchor_t": 1.0},
        headers={AUTHOR_KEY_HEADER: key, **headers},
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_comment_author_codes.py tests/test_comments_api.py -n0 -q
```

Expected: `TypeError: Comment.__init__() got an unexpected keyword argument 'author_user_id'` and `KeyError: 'author_code'`.

- [ ] **Step 3: Add the column to the model**

In `src/splitsmith/db/models.py`, in `CommentRow`, directly after `author_key_hash`:

```python
    # Stable public identifier for the author, denormalized at write
    # time for the same reason author_handle is: rotating the handle
    # secret must not re-identify every historical author. Nullable only
    # to carry rows written before #867 - see ui/comments.to_out, which
    # computes the same value for those through author_code_for.
    author_code: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: Generate and edit the migration**

```bash
uv run alembic revision -m "add author_code to match_comments"
```

Edit the generated file so its body reads:

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("match_comments", sa.Column("author_code", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("match_comments", "author_code")
```

Confirm the generated header has `down_revision: str | Sequence[str] | None = "b4d8f1a90c27"` -- that is the current head (the match-comments table migration). If Alembic picked a different parent, the head moved; re-check with `uv run alembic heads` and set it to the real head.

Add a module docstring note under the auto-generated one:

```
No backfill. #866 landed after the 0.29.0 release and is unreleased, so
production has no comment rows. Dev and staging rows written before this
migration keep author_code NULL and get their code computed at read time
by ui/comments.to_out -- a backfill here would have to reproduce the
HMAC secret in the migration environment, and getting that wrong would
write plausible-looking wrong codes with no error.
```

- [ ] **Step 5: Thread the code through the store**

In `src/splitsmith/db/comments.py`:

Add two fields to the `Comment` dataclass, after `author_kind`:

```python
    author_user_id: str | None
```

and after `author_key_hash`:

```python
    author_code: str | None
```

Add both to `_to_comment`:

```python
        author_user_id=row.author_user_id,
```
```python
        author_code=row.author_code,
```

Add the parameter to `create`, after `author_handle`, and pass it to the row:

```python
        author_code: str,
```
```python
            author_code=author_code,
```

- [ ] **Step 6: Emit it on the wire**

In `src/splitsmith/ui/comments.py`:

Add the import:

```python
from ..comment_identity import author_code_for
```

Add the field to `CommentOut`, after `author_handle`:

```python
    author_code: str
```

In `to_out`, compute it once above the `if owner_view:` branch and pass it into **both** constructors:

```python
    # A row written before #867 has no stored code; compute the same
    # value the write path would have. Going through author_code_for
    # (rather than repeating the key choice here) is what guarantees a
    # legacy comment and a new one from the same author read as one
    # person.
    author_code = comment.author_code or author_code_for(
        author_kind=comment.author_kind,
        author_user_id=comment.author_user_id,
        author_key_hash=comment.author_key_hash,
    )
```

```python
            author_code=author_code,
```

Extend the `CommentOut` docstring with a sentence:

```
``author_code`` is on the base model, not :class:`CommentOwnerOut`:
visitors need it for the tooltip and for the client-side name-collision
check, and it exposes nothing -- it is an HMAC of an identifier, not the
identifier.
```

- [ ] **Step 7: Compute it on the write path**

In `src/splitsmith/ui/server.py`, in `create_stage_comment`, after the `author_kind` / `author_user_id` / `author_handle` branch and before `store.create`:

```python
        author_code = author_code_for(
            author_kind=author_kind,
            author_user_id=author_user_id,
            author_key_hash=hash_author_key(author_key),
        )
```

Pass it into the `store.create(...)` call:

```python
            author_code=author_code,
```

Add `author_code_for` to the existing `comment_identity` import in that module.

- [ ] **Step 8: Run the tests to verify they pass**

```bash
uv run pytest tests/test_comment_author_codes.py tests/test_comments_api.py tests/test_comments_store.py tests/test_comments_schema.py -n0 -q
```

Expected: all pass. `tests/test_comments_store.py` constructs `Comment` and calls `create` directly; update its call sites for the new fields, keeping every existing assertion intact.

- [ ] **Step 9: Verify the migration actually runs**

```bash
uv run pytest tests/test_comments_schema.py -n0 -q
```

Expected: pass. If the repo has a docker-marked migration test, also run:

```bash
uv run pytest -m docker -k comments -n0 -q
```

Expected: pass, or skip cleanly if docker is unavailable locally.

- [ ] **Step 10: Mutation check**

Temporarily change `to_out`'s fallback to `comment.author_code or "ZZZZZZ"`. Re-run `tests/test_comment_author_codes.py` and confirm `test_to_out_computes_the_code_for_a_legacy_row` fails. Restore.

- [ ] **Step 11: Format, lint, commit**

```bash
uv run black src/splitsmith/db/models.py src/splitsmith/db/comments.py src/splitsmith/ui/comments.py src/splitsmith/ui/server.py alembic/versions/ tests/
uv run ruff check src/splitsmith tests
git add -A
git commit -m "feat(comments): denormalize a stable author code on every comment (#867)"
```

---

## Task 6: End-to-end reachability

**Files:**
- Modify: `tests/test_comments_signed_in.py`

**Interfaces:**
- Consumes: `PATCH /api/me` (Task 3), `author_code` on `CommentOut` (Task 5).
- Produces: nothing. This task is the proof that #867 is closed.

This is the task the issue exists for. Every existing test of the account branch sets `users.display_name` by hand, which is exactly why the branch shipped unreachable. This one drives the real route.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_comments_signed_in.py`:

```python
def test_a_signed_in_visitor_can_set_a_name_and_comment_under_it(
    hosted_env: str, hosted_app, comment_token_client
) -> None:
    """The reachability proof for #867.

    Nothing here writes users.display_name directly. The visitor signs
    in, sets a name through the same route the /account page calls, and
    posts through a comment-scoped share link. Before #867 there was no
    such route, so this branch could not be reached by any sequence of
    requests a real user could make.
    """
    client, sender = hosted_app
    login(client, sender, "reachable@example.com")
    secret = client.cookies.get(SESSION_COOKIE_NAME)
    assert secret is not None

    resp = client.patch("/api/me", json={"display_name": "Anders Berg"})
    assert resp.status_code == 200

    client.cookies.clear()
    headers = {"Cookie": f"{SESSION_COOKIE_NAME}={secret}"}
    share_client, token = comment_token_client
    created = _post(share_client, token, **headers).json()

    assert created["author_kind"] == "account"
    assert created["author_handle"] == "Anders Berg"
    assert len(created["author_code"]) == 6


def test_two_accounts_with_the_same_name_get_different_codes(
    hosted_env: str, hosted_app, comment_token_client
) -> None:
    """The disambiguation the code exists for. Two real accounts, one
    name, two codes."""
    client, sender = hosted_app
    share_client, token = comment_token_client
    codes = []
    for email in ("twin-a@example.com", "twin-b@example.com"):
        login(client, sender, email)
        secret = client.cookies.get(SESSION_COOKIE_NAME)
        assert secret is not None
        assert client.patch("/api/me", json={"display_name": "Anders Berg"}).status_code == 200
        client.cookies.clear()
        created = _post(
            share_client,
            token,
            key=email.replace("@", "").ljust(64, "x")[:64],
            **{"Cookie": f"{SESSION_COOKIE_NAME}={secret}"},
        ).json()
        assert created["author_handle"] == "Anders Berg"
        codes.append(created["author_code"])

    assert codes[0] != codes[1]
```

`_post` in this file takes `(client, token, **headers)` and hard-codes `KEY`. Widen it exactly as Task 5 widened the twin in `tests/test_comments_api.py`:

```python
def _post(client, token, key=KEY, **headers):
    return client.post(
        f"/api/share/{token}/shooters/alice/stages/3/comments",
        json={"body": "nice draw", "anchor_t": 1.0},
        headers={AUTHOR_KEY_HEADER: key, **headers},
    )
```

- [ ] **Step 2: Run the tests to verify they pass**

```bash
uv run pytest tests/test_comments_signed_in.py -n0 -q
```

Expected: all pass, including the pre-existing fallback tests.

- [ ] **Step 3: Prove the new test would have caught the bug**

This is the step that matters. Verify the test genuinely depends on the route existing:

```bash
git stash
uv run pytest tests/test_comments_signed_in.py::test_a_signed_in_visitor_can_set_a_name_and_comment_under_it -n0 -q
git stash pop
```

Expected: **FAIL**, with a 405 or 404 on the PATCH. If it passes, the test is not testing what it claims and must be rewritten before this task is done.

Two ways this check silently lies, both worth guarding against:

- **Stash in place; do not use a second worktree.** The project is an editable install pointing at *this* directory, so a separate pre-fix worktree would still import the post-fix code from here and pass -- failing toward reassurance, which is the direction that hides bugs.
- **Read the failure, do not just observe one.** A stale `__pycache__` or an unrelated `ImportError` also produces a red test. Confirm the message names the PATCH returning 405/404, not an import or collection error.

- [ ] **Step 4: Update the stale helper docstring**

`_set_display_name` in this file says "There is no route that lets an account set its own display name yet". That is now false. Keep the helper -- the fallback fixtures still use it to set a whitespace-only name, which the route deliberately refuses to store -- but correct the docstring:

```python
def _set_display_name(db_url: str, email: str, display_name: str | None) -> None:
    """Set ``users.display_name`` directly, bypassing PATCH /api/me.

    Kept after #867 for exactly one purpose: seeding states the route
    refuses to produce. ``"   "`` is the important one -- the route
    normalizes a blank name to ``None``, so a whitespace-only column
    value can only be reached by writing the row, and the fallback guard
    it exercises (a non-None string that is blank after stripping) has no
    other way to be tested. Any test asserting the *reachable* account
    branch must go through the route instead; see
    ``test_a_signed_in_visitor_can_set_a_name_and_comment_under_it``.
    """
```

- [ ] **Step 5: Run the whole comment suite**

```bash
uv run pytest tests/test_comments_signed_in.py tests/test_comments_api.py tests/test_comments_moderation.py tests/test_comments_seams.py tests/test_share_comment_scope.py -n0 -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
uv run black tests/test_comments_signed_in.py
git add tests/test_comments_signed_in.py
git commit -m "test(comments): prove account attribution is reachable end to end (#867)"
```

---

## Task 7: Owner author summaries

**Files:**
- Modify: `src/splitsmith/db/comments.py` (`CommentAuthorSummary`, `author_summaries`)
- Modify: `src/splitsmith/ui/comments.py` (`CommentAuthorOut`, `CommentAuthorListResponse`)
- Modify: `src/splitsmith/ui/server.py` (`GET /api/match/comment-authors`)
- Test: `tests/test_comment_author_summaries.py`

**Interfaces:**
- Consumes: `author_code_for` (Task 4), `CommentStore` (Task 5).
- Produces:
  - `CommentAuthorSummary` frozen dataclass: `author_code: str`, `author_kind: str`, `first_comment_at: datetime`, `comment_count: int`, `handles: tuple[str, ...]`
  - `CommentStore.author_summaries(match_id: str) -> list[CommentAuthorSummary]`
  - `GET /api/match/comment-authors` -> `CommentAuthorListResponse` with `authors: list[CommentAuthorOut]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_comment_author_summaries.py`. Copy the whole preamble from `tests/test_comments_signed_in.py` verbatim -- the imports (`login`, `seed_match`, `SESSION_COOKIE_NAME`, `AUTHOR_KEY_HEADER`), the `KEY` / `MID` / `SLUG` / `STAGE` constants, and the helpers `_post`, `_seed_state_docs`, `_mint_share_token`, `_seeded_match`, `comment_token_client`, `owner_client`. This repo's convention is that each comment test file carries its own self-contained fixture set rather than importing another file's; `test_comments_moderation.py` and `test_comments_signed_in.py` both do this and say so in their module docstrings.

Use the widened `_post` from Task 6 (the one taking `key=KEY`), since three tests below vary the browser key.

The URL prefix for owner requests (`/api/matches/{MID}/...`) is the match-alias form the existing owner tests use -- copy the exact shape from `tests/test_comments_moderation.py` rather than the sketch below if they differ.

```python
"""GET /api/match/comment-authors -- owner-only author detail (#867).

The name history is the impersonation signal: an account that renamed
itself to match another commenter shows two handles under one code,
which no single comment can reveal.

Owner-only by construction, not by a check in the handler: the route is
absent from _SHARE_PATH_RE, so an anonymous caller gets the same uniform
404 the share surface returns for anything it does not admit.
"""


def test_summaries_group_by_author_code(comment_token_client, owner_client) -> None:
    client, token = comment_token_client
    _post(client, token, key="a" * 64)
    _post(client, token, key="a" * 64)
    _post(client, token, key="b" * 64)

    resp = owner_client.get(f"/api/matches/{MID}/match/comment-authors")

    assert resp.status_code == 200
    authors = resp.json()["authors"]
    assert len(authors) == 2
    assert sorted(a["comment_count"] for a in authors) == [1, 2]


def test_every_handle_a_code_posted_under_is_listed(
    hosted_env: str, hosted_app, comment_token_client, owner_client
) -> None:
    """One account, two names, one code. This is the whole point."""
    client, sender = hosted_app
    share_client, token = comment_token_client
    login(client, sender, "renamer@example.com")
    secret = client.cookies.get(SESSION_COOKIE_NAME)
    headers = {"Cookie": f"{SESSION_COOKIE_NAME}={secret}"}
    client.cookies.clear()

    client.patch("/api/me", json={"display_name": "Anders Berg"}, headers=headers)
    _post(share_client, token, **headers)
    client.patch("/api/me", json={"display_name": "Bertil Lund"}, headers=headers)
    _post(share_client, token, **headers)

    authors = owner_client.get(f"/api/matches/{MID}/match/comment-authors").json()["authors"]

    account = [a for a in authors if a["author_kind"] == "account"]
    assert len(account) == 1
    assert sorted(account[0]["handles"]) == ["Anders Berg", "Bertil Lund"]
    assert account[0]["comment_count"] == 2


def test_soft_deleted_comments_are_excluded(comment_token_client, owner_client) -> None:
    client, token = comment_token_client
    created = _post(client, token, key="a" * 64).json()
    _post(client, token, key="a" * 64)
    owner_client.delete(f"/api/matches/{MID}/shooters/alice/stages/3/comments/{created['id']}")

    authors = owner_client.get(f"/api/matches/{MID}/match/comment-authors").json()["authors"]

    assert [a["comment_count"] for a in authors] == [1]


def test_an_anonymous_caller_gets_a_404(comment_token_client) -> None:
    client, token = comment_token_client

    resp = client.get(f"/api/share/{token}/match/comment-authors")

    assert resp.status_code == 404


def test_first_comment_at_is_the_earliest(comment_token_client, owner_client) -> None:
    client, token = comment_token_client
    first = _post(client, token, key="a" * 64).json()
    _post(client, token, key="a" * 64)

    authors = owner_client.get(f"/api/matches/{MID}/match/comment-authors").json()["authors"]

    assert authors[0]["first_comment_at"][:19] == first["created_at"][:19]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_comment_author_summaries.py -n0 -q
```

Expected: 404 on every owner request -- the route does not exist.

- [ ] **Step 3: Add the store method**

In `src/splitsmith/db/comments.py`, add the dataclass after `Comment`:

```python
@dataclass(frozen=True)
class CommentAuthorSummary:
    """One author's footprint on a match, as the owner's moderation view
    reports it."""

    author_code: str
    author_kind: str
    first_comment_at: datetime
    comment_count: int
    handles: tuple[str, ...]
```

Add the method to `CommentStore`:

```python
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
                        select(CommentRow).where(
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
```

Add the import at the top of the module:

```python
from ..comment_identity import author_code_for
```

- [ ] **Step 4: Add the wire models**

In `src/splitsmith/ui/comments.py`, after `CommentOwnerListResponse`:

```python
class CommentAuthorOut(BaseModel):
    """One author's footprint on a match. Owner-only -- the route that
    returns this is absent from ``_SHARE_PATH_RE``, so the anonymous
    surface cannot reach it.

    ``handles`` is every distinct name the code posted under, oldest
    first. An account that renamed itself to match another commenter
    shows two names here under one code."""

    author_code: str
    author_kind: str
    first_comment_at: datetime
    comment_count: int
    handles: list[str]


class CommentAuthorListResponse(BaseModel):
    authors: list[CommentAuthorOut]
```

- [ ] **Step 5: Add the route**

In `src/splitsmith/ui/server.py`, directly above `delete_match_comments`:

```python
    @app.get("/api/match/comment-authors", response_model=CommentAuthorListResponse)
    async def list_match_comment_authors() -> CommentAuthorListResponse:
        """Per-author detail for the owner's moderation view.

        Owner-only by construction: the shape is absent from
        ``_SHARE_PATH_RE``, so an anonymous share caller gets the same
        uniform 404 as any unadmitted path. No capability entry is
        needed -- ``required_capability`` returns ``None`` for GET.

        Match-scoped on purpose. Aggregating an author across matches
        would reveal that they commented on other people's share links,
        which is a disclosure they never opted into.
        """
        store = state.comments
        mid = current_match_id.get()
        if store is None or mid is None:
            raise HTTPException(status_code=404, detail="not found")
        summaries = await store.author_summaries(mid)
        return CommentAuthorListResponse(
            authors=[
                CommentAuthorOut(
                    author_code=s.author_code,
                    author_kind=s.author_kind,
                    first_comment_at=s.first_comment_at,
                    comment_count=s.comment_count,
                    handles=list(s.handles),
                )
                for s in summaries
            ]
        )
```

Add `CommentAuthorListResponse` and `CommentAuthorOut` to the existing `from .comments import (...)` import in that module.

- [ ] **Step 6: Run the test to verify it passes**

```bash
uv run pytest tests/test_comment_author_summaries.py -n0 -q
```

Expected: all pass.

- [ ] **Step 7: Confirm the anonymous surface did not widen**

```bash
uv run pytest tests/test_share_comment_scope.py tests/test_comments_seams.py tests/test_mirror_read_only.py -n0 -q
```

Expected: all pass. `test_an_anonymous_caller_gets_a_404` in the new file is the direct assertion; these are the surrounding guards.

- [ ] **Step 8: Mutation check**

Temporarily add `r"|match/comment-authors"` to `_SHARE_PATH_RE`, re-run `tests/test_comment_author_summaries.py`, and confirm `test_an_anonymous_caller_gets_a_404` fails. Restore -- and leave the pattern exactly as it was.

- [ ] **Step 9: Format, lint, commit**

```bash
uv run black src/splitsmith/db/comments.py src/splitsmith/ui/comments.py src/splitsmith/ui/server.py tests/test_comment_author_summaries.py
uv run ruff check src/splitsmith tests
git add -A
git commit -m "feat(comments): owner-only per-author detail for a match (#867)"
```

---

## Task 8: Frontend API surface

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts`

**Interfaces:**
- Consumes: the routes from Tasks 3, 5, 7.
- Produces:
  - `Comment.author_code: string`
  - `api.updateMe(displayName: string | null): Promise<AuthUser>`
  - `CommentAuthor` interface + `CommentAuthorListResponse`
  - `api.listCommentAuthors(): Promise<CommentAuthorListResponse>`

- [ ] **Step 1: Add the comment field**

In the `Comment` interface, after `author_handle`:

```ts
  /** Stable public identifier for the author -- an HMAC of their account
   *  id or browser key, never either one. Two authors posting under the
   *  same name are told apart by this. See lib/authorAmbiguity.ts. */
  author_code: string;
```

- [ ] **Step 2: Add the author-summary types**

After `CommentCreateInput`:

```ts
/** One author's footprint on a match. Owner-only: the route is not on
 *  the anonymous share surface. */
export interface CommentAuthor {
  author_code: string;
  author_kind: "handle" | "account";
  first_comment_at: string;
  comment_count: number;
  /** Every distinct name this code posted under, oldest first. Two
   *  entries means the author renamed themselves mid-thread. */
  handles: string[];
}

export interface CommentAuthorListResponse {
  authors: CommentAuthor[];
}
```

- [ ] **Step 3: Add the calls**

Next to `getMe`:

```ts
  /** Hosted mode -- set or clear the account display name. 404s in local
   *  mode. A blank name stores null server-side, never an empty string. */
  updateMe: (displayName: string | null) =>
    request<AuthUser>("/api/me", {
      method: "PATCH",
      json: { display_name: displayName },
    }),
```

Next to `deleteStageComment`:

```ts
  /** Per-author detail across the whole match. Owner-only -- an
   *  anonymous share caller gets a 404. */
  listCommentAuthors(): Promise<CommentAuthorListResponse> {
    return request<CommentAuthorListResponse>("/api/match/comment-authors");
  },
```

Check how `listStageComments` builds its URL versus how an existing `/api/match/...` call does (for example the share-management calls). If match-scoped calls in this file go through a match-id prefix helper, use the same one rather than the bare path above.

- [ ] **Step 4: Typecheck**

```bash
cd src/splitsmith/ui_static && pnpm typecheck
```

Expected: errors in every test file that constructs a `Comment` literal, because `author_code` is now required. That is the correct failure -- fix them in Step 5.

- [ ] **Step 5: Fix the fixtures**

Add `author_code` to every `Comment` literal in the existing tests. Use a distinct value per author within a file so a future collision test is not accidentally passing on identical codes:

```bash
grep -rln "author_handle" src/splitsmith/ui_static/src --include="*.test.tsx" --include="*.test.ts"
```

- [ ] **Step 6: Typecheck and test**

```bash
cd src/splitsmith/ui_static && pnpm typecheck && pnpm test
```

Expected: clean typecheck, all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/ui_static/src
git commit -m "feat(ui): author codes and updateMe on the API client (#867)"
```

---

## Task 9: Name-collision detection

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/authorAmbiguity.ts`
- Create: `src/splitsmith/ui_static/src/lib/authorAmbiguity.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `normalizeAuthorName(name: string): string`
  - `ambiguousCodes(authors: readonly { author_handle: string; author_code: string }[]): Set<string>`

- [ ] **Step 1: Write the failing test**

Create `src/splitsmith/ui_static/src/lib/authorAmbiguity.test.ts`:

```ts
/**
 * Name-collision detection for a comment thread (#867).
 *
 * The rule is deliberately narrow -- equality after folding, not edit
 * distance. The test for what it does NOT catch is as load-bearing as
 * the ones for what it does: the always-present tooltip is what covers
 * the rest, and a rule that quietly widened would make the visible code
 * appear on names that merely rhyme.
 */
import { describe, expect, it } from "vitest";

import { ambiguousCodes, normalizeAuthorName } from "@/lib/authorAmbiguity";

function author(author_handle: string, author_code: string) {
  return { author_handle, author_code };
}

describe("normalizeAuthorName", () => {
  it("folds case", () => {
    expect(normalizeAuthorName("Mathias Axell")).toBe(
      normalizeAuthorName("mathias axell"),
    );
  });

  it("collapses whitespace", () => {
    expect(normalizeAuthorName("Mathias  Axell")).toBe(
      normalizeAuthorName("Mathias Axell"),
    );
  });

  it("strips diacritics", () => {
    expect(normalizeAuthorName("M\u00e5thias Axell")).toBe(
      normalizeAuthorName("Mathias Axell"),
    );
  });

  it("drops non-alphanumeric characters", () => {
    expect(normalizeAuthorName("Mathias-Axell")).toBe(
      normalizeAuthorName("Mathias Axell"),
    );
  });

  it("folds compatibility forms", () => {
    // U+FF2D, fullwidth Latin capital M. Written as an escape so the
    // case is legible; a literal would look like a plain "M".
    expect(normalizeAuthorName("\uff2dathias Axell")).toBe(
      normalizeAuthorName("Mathias Axell"),
    );
  });

  it("keeps genuinely different names apart", () => {
    expect(normalizeAuthorName("Mathlas Axell")).not.toBe(
      normalizeAuthorName("Mathias Axell"),
    );
  });
});

describe("ambiguousCodes", () => {
  it("is empty when every name is distinct", () => {
    const codes = ambiguousCodes([
      author("Mathias Axell", "AAA111"),
      author("Anders Berg", "BBB222"),
    ]);
    expect(codes.size).toBe(0);
  });

  it("flags both codes when two authors share a name", () => {
    const codes = ambiguousCodes([
      author("Mathias Axell", "AAA111"),
      author("mathias  axell", "BBB222"),
    ]);
    expect(codes).toEqual(new Set(["AAA111", "BBB222"]));
  });

  it("does not flag one author posting twice under one name", () => {
    const codes = ambiguousCodes([
      author("Mathias Axell", "AAA111"),
      author("Mathias Axell", "AAA111"),
    ]);
    expect(codes.size).toBe(0);
  });

  it("does not flag one author who renamed themselves", () => {
    // Same code, two names. That is the owner view's business, not the
    // thread's -- nothing here is ambiguous to a reader.
    const codes = ambiguousCodes([
      author("Mathias Axell", "AAA111"),
      author("Anders Berg", "AAA111"),
    ]);
    expect(codes.size).toBe(0);
  });

  it("flags an account shadowing a generated handle", () => {
    const codes = ambiguousCodes([
      author("Prone Popper 47", "AAA111"),
      author("Prone Popper 47", "BBB222"),
    ]);
    expect(codes).toEqual(new Set(["AAA111", "BBB222"]));
  });

  it("flags only the colliding pair in a mixed thread", () => {
    const codes = ambiguousCodes([
      author("Mathias Axell", "AAA111"),
      author("Mathias Axell", "BBB222"),
      author("Anders Berg", "CCC333"),
    ]);
    expect(codes).toEqual(new Set(["AAA111", "BBB222"]));
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd src/splitsmith/ui_static && pnpm exec vitest run src/lib/authorAmbiguity.test.ts
```

Expected: `Failed to resolve import "@/lib/authorAmbiguity"`.

- [ ] **Step 3: Write the implementation**

Create `src/splitsmith/ui_static/src/lib/authorAmbiguity.ts`:

```ts
/**
 * Detects two comment authors posting under confusingly similar names.
 *
 * The server publishes a display name an account chose for itself, so
 * an account holder can set theirs to another commenter's -- including
 * a generated pseudonym like "Prone Popper 47". Every author also
 * carries an `author_code`, which is always in the DOM and always in a
 * tooltip. This module decides when that code additionally becomes
 * *visible*, so a reader does not have to get suspicious first.
 *
 * The rule is equality after folding, not similarity. It catches case,
 * spacing, punctuation, diacritics, and Unicode compatibility forms --
 * the variants someone would reach for on purpose. It does NOT catch
 * "Mathlas" against "Mathias": no edit distance, no homoglyph table.
 * That limit is deliberate. A fuzzier rule would surface codes on names
 * that merely rhyme, training the reader to ignore the signal, and the
 * always-present tooltip already covers everything this misses.
 *
 * Storage normalization is a different function with different rules
 * (`splitsmith.display_name.normalize_display_name`, NFC): it preserves
 * the name the user typed. This one folds aggressively because it is
 * adversarial. Do not reuse either for the other's job.
 */

/** Fold a display name to its comparison form. */
export function normalizeAuthorName(name: string): string {
  return name
    .normalize("NFKD")
    // Strip combining marks left by NFKD, so "a" with a ring above
    // folds onto a plain "a".
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    // Everything that is not a letter or a number becomes a separator,
    // so punctuation and spacing stop being a way to differ.
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

/**
 * Codes whose display name is shared with a *different* author in the
 * same thread.
 *
 * One author posting many times is not ambiguous, and neither is one
 * author who renamed themselves -- both are a single code. Only two
 * distinct codes landing on one normalized name are.
 */
export function ambiguousCodes(
  authors: readonly { author_handle: string; author_code: string }[],
): Set<string> {
  const byName = new Map<string, Set<string>>();
  for (const a of authors) {
    const key = normalizeAuthorName(a.author_handle);
    const codes = byName.get(key) ?? new Set<string>();
    codes.add(a.author_code);
    byName.set(key, codes);
  }
  const ambiguous = new Set<string>();
  for (const codes of byName.values()) {
    if (codes.size < 2) continue;
    for (const code of codes) ambiguous.add(code);
  }
  return ambiguous;
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd src/splitsmith/ui_static && pnpm exec vitest run src/lib/authorAmbiguity.test.ts
```

Expected: all tests pass.

- [ ] **Step 5: Mutation check**

Temporarily change `if (codes.size < 2) continue;` to `if (codes.size < 1) continue;` and confirm `it("does not flag one author posting twice under one name")` fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/authorAmbiguity.ts src/splitsmith/ui_static/src/lib/authorAmbiguity.test.ts
git commit -m "feat(ui): detect comment authors with confusingly similar names (#867)"
```

---

## Task 10: Render author codes in the thread

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/comments/CommentPanel.tsx`
- Test: `src/splitsmith/ui_static/src/components/comments/CommentPanel.test.tsx`

**Interfaces:**
- Consumes: `ambiguousCodes` (Task 9), `Comment.author_code` (Task 8), `api.listCommentAuthors` (Task 8).
- Produces: nothing consumed downstream.

- [ ] **Step 1: Write the failing test**

Add to `src/splitsmith/ui_static/src/components/comments/CommentPanel.test.tsx` (match the file's existing mock setup and comment factory):

```tsx
it("puts every author code in the DOM and in a tooltip", async () => {
  mockList([comment({ author_handle: "Anders Berg", author_code: "AAA111" })]);
  render(<CommentPanel {...baseProps} />);

  const author = await screen.findByText("Anders Berg");
  expect(author).toHaveAttribute("data-author-code", "AAA111");
  expect(author).toHaveAttribute("title", expect.stringContaining("AAA111"));
});

it("does not show a code when every name is distinct", async () => {
  mockList([
    comment({ id: "c1", author_handle: "Anders Berg", author_code: "AAA111" }),
    comment({ id: "c2", author_handle: "Bertil Lund", author_code: "BBB222" }),
  ]);
  render(<CommentPanel {...baseProps} />);

  await screen.findByText("Anders Berg");
  expect(screen.queryByText("AAA111")).not.toBeInTheDocument();
});

it("shows both codes when two authors share a name", async () => {
  mockList([
    comment({ id: "c1", author_handle: "Anders Berg", author_code: "AAA111" }),
    comment({ id: "c2", author_handle: "anders  berg", author_code: "BBB222" }),
  ]);
  render(<CommentPanel {...baseProps} />);

  expect(await screen.findByText("AAA111")).toBeInTheDocument();
  expect(await screen.findByText("BBB222")).toBeInTheDocument();
});

it("shows no code when one author posts twice", async () => {
  mockList([
    comment({ id: "c1", author_handle: "Anders Berg", author_code: "AAA111" }),
    comment({ id: "c2", author_handle: "Anders Berg", author_code: "AAA111" }),
  ]);
  render(<CommentPanel {...baseProps} />);

  await screen.findAllByText("Anders Berg");
  expect(screen.queryByText("AAA111")).not.toBeInTheDocument();
});

it("offers author detail only to a moderator", async () => {
  mockList([comment({ author_handle: "Anders Berg", author_code: "AAA111" })]);
  const { rerender } = render(<CommentPanel {...baseProps} canModerate={false} />);
  await screen.findByText("Anders Berg");
  expect(screen.queryByRole("button", { name: /author detail/i })).not.toBeInTheDocument();

  rerender(<CommentPanel {...baseProps} canModerate />);
  expect(
    await screen.findByRole("button", { name: /author detail/i }),
  ).toBeInTheDocument();
});

it("shows every name a code posted under when a moderator opens detail", async () => {
  mockList([comment({ author_handle: "Bertil Lund", author_code: "AAA111" })]);
  vi.mocked(api.listCommentAuthors).mockResolvedValue({
    authors: [
      {
        author_code: "AAA111",
        author_kind: "account",
        first_comment_at: "2026-08-13T10:00:00Z",
        comment_count: 2,
        handles: ["Anders Berg", "Bertil Lund"],
      },
    ],
  });
  render(<CommentPanel {...baseProps} canModerate />);

  fireEvent.click(await screen.findByRole("button", { name: /author detail/i }));

  expect(await screen.findByText(/Anders Berg/)).toBeInTheDocument();
  expect(await screen.findByText(/2 comments/i)).toBeInTheDocument();
});
```

Add `listCommentAuthors: vi.fn()` to the file's `vi.mock("@/lib/api", ...)` api stub.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd src/splitsmith/ui_static && pnpm exec vitest run src/components/comments/CommentPanel.test.tsx
```

Expected: the new tests fail -- no `data-author-code` attribute, no detail button.

- [ ] **Step 3: Implement**

In `CommentPanel.tsx`:

Add the imports:

```ts
import { ambiguousCodes } from "@/lib/authorAmbiguity";
import { api, apiErrorText, type Comment, type CoachShot, type CommentAuthor } from "@/lib/api";
```

Add state next to the existing hooks:

```ts
  const [openAuthor, setOpenAuthor] = useState<string | null>(null);
  const [authors, setAuthors] = useState<CommentAuthor[] | null>(null);
```

Derive the ambiguity set from the loaded thread (recomputed per render off `comments`, which is cheap for a thread bounded by the per-stage cap):

```ts
  const ambiguous = ambiguousCodes(comments ?? []);
```

Load author detail lazily, only for a moderator and only once:

```ts
  // Owner-only, and only when they actually ask: the endpoint is a
  // match-wide aggregate, so fetching it on mount would cost every
  // reader a query for a panel most of them never open.
  async function openAuthorDetail(code: string) {
    setOpenAuthor((current) => (current === code ? null : code));
    if (authors !== null) return;
    try {
      const resp = await api.listCommentAuthors();
      setAuthors(resp.authors);
    } catch {
      // Detail is an enrichment - a failed fetch leaves the panel
      // showing the code alone, which is still the disambiguator.
      setAuthors([]);
    }
  }
```

Replace the author `<span>` in the comment row with:

```tsx
                    <span
                      className="font-mono text-xs font-bold uppercase tracking-[0.06em] text-ink"
                      data-author-code={c.author_code}
                      title={`${c.author_handle} - author ${c.author_code}`}
                    >
                      {c.author_handle}
                    </span>
                    {/* The code goes visible only when another author in
                        this thread posts under the same name. Always
                        showing it would put a code on every line of a
                        thread that is usually one or two people; never
                        showing it would leave a spoofed name reading as
                        authoritative. */}
                    {ambiguous.has(c.author_code) ? (
                      <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                        {c.author_code}
                      </span>
                    ) : null}
                    {canModerate ? (
                      <button
                        type="button"
                        aria-label={`Author detail for ${c.author_handle}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          void openAuthorDetail(c.author_code);
                        }}
                        className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted transition-colors hover:text-ink"
                      >
                        {c.author_code}
                      </button>
                    ) : null}
```

Note the author line currently sits inside the seek `<button>`. A nested `<button>` is invalid HTML and React will warn. Restructure the row so the author line is a sibling of the seek button rather than a child of it, keeping the seek affordance on the body and the anchor chip. Verify no console warning appears in the test output.

Render the detail panel below the author line when `openAuthor === c.author_code`:

```tsx
                    {openAuthor === c.author_code ? (
                      <span className="mt-1 block rounded border border-rule bg-surface-2 p-2 text-[0.6875rem] text-muted">
                        {(() => {
                          const detail = authors?.find(
                            (a) => a.author_code === c.author_code,
                          );
                          if (!detail) return "Author detail unavailable.";
                          return `${detail.author_kind === "account" ? "Account" : "Pseudonym"} - ${detail.comment_count} comments since ${new Date(detail.first_comment_at).toISOString().slice(0, 10)} - posted as ${detail.handles.join(", ")}`;
                        })()}
                      </span>
                    ) : null}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd src/splitsmith/ui_static && pnpm exec vitest run src/components/comments/CommentPanel.test.tsx
```

Expected: all pass, including the file's pre-existing tests, with no nested-button warning.

- [ ] **Step 5: Read the rendered output**

Start the dev server and open a stage with two comments whose authors share a name. Confirm with your own eyes that the code renders visibly on both and that it is absent when names differ. A passing assertion is not proof the user sees it -- #617 shipped a fix whose text reached the cell and was then ellipsized away.

```bash
cd src/splitsmith/ui_static && pnpm dev
```

- [ ] **Step 6: Commit**

```bash
cd src/splitsmith/ui_static && pnpm typecheck && pnpm lint
git add src/splitsmith/ui_static/src/components/comments
git commit -m "feat(ui): surface an author code when two commenters share a name (#867)"
```

---

## Task 11: Desktop tokens become a page section

**Files:**
- Create: `src/splitsmith/ui_static/src/components/account/DesktopTokensSection.tsx`
- Create: `src/splitsmith/ui_static/src/components/account/DesktopTokensSection.test.tsx`
- Delete: `src/splitsmith/ui_static/src/components/account/DesktopTokensDialog.tsx`, `DesktopTokensDialog.test.tsx`
- Modify: `src/splitsmith/ui_static/src/components/match/SyncCard.tsx`, `SyncSettingsDialog.tsx`, `account/DeviceLoginDialog.tsx` (comment references only)

**Interfaces:**
- Consumes: `api.listDesktopTokens`, `api.createDesktopToken`, `api.revokeDesktopToken` (unchanged).
- Produces: `<DesktopTokensSection />` -- no props.

- [ ] **Step 1: Create the section**

Copy `DesktopTokensDialog.tsx` to `DesktopTokensSection.tsx` and make exactly these changes:

1. Rename the component to `DesktopTokensSection` and drop the `DesktopTokensDialogProps` interface and the `onClose` prop.
2. Remove the `Portal` wrapper, the `role="dialog"` / `aria-modal` / `aria-labelledby` / `aria-describedby` div, the `onClick={onClose}` backdrop, the `onClick={(e) => e.stopPropagation()}` guard, `panelRef`, and the `useDialogFocus` call.
3. Remove the closing `<div>` footer containing the Close button.
4. Keep the `Card` / `CardHeader` / `CardTitle` / `CardContent` structure as the section's own chrome, dropping `max-h-[90vh]`, `w-full max-w-lg`, `shadow-xl`, `outline-none`, and `tabIndex={-1}` from the Card.
5. Change `CardTitle id="desktop-tokens-title"` and `CardDescription id="desktop-tokens-desc"` to keep the ids -- they are now a plain heading and description, but the ids cost nothing and any test targeting them keeps working.
6. **Keep every accessibility property that was not dialog-specific**: the `aria-live="polite"` container around the one-time reveal, the "you will not see this again" text warning, the `Copied` label swap, `aria-label` on the copy button, `role="alert"` on the create error, and the explicit `Revoked` text label.
7. Replace the file's header comment. It should say what the section is, note that it moved from a dialog in #867, and keep the accessibility paragraph -- with the sentence about the modal skeleton removed, since there is no longer a modal.

- [ ] **Step 2: Migrate the tests**

Copy `DesktopTokensDialog.test.tsx` to `DesktopTokensSection.test.tsx`. Change the import and every `render(<DesktopTokensDialog onClose={...} />)` to `render(<DesktopTokensSection />)`. Delete any test that asserts dialog-specific behaviour (focus trap, Escape closes, backdrop click closes, the Close button). Keep every test of token listing, creation, the one-time reveal, copy, and revoke.

Add one test that pins what must not be lost in the move:

```tsx
it("keeps the one-time reveal in a live region", async () => {
  vi.mocked(api.listDesktopTokens).mockResolvedValue({ tokens: [] });
  vi.mocked(api.createDesktopToken).mockResolvedValue({
    record: { id: "t1", name: "workshop-mac", created_at: "2026-08-13T10:00:00Z", last_used_at: null, revoked_at: null },
    token: "raw-token-value",
  });
  render(<DesktopTokensSection />);

  fireEvent.change(await screen.findByLabelText("Name"), {
    target: { value: "workshop-mac" },
  });
  fireEvent.click(screen.getByRole("button", { name: /create token/i }));

  const field = await screen.findByLabelText("New desktop token");
  expect(field).toHaveValue("raw-token-value");
  expect(field.closest("[aria-live='polite']")).not.toBeNull();
  expect(screen.getByText(/you will not see this again/i)).toBeInTheDocument();
});
```

Adjust the mocked response shapes to match what the existing test file uses -- copy them from there rather than from the block above if they differ.

- [ ] **Step 3: Delete the dialog**

```bash
git rm src/splitsmith/ui_static/src/components/account/DesktopTokensDialog.tsx \
       src/splitsmith/ui_static/src/components/account/DesktopTokensDialog.test.tsx
```

- [ ] **Step 4: Fix the dangling comment references**

Three files cite `DesktopTokensDialog` as a modal-skeleton precedent in comments. Point each at `SyncSettingsDialog` instead, which is still a dialog:

- `src/splitsmith/ui_static/src/components/match/SyncCard.tsx` line ~82
- `src/splitsmith/ui_static/src/components/match/SyncSettingsDialog.tsx` line ~32 (this one cites itself as the skeleton -- change it to reference `ShareDialog`)
- `src/splitsmith/ui_static/src/components/account/DeviceLoginDialog.tsx` line ~11

```bash
grep -rn "DesktopTokensDialog" src/splitsmith/ui_static/src
```

Expected after the edits: no matches.

- [ ] **Step 5: Run the tests**

```bash
cd src/splitsmith/ui_static && pnpm exec vitest run src/components/account
```

Expected: `AccountChip` tests now fail (it still imports the deleted dialog). That is expected and Task 12 fixes it -- but to keep this task's tree green, do the `AccountChip` edit here as part of Step 6.

- [ ] **Step 6: Point AccountChip at the page**

In `src/splitsmith/ui_static/src/components/AccountChip.tsx`:

- Remove the `DesktopTokensDialog` import, the `tokensOpen` state, the `KeyRound` icon button, and the trailing `{tokensOpen ? <DesktopTokensDialog .../> : null}`.
- Add `UserCog` to the `lucide-react` import and drop `KeyRound`.
- Add a `Link` in the key icon's place, shaped exactly like the existing admin link:

```tsx
      <Link
        to="/account"
        aria-label="Account"
        title="Account"
        className={iconButtonVariants({
          variant: "subtle",
          size: "sm",
          className: "shrink-0",
        })}
      >
        <UserCog className="size-3.5" />
      </Link>
```

- Update the file's header comment: it currently says the chip "carries the entry point to DesktopTokensDialog (#631 Task 10) - desktop token management is an account-level concern, same tier as sign-out". Rewrite that paragraph to say the chip links to `/account`, which owns display name and desktop tokens, and note that the control count is unchanged so the phone-width reasoning below it still holds.

- [ ] **Step 7: Update the AccountChip tests**

In `AccountChip.test.tsx` and `AccountChip.mobile.test.tsx`, replace any assertion about the desktop-tokens button opening a dialog with:

```tsx
it("links to the account page", () => {
  render(<AccountChip />, { wrapper: MemoryRouter });
  expect(screen.getByRole("link", { name: "Account" })).toHaveAttribute(
    "href",
    "/account",
  );
});
```

Use whatever router wrapper the file already uses for the existing admin-link assertion.

- [ ] **Step 8: Run the tests**

```bash
cd src/splitsmith/ui_static && pnpm typecheck && pnpm test
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add -A src/splitsmith/ui_static/src
git commit -m "refactor(ui): desktop tokens move from a dialog to the account page (#867)"
```

---

## Task 12: The `/account` page

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/Account.tsx`
- Create: `src/splitsmith/ui_static/src/pages/Account.test.tsx`
- Modify: `src/splitsmith/ui_static/src/App.tsx`

**Interfaces:**
- Consumes: `api.updateMe` (Task 8), `DesktopTokensSection` (Task 11), `useAuth`, `useDeploymentMode`.
- Produces: `<Account />` at route `account`.

- [ ] **Step 1: Write the failing test**

Create `src/splitsmith/ui_static/src/pages/Account.test.tsx`:

```tsx
/**
 * The /account page (#867) - the surface that makes users.display_name
 * writable, which is what makes #866's account attribution reachable.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockRefresh = vi.fn();
let mockUser: { id: string; email: string; display_name: string | null; is_admin: boolean } | null = {
  id: "u1",
  email: "m@thias.se",
  display_name: null,
  is_admin: false,
};
let mockMode: "local" | "hosted" = "hosted";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ status: "authed", user: mockUser, refresh: mockRefresh, logout: vi.fn() }),
}));

vi.mock("@/lib/features", () => ({
  useDeploymentMode: () => ({ mode: mockMode, resolved: true }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      updateMe: vi.fn(),
      listDesktopTokens: vi.fn().mockResolvedValue({ tokens: [] }),
      createDesktopToken: vi.fn(),
      revokeDesktopToken: vi.fn(),
    },
  };
});

import { api } from "@/lib/api";
import { Account } from "@/pages/Account";

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/account"]}>
      <Account />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockMode = "hosted";
  mockUser = { id: "u1", email: "m@thias.se", display_name: null, is_admin: false };
});

it("shows the account email read-only", () => {
  renderPage();
  expect(screen.getByText("m@thias.se")).toBeInTheDocument();
});

it("prefills the field with the current display name", () => {
  mockUser = { ...mockUser!, display_name: "Anders Berg" };
  renderPage();
  expect(screen.getByLabelText(/display name/i)).toHaveValue("Anders Berg");
});

it("saves a display name and refreshes the session", async () => {
  vi.mocked(api.updateMe).mockResolvedValue({
    id: "u1",
    email: "m@thias.se",
    display_name: "Anders Berg",
    is_admin: false,
  });
  renderPage();

  fireEvent.change(screen.getByLabelText(/display name/i), {
    target: { value: "Anders Berg" },
  });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));

  await waitFor(() => expect(api.updateMe).toHaveBeenCalledWith("Anders Berg"));
  await waitFor(() => expect(mockRefresh).toHaveBeenCalled());
});

it("sends null when the field is cleared", async () => {
  mockUser = { ...mockUser!, display_name: "Anders Berg" };
  vi.mocked(api.updateMe).mockResolvedValue({
    id: "u1",
    email: "m@thias.se",
    display_name: null,
    is_admin: false,
  });
  renderPage();

  fireEvent.change(screen.getByLabelText(/display name/i), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));

  await waitFor(() => expect(api.updateMe).toHaveBeenCalledWith(null));
});

it("surfaces a server rejection inline", async () => {
  vi.mocked(api.updateMe).mockRejectedValue(new Error("too long"));
  renderPage();

  fireEvent.change(screen.getByLabelText(/display name/i), {
    target: { value: "x".repeat(61) },
  });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));

  expect(await screen.findByRole("alert")).toBeInTheDocument();
});

it("explains what a display name is used for", () => {
  renderPage();
  expect(screen.getByText(/comment/i)).toBeInTheDocument();
});

it("renders the desktop-token section", async () => {
  renderPage();
  expect(await screen.findByText(/desktop sync tokens/i)).toBeInTheDocument();
});

it("redirects to the picker in local mode", () => {
  mockMode = "local";
  const { container } = renderPage();
  expect(container.querySelector("input")).toBeNull();
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd src/splitsmith/ui_static && pnpm exec vitest run src/pages/Account.test.tsx
```

Expected: `Failed to resolve import "@/pages/Account"`.

- [ ] **Step 3: Write the page**

Create `src/splitsmith/ui_static/src/pages/Account.tsx`:

```tsx
/**
 * Account settings (#867).
 *
 * Two things live here, both account-level rather than match-scoped:
 * the display name and desktop sync tokens. Tokens moved off the
 * account chip in #867 - the chip now links here instead of opening a
 * dialog, which keeps its control count (and its phone-width budget)
 * unchanged.
 *
 * The display name is the reason this page exists. Before it, nothing
 * in the codebase wrote `users.display_name`, so a signed-in visitor
 * commenting on a share link always fell through to a generated
 * pseudonym and #866's account-attribution branch was unreachable.
 *
 * Hosted-only: local mode has no account, and PATCH /api/me 404s there.
 * Redirecting rather than rendering a notice because the only way to
 * land here in local mode is by typing the URL - the chip that links
 * here does not render outside hosted mode.
 */
import { useState } from "react";
import { Navigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DesktopTokensSection } from "@/components/account/DesktopTokensSection";
import { api, apiErrorText } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useDeploymentMode } from "@/lib/features";

const SAVE_FAILED_FALLBACK = "Could not save the display name - check the connection and retry.";

export function Account() {
  const { mode, resolved } = useDeploymentMode();
  const { user, refresh } = useAuth();
  const [name, setName] = useState(user?.display_name ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (resolved && mode === "local") return <Navigate to="/pick" replace />;

  async function onSave() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      // Empty means "no name": the server normalizes blank to null so an
      // account without a name publishes a generated handle rather than
      // an empty author. Sending null explicitly rather than "" keeps
      // the client honest about which of the two it means.
      await api.updateMe(name.trim() === "" ? null : name);
      // The account chip renders display_name ?? email, so the session
      // has to be re-read or the bar keeps showing the old label.
      await refresh();
      setSaved(true);
    } catch (e) {
      setError(apiErrorText(e, SAVE_FAILED_FALLBACK));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 p-6">
      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
          <CardDescription>{user?.email}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="flex flex-col gap-1">
            <label
              htmlFor="account-display-name"
              className="font-mono text-xs uppercase tracking-[0.08em] text-muted"
            >
              Display name
            </label>
            <input
              id="account-display-name"
              type="text"
              value={name}
              maxLength={60}
              onChange={(e) => setName(e.target.value)}
              disabled={saving}
              placeholder="Leave blank for a generated name"
              className="rounded border border-rule bg-bg px-3 py-1.5 text-sm disabled:opacity-50"
            />
            <p className="text-xs text-muted">
              The name shown on comments you post on other people's shared
              stages. Leave it blank and your comments get a generated name
              instead - splitsmith never publishes your email address.
            </p>
          </div>
          {error ? (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          ) : null}
          <div className="flex items-center gap-2">
            <Button type="button" size="sm" onClick={() => void onSave()} disabled={saving}>
              {saving ? "Saving..." : "Save"}
            </Button>
            {saved ? <span className="text-xs text-muted">Saved</span> : null}
          </div>
        </CardContent>
      </Card>

      <DesktopTokensSection />
    </div>
  );
}
```

The `maxLength={60}` mirrors the server cap for immediate feedback. The server is still the authority -- it normalizes first and a 422 renders through the `role="alert"` paragraph.

- [ ] **Step 4: Add the route**

In `src/splitsmith/ui_static/src/App.tsx`:

Add the import next to `AdminWorkers`:

```ts
import { Account } from "@/pages/Account";
```

Add the route next to `admin/workers`, under `RootLayout`:

```tsx
          {/* Account settings (#867). Under RootLayout for the same
              reason the admin surfaces are: server-wide, not
              project-scoped. Hosted-only - the page itself redirects to
              /pick in local mode. */}
          <Route path="account" element={<Account />} />
```

- [ ] **Step 5: Run the tests**

```bash
cd src/splitsmith/ui_static && pnpm exec vitest run src/pages/Account.test.tsx
```

Expected: all pass.

- [ ] **Step 6: Mutation check**

Temporarily change `name.trim() === "" ? null : name` to just `name`. Re-run and confirm `it("sends null when the field is cleared")` fails. Restore.

- [ ] **Step 7: Look at the page**

```bash
cd src/splitsmith/ui_static && pnpm dev
```

Open `/account` in hosted mode, set a name, and confirm the account chip in the global bar switches from the email to the name without a reload. Then check the page at a 390px viewport -- the field, the help text, and the token list must all fit without horizontal scroll.

- [ ] **Step 8: Full frontend suite, then commit**

```bash
cd src/splitsmith/ui_static && pnpm typecheck && pnpm lint && pnpm test
git add src/splitsmith/ui_static/src
git commit -m "feat(ui): an account page that owns the display name (#867)"
```

---

## Task 13: Documentation and final verification

**Files:**
- Modify: `SPEC.md`, `CLAUDE.md`

- [ ] **Step 1: Document the display name in SPEC.md**

Find the section covering accounts / hosted identity and add:

```markdown
### Account display name

`users.display_name` is set through `PATCH /api/me` from the `/account`
page, and is the name published on comments the account posts on other
people's shared stages. It is optional: an account with no name comments
under a server-derived pseudonym instead, and splitsmith never publishes
an account's email address.

Validation lives in `splitsmith.display_name.normalize_display_name`
(NFC, whitespace-collapsed, no control characters, 60 characters). A
blank name normalizes to `NULL`, never `""` -- the comment attribution
branch falls back on a blank name, and an empty string would publish an
empty author.
```

- [ ] **Step 2: Document author codes in SPEC.md**

In the comments section:

```markdown
### Author codes

Every comment carries an `author_code`: six Crockford-base32 characters
derived by HMAC from the author's account id (signed-in) or hashed
browser key (pseudonymous), never from the raw identifier. It is
denormalized onto the row at write time for the same reason
`author_handle` is -- rotating the handle secret must not re-identify
historical authors.

It exists because a display name is self-chosen: an account can set
theirs to another commenter's, including a generated pseudonym. The code
is always in the DOM and in a tooltip; it renders visibly only when two
distinct codes in one thread normalize to the same name
(`lib/authorAmbiguity.ts`). The owner's view can expand a code to see
every name it has posted under, which is the signal a rename leaves.

That aggregate is match-scoped and owner-only (`GET
/api/match/comment-authors`, absent from `_SHARE_PATH_RE`). Aggregating
an author across matches would reveal that they commented on other
people's share links.
```

- [ ] **Step 3: Update CLAUDE.md**

In the comments/share section, add:

```markdown
A signed-in visitor comments under `users.display_name`, which the
`/account` page writes through `PATCH /api/me` (#867). Nothing else in
the codebase writes that column -- `splitsmith.db.profile` is the single
owner, which is what makes "can this branch be reached?" answerable by
grep. #866 shipped the branch with no writer and it was dead in
production for exactly that reason. An account with a blank name still
falls back to a generated handle; that invariant is pinned in
`tests/test_comments_signed_in.py` and does not move.
```

- [ ] **Step 4: Run the full backend suite**

```bash
uv run pytest -q
```

Expected: green. This runs in parallel -- no `-n0`.

- [ ] **Step 5: Run the full frontend suite**

```bash
cd src/splitsmith/ui_static && pnpm typecheck && pnpm lint && pnpm test
```

Expected: green.

- [ ] **Step 6: Verify the migration applies to a real Postgres**

```bash
uv run pytest -m docker -n0 -q
```

Expected: green, or a clean skip if docker is unavailable. If it skips locally, say so explicitly when reporting -- CI runs it and a migration that only ever ran against SQLite is not verified.

- [ ] **Step 7: Confirm the issue is actually closed**

Re-read #867's "The gap" paragraph. Confirm by running, not by reading:

```bash
uv run pytest tests/test_comments_signed_in.py::test_a_signed_in_visitor_can_set_a_name_and_comment_under_it -n0 -v
```

Expected: PASS, having driven `PATCH /api/me` rather than writing the column.

- [ ] **Step 8: Commit**

```bash
uv run black src tests
git add -A
git commit -m "docs: account display name and comment author codes (#867)"
```

---

## Self-Review Notes

Checked against the spec, section by section:

| Spec section | Task |
|---|---|
| 1. Storage and validation | Task 1 |
| 2. API (`PATCH /api/me`, hosted-only, sync-token refusal, `PostgresProfileStore`) | Tasks 2, 3 |
| 3. `/account` page, desktop-token move, `AccountChip` link | Tasks 11, 12 |
| 4. Author codes: derivation, storage, wire format | Tasks 4, 5 |
| 5. Rendering: surface on collision | Tasks 9, 10 |
| 6. Owner-only author detail | Tasks 7, 10 |
| 7. Known limitation (`HostedAccountInfo` staleness) | Task 13 -- see below |
| 8. Testing (end-to-end reachability) | Task 6 |
| 9. Out of scope | Not built |

**One spec item needs an explicit home.** Spec section 7 says the `HostedAccountInfo` cache staleness "gets a comment where the cache is populated, not a fix". Add that comment during Task 13 Step 3, in `src/splitsmith/ui/device_auth_api.py` where `display_name=result.account.display_name` is written into the cached record:

```python
# Snapshot, not a live read: a display name set later through
# PATCH /api/me (#867) does not reach the desktop chip until the
# device is linked again. A sync-scoped token cannot read /api/me,
# so refreshing this would need a new route - out of scope.
```

**Naming consistency verified across tasks:** `normalize_display_name`, `MAX_DISPLAY_NAME_LEN`, `PostgresProfileStore.set_display_name`, `derive_author_code`, `author_code_for`, `AUTHOR_CODE_LEN`, `CommentAuthorSummary.handles`, `CommentAuthorOut.handles`, `api.updateMe`, `api.listCommentAuthors`, `normalizeAuthorName`, `ambiguousCodes`, `DesktopTokensSection`, `Account`.
