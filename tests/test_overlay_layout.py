"""The shared layout vocabulary: anchors, roles, and one type scale."""

import pytest

from splitsmith.overlay_layout import (
    Anchor,
    CellScale,
    Element,
    Emphasis,
    Flow,
    Group,
    Role,
)


@pytest.mark.parametrize("cell_height", [90, 180, 270, 360, 540, 720, 1080])
def test_live_primary_and_pad_match_what_the_grid_computes_today(cell_height):
    """The live sprite and the drawtext clock adopt this resolver without
    changing a pixel, so these two formulas are not free to drift.

    ``overlay_sprites.render_state`` computes ``big`` and ``pad`` inline
    today and ``mp4_grid._stage_overlay_plan`` / ``_clock_pad`` repeat
    them. If either formula changes here, the sprite moves under the
    clock and the two halves of the overlay stop lining up.
    """
    scale = CellScale.for_cell(cell_height)
    assert scale.live_primary == max(48, cell_height // 14)
    assert scale.pad == max(24, cell_height // 36)


@pytest.mark.parametrize("cell_height", [90, 360, 1080])
def test_every_size_is_at_least_the_legibility_floor(cell_height):
    """Below 12px a further shrink reads as noise rather than smaller
    text -- the same floor ``overlay_sprites._MIN_FONT_SIZE`` and
    ``overlay_summary._MIN_FONT_SIZE`` already enforce."""
    scale = CellScale.for_cell(cell_height)
    for role in Role:
        assert scale.size_for(role) >= 12
    assert scale.caption >= 12


def test_sizes_are_ordered_by_prominence():
    """A headline must outrank a detail at every cell size, or the
    hierarchy the composition depends on does not exist."""
    scale = CellScale.for_cell(360)
    assert scale.headline > scale.detail
    assert scale.identity > scale.detail
    assert scale.verdict > scale.detail
    assert scale.caption <= scale.detail


def test_size_for_covers_every_role():
    scale = CellScale.for_cell(360)
    for role in Role:
        assert isinstance(scale.size_for(role), int)


def test_a_group_holds_elements_at_one_anchor():
    group = Group(
        anchor=Anchor.BOTTOM_LEFT,
        flow=Flow.ROW,
        elements=(
            Element(role=Role.HEADLINE, text="4.50", caption="TIME"),
            Element(role=Role.HEADLINE, text="12.00", caption="HF"),
        ),
    )
    assert group.anchor is Anchor.BOTTOM_LEFT
    assert len(group.elements) == 2
    assert group.elements[0].caption == "TIME"
    assert group.elements[0].emphasis is Emphasis.PLAIN


def test_elements_and_groups_are_frozen():
    """Composition is data. A renderer that could mutate a declaration
    would make the declaration untrustworthy."""
    element = Element(role=Role.DETAIL, text="Draw 0.50")
    with pytest.raises(AttributeError):
        element.text = "tampered"
    group = Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW, elements=(element,))
    with pytest.raises(AttributeError):
        group.anchor = Anchor.TOP_RIGHT
