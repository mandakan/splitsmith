"""Grid layout math tests for the compare module."""

from __future__ import annotations

import dataclasses
import math

import pytest

from splitsmith.compare.layout import (
    GridLayout,
    GridSlot,
    choose_grid,
    compute_layout,
)


def test_choose_grid_table() -> None:
    assert choose_grid(1) == "1up"
    assert choose_grid(2, layout_2up="horizontal") == "2up-h"
    assert choose_grid(2, layout_2up="vertical") == "2up-v"
    assert choose_grid(3) == "2x2"
    assert choose_grid(4) == "2x2"
    assert choose_grid(5) == "3x3"
    assert choose_grid(9) == "3x3"
    assert choose_grid(10) == "4x4"
    assert choose_grid(16) == "4x4"


def test_choose_grid_rejects_zero_and_overflow() -> None:
    with pytest.raises(ValueError):
        choose_grid(0)
    with pytest.raises(ValueError):
        choose_grid(17)


def test_2up_horizontal_mirrored_around_x_zero() -> None:
    layout = compute_layout(
        sorted_labels=["A", "B"],
        present_labels={"A", "B"},
        sequence_width=1920,
        sequence_height=1080,
        cam_width=1920,
        cam_height=1080,
        layout_2up="horizontal",
    )
    assert layout.kind == "2up-h"
    a, b = layout.slots_per_label["A"], layout.slots_per_label["B"]
    # cell width = 960; centres at +/- 480 px from sequence centre
    assert a.position_px == (-480.0, 0.0)
    assert b.position_px == (480.0, 0.0)
    # letterbox: 1920-wide cam in a 960-wide cell -> 0.5 scale
    assert math.isclose(a.scale, 0.5)
    assert math.isclose(b.scale, 0.5)
    assert layout.empty_slots == []


def test_2up_vertical_stacks_with_x_zero() -> None:
    layout = compute_layout(
        sorted_labels=["A", "B"],
        present_labels={"A", "B"},
        sequence_width=1920,
        sequence_height=1080,
        cam_width=1920,
        cam_height=1080,
        layout_2up="vertical",
    )
    assert layout.kind == "2up-v"
    # Two rows, one col: cell h = 540; +Y up so row 0 lives at +270
    assert layout.slots_per_label["A"].position_px == (0.0, 270.0)
    assert layout.slots_per_label["B"].position_px == (0.0, -270.0)


def test_alphabetical_slot_assignment_is_stable_across_stages() -> None:
    """A label always lands in the same cell, regardless of who's missing."""
    sorted_labels = ["Anders", "Mathias", "Per"]

    full = compute_layout(
        sorted_labels=sorted_labels,
        present_labels={"Anders", "Mathias", "Per"},
        sequence_width=1920,
        sequence_height=1080,
        cam_width=1920,
        cam_height=1080,
    )
    # roster of 3 -> 2x2 grid; alphabetical assignment fills cells 0,1,2
    # (top-left, top-right, bottom-left), leaving cell 3 empty.
    assert full.kind == "2x2"
    a_pos = full.slots_per_label["Anders"].position_px
    m_pos = full.slots_per_label["Mathias"].position_px
    p_pos = full.slots_per_label["Per"].position_px
    assert len(full.empty_slots) == 1

    # Drop Anders for one stage: Mathias and Per stay in cells 1 and 2;
    # cell 0 (where Anders normally lives) becomes a filler slot.
    no_anders = compute_layout(
        sorted_labels=sorted_labels,
        present_labels={"Mathias", "Per"},
        sequence_width=1920,
        sequence_height=1080,
        cam_width=1920,
        cam_height=1080,
    )
    assert no_anders.kind == "2x2"
    assert no_anders.slots_per_label["Mathias"].position_px == m_pos
    assert no_anders.slots_per_label["Per"].position_px == p_pos
    assert "Anders" not in no_anders.slots_per_label
    assert len(no_anders.empty_slots) == 2  # cell 0 (Anders) + cell 3 (unused)
    anders_filler = next(s for s in no_anders.empty_slots if s.position_px == a_pos)
    assert anders_filler.position_px == a_pos

    # Drop Mathias instead: Anders stays at cell 0, Per stays at cell 2.
    no_mathias = compute_layout(
        sorted_labels=sorted_labels,
        present_labels={"Anders", "Per"},
        sequence_width=1920,
        sequence_height=1080,
        cam_width=1920,
        cam_height=1080,
    )
    assert no_mathias.slots_per_label["Anders"].position_px == a_pos
    assert no_mathias.slots_per_label["Per"].position_px == p_pos
    assert any(s.position_px == m_pos for s in no_mathias.empty_slots)


def test_present_labels_must_be_subset_of_sorted_labels() -> None:
    with pytest.raises(ValueError, match="unknown"):
        compute_layout(
            sorted_labels=["A"],
            present_labels={"B"},
            sequence_width=1920,
            sequence_height=1080,
            cam_width=1920,
            cam_height=1080,
        )


def test_empty_inputs_rejected() -> None:
    with pytest.raises(ValueError):
        compute_layout(
            sorted_labels=[],
            present_labels=set(),
            sequence_width=1920,
            sequence_height=1080,
            cam_width=1920,
            cam_height=1080,
        )
    with pytest.raises(ValueError):
        compute_layout(
            sorted_labels=["A"],
            present_labels=set(),
            sequence_width=1920,
            sequence_height=1080,
            cam_width=1920,
            cam_height=1080,
        )


def test_oversized_roster_raises() -> None:
    sorted_labels = [f"L{i:02d}" for i in range(17)]
    with pytest.raises(ValueError, match="exceeds"):
        compute_layout(
            sorted_labels=sorted_labels,
            present_labels=set(sorted_labels),
            sequence_width=1920,
            sequence_height=1080,
            cam_width=1920,
            cam_height=1080,
        )


def test_3x3_full_grid_empty_slots_are_empty() -> None:
    labels = [chr(ord("A") + i) for i in range(9)]
    layout = compute_layout(
        sorted_labels=labels,
        present_labels=set(labels),
        sequence_width=1920,
        sequence_height=1080,
        cam_width=1920,
        cam_height=1080,
    )
    assert layout.kind == "3x3"
    assert layout.empty_slots == []
    # Centre cell is at the sequence centre.
    assert layout.slots_per_label["E"].position_px == (0.0, 0.0)


def test_letterbox_scale_for_non_matching_aspect() -> None:
    # 4:3 cam in a 16:9 cell at 2x2 -> scale clamped by height (cell_h/cam_h)
    layout = compute_layout(
        sorted_labels=["A", "B", "C", "D"],
        present_labels={"A", "B", "C", "D"},
        sequence_width=1920,
        sequence_height=1080,
        cam_width=1440,
        cam_height=1080,
        layout_2up="horizontal",
    )
    cell_w, cell_h = 960, 540
    expected = min(cell_w / 1440, cell_h / 1080)
    for slot in layout.slots_per_label.values():
        assert math.isclose(slot.scale, expected)


def test_dataclasses_are_hashable_and_immutable() -> None:
    slot = GridSlot(scale=0.5, position_px=(1.0, 2.0))
    layout = GridLayout(kind="1up", slots_per_label={"A": slot}, empty_slots=[])
    assert isinstance(slot.position_px, tuple)
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        slot.scale = 0.25  # type: ignore[misc]
    # GridLayout has a dict field, so it isn't hashable, but slots are.
    assert hash(slot) is not None
    assert layout.kind == "1up"
