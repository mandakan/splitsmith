# Migration CI Gate (#876) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CI fail on a migration that does not apply to Postgres, does not agree with the SQLAlchemy models, does not reverse, or leaves the revision chain with two heads -- so the production deploy stops being the first process that ever runs `alembic upgrade head`.

**Architecture:** One always-on CI job with a `postgres:16-alpine` service runs a single standalone script. The script asserts single-head, applies the chain, diffs the resulting schema against `Base.metadata` with `alembic.autogenerate.compare_metadata`, then downgrades to base and re-applies. The object filter that keeps procrastinate's raw-SQL schema out of that diff lives in one shared module and is wired into `alembic/env.py` as well, so the developer's `alembic revision --autogenerate` and the CI gate cannot disagree.

**Tech Stack:** Alembic 1.13+, SQLAlchemy 2.x async + asyncpg, GitHub Actions service containers, uv, pytest.

## Global Constraints

- Python 3.11+, type hints everywhere. `pathlib.Path` for paths, never strings.
- Black formatting, line length 110. Ruff for linting. Both run in CI over `src tests scripts`.
- `uv` for dependency management, never `pip`. **No new dependencies** -- everything this plan needs (`alembic`, `sqlalchemy`, `asyncpg`, `procrastinate`) is already in the `hosted` extra.
- Imports: stdlib, third-party, local, separated by blank lines. No relative imports beyond a single dot.
- Branch: `ci/migration-gate-876`, cut from `main`. It already exists and carries the design doc commit.
- Conventional-commit subjects. Squash bodies stay short -- a many-commit body breaks release-please's parser.
- The repo's docker-compose deployment on this box owns the compose project name `splitsmith`. Any throwaway container this plan starts uses an unmistakably different name and a shifted port.

---

### Task 1: The shared autogenerate object filter

`compare_metadata` diffs the live schema against `Base.metadata`. Procrastinate's tables, sequences, types and functions are applied by migration `ba72882f8c1c` as raw SQL and are not in `Base.metadata`, so an unfiltered diff emits `drop_table` for every one of them.

This is not only a CI problem: running `alembic revision --autogenerate` on `main` today would generate a migration that drops the job queue. The filter fixes both, which is why it is shared rather than living inside the CI script.

Prefix matching is sound here, and the codebase already says so: `ba72882f8c1c`'s own `downgrade()` carries the comment "The schema only creates objects prefixed `procrastinate_*`".

**Files:**
- Create: `src/splitsmith/db/schema_diff.py`
- Modify: `alembic/env.py` (both `context.configure()` calls)
- Test: `tests/test_schema_diff.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `splitsmith.db.schema_diff.include_object(obj, name, type_, reflected, compare_to) -> bool`, the Alembic `include_object` hook. Task 2's CI script passes it into `MigrationContext.configure(opts={"include_object": include_object})`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_schema_diff.py`:

```python
"""The autogenerate filter: what a schema diff is allowed to ignore (#876).

The filter is the difference between a working migration gate and one
that false-fails on its first run, and between ``alembic revision
--autogenerate`` producing a useful migration and one that drops the job
queue. It is four lines of predicate, so these are the only thing
standing behind it -- run the mutation drill before trusting them:
delete a clause, watch the matching test go red, restore.
"""

from __future__ import annotations

from types import SimpleNamespace

from splitsmith.db.schema_diff import include_object


def test_procrastinate_tables_are_ignored() -> None:
    assert include_object(None, "procrastinate_jobs", "table", True, None) is False
    assert include_object(None, "procrastinate_events", "table", True, None) is False


def test_alembic_version_is_ignored() -> None:
    assert include_object(None, "alembic_version", "table", True, None) is False


def test_our_own_tables_are_compared() -> None:
    for name in ("users", "match_comments", "matches", "state_docs", "desktop_tokens"):
        assert include_object(None, name, "table", True, None) is True


def test_an_index_on_an_ignored_table_is_ignored_with_it() -> None:
    # Indexes and constraints arrive with their own name, which does not
    # carry the prefix -- the table they hang off does. Without the
    # parent lookup, every procrastinate index reads as a diff.
    index = SimpleNamespace(table=SimpleNamespace(name="procrastinate_jobs"))
    assert include_object(index, "idx_procrastinate_jobs_queue", "index", True, None) is False


def test_an_index_on_our_own_table_is_compared() -> None:
    index = SimpleNamespace(table=SimpleNamespace(name="match_comments"))
    assert include_object(index, "ix_match_comments_match_id", "index", True, None) is True


def test_an_unnamed_object_is_compared() -> None:
    # Alembic passes ``name=None`` for some reflected constructs. The
    # filter must fall open there: ignoring an object we cannot name
    # would silently shrink the diff.
    assert include_object(None, None, "table", True, None) is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_schema_diff.py -n0 -q`
