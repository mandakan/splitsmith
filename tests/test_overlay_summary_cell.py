"""The stage summary as one full-frame still (issue #972)."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from splitsmith import overlay_summary_cell as cell
from splitsmith.match_project import StageScorecard
from splitsmith.overlay_theme import load_theme
from splitsmith.stage_summary_data import TileShot, TileStageData

THEME = load_theme("splitsmith")


class _FakeRasterizer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append(html)
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()


def _tile() -> TileStageData:
    return TileStageData(
        label="Me",
        stage_number=3,
        shots=(TileShot(1.2, 1.2), TileShot(1.5, 0.3), TileShot(1.9, 0.4)),
        stage_time_seconds=4.5,
        scorecard=StageScorecard(hit_factor=12.0, alphas=10, charlies=1, deltas=1, misses=0),
    )


def _frame(tmp_path: Path) -> Path:
    path = tmp_path / "last.png"
    Image.new("RGB", (640, 360), (200, 200, 200)).save(path)
    return path


def test_summary_groups_are_the_grids_cell_declaration() -> None:
    """The single-shooter summary says exactly what a grid cell says: the
    hoist moved the function, not the design."""
    from splitsmith.compare import overlay_summary as grid

    assert grid._cell_groups is cell.summary_groups
    assert grid._count_elements is cell.count_elements
    assert grid._summary_scale is cell.summary_scale


def test_still_composes_text_over_the_blurred_frame(tmp_path: Path) -> None:
    fake = _FakeRasterizer()
    still = cell.build_summary_still(
        _tile(), "Me", width=320, height=180, theme=THEME, rasterizer=fake, backdrop=_frame(tmp_path)
    )
    assert still is not None
    assert still.size == (320, 180) and still.mode == "RGB"
    (html,) = fake.calls
    for text in ("Me", "12.00", "4.50s", "A10", "0.30", "Draw"):
        assert text in html
    r, g, b = still.getpixel((160, 90))
    assert 0 < r < 200 and r == g == b


def test_no_browser_keeps_the_blurred_frame_without_text(tmp_path: Path) -> None:
    still = cell.build_summary_still(
        _tile(), "Me", width=64, height=36, theme=THEME, rasterizer=None, backdrop=_frame(tmp_path)
    )
    assert still is not None
    assert still.size == (64, 36)


def test_no_frame_paints_the_surface_under_the_text(tmp_path: Path) -> None:
    still = cell.build_summary_still(
        _tile(), "Me", width=64, height=36, theme=THEME, rasterizer=_FakeRasterizer(), backdrop=None
    )
    assert still is not None
    assert still.getpixel((1, 1)) == THEME.surface


def test_nothing_to_hold_on_is_none(tmp_path: Path) -> None:
    assert (
        cell.build_summary_still(
            _tile(), "Me", width=64, height=36, theme=THEME, rasterizer=None, backdrop=None
        )
        is None
    )
