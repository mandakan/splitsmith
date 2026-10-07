"""A Look's own font file (issue #1272, step 2; desktop only): the upload's
checks, where the file lives, and that the cards, the clock and the editor's
draft all draw with it. The uploaded file in these tests is a real OFL face
from ``data/fonts`` standing in for one the user owns."""

from __future__ import annotations

import io
import json
from importlib import resources
from pathlib import Path

import pytest

from splitsmith import fonts, looks, own_fonts
from splitsmith.compare.overlay_sprites import theme_font_face
from splitsmith.overlay_html import single_css
from splitsmith.overlay_layout import CellScale
from splitsmith.overlay_text import overlay_font_file
from splitsmith.overlay_theme import theme_for

FONT_DIR = Path(str(resources.files("splitsmith.data").joinpath("fonts")))
BEBAS = (FONT_DIR / "BebasNeue-Regular.ttf").read_bytes()
PLEX = (FONT_DIR / "IBMPlexMono-Bold.ttf").read_bytes()


def _look(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **font_choice: str) -> looks.Look:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    raw = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    raw.update(name="club", fonts=font_choice)
    root = tmp_path / "looks" / "club"
    root.mkdir(parents=True, exist_ok=True)
    (root / "look.json").write_text(json.dumps(raw), encoding="utf-8")
    return looks.load_look("club")


# --- the value grammar -----------------------------------------------------------------


def test_an_own_face_is_a_content_named_file_in_the_looks_fonts_folder() -> None:
    assert fonts.check({"display": "own:font-0123456789ab.ttf", "mono": "own:font-ba9876543210.otf"})
    for bad in (
        "own:../escape.ttf",
        "own:font-0123456789ab.woff2",
        "own:font-XYZ.ttf",
        "own:fonts/font-0123456789ab.ttf",
        "own:",
    ):
        with pytest.raises(ValueError):
            fonts.check({"display": bad})


def test_an_own_face_resolves_to_its_file_or_falls_back_when_missing(tmp_path: Path) -> None:
    (tmp_path / "fonts").mkdir()
    file = tmp_path / "fonts" / "font-0123456789ab.ttf"
    file.write_bytes(BEBAS)
    declared = {"display": "own:font-0123456789ab.ttf", "mono": "own:font-ba9876543210.otf"}
    assert fonts.resolve(declared, root=tmp_path) == {"display": str(file), "mono": "jetbrains-mono"}
    # A Look with no folder of its own (a hosted materialization) cannot have one.
    assert fonts.resolve(declared) == fonts.DEFAULTS
    # ``normalize`` keeps the value as written, so a manifest round-trips.
    assert fonts.normalize(declared) == declared


# --- the upload ------------------------------------------------------------------------


def test_an_upload_is_stored_by_content_with_its_family_name(tmp_path: Path) -> None:
    saved = own_fonts.save_font(tmp_path, BEBAS)
    assert saved.value.startswith("own:font-") and saved.value.endswith(".ttf")
    assert saved.family == "Bebas Neue"
    assert (tmp_path / "fonts" / saved.file).read_bytes() == BEBAS
    assert own_fonts.save_font(tmp_path, BEBAS) == saved  # the same bytes, the same file
    assert [f.value for f in own_fonts.list_fonts(tmp_path)] == [saved.value]


@pytest.mark.parametrize(
    "data, says",
    [
        (b"wOF2" + b"\0" * 64, "WOFF"),
        (b"wOFF" + b"\0" * 64, "WOFF"),
        (b"ttcf" + b"\0" * 64, "collection"),
        (b"<svg></svg>", "TrueType or OpenType"),
        (b"OTTO" + b"\0" * 64, "could not be read"),
        (b"\x00\x01\x00\x00" + b"\xff" * 64, "could not be read"),
    ],
)
def test_an_upload_that_is_not_a_readable_ttf_or_otf_is_refused(
    tmp_path: Path, data: bytes, says: str
) -> None:
    with pytest.raises(own_fonts.OwnFontError, match=says):
        own_fonts.save_font(tmp_path, data)
    assert not (tmp_path / "fonts").exists() or not any((tmp_path / "fonts").iterdir())


def test_an_upload_over_the_cap_is_refused_before_it_is_parsed(tmp_path: Path) -> None:
    with pytest.raises(own_fonts.OwnFontError, match="2 MB"):
        own_fonts.save_font(tmp_path, BEBAS + b"\0" * own_fonts.MAX_FONT_BYTES)


# --- every surface draws with it -------------------------------------------------------


def _own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[looks.Look, Path, Path]:
    look = _look(tmp_path, monkeypatch)
    display = own_fonts.save_font(look.root, BEBAS)
    mono = own_fonts.save_font(look.root, PLEX)
    look = _look(tmp_path, monkeypatch, display=display.value, mono=mono.value)
    return look, look.root / "fonts" / display.file, look.root / "fonts" / mono.file


def test_the_cards_declare_the_own_files_under_the_role_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    look, display, mono = _own(tmp_path, monkeypatch)
    theme = theme_for(look)
    assert (theme.display_font, theme.mono_font) == (str(display), str(mono))
    css = single_css(width=1280, height=720, scale=CellScale.for_cell(720), theme=theme)
    assert display.as_uri() in css and mono.as_uri() in css
    assert '"Splitsmith Display"' in css and '"Splitsmith Mono"' in css
    # A single-weight file the user brings is drawn as is at every weight
    # a template asks for, never synthesized bold.
    assert "font-weight: 100 900;" in css


def test_the_clock_draws_the_own_mono_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    look, _display, mono = _own(tmp_path, monkeypatch)
    assert overlay_font_file(theme_font_face(theme_for(look)), tmp_path / "work") == mono