Expected: FAIL, collection error -- `ModuleNotFoundError: No module named 'splitsmith.db.schema_diff'`.

- [ ] **Step 3: Write the filter**

Create `src/splitsmith/db/schema_diff.py`:

```python
"""What a schema diff ignores, shared by ``env.py`` and the CI gate (#876).

``alembic.autogenerate`` compares the live database against
``Base.metadata``. Anything in the database that the models do not
declare reads as "drop this" -- which is correct for a table someone
added by hand, and wrong for the procrastinate schema, which migration
``ba72882f8c1c`` applies as raw SQL on purpose (procrastinate ships its
own DDL; re-declaring it as SQLAlchemy models would fork it from
whatever version ``uv.lock`` resolves).

So both consumers filter, and they filter through this one function.
Two implementations would be worse than none: the CI gate would go green
on a diff the developer's ``alembic revision --autogenerate`` still
emits, or the reverse.

Nothing else is excluded. A diff on a table splitsmith owns is a
finding, not noise -- the fix for one is a migration, never a wider
filter here.
"""

from __future__ import annotations

from typing import Any

#: Object-name prefixes applied by raw-SQL migrations rather than
#: declared on ``Base.metadata``. ``ba72882f8c1c``'s own ``downgrade()``
#: relies on the same fact ("the schema only creates objects prefixed
#: ``procrastinate_*``"), so a prefix match is exactly as precise as the
#: teardown that already ships.
IGNORED_PREFIXES: tuple[str, ...] = ("procrastinate_",)

#: Alembic's own bookkeeping table. Never in anyone's metadata.
IGNORED_NAMES: frozenset[str] = frozenset({"alembic_version"})


def _is_ignored(name: str | None) -> bool:
    if name is None:
        return False
    return name in IGNORED_NAMES or name.startswith(IGNORED_PREFIXES)


def include_object(
    obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Alembic ``include_object`` hook: True to compare, False to ignore.

    Signature is Alembic's, not ours -- see its autogenerate docs. Falls
    open on anything it cannot name: silently shrinking the diff is the
    failure mode this gate exists to prevent.
    """
    if _is_ignored(name):
        return False
    # Indexes, constraints and columns carry their own name; the prefix
    # lives on the table they belong to. Without this, every
    # procrastinate index reads as a diff.
    parent = getattr(obj, "table", None)
    if _is_ignored(getattr(parent, "name", None)):
        return False
    return True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_schema_diff.py -n0 -q`
Expected: PASS, 6 passed.

- [ ] **Step 5: Run the mutation drill**

Prove each clause can fail. For each of the three, make the edit, run the test file, confirm the named test goes red, then revert:

1. Delete `name in IGNORED_NAMES` from `_is_ignored` -> `test_alembic_version_is_ignored` fails.
2. Delete `name.startswith(IGNORED_PREFIXES)` -> `test_procrastinate_tables_are_ignored` fails.
3. Delete the `parent` lookup block from `include_object` -> `test_an_index_on_an_ignored_table_is_ignored_with_it` fails.

