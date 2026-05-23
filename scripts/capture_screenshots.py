"""Capture README screenshots from a live splitsmith UI session.

Boots ``splitsmith ui`` against a real project directory, drives Chromium
through the workflow pages via Playwright, writes PNGs under
``docs/screenshots/``. Re-run after a UI redesign to refresh the README
gallery.

One-time prereqs (not part of the project's locked deps):

    uv pip install --with playwright playwright
    playwright install chromium

Then point at a project that has shot detection completed for at least
one stage (use ``--stage`` to pick which one Audit + Compare visit;
defaults to the first stage with assigned videos):

    uv run python scripts/capture_screenshots.py \\
        --project ~/matches/tallmilan-2026 \\
        --stage 3 \\
        --output docs/screenshots/

Compare needs the project to have multiple shooters' trims on disk
(see ``splitsmith compare`` in the README). On a single-shooter project
the Compare PNG is skipped with a warning instead of failing.

The script starts ``splitsmith ui --no-browser`` on a free port, polls
``/api/health`` until it reports ``bound=true``, runs the capture pass,
and terminates the server on exit (or on Ctrl+C).
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

SCREENSHOTS = [
    # (route_template, filename, description, optional)
    ("/", "home.png", "Home / overview after project bind", False),
    ("/ingest", "ingest.png", "Ingest page: scan + auto-match videos", False),
    ("/beep-review", "beep-review.png", "Beep review queue (HITL)", False),
    ("/audit/{stage}", "audit.png", "Audit page: waveform + shot markers", False),
    ("/compare/{stage}", "compare.png", "Multi-shooter Compare grid", True),
    ("/export", "export.png", "Per-stage / match export panel", False),
]


def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_health(base_url: str, timeout_s: float = 30.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=2) as r:
                payload = json.loads(r.read().decode())
                if payload.get("bound"):
                    return payload
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            last_err = e
        time.sleep(0.3)
    raise TimeoutError(f"splitsmith ui never reported bound=true at {base_url} (last error: {last_err})")


@contextmanager
def boot_ui(project: Path, port: int) -> Iterator[subprocess.Popen[bytes]]:
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "splitsmith",
            "ui",
            "--project",
            str(project),
            "--no-browser",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def pick_default_stage(base_url: str) -> int:
    with urllib.request.urlopen(f"{base_url}/api/project", timeout=5) as r:
        project = json.loads(r.read().decode())
    stages = project.get("stages", []) or []
    for s in stages:
        if s.get("videos") and not s.get("skipped"):
            return int(s["stage_number"])
    if stages:
        return int(stages[0]["stage_number"])
    raise RuntimeError("project has no stages -- ingest something first")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--project",
        type=Path,
        required=True,
        help="MatchProject root directory (must have shot detection completed).",
    )
    parser.add_argument(
        "--stage",
        type=int,
        default=None,
        help="Stage number to feature on Audit + Compare. Defaults to the "
        "first stage with assigned videos.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/screenshots"),
        help="Output directory for PNGs. Created if missing.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1440,
        help="Viewport width in CSS pixels (default 1440).",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=900,
        help="Viewport height in CSS pixels (default 900).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind splitsmith ui on. Defaults to an OS-assigned free port.",
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright is not installed. Run:\n"
            "  uv pip install --with playwright playwright\n"
            "  playwright install chromium",
            file=sys.stderr,
        )
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    port = args.port or find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    with boot_ui(args.project, port) as proc:
        print(f"[capture] starting splitsmith ui on {base_url} ...")
        try:
            health = wait_for_health(base_url)
        except TimeoutError as e:
            tail = (proc.stdout.read() if proc.stdout else b"").decode(errors="replace")
            print(f"[capture] {e}\n--- server output tail ---\n{tail}", file=sys.stderr)
            return 1
        print(f"[capture] bound to project: {health.get('project_name')!r}")

        stage = args.stage if args.stage is not None else pick_default_stage(base_url)
        print(f"[capture] feature stage: {stage}")

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport={"width": args.width, "height": args.height},
                device_scale_factor=2,
            )
            page = context.new_page()
            for route, filename, description, optional in SCREENSHOTS:
                url = base_url + route.format(stage=stage)
                target = args.output / filename
                print(f"[capture] {filename:18} <- {route.format(stage=stage)}")
                try:
                    page.goto(url, wait_until="networkidle", timeout=15_000)
                    page.wait_for_timeout(500)
                    page.screenshot(path=str(target), full_page=False)
                except Exception as e:
                    msg = f"[capture] failed: {description}: {e}"
                    if optional:
                        print(f"{msg} (skipped, optional)", file=sys.stderr)
                        continue
                    print(msg, file=sys.stderr)
                    browser.close()
                    return 1
            browser.close()

    print(f"[capture] wrote {len(SCREENSHOTS)} PNGs to {args.output}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
