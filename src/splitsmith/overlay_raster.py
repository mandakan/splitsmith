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
import json
import logging
import math
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from PIL import Image
from playwright.sync_api import Browser, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import look_sandbox

if TYPE_CHECKING:
    from .look_template import TemplateContext

logger = logging.getLogger(__name__)


#: Chromium's transient screenshot failure: a compositor frame was not ready.
#: It cleared on a second try every time it was seen (a CI flake, Oct 2026).
_TRANSIENT_SCREENSHOT = "Unable to capture screenshot"


def _screenshot(page, *, timeout: float | None = None) -> bytes:  # type: ignore[no-untyped-def]
    """The page as a transparent PNG; Chromium's transient "Unable to capture
    screenshot" is tried once more, every other error is raised as is.
    ``timeout`` (ms) bounds a template page's (``look_sandbox``)."""
    bound = {} if timeout is None else {"timeout": timeout}
    try:
        return page.screenshot(type="png", omit_background=True, **bound)
    except PlaywrightError as exc:
        if _TRANSIENT_SCREENSHOT not in str(exc) or isinstance(exc, PlaywrightTimeoutError):
            raise
        return page.screenshot(type="png", omit_background=True, **bound)


def describe_page_error(error: object, template_name: str) -> str:
    """A ``pageerror`` as the author needs it: its message, led by the line in
    the template when the stack's first frame in that file names one (the
    template editor and ``looks check``, #1265)."""
    message = getattr(error, "message", None) or str(error)
    stack = getattr(error, "stack", None) or ""
    hit = re.search(rf"{re.escape(template_name)}:(\d+):\d+", stack)
    return f"line {hit.group(1)}: {message}" if hit else message


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
    #: Every request the sandbox refused (a path outside the Look, a
    #: network host, a file over the size cap), for ``looks check``.
    blocked: tuple[str, ...] = ()


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


#: How long past a sandbox budget the watchdog waits before it kills the
#: browser: Playwright's own timeout gets the first chance.
WATCHDOG_GRACE_SECONDS = 2.0


def _pids_with(marker: str) -> list[int]:
    """Every process whose command line carries ``marker``: through
    ``/proc`` on Linux (a slim container has no ``ps``), else ``ps``."""
    if not marker:
        return []
    found: list[int] = []
    proc = Path("/proc")
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            if marker.encode() in cmdline:
                found.append(int(entry.name))
        return found
    try:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        if marker in command and pid.isdigit():
            found.append(int(pid))
    return found


class _HookError(Exception):
    """A template's own hook threw (or answered nothing); the message is
    what the author needs."""


class TemplateTimeoutError(TemplateScriptError):
    """A template overran a sandbox budget (``look_sandbox``): its load, a
    call into its code, or its run of frames. The context is closed, which
    kills its renderer, so a stuck page never holds the browser."""


