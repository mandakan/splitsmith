"""Grid-tile placement math for compare exports.

Pure functions: no I/O, no FCPXML. Slot positions are returned in
sequence-frame pixels (centre-of-tile, sequence-centre origin, +Y up);
the FCPXML emitter converts them to FCP's normalised units using the
same ``unit_per_px = 100.0 / sequence_height`` rule
:func:`splitsmith.fcpxml_gen._pip_transform_attrs` uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GridKind = Literal["1up", "2up-h", "2up-v", "2x2", "3x3", "4x4"]
Layout2Up = Literal["horizontal", "vertical"]

# (rows, cols) per grid kind. ``2up-h`` is one row of two; ``2up-v`` is
# two stacked rows. Bigger grids are square.
_GRID_SHAPE: dict[GridKind, tuple[int, int]] = {
    "1up": (1, 1),
    "2up-h": (1, 2),
    "2up-v": (2, 1),
    "2x2": (2, 2),
    "3x3": (3, 3),
    "4x4": (4, 4),
}


@dataclass(frozen=True)
class GridSlot:
    """One tile's transform inside the sequence frame."""

    scale: float
    """Uniform letterbox factor vs. native cam size (``1.0`` == native)."""

    position_px: tuple[float, float]
    """Centre-of-tile pixel offset from the sequence centre, +Y up."""


@dataclass(frozen=True)
class GridLayout:
    """A resolved grid for one stage of one compare export."""

    kind: GridKind
    slots_per_label: dict[str, GridSlot]
    """Slot keyed by label, in alphabetical order. Only labels actually
    present in this stage appear; missing-tile labels are absent."""

    empty_slots: list[GridSlot]
    """Filler-tile slots for cells the chosen grid leaves empty."""


def choose_grid(roster_count: int, *, layout_2up: Layout2Up = "horizontal") -> GridKind:
    """Smallest grid whose capacity is ``>= roster_count``.

    Sized for the *full* manifest roster, not the per-stage present
    subset, so slot indices stay stable across stages of one export
    (a label always lands in the same tile; missing tiles become
    filler). 1 -> ``1up``; 2 -> ``2up-h`` or ``2up-v`` per
    ``layout_2up``; 3..4 -> ``2x2``; 5..9 -> ``3x3``; 10..16 ->
    ``4x4``. Counts of 0 or above 16 raise :class:`ValueError`.
    """
    if roster_count <= 0:
        raise ValueError(f"roster_count must be >= 1, got {roster_count}")
    if roster_count == 1:
        return "1up"
    if roster_count == 2:
        return "2up-h" if layout_2up == "horizontal" else "2up-v"
    if roster_count <= 4:
        return "2x2"
    if roster_count <= 9:
        return "3x3"
    if roster_count <= 16:
        return "4x4"
    raise ValueError(f"roster_count={roster_count} exceeds the largest supported grid (16)")


def _slot_for_index(
    *,
    index: int,
    rows: int,
    cols: int,
    sequence_width: int,
    sequence_height: int,
    cam_width: int,
    cam_height: int,
) -> GridSlot:
    """Compute the transform for tile ``index`` (0-based, row-major)."""
    row = index // cols
    col = index % cols
    cell_w = sequence_width / cols
    cell_h = sequence_height / rows
    scale = min(cell_w / cam_width, cell_h / cam_height)
    # Centre of cell (col, row) in the same +Y-up convention the emitter
    # uses (row 0 sits at the top of the frame, so y is positive).
    centre_x = (col + 0.5) * cell_w - sequence_width / 2.0
    centre_y = sequence_height / 2.0 - (row + 0.5) * cell_h
    return GridSlot(scale=scale, position_px=(centre_x, centre_y))


def compute_layout(
    *,
    sorted_labels: list[str],
    present_labels: set[str],
    sequence_width: int,
    sequence_height: int,
    cam_width: int,
    cam_height: int,
    layout_2up: Layout2Up = "horizontal",
) -> GridLayout:
    """Resolve per-tile transforms for one stage.

    ``sorted_labels`` is the alphabetically-sorted list of every label
    in the manifest (the full roster -- ``len(sorted_labels)`` drives
    the chosen grid kind). Slot indices follow that order so a label
    always lands in the same tile across every stage of the export,
    regardless of who's missing. Missing labels leave their cell empty
    for the filler; remaining unused cells in the chosen grid (when
    the roster doesn't fill it perfectly) also go into ``empty_slots``.
    """
    if not sorted_labels:
        raise ValueError("sorted_labels must not be empty")
    if not present_labels:
        raise ValueError("present_labels must not be empty")
    unknown = present_labels - set(sorted_labels)
    if unknown:
        raise ValueError(f"present_labels has unknown labels: {sorted(unknown)}")

    kind = choose_grid(len(sorted_labels), layout_2up=layout_2up)
    rows, cols = _GRID_SHAPE[kind]
    capacity = rows * cols

    # Slot index = position in sorted_labels. Stable across stages even
    # when a different shooter is missing each stage.
    slots_per_label: dict[str, GridSlot] = {}
    used_indices: set[int] = set()
    for index, label in enumerate(sorted_labels):
        if label in present_labels:
            slots_per_label[label] = _slot_for_index(
                index=index,
                rows=rows,
                cols=cols,
                sequence_width=sequence_width,
                sequence_height=sequence_height,
                cam_width=cam_width,
                cam_height=cam_height,
            )
            used_indices.add(index)

    empty_slots = [
        _slot_for_index(
            index=i,
            rows=rows,
            cols=cols,
            sequence_width=sequence_width,
            sequence_height=sequence_height,
            cam_width=cam_width,
            cam_height=cam_height,
        )
        for i in range(capacity)
        if i not in used_indices
    ]
    return GridLayout(kind=kind, slots_per_label=slots_per_label, empty_slots=empty_slots)
