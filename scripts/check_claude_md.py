"""Guard the size of CLAUDE.md and the health of the path-scoped rules.

The root CLAUDE.md is loaded into every Claude Code session and subagent;
subsystem detail lives in ``.claude/rules/*.md``, each loaded only when a file
matching its ``paths:`` frontmatter is read or edited. This check keeps that
split working:

- the root stays under ``MAX_ROOT_BYTES``;
- every rule file is scoped (an unscoped rule loads in every session, which is
  the root's job);
- every ``paths:`` glob matches at least one file (a rename silently stops a
  rule from loading);
- every ``*.md`` rule file a rule or the root names exists.

Stdlib only, so the CI job needs no install. Exit status 1 on any problem.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOT_DOC = REPO / "CLAUDE.md"
RULES_DIR = REPO / ".claude" / "rules"

# 34 KB when the split finished (2026-10-18). Raise it only by moving detail
# out, not by adding it here.
MAX_ROOT_BYTES = 40_000

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_PATH_ITEM = re.compile(r'^\s+-\s+"([^"]+)"\s*$')
_RULE_REF = re.compile(r"`([a-z0-9-]+\.md)`")


def rule_globs(text: str) -> list[str] | None:
    """The ``paths:`` globs of a rule file, or None when it has no frontmatter."""
    match = _FRONTMATTER.match(text)
    if match is None:
        return None
    globs: list[str] = []
    in_paths = False
    for line in match.group(1).splitlines():
        if line.startswith("paths:"):
            in_paths = True
            continue
        if in_paths:
            item = _PATH_ITEM.match(line)
            if item is None:
                break
            globs.append(item.group(1))
    return globs


def problems(repo: Path = REPO) -> list[str]:
    root_doc = repo / "CLAUDE.md"
    rules_dir = repo / ".claude" / "rules"
    found: list[str] = []

    size = root_doc.stat().st_size
    if size > MAX_ROOT_BYTES:
        found.append(
            f"CLAUDE.md is {size} bytes (limit {MAX_ROOT_BYTES}). It loads into every session: "
            "move subsystem detail into a path-scoped file under .claude/rules/."
        )

    rule_files = sorted(rules_dir.glob("*.md"))
    rule_names = {path.name for path in rule_files}
    for rule in rule_files:
        rel = rule.relative_to(repo)
        globs = rule_globs(rule.read_text())
        if not globs:
            found.append(f"{rel}: no `paths:` frontmatter, so it would load in every session")
            continue
        for pattern in globs:
            if next(repo.glob(pattern), None) is None:
                found.append(f"{rel}: glob {pattern!r} matches no file")

    for doc in [root_doc, *rule_files]:
        for name in sorted(set(_RULE_REF.findall(doc.read_text()))):
            if name in rule_names or name in {"CLAUDE.md", "SPEC.md"}:
                continue
            if next(repo.glob(f"docs/**/{name}"), None) is not None:
                continue
            rel = doc.relative_to(repo)
            found.append(f"{rel}: names `{name}`, which is neither a rule file nor in docs/")
    return found


def main() -> int:
    found = problems()
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
