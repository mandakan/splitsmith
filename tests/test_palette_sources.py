"""``splitsmith.palette_sources``: the colours a palette is chosen against
(issue #1273), from the stage's frames and from the shooter's logo, and the
route that serves them. Images are drawn here, never fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from splitsmith import palette_sources
from splitsmith.ui import palette_api

from .test_ui_server import _seed_match_export_project


def _two_tone(left: tuple[int, int, int], right: tuple[int, int, int], split: float = 0.75) -> Image.Image:
    image = Image.new("RGB", (400, 200), right)
    ImageDraw.Draw(image).rectangle((0, 0, int(400 * split) - 1, 199), fill=left)
    return image


def test_dominant_colours_find_the_areas_and_their_share() -> None:
    swatches = palette_sources.dominant_colours([_two_tone((60, 110, 40), (150, 120, 80))], k=4)
    assert swatches[0].share == pytest.approx(0.75, abs=0.03)
    assert all(abs(a - b) <= 6 for a, b in zip(swatches[0].rgb, (60, 110, 40), strict=True))
    assert any(all(abs(a - b) <= 6 for a, b in zip(s.rgb, (150, 120, 80), strict=True)) for s in swatches)
    assert sum(s.share for s in swatches) == pytest.approx(1.0, abs=1e-6)


def test_the_same_pictures_give_the_same_swatches() -> None:
    pictures = [_two_tone((30, 30, 30), (200, 40, 40), 0.5), _two_tone((20, 140, 200), (240, 240, 240), 0.3)]
    assert palette_sources.dominant_colours(pictures) == palette_sources.dominant_colours(pictures)


def test_a_logo_ignores_its_transparent_pixels() -> None:
    logo = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(logo).ellipse((50, 50, 150, 150), fill=(220, 30, 30, 255))
    swatches = palette_sources.dominant_colours([logo])
    assert swatches[0].share > 0.95 and swatches[0].rgb[0] > 200 and swatches[0].rgb[1] < 50


def test_the_average_colour_is_the_mean_of_the_pictures() -> None:
    average = palette_sources.average_colour(
        [Image.new("RGB", (10, 10), (0, 0, 0)), Image.new("RGB", (10, 10), (200, 100, 50))]
    )
    assert average == (100, 50, 25)
    assert palette_sources.average_colour([]) is None


# --- the route -----------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path):
    client, _root = _seed_match_export_project(tmp_path, stage_count=2)
    return client


def test_the_route_reads_the_stages_frames(client, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    def frames(project, root, stage_number, *, ffmpeg_binary, work):  # type: ignore[no-untyped-def]
        seen.append(stage_number)
        return [_two_tone((60, 110, 40), (150, 120, 80))]

    monkeypatch.setattr(palette_api, "stage_frames", frames)
    r = client.post("/api/shooters/me/palette-sources", json={"stage_numbers": [1, 2]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert seen == [1, 2]
    assert body["footage"][0]["share"] == pytest.approx(0.75, abs=0.03)
    assert len(body["average"]) == 3
    assert body["logo"] == []


def test_without_footage_the_footage_is_empty_not_an_error(client) -> None:
    r = client.post("/api/shooters/me/palette-sources", json={"stage_numbers": [1]})
    assert r.status_code == 200, r.text
    assert r.json() == {"footage": [], "average": None, "logo": []}


def test_the_route_bounds_the_stage_list(client) -> None:
    r = client.post("/api/shooters/me/palette-sources", json={"stage_numbers": list(range(1, 20))})
    assert r.status_code == 422
