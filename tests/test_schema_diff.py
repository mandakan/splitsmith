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
    # carry the prefix -- the table they hang off does. This pins the
    # parent-lookup clause as defensive rather than as a regression
    # guard: on the current schema Alembic never calls include_object
    # for an index/constraint whose parent table it hasn't already
    # rejected at the table level (ablating the clause and re-running
    # compare_metadata against the live schema still yields 0 diffs).
    # Kept in case Alembic's traversal order ever changes.
    index = SimpleNamespace(table=SimpleNamespace(name="procrastinate_jobs"))
    assert include_object(index, "idx_procrastinate_jobs_queue", "index", True, None) is False


def test_an_index_on_our_own_table_is_compared() -> None:
    index = SimpleNamespace(table=SimpleNamespace(name="match_comments"))
    assert include_object(index, "ix_match_comments_thread", "index", True, None) is True


def test_an_unnamed_object_is_compared() -> None:
    # Alembic passes ``name=None`` for some reflected constructs. The
    # filter must fall open there: ignoring an object we cannot name
    # would silently shrink the diff.
    assert include_object(None, None, "table", True, None) is True