_DURATION_JS = "typeof window.duration === 'function' ? Number(window.duration()) || 0 : 0"
_POSTER_JS = (
    "typeof window.poster === 'function' ? Number(window.poster()) || 0 : "
    "(typeof window.duration === 'function' ? (Number(window.duration()) || 0) / 2 : 0)"
)
_PROBE_JS = """(() => {
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

#: Every call into a template's code runs inside this predicate, under
#: ``page.wait_for_function``: its first poll starts the hook, later polls
#: wait for it, and the last hands the answer to Python through the exposed
#: binding. Playwright bounds ``wait_for_function`` on its own side, so a
#: page whose main thread a template has stuck still times out, where a
#: plain ``page.evaluate`` would wait forever (measured: #1266).
_GUARD_JS = (
    """({id, kind, arg}) => {
  const box = (window.__splitsmithCalls = window.__splitsmithCalls || {});
  if (!(id in box)) {
    box[id] = null;
    const hooks = {
      duration: () => ("""
    + _DURATION_JS
    + """),
      poster: () => ("""
    + _POSTER_JS
    + """),
      seek: (t) => typeof window.seek === 'function'
        ? Promise.resolve(window.seek(t)).then(() => null) : null,
      fit: () => Promise.resolve(window.__splitsmithFit && window.__splitsmithFit()).then(() => null),
      fonts: () => document.fonts.ready.then(() => null),
      probe: () => ("""
    + _PROBE_JS
    + """),
    };
    Promise.resolve().then(() => hooks[kind](arg)).then(
      (v) => { box[id] = {value: v === undefined ? null : v}; },
      (e) => { box[id] = {error: String((e && e.message) || e)}; });
    return false;
  }
  const r = box[id];
  if (r === null) return false;
  delete box[id];
  window.__splitsmithDeliver(id, JSON.stringify(r));
  return true;
}"""
)


@dataclass
class _TemplatePage:
    """One sandboxed template page and the calls into it (#1266)."""

    template: Path
    sandbox: look_sandbox.Sandbox
    context: Any
    page: Any = None
    errors: list[str] = field(default_factory=list)
    delivered: dict[int, dict[str, Any]] = field(default_factory=dict)
    calls: int = 0
    #: Kills the browser (the rasterizer's ``_kill_browser``); set when the
    #: page is watched, which every real one is.
    kill: Callable[[], None] | None = None
    killed: bool = False

    @contextmanager
    def watched(self, seconds: float, what: str) -> Iterator[None]:
        """Run the body under Playwright's own timeout and, past it, a
        watchdog that kills the browser (#1266). Playwright's timeouts do
        not hold once a template sticks the page while a call is in
        flight (measured: a ``wait_for_function`` started 30 ms before a
        ``while (true)`` never returned); the kill always does. Either way
        the caller gets a :class:`TemplateTimeoutError` naming ``what``."""
        message = f"{self.template.name}: {what} within {seconds:g} s"
        timer: threading.Timer | None = None
        if self.kill is not None:

            def fire() -> None:
                self.killed = True
                assert self.kill is not None
                self.kill()

            timer = threading.Timer(seconds + WATCHDOG_GRACE_SECONDS, fire)
            timer.daemon = True
            timer.start()
        try:
            yield
        except PlaywrightTimeoutError as exc:
            raise TemplateTimeoutError(message) from exc
        except PlaywrightError as exc:
            if self.killed:
                raise TemplateTimeoutError(message) from exc
            raise
        finally:
            if timer is not None:
                timer.cancel()
        if self.killed:
            raise TemplateTimeoutError(message)

    def deliver(self, _source: object, call_id: object, payload: object) -> None:
        """The binding the guard answers through. The template can call it
        too, so it takes only a known call's well-formed answer."""
        if not isinstance(call_id, int) or not isinstance(payload, str) or len(payload) > 1_000_000:
            return
        if not 0 < call_id <= self.calls:
            return
        try:
            value = json.loads(payload)
        except ValueError:
            return
        if isinstance(value, dict):
            self.delivered[call_id] = value

    def call(self, kind: str, arg: Any = None) -> Any:
        """Run one hook (``duration``, ``poster``, ``seek``, ``fit``,
        ``fonts``, ``probe``) and return its value. A hook that throws is a
        :class:`_HookError`; one that does not answer within
        :data:`look_sandbox.CALL_SECONDS` a :class:`TemplateTimeoutError`."""
        self.calls += 1
        call_id = self.calls
        try:
            with self.watched(look_sandbox.CALL_SECONDS, f"{kind} did not answer"):
                self.page.wait_for_function(
                    _GUARD_JS,
                    arg={"id": call_id, "kind": kind, "arg": arg},
                    polling=20,
                    timeout=look_sandbox.CALL_SECONDS * 1000,
                )
                for _ in range(100):
                    if call_id in self.delivered:
                        break
                    self.page.wait_for_timeout(10)
        except TemplateTimeoutError:
            raise
        except PlaywrightError as exc:
            # The guard itself threw: the template replaced what it relies on.
            raise _HookError(str(exc).splitlines()[0]) from exc
        result = self.delivered.pop(call_id, None)
        if result is None:
            raise _HookError(f"{kind} gave no answer")
        if "error" in result:
            raise _HookError(str(result["error"])[:500])
        return result.get("value")

    def screenshot(self) -> bytes:
        with self.watched(look_sandbox.CALL_SECONDS, "the frame did not draw"):
            return _screenshot(self.page, timeout=look_sandbox.CALL_SECONDS * 1000)

    def close(self) -> None:
        """Close the context, which kills its renderer. After a kill there
        is nothing left to close."""
        if self.killed:
            return
        try:
            with self.watched(look_sandbox.CALL_SECONDS, "did not close"):
                self.context.close()
        except TemplateTimeoutError:
            pass


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
        self._marker = ""

    def __enter__(self) -> ChromiumRasterizer:
        try:
            self._playwright = sync_playwright().start()
        except Exception as exc:  # pragma: no cover - defensive; no known trigger on a synced env
            raise _unavailable(exc) from exc
        try:
            self._launch()
        except (PlaywrightError, OSError) as exc:
            self._playwright.stop()
            self._playwright = None
            raise _unavailable(exc) from exc
        return self

    def _launch(self) -> None:
        """Start the browser with a switch only this rasterizer's process
        carries, so :meth:`_kill_browser` finds it by command line. Chromium
        ignores a switch it does not know."""
        assert self._playwright is not None
        self._marker = f"--splitsmith-rasterizer={uuid.uuid4().hex}"
        self._browser = self._playwright.chromium.launch(
            channel=self._channel, headless=self._headless, args=[self._marker]
        )

    def _kill_browser(self) -> None:
        """The watchdog's last resort (#1266): SIGKILL this rasterizer's
        browser, which takes every renderer with it, so a call blocked on a
        page a template has stuck fails instead of waiting forever. Runs on
        the watchdog's thread; it touches no Playwright object."""
        for pid in _pids_with(self._marker):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def _live_browser(self) -> Browser:
        """The browser, relaunched when the watchdog killed the last one."""
        if self._browser is None:
            raise RuntimeError(
                "ChromiumRasterizer template rendering called outside its own 'with' block -- the browser is "
                "only live between __enter__ and __exit__"
            )
        if not self._browser.is_connected():
            self._launch()
        return self._browser

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        # Explicit lifecycle teardown, browser first then driver, both
        # guarded so a failure closing one does not skip closing the
        # other and leak a browser process on an exception mid-render.
        try:
            if self._browser is not None and self._browser.is_connected():
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
                return _screenshot(page)
            finally:
                context.close()

    def engine_version(self) -> str:
        if self._browser is None:
            raise RuntimeError("ChromiumRasterizer.engine_version() called outside its own 'with' block")
        return str(self._browser.version)

    def _open_template(
        self, template: Path, *, context: TemplateContext, width: int, height: int
    ) -> _TemplatePage:
        """A fresh, sandboxed context (``look_sandbox``, #1266) with
        ``window.splitsmith`` installed and the template loaded past
        ``load`` and its fonts. Every request the page makes is answered
        by the sandbox's allowlist, a websocket is closed, a service worker
        never registers, and the page is navigated from the virtual origin,
        never ``file://``. ``errors`` collects ``pageerror`` messages from
        before navigation on: an exception thrown by the template's own
        script reaches Playwright only as that event (``goto`` and the
        screenshot both succeed and hand back a transparent PNG). A load
        that overruns :data:`look_sandbox.LOAD_SECONDS` closes the context
        and raises :class:`TemplateScriptError`."""
        browser = self._live_browser()
        sandbox, context = look_sandbox.prepare(template, context)
        browser_context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=DEVICE_SCALE_FACTOR,
            service_workers="block",
        )
        view = _TemplatePage(
            template=template, sandbox=sandbox, context=browser_context, kill=self._kill_browser
        )
        try:
            browser_context.route("**/*", sandbox.handle)
            # Never connected to a server, the route answers the page's
            # socket itself and nothing reaches the network. (Closing it from
            # the handler deadlocks Playwright's dispatcher.)
            browser_context.route_web_socket("**/*", lambda ws: None)
            browser_context.expose_binding("__splitsmithDeliver", view.deliver)
            browser_context.add_init_script(context.init_script())
            page = browser_context.new_page()
            view.page = page
            page.on("pageerror", lambda error: view.errors.append(describe_page_error(error, template.name)))
            with view.watched(look_sandbox.LOAD_SECONDS, "did not finish loading"):
                page.goto(sandbox.entry_url, wait_until="load", timeout=look_sandbox.LOAD_SECONDS * 1000)
            view.call("fonts")
        except BaseException:
            view.close()
            raise
        return view

    @staticmethod
    def _check(errors: list[str], template: Path) -> None:
        if errors:
            raise TemplateScriptError(f"{template.name}: {errors[0]}")

    def probe_template(
        self, template: Path, *, context: TemplateContext, width: int, height: int
    ) -> TemplateProbe:
        """Load ``template`` as :meth:`render_template` does (the context,
        fonts, the poster seek, the fit policy) and report what the
        authoring checks need, instead of a picture: script errors from
        any point of the load, ``duration()`` / ``poster()`` / whether
        ``seek`` exists, the font families visible text uses, text that
        overruns the canvas or a clipped box, and every request the
        sandbox refused. Never raises for a template's own error or
        overrun; that is a finding, not a failure."""
        if self._browser is None:
            raise RuntimeError(
                "ChromiumRasterizer.probe_template() called outside its own 'with' block -- the browser is "
                "only live between __enter__ and __exit__"
            )
        try:
            view = self._open_template(template, context=context, width=width, height=height)
        except TemplateScriptError as exc:
            return TemplateProbe(errors=(str(exc).removeprefix(f"{template.name}: "),))

        def hook(kind: str, default: Any, arg: Any = None) -> Any:
            # A template's own hook that throws is a finding like a load error.
            try:
                return view.call(kind, arg)
            except _HookError as exc:
                view.errors.append(str(exc))
                return default

        try:
            try:
                duration = float(hook("duration", 0) or 0)
                poster = float(hook("poster", 0) or 0)
                hook("seek", None, poster)
                hook("fonts", None)
                hook("fit", None)
                seen = view.call("probe")
            except TemplateTimeoutError as exc:
                return TemplateProbe(
                    errors=(*view.errors, str(exc).removeprefix(f"{template.name}: ")),
                    blocked=tuple(view.sandbox.blocked),
                )
            return TemplateProbe(
                errors=tuple(view.errors),
                duration=duration,
                poster=poster,
                has_seek=bool(seen["hasSeek"]),
                families=tuple(seen["families"]),
                overflow=tuple((str(t), int(b)) for t, b in seen["overflow"]),
                blocked=tuple(view.sandbox.blocked),
            )
        finally:
            view.close()

    def render_template(
        self,
        template: Path,
        *,
        context: TemplateContext,
        width: int,
        height: int,
        at: float | None = None,
    ) -> bytes:
        """Render a Look template to a ``width`` x ``height`` alpha PNG at
        its poster frame (``poster()``, else the midpoint of
        ``duration()``, else 0: a still renders at 0), or at ``at`` seconds
        when given (the Look editor's time slider, #1264). The template
        loads in the sandbox (:meth:`_open_template`), so its relative
        references resolve inside its Look folder and ``assets.shared``
        names the engine scripts on the virtual origin. After load: fonts,
        the poster seek, fonts again (a template may add a face while
        mounting), the fit policy when the template defines it, then the
        same transparent screenshot :meth:`png` takes. A hook that throws
        or overruns raises :class:`TemplateScriptError`.
        """
        if self._browser is None:
            raise RuntimeError(
                "ChromiumRasterizer.render_template() called outside its own 'with' block -- the browser is "
                "only live between __enter__ and __exit__"
            )
        view = self._open_template(template, context=context, width=width, height=height)
        try:
            seconds = at if at is not None else float(view.call("poster") or 0)
            view.call("seek", seconds)
            view.call("fonts")
            view.call("fit")
            self._check(view.errors, template)
            return view.screenshot()
        except _HookError as exc:
            raise TemplateScriptError(f"{template.name}: {exc}") from exc
        finally:
            view.close()

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
        the finished state, not one frame short of it). The span never
        exceeds :data:`look_sandbox.MAX_ANIMATION_SECONDS`, and the whole
        run :data:`look_sandbox.TEMPLATE_SECONDS`. The fit policy runs
        once, at the poster, before any frame is sampled: a transform
        mid-animation inflates what ``fit.js`` measures, so fitting at
        t=0 would shrink a long card's text below what the preview (the
        poster) shows. A ``pageerror``, a hook that throws or an overrun
        at any point raises :class:`TemplateScriptError` from the iterator
        and closes the context; :meth:`TemplateFrames.close` releases it
        either way.
        """
        if self._browser is None:
            raise RuntimeError(
                "ChromiumRasterizer.render_template_frames() called outside its own 'with' block -- the "
                "browser is only live between __enter__ and __exit__"
            )
        started = time.monotonic()
        view = self._open_template(template, context=context, width=width, height=height)
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                view.close()

        try:
            duration = float(view.call("duration") or 0)
            self._check(view.errors, template)
            poster = view.call("poster")
            view.call("seek", float(poster or 0))
            view.call("fonts")
            view.call("fit")
            self._check(view.errors, template)
        except _HookError as exc:
            release()
            raise TemplateScriptError(f"{template.name}: {exc}") from exc
        except BaseException:
            release()
            raise
        shown = min(duration, max_seconds, look_sandbox.MAX_ANIMATION_SECONDS) if duration > 0 else 0.0
        count = max(1, math.ceil(shown * fps - 1e-9)) if shown > 0 else 1
        times = [round(index / fps, 6) for index in range(count)]
        if shown > 0:
            times[-1] = round(shown, 6)

        def generate() -> Iterator[bytes]:
            try:
                for seconds in times:
                    if time.monotonic() - started > look_sandbox.TEMPLATE_SECONDS:
                        raise TemplateTimeoutError(
                            f"{template.name}: frames took longer than {look_sandbox.TEMPLATE_SECONDS:g} s"
                        )
                    try:
                        view.call("seek", seconds)
                    except _HookError as exc:
                        raise TemplateScriptError(f"{template.name}: {exc}") from exc
                    self._check(view.errors, template)
                    png = view.screenshot()
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