A clause no test can kill is a clause with no coverage. Record in the commit message that the drill ran.

- [ ] **Step 6: Wire the filter into `alembic/env.py`**

Add the import next to the existing `Base` import:

```python
from splitsmith.db.models import Base  # noqa: E402
from splitsmith.db.schema_diff import include_object  # noqa: E402
```

Then pass it to **both** configure calls. In `run_migrations_offline()`:

```python
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
```

And in `do_run_migrations()`:

```python
def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()
```

Both, not just the online one: `alembic revision --autogenerate --sql` and offline runs go through the other, and a filter that applies to one of two paths is the kind of half-fix that reads as done.

- [ ] **Step 7: Verify the filter reaches autogenerate**

This is the live-bug half of the task, so prove it rather than assume it. Against a throwaway Postgres (see Task 2 Step 3 for how to start one), with the chain applied:

Run: `SPLITSMITH_DATABASE_URL=postgresql+asyncpg://splitsmith:splitsmith@localhost:55432/splitsmith_ci uv run alembic revision --autogenerate -m "scratch" --rev-id zzzz_scratch`

Expected: the generated file's `upgrade()` contains **no** `op.drop_table("procrastinate_...")` lines. Before Step 6 it would have contained one per procrastinate table.

Delete the generated revision file afterwards -- it is a probe, not a migration.

- [ ] **Step 8: Lint, format, commit**

```bash
uv run ruff check src tests scripts
uv run black --check src tests scripts
git add src/splitsmith/db/schema_diff.py tests/test_schema_diff.py alembic/env.py
git commit -m "fix(db): keep procrastinate's raw-SQL schema out of autogenerate diffs

alembic revision --autogenerate would have emitted drop_table for every
procrastinate object, since ba72882f8c1c applies them as raw SQL and
they are not on Base.metadata. Shared with the #876 CI gate so the
developer command and the gate cannot disagree."
```

---

### Task 2: The check script

**Files:**
- Create: `scripts/ci/assert_migrations_match_models.py`

**Interfaces:**
- Consumes: `splitsmith.db.schema_diff.include_object` from Task 1; `splitsmith.db.models.Base`; `SPLITSMITH_DATABASE_URL` from the environment (which `alembic/env.py` already honours as a URL override, so no CI-specific ini file is needed).
- Produces: a script Task 3's workflow step invokes as `uv run python scripts/ci/assert_migrations_match_models.py`. Exit 0 on success, 1 on a failed assertion, 2 on a usage error.

- [ ] **Step 1: Write the script**

Create `scripts/ci/assert_migrations_match_models.py`:

