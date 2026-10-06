"""The template contract: what a Look template receives, and the shipped
card template's parity with the Python markup."""

from __future__ import annotations

import json

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


@pytest.mark.integration
def test_the_shipped_card_template_builds_the_markup_python_builds() -> None:
    """``cell.js`` is a port of ``overlay_html._cell_div``. Same groups in,
    same DOM out, through the browser's own serializer on both sides so
    escaping differences cannot hide. Text carries every character Python
    escapes."""
    from playwright.sync_api import sync_playwright

    from splitsmith.composition import TitleCard
    from splitsmith.overlay_card import card_groups
    from splitsmith.overlay_html import _cell_div, single_css
    from splitsmith.overlay_layout import CellScale
    from splitsmith.overlay_raster import CHROMIUM_CHANNEL

    card = TitleCard(
        text='O\'Neil & <Sons> "Classic"',
        duration_seconds=1.5,
        style="lower-third",
        info=("24 rounds", "Comstock & co"),
    )
    groups = card_groups(card)
    theme = load_theme("splitsmith")
    ctx = look_template.TemplateContext(
        theme=look_template.theme_tokens(theme),
        data={"card": {"text": card.text}, "groups": [look_template.group_json(g) for g in groups]},
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
