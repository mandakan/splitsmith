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

import io
import logging
import math
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from PIL import Image
from playwright.sync_api import Browser, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

if TYPE_CHECKING:
    from .look_template import TemplateContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TemplateProbe:
    """What :meth:`ChromiumRasterizer.probe_template` saw in a loaded
    template at its poster frame (issue #1262): the script errors, the
    animation hooks, the first font family of every visible piece of text,
    and text that runs past the canvas or its own clipped box (with the
    overrun in CSS pixels). The authoring checks (``look_tools``) word it."""

    errors: tuple[str, ...] = ()
    duration: float = 0.0
    poster: float = 0.0
    has_seek: bool = False
    families: tuple[str, ...] = ()
    overflow: tuple[tuple[str, int], ...] = ()


@dataclass
class TemplateFrames:
    """One template's frames at a fixed rate: ``frame_count`` raw RGBA
    buffers of ``width * height * 4`` bytes from ``frames``, rendered
    lazily (a browser context stays open until the iterator is exhausted
    or :meth:`close` is called). ``duration`` is the template's own
    ``duration()``; a still reports 0 and one frame."""

    duration: float
    frame_count: int
    width: int
    height: int
    frames: Iterator[bytes]
    #: Releases what the load opened (the browser context). Idempotent,
    #: and called by :meth:`close` whether or not a frame was ever pulled:
    #: closing an unstarted generator never runs its ``finally``, and a
    #: cached segment pulls no frame.
    release: Callable[[], None] = lambda: None

    def close(self) -> None:
        close = getattr(self.frames, "close", None)
        if close is not None:
            close()
        self.release()


class Rasterizer(Protocol):
    """One HTML document in, one canvas-sized PNG out.

    ``overlay_summary.build_hold_still`` (Task 6R-3) composes through
    this protocol rather than importing Playwright directly, so its own
    unit tests inject a fake implementation and never launch a browser --
    the same seam ``compare.mp4_grid.Runner`` gives ``subprocess.run``.
    """

    def png(self, html: str, *, width: int, height: int) -> bytes: ...

    def render_template(self, template: Path, *, context: TemplateContext, width: int, height: int) -> bytes:
        """A Look template (``splitsmith.look_template``) to an alpha PNG:
        ``context`` is installed as ``window.splitsmith`` before the
        document's scripts run, the template is rendered at its poster
        (``poster()``, else the midpoint of ``duration()``, else 0)."""
        ...

    def engine_version(self) -> str:
        """The rendering engine's version, part of every frame digest."""
        ...

    def render_template_frames(
        self,
        template: Path,
        *,
        context: TemplateContext,
        width: int,
        height: int,
        fps: float,
        max_seconds: float,
    ) -> TemplateFrames:
        """The template's frames at ``fps``: one at ``seek(0)`` for a
        still, ``ceil(min(duration, max_seconds) * fps)`` for an
        animation, rendered lazily."""
        ...


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


