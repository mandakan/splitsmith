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
import json
import types
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Error as PlaywrightError

from splitsmith import look_sandbox, overlay_raster
from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

# --- recording doubles for Playwright's Browser/BrowserContext/Page -------
#
# None of these define ``set_content`` on purpose -- see the module
# docstring above.


def _png_2x2() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (2, 2), (1, 2, 3, 4)).save(buf, format="PNG")
    return buf.getvalue()


_PNG_2x2 = _png_2x2()


class _RecordingPage:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.goto_url: str | None = None
        self.goto_file_content: str | None = None
        self.handlers: dict[str, list] = {}
        #: Answers for ``evaluate``, matched by a needle the expression
        #: contains, in insertion order. The renderer reads two template
        #: facts: one expression starting ``typeof window.poster`` (the
        #: poster, or the midpoint, computed in the page) and one starting
        #: ``typeof window.duration``. Nothing answered means a still.
        self.answers: dict[str, object] = {}
        self.screenshots = 0

    def on(self, event: str, handler) -> None:  # noqa: ANN001 -- Playwright's own loose signature
        self.handlers.setdefault(event, []).append(handler)

    def goto(self, url: str, *, wait_until: str | None = None, timeout: float | None = None) -> None:
        self.calls.append(("goto", url, wait_until))
        self.goto_url = url
        if url.startswith(look_sandbox.ORIGIN):
            return  # a template: the sandbox answers its requests, not a file read
        assert url.startswith("file://"), f"expected a file:// URL, got {url!r}"
        path = Path(url[len("file://") :])
        self.goto_file_content = path.read_text(encoding="utf-8")

    def evaluate(self, expression: str, arg=None):  # noqa: ANN001
        self.calls.append(("evaluate", expression))
        for needle, value in self.answers.items():
            if needle in expression:
                return value
        return None

    #: What each guarded hook (``overlay_raster._GUARD_JS``) is recorded as,
    #: in the words the page-side expressions use.
    _HOOK_TEXT = {
        "duration": "typeof window.duration === 'function' ? Number(window.duration()) || 0 : 0",
        "poster": "typeof window.poster === 'function' ? Number(window.poster()) || 0 : 0",
        "fit": "window.__splitsmithFit && window.__splitsmithFit()",
        "fonts": "document.fonts.ready",
        "probe": "probe",
    }
    binding = None

    def wait_for_function(self, expression: str, *, arg=None, polling=None, timeout=None):  # noqa: ANN001
        """The sandbox's bounded call (#1266): answered like ``evaluate`` and
        delivered through the exposed binding, as the page's guard does."""
        kind = arg["kind"]
        text = (
            f"typeof window.seek === 'function' ? window.seek({arg['arg']}) : undefined"
            if kind == "seek"
            else self._HOOK_TEXT[kind]
        )
        value = self.evaluate(text)
        self.binding(None, arg["id"], json.dumps({"value": value}))
        return True

    def wait_for_timeout(self, ms: float) -> None:
        return None

    def screenshot(
        self, *, type: str, omit_background: bool, timeout: float | None = None
    ) -> bytes:  # noqa: A002
        self.calls.append(("screenshot", type, omit_background))
        self.screenshots += 1
        return _PNG_2x2


class _AnimatedPage(_RecordingPage):
    """A 0.5 s template with no ``poster()``: the page's own expression
    yields the midpoint."""

    def __init__(self) -> None:
        super().__init__()
        self.answers = {"typeof window.poster": 0.25, "typeof window.duration": 0.5}


class _PosterPage(_RecordingPage):
    """A 2 s template that names its poster frame."""

    def __init__(self) -> None:
        super().__init__()
        self.answers = {"typeof window.poster": 1.5, "typeof window.duration": 2.0}


class _ThrowingTemplatePage(_RecordingPage):
    """A page whose document throws during load: Playwright reports that
    through ``pageerror`` and never through ``goto`` or ``screenshot``."""

    def goto(self, url: str, *, wait_until: str | None = None, timeout: float | None = None) -> None:
        super().goto(url, wait_until=wait_until)
        for handler in self.handlers.get("pageerror", []):
            handler(types.SimpleNamespace(message="TypeError: window.splitsmith.nope is undefined"))


