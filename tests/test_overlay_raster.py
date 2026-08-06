"""Tests for ``overlay_raster`` (issue #683 amendment, Task 6R-2).

Two tiers, deliberately kept apart:

- **Unit tests** never launch a browser. They drive
  :class:`~splitsmith.overlay_raster.ChromiumRasterizer` against
  hand-written recording doubles standing in for Playwright's
  ``Browser``/``BrowserContext``/``Page`` objects, so a test can assert
  exactly which calls were made and in what order without paying for a
  real Chromium process. The doubles deliberately omit a
  ``set_content`` method -- if a future edit ever routes ``png()``
  through ``page.set_content()`` instead of ``page.goto(file://...)``,
  the call raises ``AttributeError`` immediately rather than silently
  passing.
- **Integration tests** (``@pytest.mark.integration``) launch a real
  Chromium via Playwright. ``SPLITSMITH_REQUIRE_INTEGRATION`` (set by
  CI) escalates any skip of a marked test to a failure -- see
  ``tests/conftest.py``'s "integration-suite skip gate" -- so these
  catch ``RasterizerUnavailableError`` and skip only when the browser is
  genuinely missing, exactly mirroring ``ffmpeg_available()`` gated
  tests elsewhere in this suite.

The font test is the one that matters most: see
``test_bundled_font_face_actually_loads_not_the_browsers_fallback``.
"""

from __future__ import annotations

import io
import types
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Error as PlaywrightError

from splitsmith import overlay_raster
from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

# --- recording doubles for Playwright's Browser/BrowserContext/Page -------
#
# None of these define ``set_content`` on purpose -- see the module
# docstring above.


class _RecordingPage:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.goto_url: str | None = None
        self.goto_file_content: str | None = None

    def goto(self, url: str, *, wait_until: str | None = None) -> None:
        self.calls.append(("goto", url, wait_until))
        self.goto_url = url
        assert url.startswith("file://"), f"expected a file:// URL, got {url!r}"
        path = Path(url[len("file://") :])
        self.goto_file_content = path.read_text(encoding="utf-8")

    def evaluate(self, expression: str):
        self.calls.append(("evaluate", expression))
        return None

    def screenshot(self, *, type: str, omit_background: bool) -> bytes:  # noqa: A002
        self.calls.append(("screenshot", type, omit_background))
        return b"FAKE-PNG-BYTES"


class _BoomOnScreenshotPage(_RecordingPage):
    def screenshot(self, *, type: str, omit_background: bool) -> bytes:  # noqa: A002
        self.calls.append(("screenshot", type, omit_background))
        raise RuntimeError("screenshot boom")


class _RecordingContext:
    def __init__(self, *, page_factory=_RecordingPage) -> None:
        self._page_factory = page_factory
        self.pages: list[_RecordingPage] = []
        self.closed = False
        self.viewport: dict | None = None
        self.device_scale_factor: int | None = None

    def new_page(self) -> _RecordingPage:
        page = self._page_factory()
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class _RecordingBrowser:
    def __init__(self, *, page_factory=_RecordingPage) -> None:
        self._page_factory = page_factory
        self.contexts: list[_RecordingContext] = []

    def new_context(self, *, viewport: dict, device_scale_factor: int) -> _RecordingContext:
        ctx = _RecordingContext(page_factory=self._page_factory)
        ctx.viewport = viewport
        ctx.device_scale_factor = device_scale_factor
        self.contexts.append(ctx)
        return ctx


# --- Rasterizer.png(): structure, determinism, the font-loading contract --


def test_png_outside_context_manager_raises_runtime_error() -> None:
    """A browser only lives between __enter__/__exit__; calling ``png()``
    without going through the context manager must not silently no-op or
    crash inside Playwright with a confusing ``NoneType`` error."""
    rasterizer = ChromiumRasterizer()
    with pytest.raises(RuntimeError, match="outside its own"):
        rasterizer.png("<html></html>", width=10, height=10)


def test_png_writes_html_to_a_real_file_and_navigates_via_file_url() -> None:
    """The one invariant the whole module exists to protect: ``png()``
    must write the document to disk and ``goto()`` a ``file://`` URL, and
    must never call ``page.set_content()`` -- the recording ``Page`` has
    no such method, so a regression here raises ``AttributeError``
    rather than passing quietly."""
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser()  # whitebox: bypass __enter__

    html = "<html><body>hello splitsmith</body></html>"
    result = rasterizer.png(html, width=640, height=360)

    assert result == b"FAKE-PNG-BYTES"
    browser = rasterizer._browser
    assert len(browser.contexts) == 1, "one context per png() call, not reused across calls"
    ctx = browser.contexts[0]
    assert ctx.closed is True, "the context must be closed before png() returns"
    assert len(ctx.pages) == 1
    page = ctx.pages[0]
    assert page.goto_file_content == html, "the exact HTML string must reach disk unmodified"


