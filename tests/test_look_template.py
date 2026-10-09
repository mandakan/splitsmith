"""The template contract: what a Look template receives, and the shipped
card template's parity with the Python markup."""

from __future__ import annotations

import io
import json
import math

import pytest

from splitsmith import look_template, looks
from splitsmith.overlay_layout import MIN_FONT_SIZE, Anchor, ColorToken, Element, Emphasis, Flow, Group, Role
from splitsmith.overlay_theme import load_theme


def test_theme_tokens_are_hex_strings_for_every_palette_field() -> None:
    tokens = look_template.theme_tokens(load_theme("splitsmith"))
    assert tokens["ink"] == "#f4f4f5"
    assert tokens["shadow"] == tokens["stroke"]
    assert set(looks.REQUIRED_COLORS) <= set(tokens)


def test_group_json_carries_every_declaration_field_as_plain_values() -> None:
    group = Group(
        anchor=Anchor.BOTTOM_LEFT,
        flow=Flow.ROW,
        elements=(
            Element(role=Role.HEADLINE, text="Stage 3", emphasis=Emphasis.PLATE, color=ColorToken.SPLIT_GOOD),
            Element(role=Role.DETAIL, text="24", caption="rounds", unit="r", drop_priority=2),
        ),
        align="left",
        gap=4,
        margin_top=8,
    )
    assert look_template.group_json(group) == {
        "anchor": "bottom-left",
        "flow": "row",
        "divider": False,
        "align": "left",
        "gap": 4,
        "margin_top": 8,
        "elements": [
            {
                "role": "headline",
                "text": "Stage 3",
                "emphasis": "plate",
                "caption": None,
                "color": "split_good",
                "unit": None,
                "drop_priority": None,
            },
            {
                "role": "detail",
                "text": "24",
                "emphasis": "plain",
                "caption": "rounds",
                "color": None,
                "unit": "r",
                "drop_priority": 2,
            },
        ],
    }


def test_init_script_assigns_the_whole_context_to_window_splitsmith() -> None:
    ctx = look_template.TemplateContext(
        theme={"ink": "#ffffff"},
        data={"card": {"text": "O'Neil </script><b>"}},
        size={"width": 64, "height": 32},
        fps=30,
        engine=look_template.engine_block(css="body{}"),
        assets={"shared": look_template.shared_url()},
    )
    script = ctx.init_script()
    assert script.startswith("window.splitsmith = ")
    payload = json.loads(script[len("window.splitsmith = ") : -1])
    assert payload["data"]["card"]["text"] == "O'Neil </script><b>"
    assert payload["engine"]["min_font_size"] == MIN_FONT_SIZE
    assert payload["assets"]["shared"].startswith("file://")
    assert "</script>" not in script, "a value must never be able to close the init script element"


def test_shared_url_points_at_the_shipped_engine_scripts() -> None:
    url = look_template.shared_url()
    assert url.endswith("/_shared")
    assert (looks.shared_dir() / "cell.js").is_file()
    assert (looks.shared_dir() / "fit.js").is_file()


def _lower_third_groups() -> tuple[Group, ...]:
    from splitsmith.composition import TitleCard
    from splitsmith.overlay_card import card_groups

    return card_groups(
        TitleCard(
            text='O\'Neil & <Sons> "Classic"',
            duration_seconds=1.5,
            style="lower-third",
            info=("24 rounds", "Comstock & co"),
        )
    )