class _BoomOnScreenshotPage(_RecordingPage):
    def screenshot(
        self, *, type: str, omit_background: bool, timeout: float | None = None
    ) -> bytes:  # noqa: A002
        self.calls.append(("screenshot", type, omit_background))
        raise RuntimeError("screenshot boom")


class _RecordingContext:
    def __init__(self, *, page_factory=_RecordingPage) -> None:
        self._page_factory = page_factory
        self.pages: list[_RecordingPage] = []
        self.closed = False
        self.viewport: dict | None = None
        self.device_scale_factor: int | None = None
        self.init_scripts: list[str] = []
        self.routes: list[tuple[str, object]] = []
        self.socket_routes: list[str] = []
        self.bindings: dict[str, object] = {}
        self.options: dict = {}

    def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    def route(self, pattern: str, handler) -> None:  # noqa: ANN001
        self.routes.append((pattern, handler))

    def route_web_socket(self, pattern: str, handler) -> None:  # noqa: ANN001
        self.socket_routes.append(pattern)

    def expose_binding(self, name: str, callback) -> None:  # noqa: ANN001
        self.bindings[name] = callback

    def new_page(self) -> _RecordingPage:
        page = self._page_factory()
        page.binding = self.bindings.get("__splitsmithDeliver")
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class _RecordingBrowser:
    def __init__(self, *, page_factory=_RecordingPage) -> None:
        self._page_factory = page_factory
        self.contexts: list[_RecordingContext] = []
        self.closed = False
        self.version = "fake-browser"

    def new_context(self, *, viewport: dict, device_scale_factor: int, **options) -> _RecordingContext:
        ctx = _RecordingContext(page_factory=self._page_factory)
        ctx.viewport = viewport
        ctx.device_scale_factor = device_scale_factor
        ctx.options = options
        self.contexts.append(ctx)
        return ctx

    def close(self) -> None:
        self.closed = True

    def is_connected(self) -> bool:
        return not self.closed


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

    assert result == _PNG_2x2
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
    navigate, then wait for ``document.fonts.ready``, then run the
    fit-policy script (issue #683 F1's ``overlay_html._fit_script`` --
    also font-dependent: a shrink/drop decision made against fallback
    metrics would be wrong the instant the bundled face reflows
    everything under it, so this must run after fonts settle too), then
    screenshot -- not just that all four happened somewhere."""
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser()

    rasterizer.png("<html></html>", width=100, height=100)

    page = rasterizer._browser.contexts[0].pages[0]
    call_names = [call[0] for call in page.calls]
    assert call_names == ["goto", "evaluate", "evaluate", "screenshot"], page.calls
    assert page.calls[1] == ("evaluate", "document.fonts.ready")
    assert page.calls[2] == ("evaluate", "window.__splitsmithFit && window.__splitsmithFit()")


def test_png_screenshots_with_omit_background_for_an_alpha_result() -> None:
    """The rasterizer returns an alpha PNG, not an opaque one:
    ``overlay_html.grid_html`` leaves its document background
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

    def launch(self, *, channel: str, headless: bool, args: list[str] | None = None):
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


class _RecordingLaunchDriver:
    """Stands in for the object ``sync_playwright().start()`` returns,
    whose ``.chromium.launch()`` succeeds and records the kwargs it was
    called with -- unlike ``_RecordingBrowser``, which is only ever
    installed *after* ``__enter__`` in the other unit tests above and so
    never sees a real ``launch()`` call at all."""

    def __init__(self, browser: object) -> None:
        self._browser = browser
        self.launch_kwargs: dict[str, object] | None = None
        self.chromium = self

    def launch(self, *, channel: str, headless: bool, args: list[str] | None = None) -> object:
        self.launch_kwargs = {"channel": channel, "headless": headless}
        self.launch_args = args
        return self._browser

    def stop(self) -> None:
        pass


