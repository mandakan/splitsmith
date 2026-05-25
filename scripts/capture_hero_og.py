"""Render the marketing hero + og:image PNGs from the static HTML sources.

The artboards live in ``scripts/og/`` as Babel-transpiled JSX wrapped in
two standalone HTML pages -- ``hero.html`` (1600x640) and ``og.html``
(1200x630). This script drives Chromium through Playwright to capture
each at its native size, writing:

    docs/screenshots/hero.png  -- README banner
    site/og.png                -- og:image / twitter:image

One-time prereqs (not part of the project's locked deps):

    uv pip install playwright
    uv run playwright install chromium

Re-run after editing anything under ``scripts/og/``.
"""

from __future__ import annotations

import argparse
import contextlib
import socket
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "scripts" / "og"

# (source HTML, output PNG, viewport width, viewport height)
TARGETS: list[tuple[str, Path, int, int]] = [
    ("hero.html", REPO_ROOT / "docs" / "screenshots" / "hero.png", 1600, 640),
    ("og.html", REPO_ROOT / "site" / "og.png", 1200, 630),
]


@contextlib.contextmanager
def serve_directory(directory: Path):
    """Serve ``directory`` over loopback HTTP. Babel-standalone needs an
    http origin to fetch sibling .jsx files (file:// triggers CORS)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    # Silence the default per-request logging.
    handler.log_message = lambda *a, **kw: None  # type: ignore[assignment]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def capture(headless: bool = True) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright not installed. Run:\n"
            "    uv pip install playwright\n"
            "    uv run playwright install chromium",
            file=sys.stderr,
        )
        sys.exit(1)

    with serve_directory(SRC_DIR) as base_url, sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        for source, out_path, w, h in TARGETS:
            url = f"{base_url}/{source}"
            ctx = browser.new_context(
                viewport={"width": w, "height": h},
                device_scale_factor=2,  # retina-grade PNG
            )
            page = ctx.new_page()
            page.on("console", lambda msg: print(f"  [{msg.type}] {msg.text}", file=sys.stderr))
            page.on("pageerror", lambda err: print(f"  [pageerror] {err}", file=sys.stderr))
            page.goto(url, wait_until="load")
            # Babel-standalone transpiles scripts after DOMContentLoaded;
            # wait for the React tree to mount.
            page.wait_for_function(
                "document.querySelector('#root') && document.querySelector('#root').children.length > 0",
                timeout=15000,
            )
            # Let fonts settle before capture.
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(800)

            out_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out_path), full_page=False, omit_background=False)
            ctx.close()
            print(f"wrote {out_path.relative_to(REPO_ROOT)} ({w}x{h} @2x)")
        browser.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--headed", action="store_true", help="Run a visible browser (debug).")
    args = ap.parse_args()
    capture(headless=not args.headed)


if __name__ == "__main__":
    main()
