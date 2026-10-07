"""The event's own logo (the branding work, PR 3): set once per match, kept
as ``<match>/identity/event-<12hex>.<ext>`` and ``match.json``'s
``branding.event_logo``, synced like a shooter's logo, and drawn as the
centrepiece on the title page and the closing card (your brand is the corner mark;
a shooter's logo is never replaced by it)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from splitsmith import match_model
from splitsmith.composition import MatchTitle
from splitsmith.identity import EVENT_LOGO_DIR, event_logo_name
from splitsmith.overlay_card import card_context
from splitsmith.overlay_theme import load_theme
from splitsmith.sync.plan import build_push_plan
from splitsmith.sync.state import SyncState, load_sync_state
from splitsmith.ui import identity_media, sync_api

from .test_identity_media import _FakeStorage
from .test_sync_plan import _build_basic_match
from .test_sync_push import _build_match, _FakeHosted, run_push
from .test_ui_server import _match_create_app, _MatchClient


def _png(colour: tuple[int, int, int] = (20, 200, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(buf, format="PNG")
    return buf.getvalue()


# --- the match --------------------------------------------------------------------------


def test_an_event_logo_name_with_a_trailing_newline_is_refused() -> None:
    with pytest.raises(ValueError):
        match_model.MatchBranding(event_logo="event-0123456789ab.png\n")


def test_the_match_names_its_event_logo_by_content() -> None:
    branding = match_model.MatchBranding(event_logo="event-0123456789ab.png")
    assert branding.event_logo == "event-0123456789ab.png"
    for bad in ("logo-0123456789ab.png", "../event.png", "event-0123456789ab.svg"):
        with pytest.raises(ValueError):
            match_model.MatchBranding(event_logo=bad)
    assert event_logo_name(b"x", "png").startswith("event-")


# --- the routes (local) ------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path):
    root = tmp_path / "match"
    http = _MatchClient(_match_create_app(project_root=root, project_name="Event"))
    return http, root, f"/api/matches/{match_model.Match.load(root).match_id}/match/branding/event-logo"


def test_the_event_logo_is_uploaded_served_and_removed(client) -> None:
    http, root, url = client
    r = http.post(url, files={"file": ("x.bin", _png(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    name = r.json()["event_logo"]
    assert name.startswith("event-") and (root / EVENT_LOGO_DIR / name).read_bytes() == _png()
    assert match_model.Match.load(root).branding.event_logo == name
    served = http.get(url)
    assert served.status_code == 200 and served.content == _png()
    assert served.headers["x-content-type-options"] == "nosniff"
    # Same URL, new bytes after a replace: the browser must revalidate.
    assert "no-cache" in served.headers["cache-control"]
    # A new logo replaces the file; removing clears both.
    r2 = http.post(url, files={"file": ("y.png", _png((200, 20, 20)), "image/png")})
    assert not (root / EVENT_LOGO_DIR / name).exists()
    assert http.delete(url).status_code == 200
    assert match_model.Match.load(root).branding.event_logo is None
    assert not (root / EVENT_LOGO_DIR / r2.json()["event_logo"]).exists()
    assert http.get(url).status_code == 404


def test_a_bad_event_logo_is_a_422(client) -> None:
    http, _root, url = client
    r = http.post(url, files={"file": ("x.png", b"<svg/>", "image/png")})
    assert r.status_code == 422, r.text


# --- sync -----------------------------------------------------------------------------------


def test_the_event_logo_is_pushed_under_the_match_and_strays_are_not(tmp_path: Path) -> None:
    root, _slug = _build_basic_match(tmp_path)
    name = event_logo_name(b"logo", "png")
    (root / EVENT_LOGO_DIR).mkdir()
    (root / EVENT_LOGO_DIR / name).write_bytes(b"logo")
    (root / EVENT_LOGO_DIR / "notes.txt").write_text("no", encoding="utf-8")
    plan = build_push_plan(root, sync_state=SyncState())
    match_id = match_model.Match.load(root).match_id
    keys = {item.remote_key for item in plan.media}
    assert f"matches/{match_id}/{EVENT_LOGO_DIR}/{name}" in keys
    assert not any(key.endswith("notes.txt") for key in keys)


def test_a_replaced_event_logo_is_gcd(tmp_path: Path) -> None:
    root, match_id = _build_match(tmp_path)
    old = event_logo_name(b"one", "png")
    (root / EVENT_LOGO_DIR).mkdir()
    (root / EVENT_LOGO_DIR / old).write_bytes(b"one")
    fake = _FakeHosted()
    run_push(root, client=fake.clients())
    old_key = f"matches/{match_id}/{EVENT_LOGO_DIR}/{old}"
    assert old_key in load_sync_state(root).items
    (root / EVENT_LOGO_DIR / old).unlink()
    (root / EVENT_LOGO_DIR / event_logo_name(b"two", "png")).write_bytes(b"two")
    run_push(root, client=fake.clients())
    assert f"media_delete:{old_key}" in fake.calls


def test_the_hosted_key_gate_takes_the_event_logo_and_nothing_else_at_match_level() -> None:
    sync_api._validate_media_key("matches/m1/identity/event-0123456789ab.png", "m1")
    assert sync_api.deletable_media_shape("matches/m1/identity/event-0123456789ab.png")
    for bad in (
        "matches/m1/identity/logo-0123456789ab.png",
        "matches/m1/identity/event-0123456789ab.svg",
        "matches/m1/identity/../x.png",
        "matches/m2/identity/event-0123456789ab.png",
    ):
        with pytest.raises(HTTPException) as info:
            sync_api._validate_media_key(bad, "m1")
        assert info.value.status_code == 422


def test_hosted_mirrors_the_event_logo_down_for_a_render(tmp_path: Path) -> None:
    name = event_logo_name(b"x", "png")
    storage = _FakeStorage({f"matches/m1/{EVENT_LOGO_DIR}/{name}": b"logo-bytes"})
    branding = match_model.MatchBranding(event_logo=name)
    path = identity_media.ensure_local_event_logo(branding, tmp_path, storage=storage, match_id="m1")
    assert path == tmp_path / EVENT_LOGO_DIR / name and path.read_bytes() == b"logo-bytes"
    broken = _FakeStorage(boom=True)
    assert (
        identity_media.ensure_local_event_logo(branding, tmp_path / "b", storage=broken, match_id="m1")
        is None
    )


# --- the cards ----------------------------------------------------------------------------


def test_the_title_page_and_the_closing_card_carry_the_event_logo(tmp_path: Path) -> None:
    logo = tmp_path / "event-0123456789ab.png"
    logo.write_bytes(_png())
    theme = load_theme("splitsmith")
    card = MatchTitle(text="Höstfinalen XI", logo=logo)
    for slot in ("title_page", "closing"):
        ctx = card_context(card, slot=slot, width=640, height=360, fps=30, theme=theme)
        assert ctx.data["event"] == {"logo": logo.resolve().as_uri()}
    plain = card_context(MatchTitle(text="X"), slot="title_page", width=640, height=360, fps=30, theme=theme)
    assert "event" not in plain.data


def test_a_shooter_never_falls_back_to_a_match_logo() -> None:
    import inspect

    from splitsmith.identity import resolve_identity

    assert "match_logo" not in inspect.signature(resolve_identity).parameters
    assert "match_logo" not in inspect.signature(identity_media.resolved_identity_for).parameters


def _render_title(tmp_path: Path, logo: Path | None, brand: dict | None = None) -> Image.Image:
    from splitsmith import looks
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    theme = load_theme("splitsmith")
    look = looks.load_look("splitsmith")
    ctx = card_context(
        MatchTitle(text="Höstfinalen XI", logo=logo),
        slot="title_page",
        width=1280,
        height=720,
        fps=30,
        theme=theme,
    )
    if brand is not None:
        ctx.data["brand"] = brand
    try:
        with ChromiumRasterizer() as raster:
            png = raster.render_template(
                looks.template_for(look, "title_page"), context=ctx, width=1280, height=720
            )
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")
    return Image.open(io.BytesIO(png)).convert("RGBA")


def _title_top(im: Image.Image) -> int:
    px = im.load()
    for y in range(im.height):
        for x in range(0, im.width, 2):
            r, g, b, a = px[x, y]
            if a > 200 and r > 230 and g > 230 and b > 230:
                return y
    return im.height


def test_the_event_logo_is_the_title_pages_centrepiece(tmp_path: Path) -> None:
    logo = tmp_path / "event-0123456789ab.png"
    logo.write_bytes(_png((20, 200, 60)))
    im = _render_title(tmp_path, logo)
    plain = _render_title(tmp_path, None)
    r, g, b, a = im.getpixel((640, 130))
    assert a > 200 and g > 150 and r < 80, (r, g, b, a)
    assert im.getpixel((70, 70))[3] == 0  # top-left is your brand's
    assert im.getpixel((1210, 70))[3] == 0  # top-right is the shooters'
    assert _title_top(im) > _title_top(plain) + 40  # the text moved below it


def test_your_brand_and_the_event_logo_share_the_title_page(tmp_path: Path) -> None:
    event = tmp_path / "event-0123456789ab.png"
    event.write_bytes(_png((20, 200, 60)))
    brand = tmp_path / "brand-0123456789ab.png"
    brand.write_bytes(_png((220, 30, 30)))
    im = _render_title(tmp_path, event, {"logo": brand.resolve().as_uri(), "line": "Bromma PK"})
    r, g, b, a = im.getpixel((70, 70))
    assert a > 200 and r > 180 and g < 80, (r, g, b, a)
    r, g, b, a = im.getpixel((640, 130))
    assert a > 200 and g > 150 and r < 80, (r, g, b, a)


def _half_clear_png(colour: tuple[int, int, int]) -> bytes:
    """Left half fully transparent, right half ``colour``."""
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for x in range(32, 64):
        for y in range(64):
            im.putpixel((x, y), (*colour, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_logos_keep_their_transparency_on_the_card(tmp_path: Path) -> None:
    """The event logo (centre) and your brand (corner) are drawn with their
    alpha: a transparent half leaves the card clear behind it."""
    event = tmp_path / "event-0123456789ab.png"
    event.write_bytes(_half_clear_png((20, 200, 60)))
    brand = tmp_path / "brand-0123456789ab.png"
    brand.write_bytes(_half_clear_png((220, 30, 30)))
    im = _render_title(tmp_path, event, {"logo": brand.resolve().as_uri()})
    # Event logo: 173 px square centred on x=640 from y=58.
    assert im.getpixel((600, 140))[3] == 0, im.getpixel((600, 140))
    r, g, b, a = im.getpixel((690, 140))
    assert a > 200 and g > 150 and r < 80, (r, g, b, a)
    # Brand: 86 px square at (29, 29).
    assert im.getpixel((45, 70))[3] == 0, im.getpixel((45, 70))
    r, g, b, a = im.getpixel((100, 70))
    assert a > 200 and r > 180 and g < 80, (r, g, b, a)


# --- the event logo reaches every export ------------------------------------------------


def test_the_grid_title_and_closing_carry_the_event_logo(tmp_path: Path) -> None:
    from splitsmith.compare.cards import CardOptions, title_cards

    logo = tmp_path / "event-0123456789ab.png"
    logo.write_bytes(_png())
    match = match_model.Match.init(tmp_path / "m", name="Höstfinalen XI")
    title, closing = title_cards(match, CardOptions(title_page=True, closing_card=True), event_logo=logo)
    assert title is not None and title.logo == logo
    assert closing is not None and closing.logo == logo


def test_the_match_export_carries_the_event_logo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui import match_exports as match_exports_mod

    from .test_ui_server import _seed_match_export_project, _stub_match_export_probe, _wait_for_job

    http, root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)
    url = f"/api/matches/{match_model.Match.load(root).match_id}/match/branding/event-logo"
    assert http.post(url, files={"file": ("e.png", _png(), "image/png")}).status_code == 200
    seen: list = []
    real = match_exports_mod.export_match

    def capture(*args: object, **kwargs: object) -> object:
        seen.append(kwargs["request"])
        return real(*args, **kwargs)

    monkeypatch.setattr(match_exports_mod, "export_match", capture)
    r = http.post(
        "/api/shooters/me/export/match",
        json={"stage_numbers": [1], "include_overlay": False, "title_page": True},
    )
    assert r.status_code == 200, r.text
    assert _wait_for_job(http, r.json()["id"])["status"] == "succeeded"
    name = match_model.Match.load(root).branding.event_logo
    assert seen[0].event_logo == root / EVENT_LOGO_DIR / name


def test_the_title_preview_draws_the_event_logo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import runtime as runtime_module
    from splitsmith.ui import export_preview_api

    from .test_look_editor_api import _factory, _Recorder
    from .test_ui_server import _seed_match_export_project

    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _factory)
    monkeypatch.setenv(runtime_module.ENV_CACHE_DIR, str(tmp_path / "cache"))
    runtime_module._clear_runtime_cache()
    _Recorder.calls = []
    http, root = _seed_match_export_project(tmp_path, stage_count=1)
    body = {"card": "title", "stage_number": 1, "width": 480}
    assert http.post("/api/shooters/me/export-preview", json=body).status_code == 200
    assert "event" not in _Recorder.calls[-1]["data"]
    before = len(_Recorder.calls)
    url = f"/api/matches/{match_model.Match.load(root).match_id}/match/branding/event-logo"
    assert http.post(url, files={"file": ("e.png", _png(), "image/png")}).status_code == 200
    r = http.post("/api/shooters/me/export-preview", json=body)
    assert r.status_code == 200, r.text
    assert len(_Recorder.calls) > before  # rendered again, not the cached card
    name = match_model.Match.load(root).branding.event_logo
    assert _Recorder.calls[-1]["data"]["event"] == {"logo": (root / EVENT_LOGO_DIR / name).resolve().as_uri()}
    runtime_module._clear_runtime_cache()