def test_enter_launches_the_headless_shell_channel_not_the_full_browser(monkeypatch) -> None:
    """A wrong implementation that launched the full ``chromium`` browser
    (377M) rather than the intended ``chromium-headless-shell`` (260M)
    would still produce working screenshots -- the two channels are the
    same rendering engine -- so nothing about *behaviour* catches the
    substitution. Only an assertion on what ``launch()`` was actually
    called with does. Found missing by review: the other unit tests in
    this file install ``_RecordingBrowser`` directly onto ``_browser``
    and bypass ``__enter__`` entirely, so none of them ever observe a
    ``launch()`` call or its kwargs."""
    driver = _RecordingLaunchDriver(_RecordingBrowser())
    fake_module = types.SimpleNamespace(start=lambda: driver)
    monkeypatch.setattr(overlay_raster, "sync_playwright", lambda: fake_module)
    rasterizer = ChromiumRasterizer()

    with rasterizer:
        pass

    assert driver.launch_kwargs == {"channel": "chromium-headless-shell", "headless": True}
    assert driver.launch_kwargs["channel"] == overlay_raster.CHROMIUM_CHANNEL
    # A switch only this browser carries, which the sandbox's watchdog
    # finds it by (#1266).
    assert driver.launch_args[0].startswith("--splitsmith-rasterizer=")
    # Channels the sandbox's route never sees are closed at launch (review I2),
    # and a template's heap is capped (I1).
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in driver.launch_args
    assert "--dns-prefetch-disable" in driver.launch_args
    assert any(a.startswith("--js-flags=--max-old-space-size=") for a in driver.launch_args)


def test_the_watchdog_never_matches_a_process_without_a_marker() -> None:
    """An empty marker (no browser launched) must not match every process."""
    assert overlay_raster._pids_with("") == []
    assert overlay_raster._pids_with("--splitsmith-rasterizer=0000-not-running") == []


class _StoppableDriver:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _BoomOnCloseBrowser:
    def close(self) -> None:
        raise RuntimeError("browser close boom")

    def is_connected(self) -> bool:
        return True


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
    from splitsmith.overlay_html import font_face_url

    face_css = ""
    if with_bundled_face:
        font_url = font_face_url("JetBrainsMono-Bold.ttf")
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
    browser-chosen monospace, because ``set_content()``'s opaque origin
    cannot resolve either document's ``file://`` font URL.

    **The assertion below checks zero-vs-nonzero, not a magnitude
    threshold.** An earlier version asserted ``> 5px``, chosen against a
    15px difference measured on the dev host's ``chromium-headless-shell``
    channel. Review found that fragile: mutating the launch channel to
    the full ``chromium`` browser (a substitution nothing else in this
    file catches -- see
    ``test_enter_launches_the_headless_shell_channel_not_the_full_browser``
    below, added in the same round) reproduced a genuine, correctly-loaded
    bundled face, but the two Chromium builds' text shaping differed just
    enough that the measured difference dropped to exactly 5px -- tripping
    a ``> 5`` comparison by coincidence, on a passing scenario, for reasons
    having nothing to do with whether the font loaded. Any magnitude
    threshold is hostage to that kind of build-to-build shaping noise. The
    zero case has no such noise: under the bug both documents render
    through the identical fallback code path in the identical browser
    process, so the measured difference is exactly 0, not "close to 0" --
    there is nothing to threshold against. A real difference, however
    small, is real; only "no difference at all" indicates the bug.
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

    assert bundled_width != fallback_width, (
        f"bundled face width {bundled_width}px == fallback face width {fallback_width}px -- these "
        "should never come out exactly equal if the bundled @font-face genuinely loaded, since "
        "the bundled and fallback fonts have different glyph metrics. An exact match means the "
        "custom face silently failed and both documents rendered in the browser's own fallback "
        "monospace -- the exact failure mode page.set_content() causes and "
        "page.goto(file://...) fixes."
    )


# --- Rasterizer.render_template(): the Look template contract -------------


def _context_fixture():
    from splitsmith.look_template import TemplateContext, engine_block, shared_url

    return TemplateContext(
        theme={"ink": "#ffffff"},
        data={"groups": []},
        size={"width": 64, "height": 32},
        fps=30,
        engine=engine_block(css="body{}"),
        assets={"shared": shared_url()},
    )


