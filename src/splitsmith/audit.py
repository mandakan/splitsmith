"""Helpers for the hand-audit workflow.

Audit flow:
1. ``audit-prep`` (or the ad-hoc fixture script) emits ``<name>.wav``,
   ``<name>.json`` (empty ``shots[]`` ground truth), and
   ``<name>-candidates.csv`` with an ``audit_keep`` column.
2. The user opens the CSV, marks rows they want to keep, saves.
3. ``audit-apply`` reads the marked CSV and rewrites the JSON's ``shots[]``.

This module exposes the read/merge primitives. The CLI wraps them.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

# Anything in this set (case-insensitive, trimmed) marks a candidate as kept.
_KEEP_TRUTHY: frozenset[str] = frozenset({"y", "yes", "1", "x", "true", "keep", "t"})


def is_kept(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _KEEP_TRUTHY


def read_kept_candidate_numbers(candidates_csv: Path) -> list[int]:
    """Return the candidate_numbers from rows whose ``audit_keep`` cell is truthy.

    Order is preserved from the CSV, which is the natural shot order.
    """
    with candidates_csv.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "audit_keep" not in reader.fieldnames:
            raise ValueError(
                f"{candidates_csv}: missing 'audit_keep' column. Expected the audit-prep "
                f"format (audit_keep, candidate_number, ...). Got: {reader.fieldnames}"
            )
        if "candidate_number" not in reader.fieldnames:
            raise ValueError(f"{candidates_csv}: missing 'candidate_number' column")
        kept: list[int] = []
        for row in reader:
            if is_kept(row.get("audit_keep")):
                kept.append(int(row["candidate_number"]))
    return kept


def apply_audit_to_fixture(candidates_csv: Path, fixture_json: Path) -> int:
    """Merge marked-keep candidates into ``fixture_json``'s ``shots[]`` array.

    Returns the number of shots written. The JSON is rewritten in place; the
    ``_candidates_pending_audit`` block is preserved unchanged so the audit can
    be re-run from the same source if the user changes their mind.
    """
    kept_numbers = read_kept_candidate_numbers(candidates_csv)
    data = json.loads(fixture_json.read_text())

    candidates = data.get("_candidates_pending_audit", {}).get("candidates")
    if not candidates:
        raise ValueError(
            f"{fixture_json}: no candidates block found. Expected "
            f"'_candidates_pending_audit.candidates'."
        )
    by_num = {c["candidate_number"]: c for c in candidates}

    missing = sorted(n for n in kept_numbers if n not in by_num)
    if missing:
        raise ValueError(
            f"{candidates_csv}: candidate_number(s) marked keep but not present in "
            f"{fixture_json}: {missing}"
        )

    shots: list[dict] = []
    for kept_idx, cand_num in enumerate(kept_numbers, start=1):
        c = by_num[cand_num]
        shots.append(
            {
                "shot_number": kept_idx,
                "candidate_number": cand_num,
                "time": c["time"],
                "ms_after_beep": c["ms_after_beep"],
            }
        )
    data["shots"] = shots
    fixture_json.write_text(json.dumps(data, indent=2) + "\n")
    return len(shots)
