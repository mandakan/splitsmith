#!/usr/bin/env python3
"""Backfill ``camera``, ``venue``, ``gun``, and ``history`` onto existing fixture JSONs.

Idempotent: fixtures that already have all four blocks are skipped.

Usage:
    uv run python scripts/migrate_fixtures_add_camera.py [--dry-run]

Hardcoded defaults:
  camera  -- Insta360 GO 3S, head-mounted by the shooter
  venue   -- unknown (fill in per fixture once confirmed)
  gun     -- CZ P10-F, 9mm, semi-auto, minor PF, no muzzle device
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the package is importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from splitsmith.fixture_schema import (
    AgcState,
    AudioSource,
    Camera,
    CameraMount,
    CameraPosition,
    Gun,
    GunAction,
    GunMuzzleDevice,
    PowerFactor,
    Venue,
    VenueEnvironment,
    backfill_fixture,
)

FIXTURES_ROOT = Path(__file__).parent.parent / "tests" / "fixtures"

HEADCAM_GO3S = Camera(
    id="go3s",
    make="Insta360",
    model="GO 3S",
    mount=CameraMount.head,
    position=CameraPosition.shooter,
    audio_source=AudioSource.internal,
    agc_state=AgcState.unknown,
    sample_rate=48000,
    bit_depth=None,
    audio_codec=None,
)


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
    args = parser.parse_args()

    root: Path = args.fixtures_root
    fixtures = sorted(
        f
        for f in root.glob("stage-shots-*.json")
        if not any(suffix in f.name for suffix in [".bak", ".before-", ".peaks-", "-candidates"])
    )

    if not fixtures:
        print(f"No fixtures found under {root}")
        sys.exit(1)

    changed = 0
    skipped = 0
    for path in fixtures:
        gun = Gun(
            calibre="9mm",
            muzzle_device=GunMuzzleDevice.none,
            action=GunAction.semi_auto,
            power_factor=PowerFactor.minor,
        )
        venue = Venue(environment=VenueEnvironment.outdoor)
        did_change = backfill_fixture(path, HEADCAM_GO3S, venue, gun, dry_run=args.dry_run)
        status = (
            "would update"
            if (did_change and args.dry_run)
            else ("updated" if did_change else "skipped (already has camera+history)")
        )
        print(f"  {path.name}: {status}")
        if did_change:
            changed += 1
        else:
            skipped += 1

    label = "would change" if args.dry_run else "changed"
    print(f"\n{label}: {changed}  skipped: {skipped}")


if __name__ == "__main__":
    main()
