"""The sandboxed template loader (issue #1266, spec 2026-10-07 section 2).

A Look template is HTML and JavaScript that runs in Chromium on the machine
that renders, so on hosted it must reach nothing but what a card is made
of. Every template page therefore loads from one virtual origin,
``https://look.invalid/``, that :meth:`Sandbox.handle` answers request by
request through Playwright routing:

- ``/look/<path>``: the template's own Look folder (its other templates,
  images, stylesheets, own fonts), never outside it;
- ``/shared/<path>``: the engine scripts (``fit.js``, ``cell.js``, ...);
- ``/fonts/<path>``: the bundled faces;
- ``/file/<digest>/<name>``: each file a **server-built** field of the
  context names by ``file://`` URL, rewritten here to a virtual URL: the
  engine stylesheet (a Look's own font), the ``assets`` block, and every
  ``logo`` value (a shooter's or the match's). Nothing else: the context's
  ``data`` also carries what users typed (a stage name, a club line), and a
  stage named ``file:///proc/self/environ`` must stay a string the page
  cannot load. The template cannot add a mount either: the context is built
  before it runs.

Everything else is aborted: another path, another host, any network, and a
``file://`` load (Chromium refuses those from an ``https`` page). A file
over :data:`MAX_ASSET_BYTES` is refused too. The page is never navigated by
``file://``, so it has no file reach of its own. The time limits live with
the page (``overlay_raster``): every call into template code goes through
``wait_for_function``, which Playwright bounds even when the page's main
thread is stuck, and a context is closed, killing its renderer, on any
overrun.

The same loader serves local templates, so a template that works on the
desktop works hosted. Pure apart from reading the files it serves.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from .look_template import TemplateContext
from .looks import shared_dir

ORIGIN = "https://look.invalid"
HOST = "look.invalid"

#: The largest file the sandbox serves: a template, an image, a font. A
#: card's assets are kilobytes to a couple of megabytes; a 4K PNG is ~8 MB.
MAX_ASSET_BYTES = 12 * 1024 * 1024
#: Wall-clock budgets, in seconds: the template's load, each call into its
#: code (``duration``, ``seek``, the fit, the probe, a screenshot), and one
#: template's whole run of frames.
LOAD_SECONDS = 20.0
CALL_SECONDS = 10.0
TEMPLATE_SECONDS = 300.0
#: The longest animation the renderer samples, whatever ``duration()`` says.
MAX_ANIMATION_SECONDS = 60.0

_FILE_URL = re.compile(r"file://[^\"'\s)\\<>]+")


def _bundled_fonts_dir() -> Path:
    return Path(str(resources.files("splitsmith.data").joinpath("fonts")))


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass
class Sandbox:
    """What one template page may load. Built by :func:`prepare`."""

    template: Path
    #: Mount name -> directory, every path resolved.
    mounts: dict[str, Path]
    #: ``/file/...`` path -> file, the context's own file URLs.
    files: dict[str, Path] = field(default_factory=dict)
    #: Every URL the page asked for and did not get, for ``looks check``.
    blocked: list[str] = field(default_factory=list)

    @property
    def entry_url(self) -> str:
        return f"{ORIGIN}/look/{self.template.name}"

    def resolve(self, url: str) -> Path | None:
        """The file ``url`` names inside the sandbox, or ``None``."""
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != HOST:
            return None
        path = unquote(parts.path)
        if path in self.files:
            return self.files[path]
        segments = path.lstrip("/").split("/", 1)
        if len(segments) != 2 or segments[0] not in self.mounts:
            return None
        root = self.mounts[segments[0]]
        rel = segments[1]
        if not rel or "\\" in rel or "\0" in rel:
            return None
        candidate = (root / rel).resolve()
        if not _within(candidate, root) or not candidate.is_file():
            return None
        return candidate

    def handle(self, route: Any) -> None:
        """Playwright's route handler: fulfil a mounted file, abort the rest."""
        url = route.request.url
        path = self.resolve(url)
        if path is None:
            self._refuse(route, url)
            return
        try:
            if path.stat().st_size > MAX_ASSET_BYTES:
                self._refuse(route, f"{url} (over {MAX_ASSET_BYTES // (1024 * 1024)} MB)")
                return
            body = path.read_bytes()
        except OSError:
            self._refuse(route, url)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            content_type = "text/javascript"
        route.fulfill(
            status=200,
            body=body,
            content_type=content_type,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    def _refuse(self, route: Any, url: str) -> None:
        if len(self.blocked) < 50:
            self.blocked.append(url[:300])
        route.abort("blockedbyclient")


def prepare(template: Path, context: TemplateContext) -> tuple[Sandbox, TemplateContext]:
    """The sandbox for ``template`` and ``context`` with every ``file://``
    URL in it rewritten to the virtual origin: a directory the sandbox
    mounts (the engine scripts, the bundled fonts, the Look folder) by its
    mount, any other file by a ``/file/`` path of its own. A URL naming
    something that is neither is left as it was, which the page cannot
    load."""
    look_root = template.resolve().parent
    mounts = {
        "look": look_root,
        "shared": shared_dir().resolve(),
        "fonts": _bundled_fonts_dir().resolve(),
    }
    sandbox = Sandbox(template=template.resolve(), mounts=mounts)

    def rewrite(match: re.Match[str]) -> str:
        url = match.group(0)
        path = Path(url2pathname(unquote(urlsplit(url).path))).resolve()
        for name, root in mounts.items():
            if path == root:
                return f"{ORIGIN}/{name}"
            if _within(path, root):
                return f"{ORIGIN}/{name}/{path.relative_to(root).as_posix()}"
        if path.is_file():
            key = f"/file/{hashlib.sha256(str(path).encode()).hexdigest()[:16]}/{path.name}"
            sandbox.files[key] = path
            return f"{ORIGIN}{key}"
        return url

    def within_mounts(match: re.Match[str]) -> str:
        # User text may name a mounted (public) file harmlessly; it never
        # earns a ``/file/`` mount of its own.
        url = match.group(0)
        path = Path(url2pathname(unquote(urlsplit(url).path))).resolve()
        if any(path == root or _within(path, root) for root in mounts.values()):
            return rewrite(match)
        return url

    def walk(value: Any, *, trusted: bool) -> Any:
        if isinstance(value, str):
            return _FILE_URL.sub(rewrite if trusted else within_mounts, value)
        if isinstance(value, list):
            return [walk(item, trusted=trusted) for item in value]
        if isinstance(value, dict):
            return {key: walk(item, trusted=trusted or key == "logo") for key, item in value.items()}
        return value

    raw = context.model_dump()
    raw["engine"] = walk(raw["engine"], trusted=True)
    raw["assets"] = walk(raw["assets"], trusted=True)
    raw["data"] = walk(raw["data"], trusted=False)
    raw["theme"] = walk(raw["theme"], trusted=False)
    return sandbox, TemplateContext.model_validate(raw)


__all__ = [
    "CALL_SECONDS",
    "HOST",
    "LOAD_SECONDS",
    "MAX_ANIMATION_SECONDS",
    "MAX_ASSET_BYTES",
    "ORIGIN",
    "TEMPLATE_SECONDS",
    "Sandbox",
    "prepare",
]
