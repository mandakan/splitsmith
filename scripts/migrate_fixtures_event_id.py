#!/usr/bin/env python3
"""Backfill ``shooter`` + ``event_id`` onto existing fixture JSONs.

Idempotent: fixtures that already have both blocks are left alone.
Reports multi-camera event groups (slugs that resolve to the same
event_id) so sibling clustering becomes visible in the Lab UI without
further action.

For non-canonical slugs that need to merge into an existing event group
(e.g., ``stage-shots-blacksmith-handgun-open-2026-stage6`` should join
the same event as ``stage-shots-blacksmith-2026-stage6``), pass
``--alias <slug>=<event_id>`` one or more times. The migration writes
the explicit ``event_id`` to the fixture JSON so the slug parser is
bypassed.

Default ``shooter.id`` is ``"self"`` -- legacy fixtures pre-date the
field and were all the project owner. Override with ``--shooter-id``
when migrating a corpus that mixes shooters; combine with ``--scope
<slug-glob>`` to limit the override to a subset.

Usage:
    uv run python scripts/migrate_fixtures_event_id.py
    uv run python scripts/migrate_fixtures_event_id.py --dry-run
    uv run python scripts/migrate_fixtures_event_id.py \\
        --alias stage-shots-blacksmith-handgun-open-2026-stage6=blacksmith-2026:6:self
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path

# Make the ``splitsmith`` package importable when running from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from splitsmith.lab.core import (
    DEFAULT_SHOOTER_KEY,
    build_event_id,
    match_stage_from_slug,
)

FIXTURES_ROOT = Path(__file__).parent.parent / "tests" / "fixtures"


def _parse_alias(arg: str) -> tuple[str, str]:
    if "=" not in arg:
        raise argparse.ArgumentTypeError(f"--alias expects ``slug=event_id``, got {arg!r}")
    slug, event_id = arg.split("=", 1)
    slug = slug.strip()
    event_id = event_id.strip()
    if not slug or not event_id or event_id.count(":") < 2:
        raise argparse.ArgumentTypeError(
            f"--alias expects a non-empty slug and ``<match>:<n>:<shooter>`` event id, got {arg!r}"
        )
    return slug, event_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    parser.add_argument(
        "--fixtures-root",
        type=Path,
        default=FIXTURES_ROOT,
        help="Override the fixtures directory.",
    )
    parser.add_argument(
        "--alias",
        action="append",
        type=_parse_alias,
        default=[],
        help=(
            "Override the slug-derived event_id for a specific slug. "
            "Repeatable. Format: ``slug=<match>:<n>:<shooter>`` "
            "(e.g., ``...-stage6=blacksmith-2026:6:self``)."
        ),
    )
    parser.add_argument(
        "--shooter-id",
        default=DEFAULT_SHOOTER_KEY,
        help=(
            "Default ``shooter.id`` to stamp onto fixtures missing one. "
            f"Default: {DEFAULT_SHOOTER_KEY!r} (legacy fallback). Use a "
            "value like ``ssi-12345`` to migrate a corpus belonging to a "
            "specific SSI shooter."
        ),
    )
    parser.add_argument(
        "--shooter-name",
        default=None,
        help="Optional ``shooter.name`` to stamp alongside ``--shooter-id``.",
    )
    parser.add_argument(
        "--scope",
        default="*",
        help=(
            "Glob filter on slug; only fixtures matching this pattern get "
            "the ``--shooter-id`` / ``--shooter-name`` overrides. Other "
            "fixtures still have shooter=self stamped if missing. Default: "
            "all fixtures."
        ),
    )
    args = parser.parse_args()

    aliases: dict[str, str] = dict(args.alias)
    root: Path = args.fixtures_root
    fixtures = sorted(
        f
        for f in root.glob("stage-shots-*.json")
        if not any(s in f.name for s in [".bak", ".before-", ".peaks-", "-candidates"])
        and not f.name.endswith("-promotion-report.json")
    )
    if not fixtures:
        print(f"No fixtures found under {root}")
        sys.exit(1)

    changed = 0
    skipped = 0
    by_event: dict[str, list[str]] = {}
    for path in fixtures:
        slug = path.stem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  {slug}: skipped (read failed: {exc})")
            skipped += 1
            continue
        if not isinstance(data, dict):
            print(f"  {slug}: skipped (not a JSON object)")
            skipped += 1
            continue

        existing_shooter = data.get("shooter")
        wants_shooter_override = fnmatch.fnmatch(slug, args.scope) and (
            args.shooter_id != DEFAULT_SHOOTER_KEY or args.shooter_name is not None
        )
        if not isinstance(existing_shooter, dict) or not isinstance(
            existing_shooter.get("id"), str
        ):
            shooter_block: dict[str, object] = {"id": DEFAULT_SHOOTER_KEY}
        else:
            shooter_block = dict(existing_shooter)
        if wants_shooter_override:
            shooter_block["id"] = args.shooter_id
            if args.shooter_name is not None:
                shooter_block["name"] = args.shooter_name

        existing_event = data.get("event_id")
        explicit_event = aliases.get(slug)
        if isinstance(explicit_event, str):
            event_id = explicit_event
        elif isinstance(existing_event, str) and existing_event:
            event_id = existing_event
        else:
            parsed = match_stage_from_slug(slug)
            if parsed is None:
                print(f"  {slug}: no event_id derivable from slug; pass --alias to set one")
                skipped += 1
                continue
            match_slug, n = parsed
            shooter_key_raw = shooter_block.get("id")
            shooter_key = (
                shooter_key_raw
                if isinstance(shooter_key_raw, str) and shooter_key_raw
                else DEFAULT_SHOOTER_KEY
            )
            event_id = build_event_id(match_slug, n, shooter_key)

        existing_event_str = (
            existing_event if isinstance(existing_event, str) and existing_event else None
        )
        existing_shooter_id = (
            existing_shooter.get("id") if isinstance(existing_shooter, dict) else None
        )
        new_shooter_id = shooter_block.get("id")
        if existing_event_str == event_id and existing_shooter_id == new_shooter_id:
            by_event.setdefault(event_id, []).append(slug)
            skipped += 1
            continue

        action = "would write" if args.dry_run else "writing"
        print(f"  {slug}: {action} shooter.id={new_shooter_id!r} event_id={event_id}")
        if not args.dry_run:
            data["shooter"] = shooter_block
            data["event_id"] = event_id
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        changed += 1
        by_event.setdefault(event_id, []).append(slug)

    label = "would change" if args.dry_run else "changed"
    print(f"\n{label}: {changed}  skipped: {skipped}")

    multi = {ev: slugs for ev, slugs in by_event.items() if len(slugs) > 1}
    if multi:
        print("\nMulti-camera event groups:")
        for ev in sorted(multi):
            print(f"  {ev}")
            for s in sorted(multi[ev]):
                print(f"    - {s}")


if __name__ == "__main__":
    main()
