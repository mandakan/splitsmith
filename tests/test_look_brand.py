"""Your brand in a Look (the branding work, PR 2): a logo and a line kept in
the Look, drawn as a corner mark (top-left) on the title page and the closing card.
Desktop first: the logo is a file in the Look's ``brand/`` folder; hosted
refuses one until Looks have an asset store."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import look_brand, looks
from splitsmith.look_store import LookStoreError, StoredLookBody, body_from_manifest


def _png(size: int = 64, colour: tuple[int, int, int] = (230, 20, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(buf, format="PNG")
    return buf.getvalue()


def _look(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, brand: dict | None = None) -> looks.Look:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    raw = json.loads((looks.shipped_looks_dir() / "splitsmith" / "look.json").read_text(encoding="utf-8"))
    raw.update(name="club")
    raw.pop("source", None)
    if brand is not None:
        raw["brand"] = brand
    root = tmp_path / "looks" / "club"
    root.mkdir(parents=True, exist_ok=True)
    for file, src in looks.look_files(looks.shipped_looks_dir() / "splitsmith").items():
        (root / file).parent.mkdir(parents=True, exist_ok=True)
        (root / file).write_bytes(src.read_bytes())
    (root / "look.json").write_text(json.dumps(raw), encoding="utf-8")
    return looks.load_look("club")


# --- the shape -------------------------------------------------------------------------


def test_the_brand_is_a_content_named_file_and_a_short_line() -> None:
    brand = looks.LookBrand(logo="brand-0123456789ab.png", line="Bromma PK")
    assert brand.logo == "brand-0123456789ab.png"
    for bad in ("../x.png", "brand-xyz.png", "brand-0123456789ab.svg", "logo-0123456789ab.png"):
        with pytest.raises(ValueError):
            looks.LookBrand(logo=bad)
    with pytest.raises(ValueError):
        looks.LookBrand(line="x" * 61)


def test_a_stored_body_carries_the_brand_and_hosted_refuses_its_logo() -> None:
    from splitsmith.db.looks import _check_hosted

    colors = dict(looks.load_look("splitsmith").manifest.colors)
    body = StoredLookBody.model_validate(
        {"label": "Club", "colors": colors, "brand": {"logo": "brand-0123456789ab.png", "line": "Bromma PK"}}
    )
    assert body.brand is not None and body.brand.line == "Bromma PK"
    with pytest.raises(LookStoreError, match="brand"):
        _check_hosted("club", body)
    _check_hosted("club", body.model_copy(update={"brand": looks.LookBrand(line="Bromma PK")}))


def test_a_copy_keeps_the_brand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    look = _look(tmp_path, monkeypatch, {"line": "Bromma PK"})
    assert body_from_manifest(look.manifest).brand == looks.LookBrand(line="Bromma PK")


# --- the upload ------------------------------------------------------------------------


def test_a_logo_is_stored_by_content_in_the_brand_folder(tmp_path: Path) -> None:
    name = look_brand.save_brand_logo(tmp_path, _png())
    assert name.startswith("brand-") and name.endswith(".png")
    assert (tmp_path / "brand" / name).read_bytes() == _png()
    assert look_brand.save_brand_logo(tmp_path, _png()) == name


@pytest.mark.parametrize(
    "data, says",
    [
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "PNG, JPEG or WebP"),
        (b"\x89PNG\r\n\x1a\n" + b"\0" * 40, "PNG, JPEG or WebP"),
        (b"\0" * (2 * 1024 * 1024 + 1), "2 MB"),
    ],
)
def test_a_logo_that_is_not_a_small_image_is_refused(tmp_path: Path, data: bytes, says: str) -> None:
    with pytest.raises(look_brand.BrandError, match=says):
        look_brand.save_brand_logo(tmp_path, data)


def test_a_huge_logo_is_refused(tmp_path: Path) -> None:
    buf = io.BytesIO()
    Image.new("1", (5000, 10)).save(buf, format="PNG")
    with pytest.raises(look_brand.BrandError, match="4096"):
        look_brand.save_brand_logo(tmp_path, buf.getvalue())


# --- what the cards receive ---------------------------------------------------------------


def test_only_the_title_page_and_the_closing_card_get_the_brand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    look = _look(tmp_path, monkeypatch)
    name = look_brand.save_brand_logo(look.root, _png())
    look = _look(tmp_path, monkeypatch, {"logo": name, "line": "Bromma PK"})
    title = look_brand.brand_json(look, "title_page")
    assert title == {"logo": (look.root / "brand" / name).resolve().as_uri(), "line": "Bromma PK"}
    assert look_brand.brand_json(look, "closing") == title
    assert look_brand.brand_json(look, "slate") is None
    assert look_brand.brand_json(_look(tmp_path / "b", monkeypatch), "title_page") is None


def test_a_logo_that_is_gone_or_a_symlink_draws_no_logo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    look = _look(tmp_path, monkeypatch, {"logo": "brand-0123456789ab.png", "line": "Bromma PK"})
    assert look_brand.brand_json(look, "title_page") == {"logo": None, "line": "Bromma PK"}
    secret = tmp_path / "secret.png"
    secret.write_bytes(_png())
    (look.root / "brand").mkdir()
    (look.root / "brand" / "brand-0123456789ab.png").symlink_to(secret)
    assert look_brand.brand_json(look, "title_page")["logo"] is None


def test_a_look_without_a_brand_sends_the_context_it_always_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith import look_tools

    look = _look(tmp_path, monkeypatch)
    _case, ctx = look_tools.sample_contexts(look, "title_page", "default", tmp_path)[0]
    assert "brand" not in ctx.data


def test_the_brand_is_the_title_pages_corner_mark(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real render: the logo's red sits top-left, the centre stays clear,
    and the card's text stays where it was."""
    from splitsmith import look_tools
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    look = _look(tmp_path, monkeypatch)
    name = look_brand.save_brand_logo(look.root, _png(256))
    branded = _look(tmp_path, monkeypatch, {"logo": name, "line": "Bromma PK"})
    plain = _look(tmp_path / "plain", monkeypatch)

    def render(lk: looks.Look) -> Image.Image:
        _case, ctx = look_tools.sample_contexts(lk, "title_page", "default", tmp_path)[0]
        png = raster.render_template(
            looks.template_for(lk, "title_page"), context=ctx, width=1280, height=720
        )
        return Image.open(io.BytesIO(png)).convert("RGBA")

    try:
        with ChromiumRasterizer() as raster:
            with_brand = render(branded)
            without = render(plain)
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")
    r, g, b, a = with_brand.getpixel((70, 70))
    assert a > 200 and r > 180 and g < 80 and b < 80, (r, g, b, a)
    assert without.getpixel((70, 70))[3] == 0
    assert with_brand.getpixel((640, 130))[3] == 0  # the centre is the event's
    assert with_brand.getpixel((1210, 70)) == without.getpixel((1210, 70))  # the shooters' corner

    # The card's white title text did not move.
    def title_top(im: Image.Image) -> int:
        px = im.load()
        for y in range(im.height):
            for x in range(0, im.width, 2):
                r, g, b, a = px[x, y]
                if a > 200 and r > 230 and g > 230 and b > 230:
                    return y
        return im.height

    assert title_top(with_brand) == title_top(without)