def test_png_pins_viewport_and_device_scale_factor_exactly() -> None:
    """Determinism: the browser must never be left to pick its own
    viewport or pixel density. A wrong implementation that used the
    browser's default context (or forwarded ``device_scale_factor``
    inconsistently) would produce host-dependent pixel dimensions."""
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser()

    rasterizer.png("<html></html>", width=800, height=450)

    ctx = rasterizer._browser.contexts[0]
    assert ctx.viewport == {"width": 800, "height": 450}
    assert ctx.device_scale_factor == overlay_raster.DEVICE_SCALE_FACTOR == 1


def test_png_waits_for_fonts_before_screenshotting() -> None:
    """A screenshot taken before webfonts settle renders in the fallback
    face -- the same silent failure the file:// constraint guards
    against, by a different route. This asserts the exact call order:
    navigate, then wait for ``document.fonts.ready``, then screenshot --
    not just that all three happened somewhere."""
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser()

    rasterizer.png("<html></html>", width=100, height=100)

    page = rasterizer._browser.contexts[0].pages[0]
    call_names = [call[0] for call in page.calls]
    assert call_names == ["goto", "evaluate", "screenshot"], page.calls
    assert page.calls[1] == ("evaluate", "document.fonts.ready")


def test_png_screenshots_with_omit_background_for_an_alpha_result() -> None:
    """The rasterizer returns an alpha PNG, not an opaque one:
    ``overlay_html.summary_html`` leaves its document background
    transparent because the result is alpha-composited over an
    already-composed freeze-frame still (Task 6R-3). If this ever
    flipped to an opaque screenshot without also changing the CSS side
    of that contract, the composited hold would paint over the
    footage instead of sitting on top of it."""
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser()

    rasterizer.png("<html></html>", width=100, height=100)

    page = rasterizer._browser.contexts[0].pages[0]
    assert page.calls[-1] == ("screenshot", "png", True)


def test_png_closes_its_context_even_when_screenshot_raises() -> None:
    """No leaked ``BrowserContext`` on an exception mid-call."""
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_BoomOnScreenshotPage)

    with pytest.raises(RuntimeError, match="screenshot boom"):
        rasterizer.png("<html></html>", width=100, height=100)

    ctx = rasterizer._browser.contexts[0]
    assert ctx.closed is True


# --- lifecycle: launch once, never leak on a failed or torn-down launch ---


class _StartRaises:
    def start(self):
        raise RuntimeError("no display available")


def test_enter_raises_rasterizer_unavailable_when_playwright_cannot_start(monkeypatch) -> None:
    monkeypatch.setattr(overlay_raster, "sync_playwright", lambda: _StartRaises())
    rasterizer = ChromiumRasterizer()

    with pytest.raises(RasterizerUnavailableError) as excinfo:
        with rasterizer:
            pass  # pragma: no cover - must not be reached

    assert overlay_raster.INSTALL_HINT in excinfo.value.detail
    assert "no display available" in excinfo.value.detail
    assert excinfo.value.summary  # non-empty, meant for a one-line render summary
    assert rasterizer._playwright is None
    assert rasterizer._browser is None


class _RecordingDriver:
    """Stands in for the object ``sync_playwright().start()`` returns."""

    def __init__(self, launch_exc: Exception) -> None:
        self.stopped = False
        self._launch_exc = launch_exc
        self.chromium = self

    def launch(self, *, channel: str, headless: bool):
        raise self._launch_exc

    def stop(self) -> None:
        self.stopped = True


def test_enter_stops_the_playwright_driver_when_chromium_launch_fails(monkeypatch) -> None:
    """A driver that started successfully but whose browser failed to
    launch (the missing-binary case this whole feature has to degrade
    on) must not leak: ``.stop()`` has to run before the exception
    propagates, or a preflight failure leaves a driver process behind
    on every failed render attempt."""
    driver = _RecordingDriver(PlaywrightError("Executable doesn't exist at ...headless_shell"))
    fake_module = types.SimpleNamespace(start=lambda: driver)
    monkeypatch.setattr(overlay_raster, "sync_playwright", lambda: fake_module)
    rasterizer = ChromiumRasterizer()

    with pytest.raises(RasterizerUnavailableError) as excinfo:
        with rasterizer:
            pass  # pragma: no cover - must not be reached

    assert driver.stopped is True
    assert rasterizer._playwright is None
    assert "Executable doesn't exist" in excinfo.value.detail


class _StoppableDriver:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _BoomOnCloseBrowser:
    def close(self) -> None:
        raise RuntimeError("browser close boom")


def test_exit_still_stops_playwright_when_browser_close_raises() -> None:
    """Requirement 1's other half: even a *teardown* failure must not
    leak the driver process. ``__exit__`` closes the browser and stops
    the driver in that order; if ``close()`` itself raises, ``stop()``
    must still run."""
    rasterizer = ChromiumRasterizer()
    driver = _StoppableDriver()
    rasterizer._playwright = driver  # type: ignore[assignment]
    rasterizer._browser = _BoomOnCloseBrowser()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="browser close boom"):
        rasterizer.__exit__(None, None, None)

    assert driver.stopped is True
    assert rasterizer._playwright is None
    assert rasterizer._browser is None