def test_render_template_installs_the_context_before_navigating(tmp_path: Path) -> None:
    """``window.splitsmith`` must exist before the template's first script
    runs, so it goes in as an init script on the context, never as an
    ``evaluate`` after ``goto``."""
    template = tmp_path / "card.html"
    template.write_text("<!doctype html><body></body>", encoding="utf-8")
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser()

    out = rasterizer.render_template(template, context=_context_fixture(), width=64, height=32)

    assert out == _PNG_2x2
    ctx = rasterizer._browser.contexts[0]
    assert ctx.viewport == {"width": 64, "height": 32}
    # The context the page sees names the engine scripts on the sandbox's
    # origin (#1266), never by ``file://``.
    assert len(ctx.init_scripts) == 1
    assert f"{look_sandbox.ORIGIN}/shared" in ctx.init_scripts[0] and "file://" not in ctx.init_scripts[0]
    assert ctx.routes and ctx.routes[0][0] == "**/*" and ctx.socket_routes == ["**/*"]
    assert ctx.options == {"service_workers": "block"}
    page = ctx.pages[0]
    assert [c[0] for c in page.calls] == [
        "goto",
        "evaluate",
        "evaluate",
        "evaluate",
        "evaluate",
        "evaluate",
        "screenshot",
    ]
    assert page.goto_url == f"{look_sandbox.ORIGIN}/look/card.html"
    assert page.calls[1] == ("evaluate", "document.fonts.ready")
    assert page.calls[2][1].startswith("typeof window.poster"), "the poster is read before the seek"
    assert "window.seek" in page.calls[3][1]
    assert page.calls[-1] == ("screenshot", "png", True)
    assert ctx.closed


def test_render_template_closes_its_context_when_screenshot_raises(tmp_path: Path) -> None:
    template = tmp_path / "card.html"
    template.write_text("<!doctype html><body></body>", encoding="utf-8")
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_BoomOnScreenshotPage)
    with pytest.raises(RuntimeError, match="screenshot boom"):
        rasterizer.render_template(template, context=_context_fixture(), width=64, height=32)
    assert rasterizer._browser.contexts[0].closed


def test_render_template_outside_context_manager_raises_runtime_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="render_template"):
        ChromiumRasterizer().render_template(
            tmp_path / "x.html", context=_context_fixture(), width=1, height=1
        )


def test_render_template_raises_when_the_template_script_throws(tmp_path: Path) -> None:
    """A broken ``card.html`` throws in the page, which Playwright reports
    only as a ``pageerror`` event: ``goto`` and ``screenshot`` both succeed
    and the result is a fully transparent PNG. The renderer must listen
    for the event before navigating and refuse the blank result, so the
    card is skipped (``overlay_card``'s policy) rather than shipped
    textless."""
    template = tmp_path / "card.html"
    template.write_text("<!doctype html><body></body>", encoding="utf-8")
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_ThrowingTemplatePage)
    with pytest.raises(overlay_raster.TemplateScriptError, match="nope is undefined"):
        rasterizer.render_template(template, context=_context_fixture(), width=64, height=32)
    page = rasterizer._browser.contexts[0].pages[0]
    assert "pageerror" in page.handlers, "the listener must be registered before goto"
    assert rasterizer._browser.contexts[0].closed


# --- render_template_frames(): the animated half of the contract -----------


def _template(tmp_path: Path) -> Path:
    template = tmp_path / "card.html"
    template.write_text("<!doctype html><body></body>", encoding="utf-8")
    return template


def test_render_template_seeks_to_the_poster_when_the_template_defines_one(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_PosterPage)
    rasterizer.render_template(_template(tmp_path), context=_context_fixture(), width=4, height=2)
    page = rasterizer._browser.contexts[0].pages[0]
    seeks = [c[1] for c in page.calls if c[0] == "evaluate" and "window.seek(" in c[1]]
    assert len(seeks) == 1 and "window.seek(1.5)" in seeks[0]


def test_render_template_seeks_to_the_midpoint_without_a_poster(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_AnimatedPage)
    rasterizer.render_template(_template(tmp_path), context=_context_fixture(), width=4, height=2)
    page = rasterizer._browser.contexts[0].pages[0]
    seeks = [c[1] for c in page.calls if "window.seek(" in c[1]]
    assert len(seeks) == 1 and "window.seek(0.25)" in seeks[0]