# --- the routes (local only) --------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from .test_ui_server import _match_create_app, _MatchClient

    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path / "home"))
    client = _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="Brand"))
    colors = {k: list(v) for k, v in looks.load_look("splitsmith").manifest.colors.items()}
    r = client.put("/api/looks/club", json={"label": "Club", "base": "splitsmith", "colors": colors})
    assert r.status_code in (200, 201), r.text
    return client


def test_a_brand_logo_is_uploaded_served_and_saved_with_the_look(client) -> None:
    r = client.post("/api/looks/club/brand-logo", files={"file": ("x.svg", _png(), "image/svg+xml")})
    assert r.status_code == 201, r.text
    logo = r.json()
    assert logo["logo"].startswith("brand-") and logo["url"] == f"/api/looks/club/brand/{logo['logo']}"
    served = client.get(logo["url"])
    assert served.status_code == 200 and served.content == _png()
    assert served.headers["x-content-type-options"] == "nosniff"
    colors = {k: list(v) for k, v in looks.load_look("splitsmith").manifest.colors.items()}
    body = {
        "label": "Club",
        "base": "splitsmith",
        "colors": colors,
        "brand": {"logo": logo["logo"], "line": "Bromma PK"},
    }
    assert client.put("/api/looks/club", json=body).status_code == 200
    assert looks.load_look("club").manifest.brand == looks.LookBrand(logo=logo["logo"], line="Bromma PK")
    assert client.get("/api/looks/club").json()["body"]["brand"]["line"] == "Bromma PK"


def test_a_bad_brand_logo_is_a_422_and_other_files_are_404(client) -> None:
    r = client.post("/api/looks/club/brand-logo", files={"file": ("x.png", b"<svg/>", "image/png")})
    assert r.status_code == 422 and "PNG, JPEG or WebP" in r.json()["detail"]
    assert client.get("/api/looks/club/brand/look.json").status_code == 404
    assert client.get("/api/looks/club/brand/brand-000000000000.png").status_code == 404
    assert (
        client.post(
            "/api/looks/splitsmith/brand-logo", files={"file": ("x.png", _png(), "image/png")}
        ).status_code
        == 404
    )
