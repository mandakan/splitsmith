"""SVG logos: the check that admits an image and nothing else, and the
raster copy the Pillow surfaces draw (spec 2026-10-20)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from splitsmith import svg_image

CLEAN = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b"<!-- a club badge -->\n"
    b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
    b' viewBox="0 0 200 100">'
    b'<defs><linearGradient id="g"><stop offset="0" stop-color="#f00"/><stop offset="1" stop-color="#00f"/>'
    b"</linearGradient></defs>"
    b'<rect width="200" height="100" fill="url(#g)"/>'
    b'<use xlink:href="#mark"/><circle id="mark" cx="50" cy="50" r="20" style="fill: url(#g)"/>'
    b'<text x="100" y="60" font-size="24">Club</text>'
    b"</svg>"
)


def test_a_plain_svg_passes() -> None:
    assert svg_image.is_svg(CLEAN)
    svg_image.check_svg(CLEAN)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', id="script"),
        pytest.param(b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect/></svg>', id="onload"),
        pytest.param(
            b'<svg xmlns="http://www.w3.org/2000/svg"><g><rect onclick="x()"/></g></svg>', id="nested-on"
        ),
        pytest.param(
            b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><body xmlns="http://www.w3.org/1999/xhtml">'
            b"<p>hi</p></body></foreignObject></svg>",
            id="foreignObject",
        ),
        pytest.param(
            b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
            b'<image xlink:href="file:///etc/passwd"/></svg>',
            id="file-href",
        ),
        pytest.param(
            b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.example/x.png"/></svg>',
            id="remote-href",
        ),
        pytest.param(
            b'<svg xmlns="http://www.w3.org/2000/svg"><use href="other.svg#mark"/></svg>', id="relative-use"
        ),
        pytest.param(
            b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill: url(https://evil.example/p.svg#a)"/></svg>',
            id="css-url",
        ),
        pytest.param(
            b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import url(x.css);</style><rect/></svg>',
            id="css-import",
        ),
        pytest.param(
            b'<!DOCTYPE svg [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]>'
            b'<svg xmlns="http://www.w3.org/2000/svg"><text>&b;</text></svg>',
            id="entities",
        ),
        pytest.param(
            b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><rect/></a></svg>',
            id="javascript-link",
        ),
        pytest.param(
            b'<svg xmlns="http://www.w3.org/2000/svg"><rect>'
            b'<set attributeName="href" href="x"/></rect></svg>',
            id="animated-href",
        ),
        pytest.param(b'<svg xmlns="http://www.w3.org/2000/svg"><rect', id="not-well-formed"),
    ],
)
def test_anything_but_an_image_is_refused(payload: bytes) -> None:
    with pytest.raises(ValueError):
        svg_image.check_svg(payload)


def test_a_raster_data_url_and_fragments_are_fine() -> None:
    svg_image.check_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,iVBORw0KGgo="/>'
        b'<use href="#a"/><rect id="a" style="fill:url(#g); stroke: none"/></svg>'
    )


def test_a_png_is_not_an_svg() -> None:
    assert not svg_image.is_svg(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="PNG, JPEG, WebP or SVG"):
        svg_image.check_svg(b"\x89PNG\r\n\x1a\n")


def test_the_raster_keeps_the_aspect_and_caps_the_longest_side() -> None:
    wide = svg_image.rasterize_svg(CLEAN, max_side=400)
    assert wide.mode == "RGBA" and wide.size == (400, 200)
    tall = svg_image.rasterize_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="40mm">'
        b'<rect width="10" height="40"/></svg>',
        max_side=200,
    )
    assert tall.size[1] == 200 and abs(tall.size[0] - 50) <= 1
    # The drawing reached the pixels: the gradient rect is opaque.
    assert wide.getpixel((10, 10))[3] == 255


def test_physical_units_from_a_drawing_tool_draw_too() -> None:
    inkscape = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="105mm" viewBox="0 0 210 105">'
        b'<rect width="210" height="105" fill="#123"/></svg>'
    )
    svg_image.check_svg(inkscape)
    assert svg_image.rasterize_svg(inkscape, max_side=210).size == (210, 105)
    points = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="20pt" height="10pt">'
        b'<rect width="20" height="10"/></svg>'
    )
    drawn = svg_image.rasterize_svg(points, max_side=100).size
    assert drawn[0] == 100 and abs(drawn[1] - 50) <= 1


def test_open_image_reads_both_kinds(tmp_path: Path) -> None:
    svg = tmp_path / "logo-abc.svg"
    svg.write_bytes(CLEAN)
    png = tmp_path / "logo-def.png"
    Image.new("RGBA", (30, 20), (1, 2, 3, 255)).save(png)
    assert svg_image.open_image(svg).size == (svg_image.RASTER_MAX_SIDE, svg_image.RASTER_MAX_SIDE // 2)
    assert svg_image.open_image(png).size == (30, 20)