def _every_field_groups() -> tuple[Group, ...]:
    """Every declaration field ``cell.js`` reads, in one set: right, centre
    and bottom anchors, the three flows, a divider, an empty grid, caption,
    unit, colour, drop priorities, gap, margin_top, an align override and
    text with every character Python escapes. A card never declares most
    of these; the summary slot will, and ``engine.renderGroups`` is open
    to any template."""
    return (
        Group(
            anchor=Anchor.TOP_RIGHT,
            flow=Flow.ROW,
            elements=(
                Element(
                    role=Role.LABEL,
                    text="A & <b> 'q' \"d\"",
                    caption="cap <x>",
                    unit="s",
                    color=ColorToken.ACCENT_TEXT,
                ),
            ),
        ),
        Group(
            anchor=Anchor.MIDDLE_CENTER,
            flow=Flow.COLUMN,
            elements=(
                Element(role=Role.VERDICT, text="12.34", drop_priority=0),
                Element(role=Role.DETAIL, text="x", drop_priority=3, emphasis=Emphasis.MUTED),
            ),
            align="right",
            gap=6,
            margin_top=10,
        ),
        Group(anchor=Anchor.MIDDLE_CENTER, flow=Flow.GRID, elements=()),
        Group(anchor=Anchor.MIDDLE_CENTER, flow=Flow.ROW, elements=(), divider=True),
        Group(
            anchor=Anchor.BOTTOM_LEFT,
            flow=Flow.ROW,
            elements=(
                Element(
                    role=Role.IDENTITY, text="O'Neil", emphasis=Emphasis.PLATE, color=ColorToken.SPLIT_GOOD
                ),
            ),
        ),
        Group(
            anchor=Anchor.BOTTOM_CENTER, flow=Flow.COLUMN, elements=(Element(role=Role.HEADLINE, text="c"),)
        ),
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "make_groups", [_lower_third_groups, _every_field_groups], ids=["lower-third", "every-field"]
)
def test_the_shipped_card_template_builds_the_markup_python_builds(make_groups) -> None:
    """``cell.js`` is a port of ``overlay_html._cell_div``. Same groups in,
    same DOM out, through the browser's own serializer on both sides so
    escaping differences cannot hide. The second case carries every field
    the port reads; a review showed the card's own groups alone let a
    broken ``fit()`` or a dropped ``data-drop-priority`` through."""
    from playwright.sync_api import sync_playwright

    from splitsmith.overlay_html import _cell_div, single_css
    from splitsmith.overlay_layout import CellScale
    from splitsmith.overlay_raster import CHROMIUM_CHANNEL

    groups = make_groups()
    theme = load_theme("splitsmith")
    ctx = look_template.TemplateContext(
        theme=look_template.theme_tokens(theme),
        data={"card": {"text": "parity"}, "groups": [look_template.group_json(g) for g in groups]},
        size={"width": 640, "height": 360},
        fps=30,
        engine=look_template.engine_block(
            css=single_css(width=640, height=360, scale=CellScale.for_cell(360), theme=theme)
        ),
        assets={"shared": look_template.shared_url()},
    )
    template = looks.load_look("splitsmith").own_template("lower_third")
    assert template is not None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel=CHROMIUM_CHANNEL, headless=True)
        except Exception as exc:  # noqa: BLE001 -- no browser on this host: skip, the gate decides
            pytest.skip(f"no Chromium: {exc}")
        context = browser.new_context(viewport={"width": 640, "height": 360})
        context.add_init_script(ctx.init_script())
        page = context.new_page()
        page.goto(template.resolve().as_uri(), wait_until="load")
        from_js = page.evaluate("document.body.innerHTML")
        from_python = page.evaluate(
            "html => { document.body.innerHTML = html; return document.body.innerHTML; }", _cell_div(groups)
        )
        assert page.evaluate("typeof window.duration") == "function"
        assert page.evaluate("window.duration()") == 0
        browser.close()
    assert from_js == from_python


