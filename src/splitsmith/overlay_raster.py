"""The only browser-aware module in the overlay pipeline: HTML -> PNG.

See ``docs/superpowers/plans/2026-08-06-overlay-composition-seam-amendment.md``,
Task 6R-2. ``overlay_html.py`` builds a pure HTML document from declared
``Group``/``Element`` objects; this module is the one place that runs a
real browser to turn that document into pixels. The :class:`Rasterizer`
protocol is injected the way ``compare.mp4_grid.Runner`` already is: a
structural type, not a concrete import, so unit tests hand in a fake and
never launch Chromium. :class:`ChromiumRasterizer` is the only production
implementation.

Why headless Chromium via Playwright rather than something lighter: see
the amendment's "Why the pivot" and "Dependency change, stated plainly"
sections. In short, only a real box model closes the fitter defect class
that cost three review rounds, and Playwright's pinned-Chromium install
is what keeps rendered pixels stable across the dev host, CI, the hosted
deployment and the self-hosted workers -- a system browser's version
moves under you and pixel output moves with it.

**The constraint that matters most, measured rather than assumed.**
``overlay_html.py``'s ``@font-face`` rules point at ``file://`` URLs
naming the bundled TTFs. Handing that HTML to Chromium via
``page.set_content()`` gives the document an opaque/``about:blank``
origin that cannot resolve a local file URL -- the ``@font-face`` rule
silently fails and Chromium substitutes whatever monospace the host
happens to have, with no error, no warning and no exception. Measured on
the dev host, rendering identical text once with the bundled face
genuinely loaded and once forced onto the browser's own fallback:

- ``page.set_content()``: the two renders are pixel-identical -- the
  custom face never loaded, so "bundled" and "fallback" collapse onto
  the same output.
- ``page.goto(f"file://{path}")``: the two renders measurably differ,
  proving the bundled face is what actually painted the bundled
  document.

See ``overlay_html.font_face_url``'s docstring for the exact numbers.
:meth:`ChromiumRasterizer.png` therefore ALWAYS writes the HTML to a real
file and navigates to it -- **never** ``page.set_content()``. Do not
"simplify" this without re-measuring; ``test_overlay_raster.py`` carries
an integration test built specifically to catch that regression.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Protocol

from playwright.sync_api import Browser, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

logger = logging.getLogger(__name__)


class Rasterizer(Protocol):
    """One HTML document in, one canvas-sized PNG out.

    ``overlay_summary.build_hold_still`` (Task 6R-3) composes through
    this protocol rather than importing Playwright directly, so its own
    unit tests inject a fake implementation and never launch a browser --
    the same seam ``compare.mp4_grid.Runner`` gives ``subprocess.run``.
    """

    def png(self, html: str, *, width: int, height: int) -> bytes: ...


#: The headless-shell channel, not the full browser build. Verified on
#: the dev host at build 1223: the shell channel renders the same
#: screenshot byte for byte (4865 bytes either way) against the full
#: browser, for 260M installed versus 377M -- a 31% saving for zero
#: pixel difference. See the amendment's "Dependency change" section.
#:
#: ``playwright.sync_api.BrowserType.executable_path`` reports the
#: DEFAULT (full) browser's path even when this channel is what actually
#: launches -- it answers "what would launch with no channel given", not
#: "what did launch", so it must never be used to assert which binary is
#: in play.
CHROMIUM_CHANNEL = "chromium-headless-shell"

#: Pinned so the browser can never choose its own pixel density. A HiDPI
#: dev host defaulting to a 2x backing store would double every pixel of
#: the screenshot against a 1x CI runner for the same ``width``/``height``
#: arguments, breaking cross-machine determinism the same way an
#: unpinned system font would.
DEVICE_SCALE_FACTOR = 1

#: The one-time step every environment that rasterizes an overlay needs
#: -- the browser is not vendored in the wheel. Repeated in
#: :func:`_unavailable`'s message so a render failure tells the operator
#: exactly what to run rather than just that something is missing.
INSTALL_HINT = "uv run playwright install chromium --only-shell"


class RasterizerUnavailableError(RuntimeError):
    """No usable Chromium could be launched.

    Two spellings, mirroring ``compare.mp4_grid.OverlayDegradation``
    without importing it -- this module stays independent of
    ``mp4_grid`` so Task 6R-3 controls the direction of that dependency,
    not the other way around. ``.summary`` is the short clause for a
    render's final summary line; ``.detail`` is the full story, meant to
    be printed once before an encode starts.

    Degradation is required, not optional (see the amendment): 6R-3
    catches this around a whole render's ``with ChromiumRasterizer() as
    rasterizer:`` block and proceeds without the summary hold, exactly
    the way a drawtext-less ffmpeg build already degrades the running
    clock (``mp4_grid._drawtext_degradation``). It must never fall back
    to a second rendering engine -- maintaining two is what this
    amendment exists to stop.
    """

    def __init__(self, summary: str, detail: str) -> None:
        super().__init__(detail)
        self.summary = summary
        self.detail = detail


def _unavailable(exc: Exception) -> RasterizerUnavailableError:
    """The degradation notice, naming everything that is actually lost.

    Since issue #693 that is more than it used to be: the per-tile shot
    counters and split labels are rasterized through this same browser,
    not just the freeze-frame stage summary. Understating it here would
    hand the operator a message that says "summary omitted" while the
    whole composited overlay is missing from their render -- the failure
    is already quiet enough (a finished MP4, no exception, no non-zero
    exit) without the one line that describes it being wrong.
    """
    return RasterizerUnavailableError(
        summary="overlay content omitted: no usable Chromium",
        detail=(
            "Playwright could not launch a Chromium browser, so everything the overlay "
            "composites is omitted: the per-tile shot counters and split labels, and the "
            "stage summary's text. The running clock still draws (it is an ffmpeg drawtext "
            "filter and needs no browser) and the rest of the render proceeds. The browser "
            f"is not vendored in the wheel -- install it once per environment with "
            f"'{INSTALL_HINT}' (dev host, CI, the hosted image, and the self-hosted worker "
            f"all need this separately). Original error: {exc}"
        ),
    )


class ChromiumRasterizer:
    """Playwright-backed :class:`Rasterizer`. Reuses one browser across a whole render.

    A 12-stage match rasterizes 12 times; process startup dominates a
    per-stage launch, so this is a context manager that launches exactly
    once and is handed to every stage's :meth:`png` call::

        with ChromiumRasterizer() as rasterizer:
            for stage in stages:
                png_bytes = rasterizer.png(stage_html, width=w, height=h)

    ``__enter__`` is where the browser actually launches, and where a
    missing install surfaces as :class:`RasterizerUnavailableError` -- so it
    doubles as the degradation preflight the amendment calls for.
    Attempting the real launch is a truer test than guessing at an
    install path in isolation, and it costs nothing extra: the render
    needs a live browser instance anyway, so the preflight and the first
    launch are the same operation. On a failed launch nothing is leaked:
    a started ``Playwright`` driver with no browser is stopped before the
    exception is raised, and ``__exit__`` closes the browser and stops
    the driver in a ``try``/``finally`` so an exception raised *inside*
    the ``with`` block still tears both down.

    Each :meth:`png` call opens and closes its own ``BrowserContext``.
    ``device_scale_factor`` and ``viewport`` are Playwright
    context-creation-time-only settings, so pinning them per call (rather
    than once on the browser) is not optional -- and a fresh context per
    stage means one stage's page state can never bleed into the next
    stage's screenshot.
    """

    def __init__(self, *, channel: str = CHROMIUM_CHANNEL, headless: bool = True) -> None:
        self._channel = channel
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> ChromiumRasterizer:
        try:
            self._playwright = sync_playwright().start()
        except Exception as exc:  # pragma: no cover - defensive; no known trigger on a synced env
            raise _unavailable(exc) from exc
        try:
            self._browser = self._playwright.chromium.launch(channel=self._channel, headless=self._headless)
        except (PlaywrightError, OSError) as exc:
            self._playwright.stop()
            self._playwright = None
            raise _unavailable(exc) from exc
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        # Explicit lifecycle teardown, browser first then driver, both
        # guarded so a failure closing one does not skip closing the
        # other and leak a browser process on an exception mid-render.
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            self._browser = None
            if self._playwright is not None:
                self._playwright.stop()
            self._playwright = None

    def png(self, html: str, *, width: int, height: int) -> bytes:
        """Render ``html`` to a ``width`` x ``height`` PNG with an alpha channel.

        **Transparent, not opaque.** ``overlay_html.grid_html`` leaves
        ``html``/``body`` deliberately ``background: transparent``: the
        composited result is alpha-blended over an already
        blurred/dimmed freeze-frame still by
        ``overlay_summary.build_hold_still`` (Task 6R-3), so an opaque
        screenshot here would paint over that footage instead of sitting
        on top of it. ``omit_background=True`` on the screenshot call is
        the other half of that agreement -- the two must not drift apart;
        if a future document ever wants an opaque summary, both sides of
        this contract need to change together.

        Writes ``html`` to a real file under a fresh temporary directory
        and navigates to it with ``page.goto`` -- see the module
        docstring for why ``page.set_content()`` is never used here. The
        temp directory is removed before this method returns; nothing
        about the finished PNG depends on the file surviving past the
        navigation and the font loads it triggers.
        """
        if self._browser is None:
            raise RuntimeError(
                "ChromiumRasterizer.png() called outside its own 'with' block -- the browser is "
                "only live between __enter__ and __exit__"
            )
        with tempfile.TemporaryDirectory(prefix="splitsmith-overlay-raster-") as tmp:
            html_path = Path(tmp) / "summary.html"
            html_path.write_text(html, encoding="utf-8")
            context = self._browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=DEVICE_SCALE_FACTOR,
            )
            try:
                page = context.new_page()
                page.goto(html_path.resolve().as_uri(), wait_until="load")
                # A screenshot taken before webfonts finish loading
                # renders in the fallback face -- the same silent
                # failure the module docstring describes, by a different
                # route. ``document.fonts.ready`` is a Promise;
                # Playwright's ``evaluate`` awaits a returned promise
                # before handing control back, so this blocks until
                # every ``@font-face`` in the document has either loaded
                # or failed to. The same idiom is already used by
                # ``scripts/capture_hero_og.py`` for the same reason.
                page.evaluate("document.fonts.ready")
                # ``overlay_html.grid_html``'s fit policy (issue #683
                # F1) only *defines* ``window.__splitsmithFit`` --
                # nothing in that module calls it, because it has to run
                # after fonts are loaded (a shrink/drop decision made
                # against fallback-font metrics would be wrong the
                # instant the bundled face reflows everything under it)
                # and before the screenshot. This is that one call. A
                # document with no cell needing to fit (every band fits
                # its track at full size) still defines the function, so
                # calling it unconditionally costs nothing on the common
                # path -- ``window.__splitsmithFit &&`` guards only
                # against a caller handing this rasterizer HTML that
                # never went through ``overlay_html`` at all (e.g. a
                # test's own hand-built document).
                page.evaluate("window.__splitsmithFit && window.__splitsmithFit()")
                return page.screenshot(type="png", omit_background=True)
            finally:
                context.close()
