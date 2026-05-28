"""Guards for the procrastinate-schema migration's SQL splitter.

Regression coverage for the asyncpg "cannot insert multiple commands
into a prepared statement" failure: Procrastinate ships its schema as
one ~40-statement script, and ``op.execute`` of the whole blob blows
up under asyncpg (our production driver). The migration splits the
script into individual statements; these tests pin the splitter so a
future edit can't silently reintroduce the multi-command execute or
break dollar-quote handling.

The split-correctness logic is driver-agnostic and needs no database,
so it runs in the normal (non-docker) CI lane -- which is the whole
point: the bug shipped because the only Postgres coverage was the
``-m docker`` smoke that CI skips. A real end-to-end apply is covered
by ``scripts/smoke_hosted.sh`` / ``pytest -m docker``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "ba72882f8c1c_apply_procrastinate_schema.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_proc_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_comment_only(stmt: str) -> bool:
    return all((not ln.strip()) or ln.strip().startswith("--") for ln in stmt.splitlines())


def test_splits_plain_statements() -> None:
    split = _load_migration()._split_sql_statements
    out = split("CREATE TABLE a (id int); CREATE TABLE b (id int);")
    assert out == ["CREATE TABLE a (id int)", "CREATE TABLE b (id int)"]


def test_dollar_quoted_body_semicolons_do_not_split() -> None:
    """A PL/pgSQL function body holds its own ``;`` -- the split must
    treat the whole ``$$ ... $$`` block as one statement."""
    split = _load_migration()._split_sql_statements
    sql = (
        "CREATE FUNCTION f() RETURNS void AS $$\n"
        "BEGIN\n"
        "  PERFORM 1;\n"
        "  PERFORM 2;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
        "CREATE TABLE t (id int);"
    )
    out = split(sql)
    assert len(out) == 2
    assert out[0].startswith("CREATE FUNCTION f()")
    assert "PERFORM 1;" in out[0] and "PERFORM 2;" in out[0]
    assert out[1] == "CREATE TABLE t (id int)"


def test_tagged_dollar_quote() -> None:
    split = _load_migration()._split_sql_statements
    sql = (
        "CREATE FUNCTION g() RETURNS void AS $body$ BEGIN PERFORM 1; END; $body$ "
        "LANGUAGE plpgsql; SELECT 1;"
    )
    out = split(sql)
    assert len(out) == 2
    assert "$body$" in out[0] and "PERFORM 1;" in out[0]
    assert out[1] == "SELECT 1"


def test_semicolon_in_line_comment_does_not_split() -> None:
    split = _load_migration()._split_sql_statements
    sql = "CREATE TABLE a (id int); -- trailing; comment with; semicolons\nCREATE TABLE b (id int);"
    out = split(sql)
    assert len(out) == 2
    assert out[0] == "CREATE TABLE a (id int)"
    assert out[1].endswith("CREATE TABLE b (id int)")


def test_trailing_statement_without_semicolon_kept() -> None:
    split = _load_migration()._split_sql_statements
    assert split("SELECT 1; SELECT 2") == ["SELECT 1", "SELECT 2"]


def test_real_procrastinate_schema_splits_cleanly() -> None:
    """The actual shipped schema must split into many individual
    statements, none empty, none comment-only, with balanced ``$$`` in
    every function body. This is the exact blob that broke on asyncpg."""
    pytest.importorskip("procrastinate")
    from procrastinate.schema import SchemaManager

    split = _load_migration()._split_sql_statements
    out = split(SchemaManager.get_schema())

    assert len(out) > 1, "schema must split into multiple statements"
    assert all(s.strip() for s in out), "no empty statements"
    assert not any(_is_comment_only(s) for s in out), "no comment-only statements (asyncpg rejects them)"

    fns = [s for s in out if "FUNCTION" in s.upper()]
    assert fns, "expected function definitions in the schema"
    for fn in fns:
        assert fn.count("$$") % 2 == 0, f"unbalanced $$ in function body: {fn[:60]!r}"