def test_render_template_frames_yields_one_frame_for_a_still(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser()
    out = rasterizer.render_template_frames(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, fps=30, max_seconds=3.0
    )
    assert out.duration == 0 and out.frame_count == 1
    frames = list(out.frames)
    assert len(frames) == 1 and len(frames[0]) == 2 * 2 * 4
    assert frames[0][:4] == bytes((1, 2, 3, 4))
    assert rasterizer._browser.contexts[0].closed


def test_render_template_frames_seeks_each_frame_and_fits_once(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_AnimatedPage)
    out = rasterizer.render_template_frames(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, fps=10, max_seconds=3.0
    )
    assert (out.duration, out.frame_count) == (0.5, 5)
    assert len(list(out.frames)) == 5
    page = rasterizer._browser.contexts[0].pages[0]
    seeks = [c[1] for c in page.calls if "window.seek(" in c[1]]
    # The fit runs once, at the poster (the laid-out end state), before
    # any frame is sampled; the last sample is the end of the shown span,
    # not one frame short of it, since that frame is what the hold clones.
    assert [s[s.index("window.seek(") :] for s in seeks] == [
        f"window.seek({t}) : undefined" for t in (0.25, 0.0, 0.1, 0.2, 0.3, 0.5)
    ]
    fits = [i for i, c in enumerate(page.calls) if c[0] == "evaluate" and "__splitsmithFit" in c[1]]
    first_frame_seek = next(i for i, c in enumerate(page.calls) if "window.seek(0.0)" in c[1])
    assert len(fits) == 1 and fits[0] < first_frame_seek
    assert page.screenshots == 5


def test_render_template_frames_caps_at_max_seconds(tmp_path: Path) -> None:
    """A template longer than the card's hold renders only the frames the
    hold shows; the rest would be encoded and trimmed away."""
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_PosterPage)  # duration 2.0
    out = rasterizer.render_template_frames(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, fps=10, max_seconds=0.5
    )
    assert out.frame_count == 5
    assert len(list(out.frames)) == 5


def test_render_template_frames_closes_the_context_when_abandoned(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_AnimatedPage)
    out = rasterizer.render_template_frames(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, fps=10, max_seconds=3.0
    )
    next(out.frames)
    out.close()
    assert rasterizer._browser.contexts[0].closed


def test_render_template_frames_raises_on_a_page_error_mid_run(tmp_path: Path) -> None:
    """A script that throws inside ``seek`` for a later frame must fail the
    clip, not hand ffmpeg a run of frames that stops being the card."""

    class _ThrowsOnThirdSeek(_AnimatedPage):
        def evaluate(self, expression: str, arg=None):  # noqa: ANN001
            result = super().evaluate(expression, arg)
            if "window.seek(0.2)" in expression:
                for handler in self.handlers.get("pageerror", []):
                    handler(types.SimpleNamespace(message="ReferenceError: boom at frame 3"))
            return result

    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_ThrowsOnThirdSeek)
    out = rasterizer.render_template_frames(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, fps=10, max_seconds=3.0
    )
    with pytest.raises(overlay_raster.TemplateScriptError, match="frame 3"):
        list(out.frames)
    assert rasterizer._browser.contexts[0].closed


def test_engine_version_is_the_browser_version() -> None:
    rasterizer = ChromiumRasterizer()
    browser = _RecordingBrowser()
    browser.version = "131.0.6778.33"
    rasterizer._browser = browser
    assert rasterizer.engine_version() == "131.0.6778.33"


def test_render_template_frames_close_without_a_frame_closes_the_context(tmp_path: Path) -> None:
    """A cached segment never pulls a frame; closing the unstarted frames
    must still release the browser context the load opened."""
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_AnimatedPage)
    out = rasterizer.render_template_frames(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, fps=10, max_seconds=3.0
    )
    assert not rasterizer._browser.contexts[0].closed
    out.close()
    assert rasterizer._browser.contexts[0].closed
    out.close()  # idempotent