```python
"""Migrations must apply to Postgres, agree with the models, and reverse.

Nothing in CI ran a migration before this (#876). ``deploy-app.yml`` only
notes in a comment that ``serve`` runs ``alembic upgrade head`` on boot,
so the first process that ever applied a new revision was the production
deploy, and a broken one booted into a broken schema.

The default pytest suite does not close this and cannot: it is
SQLite-backed and builds its tables with ``Base.metadata.create_all``.
``tests/test_comments_schema.py`` passes whether or not a migration file
exists and whether or not it agrees with the models -- it tests the
models, which its filename does not say.

Four assertions, cheapest first, each naming what broke:

1. one head revision  -- two revisions claiming the same parent
2. ``upgrade head``   -- a migration that is fine on SQLite and not on PG
3. an empty diff      -- the models and the chain having drifted apart
4. ``downgrade base`` -- an unreversible revision

Run against a scratch database, never one with data: assertion 4 drops
every table.

    SPLITSMITH_DATABASE_URL=postgresql+asyncpg://... \\
        uv run python scripts/ci/assert_migrations_match_models.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _load_config() -> Any:
    from alembic.config import Config

    return Config(str(ALEMBIC_INI))


def check_single_head(cfg: Any) -> str | None:
    """Return an error message, or ``None`` when there is exactly one head."""
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) == 1:
        return None
    return (
        f"expected exactly one head revision, found {len(heads)}: "
        f"{', '.join(sorted(heads))}. Two revisions share a down_revision; "
        "`alembic upgrade head` fails on boot with a multiple-heads error."
    )


async def _diff_against_models(url: str) -> list[Any]:
    """Ops Alembic would generate to make the live schema match the models."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from splitsmith.db.models import Base
    from splitsmith.db.schema_diff import include_object

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(
                        sync_conn,
                        opts={"include_object": include_object},
                    ),
                    Base.metadata,
                )
            )
    finally:
        await engine.dispose()


def main() -> int:
    url = os.environ.get("SPLITSMITH_DATABASE_URL", "").strip()
    if not url:
        print(
            "SPLITSMITH_DATABASE_URL is unset. Point it at a SCRATCH database: "
            "this script downgrades to base, which drops every table.",
            file=sys.stderr,
        )
        return 2

    from alembic import command

    cfg = _load_config()

    print("[1/4] revision chain has a single head ...", flush=True)
    if (problem := check_single_head(cfg)) is not None:
        print(f"FAIL: {problem}", file=sys.stderr)
        return 1

    print("[2/4] alembic upgrade head ...", flush=True)
    command.upgrade(cfg, "head")

    print("[3/4] the resulting schema matches Base.metadata ...", flush=True)
    diffs = asyncio.run(_diff_against_models(url))
    if diffs:
        print(
            f"FAIL: the migration chain and the SQLAlchemy models disagree "
            f"({len(diffs)} difference(s)). Alembic would generate:",
            file=sys.stderr,
        )
        for op in diffs:
            print(f"  - {op}", file=sys.stderr)
        print(
            "\nFix by writing the migration that closes the gap "
            "(`alembic revision --autogenerate`), not by widening the filter "
            "in src/splitsmith/db/schema_diff.py.",
            file=sys.stderr,
        )
        return 1

    print("[4/4] downgrade base, then upgrade head again ...", flush=True)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    print("OK: migrations apply, match the models, and reverse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Lint and format it**

Run: `uv run ruff check scripts && uv run black --check scripts`
Expected: clean. Fix anything it reports before continuing.

- [ ] **Step 3: Start a throwaway Postgres**

A live docker-compose deployment on this box owns the compose project name `splitsmith`, and the docker test suite owns `splitsmith-test` on port 5433. Use neither.

```bash
docker run --rm -d --name splitsmith-migcheck \
  -e POSTGRES_USER=splitsmith \
  -e POSTGRES_PASSWORD=splitsmith \
  -e POSTGRES_DB=splitsmith_ci \
  -p 55432:5432 postgres:16-alpine
sleep 5
docker exec splitsmith-migcheck pg_isready -U splitsmith
```

Expected: `accepting connections`.

- [ ] **Step 4: Run the script for real**

```bash
SPLITSMITH_DATABASE_URL=postgresql+asyncpg://splitsmith:splitsmith@localhost:55432/splitsmith_ci \
  uv run python scripts/ci/assert_migrations_match_models.py
