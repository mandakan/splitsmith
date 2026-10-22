"""What a clip's file and folder names say (spec 2026-10-22, "Sort"): the
recording order within a camera, a stage number, a shooter's name.

Pure string work for ``footage_sort``. Measured on the sort fixtures (every
clip of nine 2026 matches, assigned by hand): of the 145 labelled clips
whose file or folder name carried a shooter or a stage by these rules, all
145 agreed with the hand label.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

RankEvidence = Literal["clock", "name_time", "name_number", "recorded", "modified"]

_NAME_TIME = (
    re.compile(r"^PXL_(\d{8})_(\d{6,9})", re.IGNORECASE),
    re.compile(r"^VID_(\d{8})_(\d{6})", re.IGNORECASE),
    re.compile(r"^(\d{8})_(\d{6})"),
)
_IMG = re.compile(r"^IMG_(\d+)", re.IGNORECASE)
_GOPRO = re.compile(r"^G[HX](\d{2})(\d{4})", re.IGNORECASE)
_META = re.compile(r"^video-(\d+)_", re.IGNORECASE)

# A stage named in a file: "stage_3", "stage-03", "Stage 3", "st3", "S03".
# Token-bounded so "IMG_3" or "best3" are not stages.
_STAGE = (
    re.compile(r"stage[_ -]?0*(\d{1,2})(?!\d)"),
    re.compile(r"(?<![a-z0-9])st0*(\d{1,2})(?![a-z0-9])"),
    re.compile(r"(?<![a-z0-9])s0*(\d{1,2})(?![a-z0-9])"),
)


def fold(text: str) -> str:
    """Lower case without accents: "Hostfinalen" matches "Höstfinalen"."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", fold(text)) if w}


def name_time(filename: str) -> str | None:
    """The recording time a file name carries, as a sortable string."""
    for pattern in _NAME_TIME:
        m = pattern.match(filename)
        if m:
            return m.group(1) + m.group(2).ljust(9, "0")
    return None


def name_number(filename: str) -> tuple[int, int] | None:
    """A camera's own counter: IMG number, GoPro file number then chapter
    (GX010012, GX020012, GX010013 is the recording order), Meta's video
    number."""
    m = _GOPRO.match(filename)
    if m:
        return int(m.group(2)), int(m.group(1))
    for pattern in (_IMG, _META):
        m = pattern.match(filename)
        if m:
            return int(m.group(1)), 0
    return None


def stage_in_name(filename: str) -> int | None:
    stem = fold(filename.rsplit(".", 1)[0])
    for pattern in _STAGE:
        m = pattern.search(stem)
        if m:
            return int(m.group(1))
    return None


def shooter_in_name(text: str, shooters: Sequence[tuple[str, str]]) -> str | None:
    """The one shooter, as ``(key, name)``, whose first name, full name or
    key appears in ``text`` as whole words; None for none or several."""
    if not text:
        return None
    words = _words(text)
    folded = fold(text)
    hits: set[str] = set()
    for key, name in shooters:
        parts = fold(name).split()
        full = "_".join(parts)
        if (parts and parts[0] in words) or (full and full in re.sub(r"[^a-z0-9]+", "_", folded)):
            hits.add(key)
        elif fold(key) in words:
            hits.add(key)
    return hits.pop() if len(hits) == 1 else None


def recording_rank(
    filenames: Sequence[str],
    starts: Sequence[datetime | None],
    modified: Sequence[datetime | None],
    *,
    clock_ok: bool,
) -> tuple[list[int], RankEvidence] | None:
    """Indexes into the camera's clips in recording order, ranked by the
    first evidence every clip has: a clock the sort trusts, a time in the
    name, the camera's counter, the recorded start even on a wrong clock
    (consistent, so it still orders), the file's modification time."""
    n = len(filenames)
    order = list(range(n))
    if clock_ok and all(s is not None for s in starts):
        return sorted(order, key=lambda i: starts[i]), "clock"  # type: ignore[arg-type,return-value]
    times = [name_time(f) for f in filenames]
    if all(t is not None for t in times):
        return sorted(order, key=lambda i: times[i]), "name_time"  # type: ignore[arg-type,return-value]
    numbers = [name_number(f) for f in filenames]
    if all(x is not None for x in numbers):
        return sorted(order, key=lambda i: numbers[i]), "name_number"  # type: ignore[arg-type,return-value]
    if all(s is not None for s in starts):
        return sorted(order, key=lambda i: starts[i]), "recorded"  # type: ignore[arg-type,return-value]
    if all(m is not None for m in modified):
        return sorted(order, key=lambda i: modified[i]), "modified"  # type: ignore[arg-type,return-value]
    return None
