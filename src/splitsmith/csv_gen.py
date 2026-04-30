"""Write and read the per-stage splits CSV.

CSV schema (per SPEC.md):

    shot_number, time_from_start, split, peak_amplitude, confidence, notes

- ``time_from_start`` is seconds from the beep (millisecond precision).
- ``split`` is seconds since the previous shot (or seconds since the beep for shot 1).
- ``notes`` starts blank; the user fills in ``draw``, ``reload``, ``transition``, etc.
  False positives are removed by deleting the row before regenerating the FCPXML.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .config import CsvShot, Shot

CSV_HEADER = ["shot_number", "time_from_start", "split", "peak_amplitude", "confidence", "notes"]


def write_splits_csv(shots: list[Shot], output_path: Path) -> None:
    """Write a list of detected ``Shot`` records to ``output_path`` as splits CSV."""
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for s in shots:
            writer.writerow(
                [
                    s.shot_number,
                    f"{s.time_from_beep:.3f}",
                    f"{s.split:.3f}",
                    f"{s.peak_amplitude:.4f}",
                    f"{s.confidence:.3f}",
                    s.notes,
                ]
            )


def read_splits_csv(path: Path) -> list[CsvShot]:
    """Read a splits CSV (possibly hand-edited) back into ``CsvShot`` records.

    Hand edits expected: deleting rows (false positives), renumbering is NOT
    required -- shot_number is preserved verbatim and not re-derived. Splits
    are also preserved verbatim; if the user removes a row, ``split`` for the
    next-kept row is now stale relative to the new neighbour. The regeneration
    step (fcpxml_gen) will recompute splits from ``time_from_start`` if needed.
    """
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != CSV_HEADER:
            raise ValueError(
                f"unexpected CSV header in {path}: got {reader.fieldnames}, "
                f"expected {CSV_HEADER}"
            )
        return [
            CsvShot(
                shot_number=int(row["shot_number"]),
                time_from_start=float(row["time_from_start"]),
                split=float(row["split"]),
                peak_amplitude=float(row["peak_amplitude"]),
                confidence=float(row["confidence"]),
                notes=row.get("notes", "") or "",
            )
            for row in reader
        ]