def test_the_editors_draft_keeps_the_looks_own_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The draft is a copy under a work dir; a font (or an image a template
    loads) left behind would preview in the fallback face."""
    from splitsmith.look_store import body_from_manifest, draft_look

    look, display, _mono = _own(tmp_path, monkeypatch)
    (look.root / "badge.png").write_bytes(b"png")
    draft = draft_look(look, body_from_manifest(look.manifest), tmp_path / "draft")
    assert draft.root != look.root
    assert (draft.root / "badge.png").is_file()
    assert Path(theme_for(draft).display_font).read_bytes() == display.read_bytes()


def test_a_hosted_look_cannot_name_an_own_file() -> None:
    from splitsmith.db.looks import _check_hosted
    from splitsmith.look_store import LookStoreError, StoredLookBody

    body = StoredLookBody.model_validate(
        {
            "label": "Club",
            "colors": dict(looks.load_look("clean").manifest.colors),
            "fonts": {"display": "own:font-0123456789ab.ttf"},
        }
    )
    with pytest.raises(LookStoreError, match="font"):
        _check_hosted("club", body)


def test_an_own_face_draws_differently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real render: the uploaded Bebas Neue sets the title narrower than
    the default Antonio, so Chromium loaded the file, not a fallback."""
    from PIL import Image

    from splitsmith import look_tools
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    def inked_width(look: looks.Look) -> int:
        _case, ctx = look_tools.sample_contexts(look, "slate", "default", tmp_path)[0]
        png = raster.render_template(looks.template_for(look, "slate"), context=ctx, width=1280, height=720)
        with Image.open(io.BytesIO(png)) as im:
            box = im.getchannel("A").getbbox()
        assert box is not None
        return box[2] - box[0]

    try:
        with ChromiumRasterizer() as raster:
            default = inked_width(_look(tmp_path / "a", monkeypatch))
            own, _display, _mono = _own(tmp_path / "b", monkeypatch)
            mine = inked_width(own)
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")
    assert abs(default - mine) > 10, (default, mine)


# --- the routes (local only) -------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from .test_ui_server import _match_create_app, _MatchClient

    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path / "home"))
    client = _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="Fonts"))
    colors = {k: list(v) for k, v in looks.load_look("splitsmith").manifest.colors.items()}
    r = client.put("/api/looks/club", json={"label": "Club", "base": "splitsmith", "colors": colors})
    assert r.status_code in (200, 201), r.text
    return client


def _upload(client, data: bytes, name: str = "MyFace.ttf"):
    return client.post("/api/looks/club/fonts", files={"file": (name, data, "application/octet-stream")})


def test_an_uploaded_font_is_listed_served_and_chosen_on_save(client) -> None:
    r = _upload(client, BEBAS, name="anything.woff2")  # the name is never read
    assert r.status_code == 201, r.text
    font = r.json()
    assert font["family"] == "Bebas Neue" and font["value"].startswith("own:font-")
    assert client.get("/api/looks/club/fonts").json() == [font]
    served = client.get(font["url"])
    assert served.status_code == 200 and served.content == BEBAS
    assert served.headers["content-type"] == "font/ttf"
    assert served.headers["x-content-type-options"] == "nosniff"

    colors = {k: list(v) for k, v in looks.load_look("splitsmith").manifest.colors.items()}
    body = {"label": "Club", "base": "splitsmith", "colors": colors, "fonts": {"display": font["value"]}}
    assert client.put("/api/looks/club", json=body).status_code == 200
    assert theme_for(looks.load_look("club")).display_font.endswith(font["value"].removeprefix("own:"))


def test_a_bad_upload_is_a_422_and_an_oversized_one_a_413(client) -> None:
    r = _upload(client, b"wOF2" + b"\0" * 64)
    assert r.status_code == 422 and "WOFF" in r.json()["detail"]
    r = _upload(client, b"\0" * (own_fonts.MAX_FONT_BYTES + 1))
    assert r.status_code == 413
    assert client.get("/api/looks/club/fonts").json() == []


def test_the_font_routes_answer_only_for_a_look_of_yours_and_its_own_files(client) -> None:
    assert _upload(client, BEBAS).status_code == 201
    assert (
        client.post("/api/looks/splitsmith/fonts", files={"file": ("a.ttf", BEBAS, "font/ttf")}).status_code
        == 404
    )
    assert client.get("/api/looks/nope/fonts").status_code == 404
    assert client.get("/api/looks/club/fonts/look.json").status_code == 404
    assert client.get("/api/looks/club/fonts/..%2Flook.json").status_code == 404
    assert client.get("/api/looks/club/fonts/font-000000000000.ttf").status_code == 404


def test_an_animated_cards_digest_moves_with_the_own_font(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A motion clip is cached by ``template_digest``, which reads the font
    only through the engine stylesheet's ``@font-face`` URL; a new file is a
    new content name there, so a font swap never serves a stale clip."""
    from splitsmith import look_tools
    from splitsmith.look_template import template_digest

    def digest(look: looks.Look) -> str:
        _case, ctx = look_tools.sample_contexts(look, "slate", "default", tmp_path)[0]
        return template_digest(looks.template_for(look, "slate"), ctx, fps=30.0, engine_version="x")

    look = _look(tmp_path, monkeypatch)
    first = own_fonts.save_font(look.root, BEBAS)
    second = own_fonts.save_font(look.root, PLEX)
    a = digest(_look(tmp_path, monkeypatch, display=first.value))
    b = digest(_look(tmp_path, monkeypatch, display=second.value))
    assert a != b
