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
