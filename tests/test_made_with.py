""" "Made with splitsmith": a small line and mark at the bottom of the closing
card, on by default and turned off under Details (remembered in presets).
The title page never carries it, and a closing card with it off renders
exactly as before."""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import match_model
from splitsmith.compare.cards import CardOptions, title_cards
from splitsmith.composition import MatchTitle
from splitsmith.export_presets import ExportPresetBody
from splitsmith.overlay_card import card_context
from splitsmith.overlay_theme import load_theme
from splitsmith.ui.exports_api import CompareGridRequest, MatchExportRequest
from splitsmith.ui.match_exports import MatchExportRequestData

CREDIT = {"text": "Made with splitsmith"}


def _context(card: MatchTitle, slot: str) -> dict:
    return card_context(card, slot=slot, width=1280, height=720, fps=30, theme=load_theme("splitsmith")).data


def test_only_the_closing_card_carries_the_credit() -> None:
    card = MatchTitle(text="Höstfinalen", credit=True)
    assert _context(card, "closing")["credit"] == CREDIT
    assert "credit" not in _context(card, "title_page")
    assert "credit" not in _context(replace(card, credit=False), "closing")


def test_the_credit_is_on_by_default_everywhere_a_user_starts() -> None:
    assert ExportPresetBody().made_with is True
    assert MatchExportRequest(stage_numbers=[1]).made_with is True
    assert CompareGridRequest(stage_numbers=[1], audio_from="a").made_with is True
    assert MatchExportRequestData.__dataclass_fields__["made_with"].default is True
    assert CardOptions().made_with is True


def test_the_grid_closing_card_carries_it_and_the_title_page_never() -> None:
    match = match_model.Match(name="Höstfinalen")
    title, closing = title_cards(match, CardOptions(title_page=True, closing_card=True))
    assert title is not None and closing is not None
    assert closing.credit is True and title.credit is False
    _title, closing = title_cards(match, CardOptions(closing_card=True, made_with=False))
    assert closing is not None and closing.credit is False


def _render(card: MatchTitle) -> Image.Image:
    from splitsmith import looks
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    look = looks.load_look("splitsmith")
    ctx = card_context(card, slot="closing", width=1280, height=720, fps=30, theme=load_theme("splitsmith"))
    try:
        with ChromiumRasterizer() as raster:
            png = raster.render_template(
                looks.template_for(look, "closing"), context=ctx, width=1280, height=720
            )
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")
    return Image.open(io.BytesIO(png)).convert("RGBA")


def _drawn_rows(im: Image.Image, top: int) -> int:
    px = im.load()
    return sum(1 for y in range(top, im.height) if any(px[x, y][3] > 0 for x in range(0, im.width, 2)))


def test_the_credit_sits_at_the_bottom_and_off_is_the_card_it_always_was() -> None:
    plain = _render(MatchTitle(text="Höstfinalen XI", info=("2026-06-27",)))
    credited = _render(MatchTitle(text="Höstfinalen XI", info=("2026-06-27",), credit=True))
    assert _drawn_rows(plain, 640) == 0
    assert _drawn_rows(credited, 640) > 10

    # The card's text stays centred in the space above the credit: up by
    # half the credit's band, no more.
    def title_top(im: Image.Image) -> int:
        px = im.load()
        for y in range(im.height):
            if any(px[x, y][3] > 200 and min(px[x, y][:3]) > 230 for x in range(0, im.width, 2)):
                return y
        return im.height

    shift = title_top(plain) - title_top(credited)
    assert 10 < shift < 45, shift


# --- the request layer ------------------------------------------------------------------


def test_the_match_export_threads_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui import match_exports as match_exports_mod

    from .test_ui_server import _seed_match_export_project, _stub_match_export_probe, _wait_for_job

    http, _root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)
    seen: list = []
    real = match_exports_mod.export_match

    def capture(*args: object, **kwargs: object) -> object:
        seen.append(kwargs["request"])
        return real(*args, **kwargs)

    monkeypatch.setattr(match_exports_mod, "export_match", capture)
    body = {"stage_numbers": [1], "include_overlay": False, "closing_card": True}
    for extra in ({}, {"made_with": False}):
        r = http.post("/api/shooters/me/export/match", json={**body, **extra})
        assert r.status_code == 200, r.text
        assert _wait_for_job(http, r.json()["id"])["status"] == "succeeded"
    assert [req.made_with for req in seen] == [True, False]


def test_the_closing_preview_shows_the_credit_unless_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith import runtime as runtime_module
    from splitsmith.ui import export_preview_api

    from .test_look_editor_api import _factory, _Recorder
    from .test_ui_server import _seed_match_export_project

    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _factory)
    monkeypatch.setenv(runtime_module.ENV_CACHE_DIR, str(tmp_path / "cache"))
    runtime_module._clear_runtime_cache()
    _Recorder.calls = []
    http, _root = _seed_match_export_project(tmp_path, stage_count=1)
    body = {"card": "closing", "stage_number": 1, "width": 480}
    assert http.post("/api/shooters/me/export-preview", json=body).status_code == 200
    assert _Recorder.calls[-1]["data"]["credit"] == CREDIT
    assert http.post("/api/shooters/me/export-preview", json={**body, "made_with": False}).status_code == 200
    assert "credit" not in _Recorder.calls[-1]["data"]  # rendered again, not the cached credited card
    runtime_module._clear_runtime_cache()


def test_a_crowded_closing_card_keeps_its_text_clear_of_the_credit(tmp_path: Path) -> None:
    """Five info lines under an event logo: the card's white text fits above
    the credit's band instead of running under it."""
    logo = tmp_path / "event-0123456789ab.png"
    Image.new("RGB", (64, 64), (20, 200, 60)).save(logo)
    info = ("2026-06-27", "Mathias Axell", "Level III, squad 4", "Production Optics", "Bromma Pistolklubb")
    im = _render(MatchTitle(text="Stockholm IPSC Open 2026", info=info, logo=logo, credit=True))
    px = im.load()
    band_top = 720 - round(720 * 0.04) - round(720 * 0.026 * 1.2) - 8
    white = [
        (x, y)
        for y in range(band_top, 720)
        for x in range(0, 1280, 2)
        if px[x, y][3] > 200 and min(px[x, y][:3]) > 230
    ]
    assert white == [], white[:5]