# --- integration: a real browser, canvas-sized output, the font contract --


@pytest.mark.integration
def test_renders_a_canvas_sized_nonblank_png() -> None:
    """Not much of a test on its own -- a PNG of the right size passes
    even with the wrong font loaded -- but it is the baseline sanity
    check the font test below builds on."""
    html = (
        "<html><body style='margin:0;padding:0;width:400px;height:300px;"
        "background:rgb(10,20,30)'></body></html>"
    )
    try:
        with ChromiumRasterizer() as rasterizer:
            png_bytes = rasterizer.png(html, width=400, height=300)
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))

    image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    assert image.size == (400, 300)
    # Non-blank: the explicit CSS background must have actually painted.
    r, g, b, a = image.getpixel((200, 150))
    assert (r, g, b) == (10, 20, 30)
    assert a > 0


def _font_probe_html(*, with_bundled_face: bool, text: str, font_size: int, canvas: int) -> str:
    """A minimal document isolating exactly one variable: whether the
    bundled ``@font-face`` rule is declared at all. Both variants share
    the identical ``font-family`` stack (``"Splitsmith Mono Test",
    monospace``) so the only thing that can move the rendered width is
    whether ``"Splitsmith Mono Test"`` actually resolves -- mirroring
    the real failure mode: a silently-failed ``@font-face`` falls
    through the same stack onto the browser's own ``monospace``.
    """
    from splitsmith.overlay_html import _font_face_url

    face_css = ""
    if with_bundled_face:
        font_url = _font_face_url("JetBrainsMono-Bold.ttf")
        face_css = f"""
@font-face {{
  font-family: "Splitsmith Mono Test";
  src: url("{font_url}") format("truetype");
  font-weight: 700;
}}
"""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
{face_css}
html, body {{ margin: 0; padding: 0; width: {canvas}px; height: 200px; background: transparent; }}
#probe {{
  position: absolute; top: 0; left: 0; white-space: nowrap;
  font-family: "Splitsmith Mono Test", monospace;
  font-size: {font_size}px; color: white;
}}
</style></head><body><div id="probe">{text}</div></body></html>"""


def _painted_width(png_bytes: bytes) -> int:
    """Pixel width of the non-transparent (rendered-glyph) region."""
    image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    alpha = image.split()[-1]
    bbox = alpha.getbbox()
    assert bbox is not None, "expected rendered (non-transparent) pixels, image is blank"
    return bbox[2] - bbox[0]


@pytest.mark.integration
def test_bundled_font_face_actually_loads_not_the_browsers_fallback() -> None:
    """THE regression test for the constraint that matters most.

    Renders the identical string twice through the real
    ``ChromiumRasterizer.png()`` -- once with the bundled
    ``@font-face`` declared, once without it at all -- and asserts the
    measured glyph widths DIFFER. A "PNG was produced" check passes
    even when ``@font-face`` silently fails (the whole point of the
    bug this guards against is that failure produces no error), so
    width is the only signal that proves the bundled TTF, and not the
    host's own monospace, is what actually painted.

    Verified while writing this test (not asserted here, since it
    would require reintroducing the bug to check): switching
    ``ChromiumRasterizer.png`` from ``page.goto(file://...)`` to
    ``page.set_content()`` collapses this measurement to a width
    difference of exactly 0 -- both documents fall back to the same
    browser-chosen monospace, because ``set_content()``'s opaque
    origin cannot resolve either document's ``file://`` font URL. With
    real navigation the measured difference on the dev host was 15px
    at a 3200x200 canvas, 72px font, 48-character string -- comfortably
    above the 5px threshold asserted below.
    """
    text = "0123456789OIl.:," * 3
    font_size = 72
    canvas = 3200

    try:
        with ChromiumRasterizer() as rasterizer:
            bundled_png = rasterizer.png(
                _font_probe_html(with_bundled_face=True, text=text, font_size=font_size, canvas=canvas),
                width=canvas,
                height=200,
            )
            fallback_png = rasterizer.png(
                _font_probe_html(with_bundled_face=False, text=text, font_size=font_size, canvas=canvas),
                width=canvas,
                height=200,
            )
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))

    bundled_width = _painted_width(bundled_png)
    fallback_width = _painted_width(fallback_png)

    assert abs(bundled_width - fallback_width) > 5, (
        f"bundled face width {bundled_width}px vs fallback width {fallback_width}px -- these "
        "should differ measurably if the bundled @font-face genuinely loaded. An equal (or "
        "near-equal) width means the custom face silently failed and both documents rendered "
        "in the browser's own fallback monospace -- the exact failure mode "
        "page.set_content() causes and page.goto(file://...) fixes."
    )
