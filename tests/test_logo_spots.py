"""Logo spots: the presets, the CLI's parser and the Pillow composite the
summaries use."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from splitsmith.logo_spots import DEFAULT_LOGO_SPOTS, PRESETS, parse_logo_spots, paste_logo


def _logo(path: Path, size: tuple[int, int] = (100, 100)) -> Path:
    Image.new("RGBA", size, (0, 200, 0, 255)).save(path)
    return path


def test_polished_is_the_default_and_cards_is_today() -> None:
    assert DEFAULT_LOGO_SPOTS == PRESETS["polished"] == {"wipe", "summaries"}
    assert PRESETS["cards"] == frozenset()


@pytest.mark.parametrize(
    ("text", "spots"),
    [
        ("polished", {"wipe", "summaries"}),
        ("cards", set()),
        ("none", set()),
        ("wipe", {"wipe"}),
        (" Summaries , wipe ", {"wipe", "summaries"}),
    ],
)
def test_parse_logo_spots(text: str, spots: set[str]) -> None:
    assert parse_logo_spots(text) == spots


def test_parse_logo_spots_names_an_unknown_spot() -> None:
    with pytest.raises(ValueError, match="'banner'"):
        parse_logo_spots("wipe,banner")


def test_paste_logo_lands_top_right_inside_the_margin(tmp_path: Path) -> None:
    canvas = Image.new("RGB", (1600, 900), (0, 0, 0))
    out = paste_logo(canvas, _logo(tmp_path / "l.png"))
    side = round(900 * 0.09)
    margin = round(900 * 0.04)
    # The logo's square: right edge one margin in, top one margin down.
    assert out.getpixel((1600 - margin - side // 2, margin + side // 2)) == (0, 200, 0)
    assert out.getpixel((1600 - margin + 2, margin + side // 2)) == (0, 0, 0)
    assert out.getpixel((800, 450)) == (0, 0, 0)
    assert out.size == canvas.size and out.mode == "RGB"


def test_paste_logo_into_a_box(tmp_path: Path) -> None:
    canvas = Image.new("RGB", (1600, 900), (0, 0, 0))
    out = paste_logo(canvas, _logo(tmp_path / "l.png"), box=(0, 450, 800, 450))
    side = round(450 * 0.09)
    margin = round(450 * 0.04)
    assert out.getpixel((800 - margin - side // 2, 450 + margin + side // 2)) == (0, 200, 0)
    assert out.getpixel((1600 - margin - side // 2, margin + side // 2)) == (0, 0, 0)


def test_a_wide_logo_is_capped_at_twice_its_height(tmp_path: Path) -> None:
    canvas = Image.new("RGB", (1600, 900), (0, 0, 0))
    out = paste_logo(canvas, _logo(tmp_path / "w.png", (1000, 100)))
    side = round(900 * 0.09)
    margin = round(900 * 0.04)
    assert out.getpixel((1600 - margin - 2 * side + 3, margin + 3)) == (0, 200, 0)
    assert out.getpixel((1600 - margin - 2 * side - 3, margin + 3)) == (0, 0, 0)


def test_a_missing_or_symlinked_logo_leaves_the_still_alone(tmp_path: Path) -> None:
    canvas = Image.new("RGB", (320, 180), (9, 9, 9))
    assert paste_logo(canvas, tmp_path / "gone.png") is canvas
    link = tmp_path / "link.png"
    link.symlink_to(_logo(tmp_path / "real.png"))
    assert paste_logo(canvas, link) is canvas
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    assert paste_logo(canvas, broken) is canvas
