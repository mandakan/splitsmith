"""Generated cards: declaration and still composition (issue #973)."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from splitsmith import overlay_card
from splitsmith.composition import MatchTitle, TitleCard
from splitsmith.overlay_layout import Anchor, Emphasis, Role
from splitsmith.overlay_theme import load_theme

THEME = load_theme("splitsmith")


class _FakeRasterizer:
    def __init__(self, *, fill: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self._fill = fill

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append((html, width, height))
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), self._fill).save(buf, format="PNG")
        return buf.getvalue()


class _BoomRasterizer:
    def png(self, html: str, *, width: int, height: int) -> bytes:
        raise RuntimeError("rasterize boom")


def _frame(tmp_path: Path, color: tuple[int, int, int] = (200, 200, 200)) -> Path:
    path = tmp_path / "frame.png"
    Image.new("RGB", (640, 360), color).save(path)
    return path


# --- declaration ------------------------------------------------------------


def test_match_title_groups_lead_with_the_name_then_one_line_per_info() -> None:
    groups = overlay_card.card_groups(MatchTitle(text="Bromma Classifier", info=("2026-05-01", "M. Axell")))
    assert [g.anchor for g in groups] == [Anchor.MIDDLE_CENTER] * 3
    assert all(g.align == "center" for g in groups)
    assert groups[0].elements[0].role is Role.IDENTITY
    assert groups[0].elements[0].text == "Bromma Classifier"
    assert [g.elements[0].text for g in groups[1:]] == ["2026-05-01", "M. Axell"]
    assert all(g.elements[0].role is Role.DETAIL for g in groups[1:])


def test_slate_groups_are_the_same_shape_as_the_title_page() -> None:
    groups = overlay_card.card_groups(
        TitleCard(text="Stage 3: Speed", duration_seconds=1.5, info=("24 rounds",))
    )
    assert [g.anchor for g in groups] == [Anchor.MIDDLE_CENTER, Anchor.MIDDLE_CENTER]
    assert groups[0].elements[0].role is Role.IDENTITY
    assert groups[1].elements[0].text == "24 rounds"


def test_lower_third_sits_bottom_left_on_a_plate_with_the_name_on_top() -> None:
    """Bottom-anchored groups stack away from the edge in declaration
    order, so the name -- read first, drawn highest -- is declared last
    and the info lines before it, last line nearest the edge."""
    groups = overlay_card.card_groups(
        TitleCard(text="Stage 3", duration_seconds=2.0, style="lower-third", info=("24 rounds", "Comstock"))
    )
    assert all(g.anchor is Anchor.BOTTOM_LEFT for g in groups)
    assert all(g.align == "left" for g in groups)
    assert [g.elements[0].text for g in groups] == ["Comstock", "24 rounds", "Stage 3"]
    assert groups[-1].elements[0].role is Role.HEADLINE
    assert groups[-1].elements[0].emphasis is Emphasis.PLATE
    assert all(g.elements[0].role is Role.DETAIL for g in groups[:-1])


def test_a_card_with_no_info_is_one_group() -> None:
    assert len(overlay_card.card_groups(MatchTitle(text="x"))) == 1


# --- still composition -------------------------------------------------------


def test_still_is_canvas_sized_rgb_and_rasterizes_the_text(tmp_path: Path) -> None:
    fake = _FakeRasterizer()
    still = overlay_card.build_card_still(
        MatchTitle(text="Bromma Classifier", info=("2026-05-01",)),
        width=320,
        height=180,
        theme=THEME,
        rasterizer=fake,
        backdrop=None,
    )
    assert still is not None
    assert still.mode == "RGB"
    assert still.size == (320, 180)
    ((html, width, height),) = fake.calls
    assert (width, height) == (320, 180)
    assert "Bromma Classifier" in html
    assert "2026-05-01" in html


def test_no_backdrop_paints_the_theme_surface(tmp_path: Path) -> None:
    still = overlay_card.build_card_still(
        MatchTitle(text="x"), width=64, height=36, theme=THEME, rasterizer=_FakeRasterizer(), backdrop=None
    )
    assert still is not None
    assert still.getpixel((1, 1)) == THEME.surface


def test_backdrop_is_blurred_and_dimmed(tmp_path: Path) -> None:
    frame = _frame(tmp_path, (200, 200, 200))
    still = overlay_card.build_card_still(
        MatchTitle(text="x"), width=64, height=36, theme=THEME, rasterizer=_FakeRasterizer(), backdrop=frame
    )
    assert still is not None
    r, g, b = still.getpixel((32, 18))
    # Dimmed: darker than the source grey, but not black -- it is still
    # recognisably the shooter's own frame.
    assert 0 < r < 200 and r == g == b


def test_unreadable_backdrop_falls_back_to_the_surface(tmp_path: Path) -> None:
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png")
    still = overlay_card.build_card_still(
        MatchTitle(text="x"), width=64, height=36, theme=THEME, rasterizer=_FakeRasterizer(), backdrop=bad
    )
    assert still is not None
    assert still.getpixel((1, 1)) == THEME.surface


def test_rasterizer_failure_skips_the_card(tmp_path: Path) -> None:
    """A card is its text. With no text there is nothing to hold on, so
    the still is ``None`` and the caller drops the segment -- unlike the
    summary, which keeps its blurred freeze without text."""
    still = overlay_card.build_card_still(
        MatchTitle(text="x"), width=64, height=36, theme=THEME, rasterizer=_BoomRasterizer(), backdrop=None
    )
    assert still is None


def test_lower_third_still_is_transparent_rgba(tmp_path: Path) -> None:
    fake = _FakeRasterizer(fill=(255, 0, 0, 255))
    still = overlay_card.build_lower_third(
        TitleCard(text="Stage 3", duration_seconds=2.0, style="lower-third"),
        width=64,
        height=36,
        theme=THEME,
        rasterizer=fake,
    )
    assert still is not None
    assert still.mode == "RGBA"
    assert still.size == (64, 36)
