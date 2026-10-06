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