@pytest.mark.integration
def test_a_user_template_that_throws_skips_the_card_with_a_real_browser(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus 4 against a real page: a Look whose ``card.html`` throws
    while mounting yields no card, never a blank one on the backdrop."""
    from splitsmith.composition import MatchTitle
    from splitsmith.overlay_card import build_card_still
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    manifest = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    manifest["name"] = "broken"
    manifest["slots"] = {"title_page": "card.html"}
    root = tmp_path / "looks" / "broken"
    root.mkdir(parents=True)
    (root / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "card.html").write_text(
        "<!doctype html><html><body><script>"
        "document.addEventListener('DOMContentLoaded', function () { window.splitsmith.nope.mount(); });"
        "</script></body></html>",
        encoding="utf-8",
    )
    try:
        with ChromiumRasterizer() as rasterizer:
            image = build_card_still(
                MatchTitle(text="x"),
                slot="title_page",
                width=64,
                height=32,
                fps=30,
                look=looks.load_look("broken"),
                rasterizer=rasterizer,
                backdrop=None,
            )
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))
    assert image is None


def test_template_digest_moves_with_every_input(tmp_path) -> None:
    template = tmp_path / "t.html"
    template.write_text("<!doctype html>", encoding="utf-8")
    ctx = look_template.TemplateContext(
        theme={"ink": "#ffffff"},
        data={"groups": []},
        size={"width": 64, "height": 32},
        fps=30,
        engine=look_template.engine_block(css="body{}"),
        assets={"shared": look_template.shared_url()},
    )
    base = look_template.template_digest(template, ctx, fps=30, engine_version="v1")
    assert base == look_template.template_digest(template, ctx, fps=30, engine_version="v1")
    assert base != look_template.template_digest(template, ctx, fps=25, engine_version="v1")
    assert base != look_template.template_digest(template, ctx, fps=30, engine_version="v2")
    dark = ctx.model_copy(update={"theme": {"ink": "#000000"}})
    assert base != look_template.template_digest(template, dark, fps=30, engine_version="v1")
    template.write_text("<!doctype html><!-- edited -->", encoding="utf-8")
    assert base != look_template.template_digest(template, ctx, fps=30, engine_version="v1")


@pytest.mark.integration
def test_the_rise_variant_animates_deterministically() -> None:
    """``card-rise.html``: nothing painted at t=0, the whole card by the
    end, and frame k identical across two runs (the determinism the
    cache's digest key rests on). The poster is the end of the rise, so a
    preview never shows the invisible first frame."""
    from PIL import Image

    from splitsmith.composition import TitleCard
    from splitsmith.overlay_card import card_context
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    look = looks.load_look("splitsmith")
    card = TitleCard(text="Stage 3", duration_seconds=1.5, info=("24 rounds",), variant="rise")
    template = looks.template_for(look, "slate", "rise")
    assert template.name == "card-rise.html"
    context = card_context(card, slot="slate", width=320, height=180, fps=20, theme=load_theme("splitsmith"))

    def run(rasterizer: ChromiumRasterizer) -> list[bytes]:
        out = rasterizer.render_template_frames(
            template, context=context, width=320, height=180, fps=20, max_seconds=5.0
        )
        assert out.duration > 0.3
        return list(out.frames)

    try:
        with ChromiumRasterizer() as rasterizer:
            first = run(rasterizer)
            second = run(rasterizer)
            poster_png = rasterizer.render_template(template, context=context, width=320, height=180)
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))
    assert first == second
    assert len(first) == 14, "two groups: 600 ms plus one 90 ms stagger at 20 fps"
    start = Image.frombytes("RGBA", (320, 180), first[0])
    end = Image.frombytes("RGBA", (320, 180), first[-1])
    assert start.getchannel("A").getbbox() is None, "nothing is painted before the rise begins"
    assert end.getchannel("A").getbbox() is not None, "the card is on screen at the end"
    with Image.open(io.BytesIO(poster_png)) as poster:
        assert poster.convert("RGBA").getchannel("A").getbbox() is not None
    assert math.isclose(len(first) / 20, 0.7, abs_tol=0.01)


@pytest.mark.integration
def test_a_long_rise_cards_held_frame_is_its_poster() -> None:
    """The clip's last frame is what the hold clones for the rest of the
    card, and the preview shows the poster. With a roster long enough to
    make the fit policy shrink the text, the two must agree: the fit runs
    on the laid-out end state, and the last sample is taken at the end of
    the rise, not one frame short of it."""
    from PIL import Image, ImageChops

    from splitsmith.composition import MatchTitle
    from splitsmith.overlay_card import card_context
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    look = looks.load_look("splitsmith")
    card = MatchTitle(
        text="Bromma Classifier",
        info=tuple(f"Shooter {i} · Production Optics" for i in range(10)),
        variant="rise",
    )
    template = looks.template_for(look, "title_page", "rise")
    context = card_context(
        card, slot="title_page", width=640, height=360, fps=20, theme=load_theme("splitsmith")
    )
    try:
        with ChromiumRasterizer() as rasterizer:
            poster_png = rasterizer.render_template(template, context=context, width=640, height=360)
            out = rasterizer.render_template_frames(
                template, context=context, width=640, height=360, fps=20, max_seconds=5.0
            )
            last = list(out.frames)[-1]
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))
    with Image.open(io.BytesIO(poster_png)) as poster_image:
        poster = poster_image.convert("RGBA")
    held = Image.frombytes("RGBA", (640, 360), last)
    diff = ImageChops.difference(poster, held)
    assert max(channel[1] for channel in diff.getextrema()) <= 1, "the held frame must be the poster"


# --- identity (slice 3, #1243) -----------------------------------------------------


def test_shooter_json_carries_the_logo_as_a_file_url_when_the_file_exists(tmp_path) -> None:
    from PIL import Image

    from splitsmith.identity import ResolvedIdentity

    logo = tmp_path / "logo-0123456789ab.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(logo)
    out = look_template.shooter_json(
        ResolvedIdentity(label="Anders", accent="#ff2d2d", logo_path=logo, club="Bromma PK")
    )
    assert out == {
        "label": "Anders",
        "accent": "#ff2d2d",
        "club": "Bromma PK",
        "logo": logo.resolve().as_uri(),
    }


def test_shooter_json_drops_a_missing_or_oversized_logo_with_a_log_line(tmp_path, caplog) -> None:
    import logging

    from splitsmith.identity import ResolvedIdentity

    missing = ResolvedIdentity(label="A", accent="#ff2d2d", logo_path=tmp_path / "gone.png", club=None)
    with caplog.at_level(logging.WARNING, logger="splitsmith.look_template"):
        assert look_template.shooter_json(missing)["logo"] is None
    assert "gone.png" in caplog.text
    big = tmp_path / "logo-0123456789ab.png"
    big.write_bytes(b"0" * (2 * 1024 * 1024 + 1))
    assert (
        look_template.shooter_json(ResolvedIdentity(label="A", accent="#ff2d2d", logo_path=big, club=None))[
            "logo"
        ]
        is None
    )


@pytest.mark.integration
def test_a_lower_third_draws_a_logo_only_when_exactly_one_shooter_has_one(tmp_path) -> None:
    """The grid's lower third is the stage's, not one shooter's: with
    several logos it draws none (they would sit over the footage for the
    whole head); with exactly one it draws that one."""
    from PIL import Image

    from splitsmith.composition import TitleCard
    from splitsmith.identity import ResolvedIdentity
    from splitsmith.overlay_card import card_context
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    look = looks.load_look("splitsmith")
    template = looks.template_for(look, "lower_third")
    card = TitleCard(text="Stage 3", duration_seconds=1.5, info=("24 rounds",))
    logo = tmp_path / "logo-0123456789ab.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(logo)
    one = (ResolvedIdentity(label="Anders", accent=None, logo_path=logo, club=None),)
    two = one + (ResolvedIdentity(label="Bea", accent=None, logo_path=logo, club=None),)
    theme = load_theme("splitsmith")

    def render(rasterizer, shooters):  # noqa: ANN001
        context = card_context(
            card, slot="lower_third", width=320, height=180, fps=30, theme=theme, shooters=shooters
        )
        return rasterizer.render_template(template, context=context, width=320, height=180)

    try:
        with ChromiumRasterizer() as rasterizer:
            none = render(rasterizer, ())
            single = render(rasterizer, one)
            several = render(rasterizer, two)
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))
    assert single != none, "one shooter with a logo: drawn"
    assert several == none, "two shooters with logos on a lower third: none drawn"


def test_a_shooters_logo_reaches_the_shipped_card_and_nothing_else_changes(tmp_path) -> None:
    """With a logo the card paints it top-right; a shooter without a logo
    leaves the render byte-identical to a card with no shooters at all."""
    from PIL import Image

    from splitsmith.composition import TitleCard
    from splitsmith.identity import ResolvedIdentity
    from splitsmith.overlay_card import card_context
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    look = looks.load_look("splitsmith")
    template = looks.template_for(look, "slate")
    card = TitleCard(text="Stage 3", duration_seconds=1.5, info=("24 rounds",))
    logo = tmp_path / "logo-0123456789ab.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(logo)
    with_logo = ResolvedIdentity(label="Anders", accent="#ff2d2d", logo_path=logo, club=None)
    without = ResolvedIdentity(label="Anders", accent="#ff2d2d", logo_path=None, club=None)
    theme = load_theme("splitsmith")

    def render(rasterizer, shooters):  # noqa: ANN001
        context = card_context(
            card, slot="slate", width=320, height=180, fps=30, theme=theme, shooters=shooters
        )
        return rasterizer.render_template(template, context=context, width=320, height=180)

    try:
        with ChromiumRasterizer() as rasterizer:
            none = render(rasterizer, ())
            plain = render(rasterizer, (without,))
            logod = render(rasterizer, (with_logo,))
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))
    assert plain == none
    assert logod != none
    with Image.open(io.BytesIO(logod)) as image:
        alpha = image.convert("RGBA").getchannel("A")
    top_right = alpha.crop((320 - 60, 0, 320, 40))
    assert top_right.getbbox() is not None, "the logo paints in the top-right corner"


@pytest.mark.integration
def test_the_shipped_sting_shows_one_logo_only_when_the_shooters_share_it(tmp_path) -> None:
    """Issue #1245, review focus 5: the wipe carries the logo when every
    shooter with a logo has the same one (one shooter; the match logo
    folded into each), and the next item's label when they differ."""
    import io

    from PIL import Image

    from splitsmith.identity import ResolvedIdentity
    from splitsmith.look_sting import sting_context
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError
    from splitsmith.overlay_theme import theme_for

    def logo(name: str, colour: tuple[int, int, int]):  # type: ignore[no-untyped-def]
        path = tmp_path / name
        Image.new("RGB", (64, 64), colour).save(path)
        return path

    green = logo("green.png", (0, 255, 0))
    other = logo("blue.png", (0, 0, 255))
    look = looks.load_look("splitsmith")
    template = looks.sting_template_for(look, "wipe")
    assert template is not None
    cases = {
        "one": [ResolvedIdentity("A", "#ff2d2d", green, None)],
        "shared": [ResolvedIdentity("A", "#ff2d2d", green, None), ResolvedIdentity("B", None, green, None)],
        "different": [
            ResolvedIdentity("A", "#ff2d2d", green, None),
            ResolvedIdentity("B", None, other, None),
        ],
    }
    counts: dict[str, int] = {}
    try:
        with ChromiumRasterizer() as rasterizer:
            for name, shooters in cases.items():
                ctx = sting_context(
                    kind="sting:wipe",
                    seconds=1.0,
                    from_label="Stage 01",
                    to_label="Stage 02",
                    width=640,
                    height=360,
                    fps=30,
                    theme=theme_for(look),
                    shooters=shooters,
                )
                png = rasterizer.render_template(template, context=ctx, width=640, height=360)
                with Image.open(io.BytesIO(png)) as im:
                    raw = im.convert("RGBA").tobytes()
                pixels = (raw[i : i + 4] for i in range(0, len(raw), 4))
                counts[name] = sum(1 for r, g, b, a in pixels if a > 200 and g > 200 and r < 80 and b < 80)
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")
    assert counts["one"] > 100 and counts["shared"] > 100, counts
    assert counts["different"] == 0, counts


def test_the_shipped_sting_names_only_fonts_the_engine_declares() -> None:
    """Review of #1245: the template loads the engine stylesheet for its
    font faces, so every family it names must be one of them, or the
    label draws in whatever the host has and the pixels stop being
    deterministic across machines."""
    import re

    from splitsmith.overlay_html import single_css
    from splitsmith.overlay_layout import CellScale

    look = looks.load_look("splitsmith")
    template = looks.sting_template_for(look, "wipe")
    assert template is not None
    css = single_css(width=640, height=360, scale=CellScale.for_cell(360), theme=load_theme("splitsmith"))
    declared = set(re.findall(r'@font-face\s*\{[^}]*font-family:\s*"([^"]+)"', css))
    assert declared, "the engine declares its faces"
    named = set()
    for value in re.findall(r"font-family:\s*([^;]+);", template.read_text(encoding="utf-8")):
        named |= set(re.findall(r'"([^"]+)"', value))
    assert named and named <= declared, (named, declared)


@pytest.mark.integration
def test_the_shipped_sting_keeps_a_long_label_inside_the_band(tmp_path) -> None:
    """Review of #1245: a long stage name must not spill across the
    footage either side of the band. At the poster the band sits in the
    middle of the frame; the outer columns stay free of the label's ink."""
    import io

    from PIL import Image

    from splitsmith.look_sting import sting_context
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError
    from splitsmith.overlay_theme import theme_for

    look = looks.load_look("splitsmith")
    template = looks.sting_template_for(look, "wipe")
    assert template is not None
    ctx = sting_context(
        kind="sting:wipe",
        seconds=1.0,
        from_label="Stage 6",
        to_label="Stage 7 - The Very Long Corridor Of Doom And Despair",
        width=640,
        height=360,
        fps=30,
        theme=theme_for(look),
        shooters=(),
    )
    try:
        with ChromiumRasterizer() as rasterizer:
            png = rasterizer.render_template(template, context=ctx, width=640, height=360)
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")
    with Image.open(io.BytesIO(png)) as im:
        rgba = im.convert("RGBA")
        width, height = rgba.size
        raw = rgba.tobytes()
    ink_outside = 0
    ink_total = 0
    for y in range(height):
        for x in range(width):
            r, g, b, a = raw[(y * width + x) * 4 : (y * width + x) * 4 + 4]
            if a > 200 and r > 200 and g > 200 and b > 200:
                ink_total += 1
                if x < width * 0.15 or x > width * 0.85:
                    ink_outside += 1
    assert ink_total > 50, "the label is drawn"
    assert ink_outside == 0, f"{ink_outside} label pixels outside the band's reach"