def test_render_template_frames_samples_the_end_of_a_capped_span(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_PosterPage)  # duration 2.0, poster 1.5
    out = rasterizer.render_template_frames(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, fps=10, max_seconds=0.5
    )
    assert len(list(out.frames)) == 5
    page = rasterizer._browser.contexts[0].pages[0]
    seeks = [c[1] for c in page.calls if "window.seek(" in c[1]]
    assert [s[s.index("window.seek(") :] for s in seeks] == [
        f"window.seek({t}) : undefined" for t in (1.5, 0.0, 0.1, 0.2, 0.3, 0.5)
    ]


# --- a transient screenshot failure (the CI flake on main, Oct 2026) ------------------------


class _FlakyScreenshotPage(_RecordingPage):
    """Chromium's transient "Unable to capture screenshot" once, then a PNG."""

    def screenshot(
        self, *, type: str, omit_background: bool, timeout: float | None = None
    ) -> bytes:  # noqa: A002
        from playwright.sync_api import Error as PlaywrightError

        self.calls.append(("screenshot", type, omit_background))
        if self.screenshots == 0:
            self.screenshots += 1
            raise PlaywrightError(
                "Page.screenshot: Protocol error (Page.captureScreenshot): Unable to capture screenshot"
            )
        self.screenshots += 1
        return _PNG_2x2


def test_png_retries_a_transient_screenshot_failure_once() -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_FlakyScreenshotPage)
    assert rasterizer.png("<html></html>", width=64, height=32) == _PNG_2x2
    page = rasterizer._browser.contexts[0].pages[0]
    assert page.screenshots == 2


def test_any_other_screenshot_failure_is_not_retried() -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_BoomOnScreenshotPage)
    with pytest.raises(RuntimeError, match="screenshot boom"):
        rasterizer.png("<html></html>", width=64, height=32)
    page = rasterizer._browser.contexts[0].pages[0]
    assert [c for c in page.calls if c[0] == "screenshot"] == [("screenshot", "png", True)]


def test_a_crashed_renderer_is_a_skipped_card_not_a_raw_error(tmp_path: Path) -> None:
    """Review I1: a template that runs its renderer out of memory crashes the
    page; that is the template's failure, worded like any other."""
    from playwright.sync_api import Error as PlaywrightError

    class _CrashOnScreenshot(_RecordingPage):
        def screenshot(
            self, *, type: str, omit_background: bool, timeout: float | None = None
        ) -> bytes:  # noqa: A002
            raise PlaywrightError("Target crashed")

    template = tmp_path / "card.html"
    template.write_text("<!doctype html><body></body>", encoding="utf-8")
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_CrashOnScreenshot)
    with pytest.raises(overlay_raster.TemplateScriptError, match="crashed"):
        rasterizer.render_template(template, context=_context_fixture(), width=64, height=32)
    assert rasterizer._browser.contexts[0].closed


def test_a_template_cannot_answer_for_a_call_it_cannot_name(tmp_path: Path) -> None:
    """Review M2: the page can call the delivery binding too, so an answer
    counts only for a call that is pending, by an id the page cannot guess
    (it could otherwise hide its own overflow from ``looks check``)."""
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_PosterPage)
    template = tmp_path / "card.html"
    template.write_text("<!doctype html><body></body>", encoding="utf-8")
    view = rasterizer._open_template(template, context=_context_fixture(), width=8, height=8)
    try:
        ids = []
        page = view.page
        real = page.wait_for_function

        def spy(expression, *, arg=None, polling=None, timeout=None):  # noqa: ANN001
            ids.append(arg["id"])
            # A guess at the next id, delivered ahead of the guard.
            view.deliver(None, 1, json.dumps({"value": 99}))
            view.deliver(None, ids[-1] + 1, json.dumps({"value": 99}))
            return real(expression, arg=arg, polling=polling, timeout=timeout)

        page.wait_for_function = spy
        assert view.call("duration") == 2.0
        assert view.call("poster") == 1.5
        assert ids[0] > 2**32 and abs(ids[1] - ids[0]) != 1
        assert view.delivered == {}
    finally:
        view.close()