```

Expected: either `OK` on all four, or a `[3/4]` failure listing real divergences between the chain and the models.

**A `[3/4]` failure here is the expected outcome and is the point of the exercise, not a defect in the script.** Nobody has ever run this comparison. Likely reports: `server_default` spellings that a migration wrote differently from the model, and index or constraint names where the models rely on SQLAlchemy's default naming and a migration spelled one by hand.

Do not fix them in this task. Record the exact op list in the task notes and carry it to Task 4 -- Task 3 needs the gate wired up before it is worth deciding what each diff means.

If the failure is instead a flood of `drop_table` on `procrastinate_*`, Task 1 Step 6 did not take: the filter is not reaching `MigrationContext`.

- [ ] **Step 5: Prove assertions 1 and 4 can fail**

A gate nobody has watched go red is not a gate. Three green-CI release bugs in this repo shared one cause: a check that validated something other than the artifact it claimed to.

**Assertion 1 (single head):** edit the newest revision's `down_revision` to point at the same parent as its predecessor. Re-run. Expected: `FAIL: expected exactly one head revision, found 2: ...`. Revert.

**Assertion 4 (reverses):** in `alembic/versions/58603835d0bd_add_author_code_to_match_comments.py`, change `downgrade()`'s `op.drop_column("match_comments", "author_code")` to drop a column that does not exist. Re-run. Expected: a `[4/4]` failure with an `UndefinedColumn` error. Revert.

Assertion 3's drill is Task 3 Step 4, where a model change is the natural mutation.

- [ ] **Step 6: Tear the container down**

```bash
docker stop splitsmith-migcheck
```

- [ ] **Step 7: Commit**

```bash
git add scripts/ci/assert_migrations_match_models.py
git commit -m "ci: script that proves migrations apply, match the models, and reverse"
```

---

### Task 3: The CI job

**Files:**
- Modify: `.github/workflows/ci.yml` (new `migrations` job, after `test`)

**Interfaces:**
- Consumes: `scripts/ci/assert_migrations_match_models.py` from Task 2.
- Produces: nothing other tasks read.

- [ ] **Step 1: Confirm the dependency flag keeps torch out**

The job installs only what it needs. Verify which flag actually excludes the `dev` group, rather than trusting one:

```bash
uv sync --frozen --extra hosted --no-dev
uv run python -c "import importlib.util as u; print('torch present:', u.find_spec('torch') is not None)"
```

Expected: `torch present: False`. If it prints `True`, use `--no-default-groups` in place of `--no-dev` in Step 2 and everywhere below.

Afterwards restore the full dev environment for the rest of your work: `uv sync --frozen --all-groups`.

- [ ] **Step 2: Add the job**

In `.github/workflows/ci.yml`, add after the `test` job and before `spa`:

```yaml
  migrations:
    # Nothing in CI applied a migration, so the first process that ever
    # ran `alembic upgrade head` on a new revision was the production
    # deploy (#876). A model/migration divergence, a migration that is
    # fine on SQLite and not on Postgres, or two revisions claiming the
    # same parent all shipped with entirely green CI.
    #
    # The default suite cannot close this: it is SQLite-backed and builds
    # its tables with Base.metadata.create_all, so it tests the models,
    # not the chain. The only coverage that genuinely ran the chain
    # against Postgres was the docker suite, which addopts deselects and
    # CI never opted into.
    #
    # Always-on because it is cheap by construction: no models, no
    # ffmpeg, no Node, no dev group -- one Postgres service and the
    # hosted extra. Seconds, not the docker suite's four minutes, and it
    # reports a schema failure rather than a stack failure.
    name: migrations apply + match the models
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: splitsmith
          POSTGRES_PASSWORD: splitsmith
          POSTGRES_DB: splitsmith_ci
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U splitsmith"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10

    steps:
      - uses: actions/checkout@v5

      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true

      - name: Install Python 3.11
        run: uv python install 3.11

      - name: Sync hosted deps only
        # Deliberately NOT --all-groups: this job needs sqlalchemy,
        # alembic, asyncpg and procrastinate, and has no business
        # installing torch. It does not need pytest either -- the check
        # is a standalone script.
        run: uv sync --frozen --extra hosted --no-dev

      - name: Migrations apply, match the models, and reverse
        env:
          # alembic/env.py honours this over the ini's baked-in URL, so
          # no CI-specific alembic ini is needed.
          SPLITSMITH_DATABASE_URL: postgresql+asyncpg://splitsmith:splitsmith@localhost:5432/splitsmith_ci
        run: uv run python scripts/ci/assert_migrations_match_models.py
