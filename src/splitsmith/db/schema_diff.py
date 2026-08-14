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
