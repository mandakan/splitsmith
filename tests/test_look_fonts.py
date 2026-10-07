"""A Look's fonts (issue #1272): the bundled catalog, how a manifest picks
from it, and that both halves of every surface (the cards and summaries in
Chromium, the clock in ffmpeg ``drawtext``) draw the face the Look chose,
while a Look that chooses nothing draws exactly what it drew before."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest

from splitsmith import fonts, looks
from splitsmith.compare.overlay_sprites import theme_font_face
from splitsmith.overlay_html import single_css
from splitsmith.overlay_layout import CellScale
from splitsmith.overlay_text import overlay_font_file
from splitsmith.overlay_theme import load_theme, theme_for

FONT_DIR = Path(str(resources.files("splitsmith.data").joinpath("fonts")))


def test_every_bundled_face_ships_with_its_licence() -> None:
    ids = [f.id for f in fonts.FONTS]
    assert len(set(ids)) == len(ids)
    for face in fonts.FONTS:
        assert (FONT_DIR / face.file).is_file(), face.file
        stem = face.file.split("-")[0]
        assert any(p.name.startswith(stem) and p.suffix == ".txt" for p in FONT_DIR.iterdir()), face.id
    assert {f.role for f in fonts.FONTS} == {"display", "mono"}
    assert fonts.DEFAULTS == {"display": "antonio", "mono": "jetbrains-mono"}


@pytest.mark.parametrize(
    "declared, display, mono",
    [
        ({}, "antonio", "jetbrains-mono"),
        ({"display": "bebas-neue"}, "bebas-neue", "jetbrains-mono"),
        ({"display": "Antonio", "mono": "JetBrains Mono", "sans": "Geist"}, "antonio", "jetbrains-mono"),
        ({"display": "Oswald", "mono": "ibm-plex-mono"}, "oswald", "ibm-plex-mono"),
        ({"display": "Comic Sans", "mono": "bebas-neue"}, "antonio", "jetbrains-mono"),
    ],
)
def test_a_manifest_picks_by_id_or_family_and_falls_back(declared: dict, display: str, mono: str) -> None:
    assert fonts.resolve(declared) == {"display": display, "mono": mono}


def test_normalize_keeps_only_what_the_catalog_knows() -> None:
    shipped = {"display": "Antonio", "mono": "JetBrains Mono", "sans": "Geist"}
    assert fonts.normalize(shipped) == {"display": "antonio", "mono": "jetbrains-mono"}
    assert fonts.normalize({"display": "bebas-neue"}) == {"display": "bebas-neue"}


def _look(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **font_choice: str) -> looks.Look:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    raw = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    raw.update(name="club", fonts=font_choice)
    root = tmp_path / "looks" / "club"
    root.mkdir(parents=True)
    (root / "look.json").write_text(json.dumps(raw), encoding="utf-8")
    return looks.load_look("club")


def test_the_theme_carries_the_chosen_faces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    theme = theme_for(_look(tmp_path, monkeypatch, display="bebas-neue", mono="roboto-mono"))
    assert (theme.display_font, theme.mono_font) == ("bebas-neue", "roboto-mono")
    shipped = load_theme("splitsmith")
    assert (shipped.display_font, shipped.mono_font) == ("antonio", "jetbrains-mono")


def test_the_card_css_declares_the_chosen_files_under_the_same_family_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scale = CellScale.for_cell(720)
    chosen = single_css(
        width=1280,
        height=720,
        scale=scale,
        theme=theme_for(_look(tmp_path, monkeypatch, display="bebas-neue", mono="ibm-plex-mono")),
    )
    assert "BebasNeue-Regular.ttf" in chosen and "IBMPlexMono-Bold.ttf" in chosen
    assert '"Splitsmith Display"' in chosen and '"Splitsmith Mono"' in chosen
    default = single_css(width=1280, height=720, scale=scale, theme=load_theme("splitsmith"))
    assert "Antonio-VariableFont.ttf" in default and "JetBrainsMono-Bold.ttf" in default
    assert "font-weight: 700;" in default and "font-weight: 400 700;" in default


def test_the_clock_draws_the_looks_mono_face(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    theme = theme_for(_look(tmp_path, monkeypatch, mono="ibm-plex-mono"))
    path = overlay_font_file(theme_font_face(theme), tmp_path / "work")
    assert path.name == "IBMPlexMono-Bold.ttf" and path.is_file()
    default = overlay_font_file(theme_font_face(load_theme("splitsmith")), tmp_path / "work2")
    assert default.name == "JetBrainsMono-Bold.ttf"


def test_the_single_shooter_clock_uses_the_theme_too() -> None:
    """``overlay_render`` named the bundled mono outright; it now asks the theme."""
    source = (Path(__file__).parent.parent / "src/splitsmith/overlay_render.py").read_text(encoding="utf-8")
    assert 'resolve_overlay_face("splitsmith-mono")' not in source
    assert "resolve_overlay_face(palette.mono_font)" in source


# --- stored Looks and the API ----------------------------------------------------------


def test_a_stored_look_validates_its_fonts_strictly() -> None:
    from pydantic import ValidationError

    from splitsmith.look_store import StoredLookBody

    base = {"label": "Club", "colors": dict(looks.load_look("clean").manifest.colors)}
    assert StoredLookBody.model_validate({**base, "fonts": {"display": "oswald"}}).fonts == {
        "display": "oswald"
    }
    for bad in ({"display": "Comic Sans"}, {"sans": "antonio"}, {"mono": "bebas-neue"}):
        with pytest.raises(ValidationError) as caught:
            StoredLookBody.model_validate({**base, "fonts": bad})
        assert caught.value.errors()[0]["loc"][0] == "fonts"


def test_a_copy_of_the_shipped_look_carries_its_fonts_as_ids() -> None:
    from splitsmith.look_store import body_from_manifest

    body = body_from_manifest(looks.load_look("splitsmith").manifest)
    assert body.fonts == {"display": "antonio", "mono": "jetbrains-mono"}


@pytest.fixture
def client(tmp_path: Path):
    from .test_ui_server import _match_create_app, _MatchClient

    return _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="Fonts"))


def test_the_catalog_lists_the_faces_and_serves_them(client) -> None:
    listed = client.get("/api/looks").json()["fonts"]
    assert [f["id"] for f in listed] == [f.id for f in fonts.FONTS]
    oswald = next(f for f in listed if f["id"] == "oswald")
    assert oswald["role"] == "display" and oswald["url"] == "/api/looks/fonts/oswald"
    r = client.get(oswald["url"])
    assert r.status_code == 200 and r.content == (FONT_DIR / "Oswald-VariableFont.ttf").read_bytes()
    assert r.headers["content-type"] == "font/ttf"
    assert client.get("/api/looks/fonts/nope").status_code == 404
    assert client.get("/api/looks/fonts/..%2Fpasswd").status_code == 404


def test_a_chosen_face_draws_differently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real render: Bebas Neue sets the same title narrower than Antonio."""
    import io

    from PIL import Image

    from splitsmith import look_tools
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    def inked_width(look: looks.Look) -> int:
        case, ctx = look_tools.sample_contexts(look, "slate", "default", tmp_path)[0]
        png = raster.render_template(looks.template_for(look, "slate"), context=ctx, width=1280, height=720)
        with Image.open(io.BytesIO(png)) as im:
            box = im.getchannel("A").getbbox()
        assert box is not None
        return box[2] - box[0]

    try:
        with ChromiumRasterizer() as raster:
            default = inked_width(_look(tmp_path, monkeypatch))
            bebas = inked_width(_look(tmp_path / "b", monkeypatch, display="bebas-neue"))
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")
    assert abs(default - bebas) > 10, (default, bebas)