```

- [ ] **Step 3: Validate the workflow parses**

Run: `uv run python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text()); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Push and watch the job actually run**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: gate migrations against Postgres and the models"
git push -u origin ci/migration-gate-876
gh pr create --fill --title "ci: no workflow applies a migration -- gate it (#876)"
gh run watch
```

Expected: the `migrations` job appears and completes. Green means the chain is clean; a `[3/4]` failure is Task 4's input.

- [ ] **Step 5: Prove assertion 3 goes red in CI, not just locally**

The local drill in Task 2 covered assertions 1 and 4. This one covers the assertion the job exists for, and proves it fires in the real environment.

On a scratch commit pushed to the PR branch, add a column to a model with no migration -- in `src/splitsmith/db/models.py`, on the `User` class:

```python
    ci_drill_column: Mapped[str | None] = mapped_column(String, nullable=True)
```

Push, watch the `migrations` job fail at `[3/4]` naming `add_column` on `users`, then revert the line with a follow-up push and confirm green.

Record both run URLs in the PR body. That is the evidence the gate works; without it this is a workflow file nobody has seen do its job.

---

### Task 4: Close whatever the first real run surfaced

**Files:**
- Create (conditional): `alembic/versions/<new_rev>_*.py`
- Modify (conditional): `src/splitsmith/db/models.py`

**Interfaces:**
- Consumes: the op list captured in Task 2 Step 4 / Task 3 Step 4.
- Produces: a chain that produces an empty diff, which is what makes the gate meaningful rather than permanently red.

- [ ] **Step 1: Decide what each reported op means**

If Task 2 Step 4 and Task 3 Step 4 both reported an empty diff, this task is complete with no commit. Note that in the PR body and move on -- do not invent work.

Otherwise, for each op in the list, decide which side is wrong:

- **The database is right, the model is stale** (a migration added something the model never declared): fix `models.py`. No migration needed -- the deployed schema already has it.
- **The model is right, the chain is missing a step** (the model declares something no migration ever applied): write the migration. Production does not have this column or index, and the only reason nothing has broken is that no query touched it yet. This is the class of defect #876 exists to catch, so say so in the commit message.
- **Cosmetic reflection noise** (a `server_default` that reflects as `'0'::integer` against a model's `text("0")`, or an unnamed constraint): still one of the two above. Resolve it by making one side match the other. Do **not** widen `IGNORED_PREFIXES` -- that filter exists for objects no model will ever declare, and using it to silence a real table's diff would hollow out the gate on its first day.

- [ ] **Step 2: Generate the migration, if one is needed**

With the throwaway Postgres from Task 2 Step 3 running and the chain applied:

```bash
SPLITSMITH_DATABASE_URL=postgresql+asyncpg://splitsmith:splitsmith@localhost:55432/splitsmith_ci \
  uv run alembic revision --autogenerate -m "align <what> with the models"
```

Read the generated file before keeping it. Autogenerate drafts; it does not decide. Confirm `upgrade()` contains only the ops you intended, that `downgrade()` reverses them, and that `down_revision` points at the current head.

- [ ] **Step 3: Re-run the gate locally**

```bash
SPLITSMITH_DATABASE_URL=postgresql+asyncpg://splitsmith:splitsmith@localhost:55432/splitsmith_ci \
  uv run python scripts/ci/assert_migrations_match_models.py
```

Expected: `OK: migrations apply, match the models, and reverse.` Assertion 4 covers the new revision's `downgrade()` for free.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: green. A model change touches the SQLite-backed suite even though the migration does not.

- [ ] **Step 5: Commit and push**

```bash
docker stop splitsmith-migcheck
git add alembic/versions src/splitsmith/db/models.py
git commit -m "fix(db): align the migration chain with the models"
git push
gh run watch
```

Expected: the `migrations` job green.

---

## Done when

- `migrations` runs on every PR and every push to main.
- It has been observed failing on all three of: a stale model, a duplicated `down_revision`, and a broken `downgrade()`.
- It is green on `main`.
- `alembic revision --autogenerate` no longer proposes dropping the job queue.
