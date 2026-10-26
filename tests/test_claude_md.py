"""The CLAUDE.md size guard and the path-scoped rules stay healthy."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_claude_md", REPO / "scripts" / "check_claude_md.py")
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def _repo(tmp_path: Path, root: str = "# Root\n", rules: dict[str, str] | None = None) -> Path:
    (tmp_path / "CLAUDE.md").write_text(root, encoding="utf-8")
    rules_dir = tmp_path / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    for name, text in (rules or {}).items():
        (rules_dir / name).write_text(text, encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "thing.py").write_text("", encoding="utf-8")
    return tmp_path


_SCOPED = '---\npaths:\n  - "src/*.py"\n---\n\n# Rule\n'


def test_the_repository_passes() -> None:
    assert check.problems() == []


def test_a_healthy_scratch_repo_passes(tmp_path: Path) -> None:
    assert check.problems(_repo(tmp_path, rules={"thing.md": _SCOPED})) == []


def test_an_oversized_root_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path, root="x" * (check.MAX_ROOT_BYTES + 1))
    assert any("CLAUDE.md is" in p for p in check.problems(repo))


def test_an_unscoped_rule_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path, rules={"thing.md": "# Rule with no frontmatter\n"})
    assert any("no `paths:`" in p for p in check.problems(repo))


def test_a_glob_that_matches_nothing_fails(tmp_path: Path) -> None:
    rule = '---\npaths:\n  - "src/*.py"\n  - "src/renamed_*.py"\n---\n'
    found = check.problems(_repo(tmp_path, rules={"thing.md": rule}))
    assert found == [".claude/rules/thing.md: glob 'src/renamed_*.py' matches no file"]


def test_a_reference_to_a_missing_rule_file_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path, root="See `gone.md`.\n", rules={"thing.md": _SCOPED + "See `thing.md`.\n"})
    found = check.problems(repo)
    assert found == ["CLAUDE.md: names `gone.md`, which is neither a rule file nor in docs/"]


def test_a_reference_into_docs_passes(tmp_path: Path) -> None:
    repo = _repo(tmp_path, root="See `cameras.md`.\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "cameras.md").write_text("", encoding="utf-8")
    assert check.problems(repo) == []


def test_non_ascii_text_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path, root="# Root\n\nuv \u2014 never pip\n")
    assert check.problems(repo) == ["CLAUDE.md:3: non-ASCII character"]