class TemplateScriptError(RuntimeError):
    """A Look template's own script threw while the page loaded or
    mounted. The page still screenshots (blank), so the renderer raises
    this instead and the card is skipped rather than shipped textless."""


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

    _POSTER = (
        "typeof window.poster === 'function' ? Number(window.poster()) || 0 : "
        "(typeof window.duration === 'function' ? (Number(window.duration()) || 0) / 2 : 0)"
    )
    _DURATION = "typeof window.duration === 'function' ? Number(window.duration()) || 0 : 0"

    def engine_version(self) -> str:
        if self._browser is None:
            raise RuntimeError("ChromiumRasterizer.engine_version() called outside its own 'with' block")
        return str(self._browser.version)

    def _open_template(self, template: Path, *, context: TemplateContext, width: int, height: int):  # type: ignore[no-untyped-def]
        """A fresh context with ``window.splitsmith`` installed and the
        template loaded past ``load`` and ``document.fonts.ready``:
        ``(browser_context, page, errors)``, where ``errors`` collects
        ``pageerror`` messages from before navigation on. An exception
        thrown by the template's own script reaches Playwright only as
        that event: ``goto`` and ``screenshot`` both succeed and hand back
        a fully transparent PNG, which the caller would composite as a
        textless card. Listening before navigation turns that into the
        error the caller already skips the card on."""
        if self._browser is None:
            raise RuntimeError(
                "ChromiumRasterizer template rendering called outside its own 'with' block -- the browser is "
                "only live between __enter__ and __exit__"
            )
        browser_context = self._browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=DEVICE_SCALE_FACTOR,
        )
        errors: list[str] = []
        try:
            browser_context.add_init_script(context.init_script())
            page = browser_context.new_page()
            page.on("pageerror", lambda error: errors.append(getattr(error, "message", None) or str(error)))
            page.goto(template.resolve().as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
        except BaseException:
            browser_context.close()
            raise
        return browser_context, page, errors

    @staticmethod
    def _seek(page, seconds: float) -> None:  # type: ignore[no-untyped-def]
        page.evaluate(f"typeof window.seek === 'function' ? window.seek({seconds}) : undefined")

    @staticmethod
    def _check(errors: list[str], template: Path) -> None:
        if errors:
            raise TemplateScriptError(f"{template.name}: {errors[0]}")

    _PROBE = """(() => {
      const families = new Set();
      const overflow = [];
      const W = window.innerWidth, H = window.innerHeight;
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const text = node.textContent.trim();
        if (!text) continue;
        const el = node.parentElement;
        if (!el || el.closest('script, style')) continue;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        let seen = true;
        for (let a = el; a; a = a.parentElement) {
          if (Number(getComputedStyle(a).opacity) === 0) { seen = false; break; }
        }
        if (!seen) continue;
        families.add(cs.fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, ''));
        const range = document.createRange();
        range.selectNodeContents(node);
        const full = range.getBoundingClientRect();
        if (full.width === 0 && full.height === 0) continue;
        // What shows is the text cut by every ancestor that clips: an
        // ellipsized label inside a band is not text running off the card.
        // The body and the root are not boxes: a template's
        // ``body { overflow: hidden }`` over absolute content is 0 px tall,
        // and the viewport is what clips there (``past`` below).
        let r = {left: full.left, right: full.right, top: full.top, bottom: full.bottom};
        let ellipsis = cs.textOverflow === 'ellipsis';
        for (let a = el; a && a !== document.body && a !== document.documentElement; a = a.parentElement) {
          const acs = getComputedStyle(a);
          if (acs.overflow === 'visible') continue;
          if (acs.textOverflow === 'ellipsis') ellipsis = true;
          const c = a.getBoundingClientRect();
          r = {left: Math.max(r.left, c.left), right: Math.min(r.right, c.right),
               top: Math.max(r.top, c.top), bottom: Math.min(r.bottom, c.bottom)};
        }
        if (r.right <= r.left || r.bottom <= r.top) continue;
        const past = Math.max(0, -r.left, r.right - W, -r.top, r.bottom - H);
        // Cut off by a clipping ancestor: a finding, unless the text ends in
        // an ellipsis (its own or its clipping box's), which is the template
        // saying it is fine.
        const cut = ellipsis ? 0 : Math.max(0, r.left - full.left, full.right - r.right,
                                               r.top - full.top, full.bottom - r.bottom);
        const by = Math.round(Math.max(past, cut));
        if (by > 1) overflow.push([text.slice(0, 60), by]);
      }
      return {families: [...families], overflow, hasSeek: typeof window.seek === 'function'};
    })()"""

    def probe_template(
        self, template: Path, *, context: TemplateContext, width: int, height: int
    ) -> TemplateProbe:
        """Load ``template`` as :meth:`render_template` does (the context,
        fonts, the poster seek, the fit policy) and report what the
        authoring checks need, instead of a picture: script errors from
        any point of the load, ``duration()`` / ``poster()`` / whether
        ``seek`` exists, the font families visible text uses, and text
        that overruns the canvas or a clipped box. Never raises for a
        template's own error; that is a finding, not a failure."""
        if self._browser is None:
            raise RuntimeError(
                "ChromiumRasterizer.probe_template() called outside its own 'with' block -- the browser is "
                "only live between __enter__ and __exit__"
            )
        browser_context, page, errors = self._open_template(
            template, context=context, width=width, height=height
        )

        def hook(call: Callable[[], Any], default: Any) -> Any:
            # A template's own hook that throws is a finding like a load error.
            try:
                return call()
            except PlaywrightError as exc:
                errors.append(str(exc).removeprefix("Page.evaluate: ").splitlines()[0])
                return default

        try:
            duration = float(hook(lambda: page.evaluate(self._DURATION), 0) or 0)
            poster = float(hook(lambda: page.evaluate(self._POSTER), 0) or 0)
            hook(lambda: self._seek(page, poster), None)
            page.evaluate("document.fonts.ready")
            hook(lambda: page.evaluate("window.__splitsmithFit && window.__splitsmithFit()"), None)
            seen = page.evaluate(self._PROBE)
            return TemplateProbe(
                errors=tuple(errors),
                duration=duration,
                poster=poster,
                has_seek=bool(seen["hasSeek"]),
                families=tuple(seen["families"]),
                overflow=tuple((str(t), int(b)) for t, b in seen["overflow"]),
            )
        finally:
            browser_context.close()

    def render_template(self, template: Path, *, context: TemplateContext, width: int, height: int) -> bytes:
        """Render a Look template to a ``width`` x ``height`` alpha PNG at
        its poster frame (``poster()``, else the midpoint of
        ``duration()``, else 0: a still renders at 0). The template is
        navigated to by ``file://`` URL so its own relative references and
        the ``file://`` script URLs in ``assets.shared`` resolve. After
        load: fonts, the poster seek, fonts again (a template may add a
        face while mounting), the fit policy when the template defines
        it, then the same transparent screenshot :meth:`png` takes.
        """
        if self._browser is None:
            raise RuntimeError(
                "ChromiumRasterizer.render_template() called outside its own 'with' block -- the browser is "
                "only live between __enter__ and __exit__"
            )
        browser_context, page, errors = self._open_template(
            template, context=context, width=width, height=height
        )
        try:
            poster = page.evaluate(self._POSTER)
            self._seek(page, float(poster or 0))
            page.evaluate("document.fonts.ready")
            page.evaluate("window.__splitsmithFit && window.__splitsmithFit()")
            self._check(errors, template)
            return page.screenshot(type="png", omit_background=True)
        finally:
            browser_context.close()

    def render_template_frames(
        self,
        template: Path,
        *,
        context: TemplateContext,
        width: int,
        height: int,
        fps: float,
        max_seconds: float,
    ) -> TemplateFrames:
        """Load ``template`` once and yield its frames at ``fps``: a still
        yields one frame at ``seek(0)``; an animated template yields
        ``ceil(min(duration, max_seconds) * fps)`` frames at ``seek(i /
        fps)``, the last one taken at the end of that span (it is the
        frame the renderers hold for the rest of the card, so it must be
        the finished state, not one frame short of it). The fit policy
        runs once, at the poster, before any frame is sampled: a transform
        mid-animation inflates what ``fit.js`` measures, so fitting at
        t=0 would shrink a long card's text below what the preview (the
        poster) shows. A ``pageerror`` at any point raises
        :class:`TemplateScriptError` from the iterator and closes the
        context; :meth:`TemplateFrames.close` releases it either way.
        """
        if self._browser is None:
            raise RuntimeError(
                "ChromiumRasterizer.render_template_frames() called outside its own 'with' block -- the "
                "browser is only live between __enter__ and __exit__"
            )
        browser_context, page, errors = self._open_template(
            template, context=context, width=width, height=height
        )
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                browser_context.close()

        try:
            duration = float(page.evaluate(self._DURATION) or 0)
            self._check(errors, template)
            poster = page.evaluate(self._POSTER)
            self._seek(page, float(poster or 0))
            page.evaluate("document.fonts.ready")
            page.evaluate("window.__splitsmithFit && window.__splitsmithFit()")
            self._check(errors, template)
        except BaseException:
            release()
            raise
        shown = min(duration, max_seconds) if duration > 0 else 0.0
        count = max(1, math.ceil(shown * fps - 1e-9)) if shown > 0 else 1
        times = [round(index / fps, 6) for index in range(count)]
        if shown > 0:
            times[-1] = round(shown, 6)

        def generate() -> Iterator[bytes]:
            try:
                for seconds in times:
                    self._seek(page, seconds)
                    self._check(errors, template)
                    png = page.screenshot(type="png", omit_background=True)
                    with Image.open(io.BytesIO(png)) as image:
                        yield image.convert("RGBA").tobytes()
            finally:
                release()

        return TemplateFrames(
            duration=duration,
            frame_count=count,
            width=width,
            height=height,
            frames=generate(),
            release=release,
        )
