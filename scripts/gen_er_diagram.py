"""Regenerate the Mermaid ER diagram in docs/architecture/data-model.md.

Reads ``splitsmith.db.models.Base.metadata`` so the entity blocks are the
real columns, keys and foreign keys -- never typed by hand. Only the block
between the two marker comments is rewritten; the prose around it is kept.

    uv run --frozen python scripts/gen_er_diagram.py          # rewrite
    uv run --frozen python scripts/gen_er_diagram.py --check  # exit 1 if stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from splitsmith.db.models import Base

DOC = Path(__file__).resolve().parent.parent / "docs" / "architecture" / "data-model.md"
BEGIN = "<!-- BEGIN GENERATED: scripts/gen_er_diagram.py -->"
END = "<!-- END GENERATED -->"


def _column_line(table, column) -> str:
    kind = str(column.type).split("(")[0].lower().replace(" ", "_")
    keys: list[str] = []
    if column.primary_key:
        keys.append("PK")
    if column.foreign_keys:
        keys.append("FK")
    single_unique = column.unique or any(
        c.__class__.__name__ == "UniqueConstraint" and [x.name for x in c.columns] == [column.name]
        for c in table.constraints
    )
    if single_unique:
        keys.append("UK")
    suffix = f" {','.join(keys)}" if keys else ""
    return f"        {kind} {column.name}{suffix}"


def render() -> str:
    lines = ["```mermaid", "erDiagram"]
    relations: list[str] = []
    for table in Base.metadata.sorted_tables:
        lines.append(f"    {table.name} {{")
        lines.extend(_column_line(table, c) for c in table.columns)
        lines.append("    }")
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            card = "o|--o{" if fk.parent.nullable else "||--o{"
            relations.append(f'    {parent} {card} {table.name} : "{fk.parent.name}"')
    lines.extend(relations)
    lines.append("```")
    return "\n".join(lines) + "\n"


def splice(doc: str, block: str) -> str:
    head, _, rest = doc.partition(BEGIN)
    _, _, tail = rest.partition(END)
    if not rest or END not in rest:
        raise SystemExit(f"{DOC}: marker comments not found")
    return f"{head}{BEGIN}\n{block}{END}{tail}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the committed block is stale")
    args = parser.parse_args()
    current = DOC.read_text()
    updated = splice(current, render())
    if args.check:
        if updated != current:
            print(f"{DOC} is stale; run scripts/gen_er_diagram.py", file=sys.stderr)
            return 1
        return 0
    DOC.write_text(updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
