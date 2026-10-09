"""Write and read the per-stage splits CSV, and the per-stage events CSV.

Splits CSV schema (per SPEC.md, plus ``moving`` added in #task-6):

    shot_number, time_from_start, split, peak_amplitude, confidence, notes, moving

- ``time_from_start`` is seconds from the beep (millisecond precision).
- ``split`` is seconds since the previous shot (or seconds since the beep for shot 1).
- ``notes`` starts blank; the user fills in ``draw``, ``reload``, ``transition``, etc.
  False positives are removed by deleting the row before regenerating the FCPXML.
- ``moving`` is ``true``/``false``: whether the shot falls inside a confirmed
  movement region (spec 2026-10-08, part 2). ``false`` throughout when the
  stage has no confirmed regions -- there is never a guess here.

``events.csv`` (``id, kind, start, end, duration, source, note``) is written
beside the splits CSV, by its own caller, only when the stage has confirmed
regions (:func:`write_events_csv`); this module never decides that gate.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

from .config import CsvShot, Shot, StageEvent
from .events import shot_is_moving

CSV_HEADER = ["shot_number", "time_from_start", "split", "peak_amplitude", "confidence", "notes"]
CSV_HEADER_WITH_MOVING = [*CSV_HEADER, "moving"]

EVENTS_CSV_HEADER = ["id", "kind", "start", "end", "duration", "source", "note"]


def write_splits_csv(shots: list[Shot], output_path: Path, *, events: Sequence[StageEvent] = ()) -> None:
    """Write a list of detected ``Shot`` records to ``output_path`` as splits CSV.

    ``events`` is the stage's **confirmed** regions only -- callers never pass
    an auto proposal here (spec 2026-10-08, part 2). Each shot's ``moving``
    column is ``events.shot_is_moving`` over them; ``false`` throughout when
    ``events`` is empty.
    """
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER_WITH_MOVING)
        for s in shots:
            writer.writerow(
                [
                    s.shot_number,
                    f"{s.time_from_beep:.3f}",
                    f"{s.split:.3f}",
                    f"{s.peak_amplitude:.4f}",
                    f"{s.confidence:.3f}",
                    s.notes,
                    "true" if shot_is_moving(s.time_from_beep, events) else "false",
                ]
            )


def write_events_csv(events: Sequence[StageEvent], output_path: Path) -> None:
    """Write the stage's regions to ``output_path`` as ``events.csv``.

    Callers write this only when ``events`` is non-empty (the same
    ``write_csv`` gate that writes the splits CSV); this function itself
    writes whatever it is given, header included, even an empty list.
    """
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(EVENTS_CSV_HEADER)
        for e in events:
            writer.writerow(
                [
                    e.id,
                    e.kind,
                    f"{e.start:.3f}",
                    f"{e.end:.3f}",
                    f"{e.end - e.start:.3f}",
                    e.source,
                    e.note or "",
                ]
            )


def read_splits_csv(path: Path) -> list[CsvShot]:
    """Read a splits CSV (possibly hand-edited) back into ``CsvShot`` records.

    Hand edits expected: deleting rows (false positives), renumbering is NOT
    required -- shot_number is preserved verbatim and not re-derived. Splits
    are also preserved verbatim; if the user removes a row, ``split`` for the
    next-kept row is now stale relative to the new neighbour. The regeneration
    step (fcpxml_gen) will recompute splits from ``time_from_start`` if needed.

    Accepts both the pre-``moving`` header and the current one -- a CSV
    exported before this column existed still reads; the ``moving`` value
    itself is not surfaced on ``CsvShot`` (nothing downstream of a hand-edited
    CSV regen needs it today).
    """
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames not in (CSV_HEADER, CSV_HEADER_WITH_MOVING):
            raise ValueError(
                f"unexpected CSV header in {path}: got {reader.fieldnames}, "
                f"expected {CSV_HEADER} or {CSV_HEADER_WITH_MOVING}"
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
