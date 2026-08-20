#!/usr/bin/env python3
"""Repoint fixture ``source_video`` paths at the consolidated X9 corpus.

The consolidation retires the legacy single-shooter project folders and
moves raw footage onto X9, which invalidates 78 of the 161 fixture
``source_video`` values. The canonical form after this migration is

    /Volumes/X9/matches/<match-slug>/shooters/<s_id>/raw/<filename>

-- the same form the 83 already-correct fixtures use. It resolves through
the project's own ``raw/`` symlink, so a future raw reorganisation is
absorbed by relinking instead of another fixture rewrite.

The rewrite is a pure directory substitution: filenames never change
during the consolidation, so a fixture whose directory prefix is not in
the mapping is REPORTED, never rewritten to something plausible.

Usage:
    uv run python scripts/migrate_fixtures_raw_root.py --dry-run
    uv run python scripts/migrate_fixtures_raw_root.py
    uv run python scripts/migrate_fixtures_raw_root.py --mapping build/consolidation/raw_mapping.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

FIXTURES_ROOT = Path(__file__).parent.parent / "tests" / "fixtures"
CANONICAL_PREFIX = "/Volumes/X9/matches/"
CANONICAL_MARKER = "/shooters/"


class RewriteOutcome(BaseModel):
    """What the rewrite decided for one ``source_video`` value."""

    original: str
    rewritten: str | None
    status: Literal["rewritten", "already_canonical", "unmapped"]


class RunReport(BaseModel):
    """Aggregate result of one pass over the fixture tree.

    ``rewritten + already_canonical + unmapped + skipped`` equals the
    number of fixture JSONs seen. ``skipped`` is what makes that sum
    total: without it a fixture with unreadable JSON, a non-object
    document or an empty ``source_video`` would leave the corpus while
    the arithmetic still balanced.
    """

    rewritten: int = 0
    already_canonical: int = 0
    unmapped: int = 0
    unmapped_paths: list[str] = []
    skipped: int = 0
    skipped_files: list[str] = []


def rewrite_source_video(source_video: str, mapping: dict[str, str]) -> RewriteOutcome:
    """Map one ``source_video`` onto its consolidated location.

    ``mapping`` is directory-prefix to directory-prefix. The longest
    matching prefix wins, so a mapping may contain both a match-level and
    a shooter-level entry without ambiguity.
    """
    path = Path(source_video)
    parent = str(path.parent)

    if source_video.startswith(CANONICAL_PREFIX) and CANONICAL_MARKER in source_video:
        return RewriteOutcome(original=source_video, rewritten=None, status="already_canonical")

    matches = [old for old in mapping if parent == old or parent.startswith(f"{old}/")]
    if not matches:
        return RewriteOutcome(original=source_video, rewritten=None, status="unmapped")

    longest = max(matches, key=len)
    suffix = parent[len(longest) :].lstrip("/")
    new_parent = Path(mapping[longest]) / suffix if suffix else Path(mapping[longest])
    return RewriteOutcome(
        original=source_video,
        rewritten=str(new_parent / path.name),
        status="rewritten",
    )


def run(*, fixtures_root: Path, mapping: dict[str, str], dry_run: bool) -> RunReport:
    """Rewrite every fixture under ``fixtures_root``. Idempotent."""
    report = RunReport()
    for fixture_path in sorted(fixtures_root.glob("*.json")):
        try:
            doc = json.loads(fixture_path.read_text())
        except json.JSONDecodeError:
            report.skipped += 1
            report.skipped_files.append(fixture_path.name)
            continue
        if not isinstance(doc, dict) or not doc.get("source_video"):
            report.skipped += 1
            report.skipped_files.append(fixture_path.name)
            continue

        outcome = rewrite_source_video(doc["source_video"], mapping)
        if outcome.status == "already_canonical":
            report.already_canonical += 1
            continue
        if outcome.status == "unmapped":
            report.unmapped += 1
            report.unmapped_paths.append(outcome.original)
            continue

        report.rewritten += 1
        if not dry_run:
            doc["source_video"] = outcome.rewritten
            fixture_path.write_text(json.dumps(doc, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
    parser.add_argument("--fixtures-root", type=Path, default=FIXTURES_ROOT)
    parser.add_argument(
        "--mapping",
        type=Path,
        required=True,
        help="JSON object of old directory prefix -> new directory prefix, "
        "as emitted by scripts/consolidate_matches.py plan.",
    )
    args = parser.parse_args()

    mapping = json.loads(args.mapping.read_text())
    report = run(fixtures_root=args.fixtures_root, mapping=mapping, dry_run=args.dry_run)

    print(f"rewritten:         {report.rewritten}")
    print(f"already canonical: {report.already_canonical}")
    print(f"unmapped:          {report.unmapped}")
    print(f"skipped:           {report.skipped}")
    for path in report.unmapped_paths:
        print(f"  unmapped: {path}")
    for name in report.skipped_files:
        print(f"  skipped:  {name} (unreadable JSON, not an object, or no source_video)")
    if report.unmapped:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
