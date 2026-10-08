"""Your account brand on every video (spec 2026-10-08): the title page and
the closing card draw it when the Look has no brand of its own."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from splitsmith.account_profile import AccountProfile, JsonAccountProfileStore, brand_digest, load_brand
from splitsmith.composition import BrandMark, MatchTitle
from splitsmith.look_brand import brand_json
from splitsmith.looks import LookBrand, load_look

from .test_shooter_book import _png

LOOK = load_look("splitsmith")


def _account_brand(line: str = "Team Axell") -> BrandMark:
    store = JsonAccountProfileStore()
    name = asyncio.run(store.put_brand_logo(_png()))
    asyncio.run(store.save(AccountProfile(brand=LookBrand(logo=name, line=line))))
    brand = load_brand(store)
    assert brand is not None
    return brand


def _branded_look(tmp_path: Path, **brand: Any):
    return LOOK.model_copy(
        update={"manifest": LOOK.manifest.model_copy(update={"brand": LookBrand(**brand)})}
    )


def test_the_account_brand_marks_a_look_without_one() -> None:
    brand = _account_brand()
    data = brand_json(LOOK, "title_page", brand)
    assert data == {"logo": brand.logo_path.resolve().as_uri(), "line": "Team Axell"}
    assert brand_json(LOOK, "closing", brand) == data


def test_only_the_title_page_and_closing_card_carry_it() -> None:
    brand = _account_brand()
    for slot in ("slate", "lower_third", "transition"):
        assert brand_json(LOOK, slot, brand) is None


def test_the_look_brand_wins_as_a_whole(tmp_path: Path) -> None:
    brand = _account_brand()
    # A Look with a line and no logo: the Look's line, never the account's logo.
    look = _branded_look(tmp_path, line="Club line")
    assert brand_json(look, "title_page", brand) == {"logo": None, "line": "Club line"}


def test_no_account_brand_changes_nothing() -> None:
    assert brand_json(LOOK, "title_page") is None
    assert brand_json(LOOK, "title_page", None) is None
    assert brand_json(LOOK, "title_page", BrandMark()) is None


def test_a_symlinked_or_missing_logo_is_left_out(tmp_path: Path) -> None:
    real = tmp_path / "real.png"
    real.write_bytes(_png())
    link = tmp_path / "brand-000000000000.png"
    link.symlink_to(real)
    assert brand_json(LOOK, "title_page", BrandMark(logo_path=link, line="L")) == {"logo": None, "line": "L"}
    assert brand_json(LOOK, "title_page", BrandMark(logo_path=tmp_path / "gone.png")) is None


def test_the_card_context_carries_it_and_off_is_the_card_it_always_was() -> None:
    from splitsmith.look_template import template_digest
    from splitsmith.looks import template_for
    from splitsmith.overlay_card import card_context
    from splitsmith.overlay_theme import theme_for

    brand = _account_brand()
    theme = theme_for(LOOK)
    plain = MatchTitle(text="Bromma Open", credit=True)
    branded = replace(plain, brand=brand)

    def ctx(card: MatchTitle):
        return card_context(
            card,
            slot="closing",
            width=640,
            height=360,
            fps=30.0,
            theme=theme,
            brand=brand_json(LOOK, "closing", card.brand),
        )

    template = template_for(LOOK, "closing", "default")
    off = ctx(plain)
    assert "brand" not in off.data
    assert template_digest(template, off, fps=30.0, engine_version="x") == template_digest(
        template, ctx(MatchTitle(text="Bromma Open", credit=True)), fps=30.0, engine_version="x"
    )
    assert ctx(branded).data["brand"]["line"] == "Team Axell"


def test_the_match_export_threads_the_brand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui import match_exports as match_exports_mod
    from tests.test_ui_server import _seed_match_export_project, _stub_match_export_probe, _wait_for_job

    brand = _account_brand()
    client, _root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)
    seen: list[Any] = []
    real = match_exports_mod.export_match

    def capture(*args: object, **kwargs: object) -> object:
        seen.append(kwargs["request"])
        return real(*args, **kwargs)

    monkeypatch.setattr(match_exports_mod, "export_match", capture)
    body = {"stage_numbers": [1], "include_overlay": False, "title_page": True}
    for extra in ({}, {"account_brand": False}):
        resp = client.post("/api/shooters/me/export/match", json={**body, **extra})
        assert resp.status_code == 200, resp.text
        assert _wait_for_job(client, resp.json()["id"])["status"] == "succeeded"
    assert seen[0].account_brand == brand
    assert seen[1].account_brand is None


def test_the_grid_cards_carry_the_brand_unless_off() -> None:
    from splitsmith.compare.cards import CardOptions, title_cards
    from splitsmith.match_model import Match

    brand = _account_brand()
    match = Match(name="Bromma Open")
    title, closing = title_cards(match, CardOptions(title_page=True, closing_card=True), brand=brand)
    assert title.brand == brand and closing.brand == brand
    title, _ = title_cards(match, CardOptions(title_page=True, account_brand=False), brand=brand)
    assert title.brand is None


def test_the_request_and_preset_default_on() -> None:
    from splitsmith.export_presets import ExportPresetBody
    from splitsmith.ui.exports_api import CompareGridRequest, MatchExportRequest

    assert MatchExportRequest(stage_numbers=[1]).account_brand is True
    assert CompareGridRequest(stage_numbers=[1], audio_from="a").account_brand is True
    assert ExportPresetBody().account_brand is True
    assert ExportPresetBody.model_validate({}).account_brand is True


def test_the_match_cli_reads_the_local_brand_unless_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith.cli import app
    from tests.test_match_cli_export import _capture_mp4, _seed, runner

    brand = _account_brand()
    root = _seed(tmp_path)
    captured = _capture_mp4(monkeypatch)
    args = ["match", "export", str(root), "--shooter", "me", "--format", "mp4", "--title-page"]
    assert runner.invoke(app, [*args, "-o", str(tmp_path / "a.mp4")]).exit_code == 0
    assert captured["comp"].title_page.brand == brand
    assert runner.invoke(app, [*args, "--no-account-brand", "-o", str(tmp_path / "b.mp4")]).exit_code == 0
    assert captured["comp"].title_page.brand is None


def test_the_preview_key_moves_with_the_brand_only_when_drawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith.ui import export_preview_api
    from tests.test_shooter_book_wiring import _NoopRasterizer
    from tests.test_ui_server import _seed_match_export_project

    client, _root = _seed_match_export_project(tmp_path)
    keys: list[str] = []
    drawn: list[Any] = []
    real_key = export_preview_api.preview_key

    def spy_key(*args: Any, **kwargs: Any) -> str:
        keys.append(real_key(*args, **kwargs))
        return keys[-1]

    def fake_render(spec: Any, **kwargs: Any) -> bytes:
        drawn.append(kwargs.get("brand"))
        return _png()

    monkeypatch.setattr(export_preview_api, "preview_key", spy_key)
    monkeypatch.setattr(export_preview_api, "render_preview", fake_render)
    monkeypatch.setattr(export_preview_api, "ChromiumRasterizer", _NoopRasterizer)
    title = {"card": "title", "stage_number": 1, "width": 480}
    client.post("/api/shooters/me/export-preview", json=title)
    brand = _account_brand()
    client.post("/api/shooters/me/export-preview", json=title)
    client.post("/api/shooters/me/export-preview", json={**title, "account_brand": False})
    # Off is the first picture again: the same key, served from the cache.
    assert keys[0] != keys[1] and keys[2] == keys[0]
    assert drawn == [None, brand]
    # A card that draws no brand never reads it.
    client.post("/api/shooters/me/export-preview", json={**title, "card": "slate"})
    assert drawn == [None, brand, None]
    assert json.loads(json.dumps(brand_digest(brand)))


def test_chromium_draws_the_account_brand_as_the_corner_mark_and_moves_nothing(tmp_path: Path) -> None:
    from PIL import Image, ImageChops

    from splitsmith.overlay_card import build_card_still
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    logo = tmp_path / "brand-0123456789ab.png"
    Image.new("RGB", (200, 200), (0, 200, 255)).save(logo)
    plain = MatchTitle(text="Bromma Open", info=("2026-05-01",))
    branded = replace(plain, brand=BrandMark(logo_path=logo, line="Team Axell"))
    try:
        with ChromiumRasterizer() as raster:
            kw = {
                "width": 1280,
                "height": 720,
                "fps": 30.0,
                "look": LOOK,
                "rasterizer": raster,
                "backdrop": None,
            }
            off = build_card_still(plain, slot="title_page", **kw).convert("RGB")
            on = build_card_still(branded, slot="title_page", **kw).convert("RGB")
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")
    box = ImageChops.difference(off, on).getbbox()
    assert box is not None and box[2] < 1280 // 3 and box[3] < 720 // 4, box
    assert on.getpixel((70, 70)) == (0, 200, 255)
