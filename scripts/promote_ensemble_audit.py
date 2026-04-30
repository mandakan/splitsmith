"""Promote an audited ensemble fixture into its canonical repo location.

Audits performed in ``build/ensemble-review/<stem>-ensemble-{N}of3.json``
live in a gitignored directory and don't propagate to the repo. This
script copies the audited ``shots[]`` (and the ensemble's candidate set
so candidate_numbers stay resolvable in the UI) into the canonical
``tests/fixtures/<stem>.json``, which IS committed and which every eval
script reads for ground truth.

A ``.json.before-promote`` backup of the canonical file is written first.

Run:
    uv run python scripts/promote_ensemble_audit.py \\
        --ensemble build/ensemble-review/stage-shots-blacksmith-2026-stage2-ensemble-3of3.json
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

FIXTURES_DIR = Path("tests/fixtures")


def _derive_canonical_path(ensemble_path: Path) -> Path:
    """``build/ensemble-review/<stem>-ensemble-{N}of3.json`` (or ``-baseline.json``)
    -> ``tests/fixtures/<stem>.json``."""
    stem = ensemble_path.stem
    for suffix in (
        "-ensemble-3of3",
        "-ensemble-2of3",
        "-ensemble-1of3",
        "-baseline",
    ):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return FIXTURES_DIR / f"{stem}.json"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--ensemble",
        type=Path,
        required=True,
        help="Path to the audited build/.../<stem>-ensemble-{N}of3.json file.",
    )
    p.add_argument(
        "--canonical",
        type=Path,
        default=None,
        help="Override target path (default: derived from ensemble stem).",
    )
    p.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the .json.before-promote backup of the canonical file.",
    )
    args = p.parse_args()

    if not args.ensemble.exists():
        raise SystemExit(f"ensemble fixture not found: {args.ensemble}")
    canonical = args.canonical or _derive_canonical_path(args.ensemble)
    if not canonical.exists():
        raise SystemExit(f"canonical fixture not found: {canonical}")

    ens_data = json.loads(args.ensemble.read_text())
    canon_data = json.loads(canonical.read_text())

    if not args.no_backup:
        backup = canonical.with_name(canonical.name + ".before-promote")
        shutil.copy2(canonical, backup)
        print(f"backup: {backup}")

    # Copy the curated shots[] AND the matching candidates list so candidate_numbers
    # in shots[] resolve correctly when re-opening the canonical fixture in the UI.
    canon_data["shots"] = ens_data.get("shots", [])
    if "_candidates_pending_audit" in ens_data:
        canon_data["_candidates_pending_audit"] = ens_data["_candidates_pending_audit"]

    canonical.write_text(json.dumps(canon_data, indent=2) + "\n")

    n_shots = len(canon_data["shots"])
    n_cands = len(canon_data.get("_candidates_pending_audit", {}).get("candidates", []))
    n_manual = sum(1 for s in canon_data["shots"] if s.get("source") == "manual")
    print(
        f"wrote {canonical}: {n_shots} shots ({n_manual} manual), "
        f"{n_cands} candidates pending"
    )
    print()
    print("Next steps:")
    print(f"  git add {canonical}")
    print(f"  git diff --stat {canonical}  # review what changed")


if __name__ == "__main__":
    main()
