"""Capture README screenshots from a live splitsmith UI session.

Boots ``splitsmith ui`` against a real project directory, drives Chromium
through the workflow pages via Playwright, writes PNGs under
``docs/screenshots/``. Re-run after a UI redesign to refresh the README
gallery.

One-time prereqs (not part of the project's locked deps):

    uv pip install playwright
    uv run playwright install chromium

Then point at a multi-shooter MatchProject (Compare needs >= 2 shooters
with trim caches; on a single-shooter project Compare is skipped with a
warning instead of failing):

    uv run python scripts/capture_screenshots.py \\
        --project ~/matches/your-match \\
        --stage 3 \\
        --output docs/screenshots/

The script starts ``splitsmith ui --no-browser`` on a free port, then polls
``/api/health`` for any HTTP 200 (the server is URL-scoped since the state
refactor -- match identity lives in the URL prefix, not in health). It reads
the ``match_id`` directly from ``<project>/match.json``, walks the canonical
path-scoped routes under ``/match/{match_id}/...``, and terminates the server
on exit (or on Ctrl+C).
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

# (route_template, filename, description, optional)
# Routes are formatted with .format(match_id=..., slug=..., stage=...).
SCREENSHOTS: list[tuple[str, str, str, bool]] = [
    ("/match/{match_id}/", "home.png", "Match overview", False),
    (
        "/match/{match_id}/ingest/{slug}",
        "ingest.png",
        "Ingest page: scan + auto-match videos",
        False,
    ),
    (
        "/match/{match_id}/beep-review",
        "beep-review.png",
        "Beep review queue (HITL)",
        False,
    ),
    (
        "/match/{match_id}/audit/{slug}/{stage}",
        "audit.png",
        "Audit page: waveform + shot markers",
        False,
    ),
    (
        "/match/{match_id}/compare/{stage}",
        "compare.png",
        "Multi-shooter Compare grid",
        True,
    ),
    (
        "/match/{match_id}/export/{slug}",
        "export.png",
        "Per-stage / match export panel",
        False,
    ),
    ("/match/{match_id}/results", "results.png", "Results overview (read-only)", False),
    (
        "/match/{match_id}/results/{slug}/{stage}",
        "results-stage.png",
        "Results stage playback",
        False,
    ),
]


def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_get_json(url: str, timeout: float = 5.0) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def wait_for_health(base_url: str, timeout_s: float = 30.0) -> None:
    """Poll until the server returns any HTTP 200 on ``/api/health``.

    Since the state refactor the server is URL-scoped: match identity lives in
    the ``/api/matches/{match_id}/`` URL prefix, not in the process. Health
    always returns ``bound: false``; this check only confirms the server is
    accepting requests.
    """
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            http_get_json(f"{base_url}/api/health", timeout=2)
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            last_err = e
        time.sleep(0.3)
    raise TimeoutError(f"splitsmith ui never responded at {base_url} (last error: {last_err})")


@contextmanager
def boot_ui(project: Path, port: int, log_path: Path) -> Iterator[subprocess.Popen[bytes]]:
    log_file = log_path.open("wb")
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
        stdout=log_file,
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
        log_file.close()


def pick_default_shooter(base_url: str, match_id: str) -> str:
    """Return the first shooter with stages_audited > 0, or the first shooter."""
    data = http_get_json(f"{base_url}/api/matches/{match_id}/match/shooters")
    shooters = data.get("shooters", []) or []
    if not shooters:
        raise RuntimeError("match has no registered shooters -- ingest something first")
    for s in shooters:
        if int(s.get("stages_audited", 0)) > 0:
            return str(s["slug"])
    return str(shooters[0]["slug"])


def pick_default_stage(base_url: str, match_id: str, slug: str) -> int:
    """Return the best stage for audit/compare/export screenshots.

    Prefers an audited stage so the views render populated. Falls back to any
    stage with videos, then any stage. Calls the URL-scoped project endpoint.
    """
    project = http_get_json(f"{base_url}/api/matches/{match_id}/shooters/{slug}/project")
    stages = project.get("stages", []) or []
    # Prefer an audited stage so the audit/compare/export views render
    # populated. Fall back to any stage with videos, then any stage.
    for s in stages:
        if s.get("status") == "audited" and not s.get("skipped"):
            return int(s["stage_number"])
    for s in stages:
        if s.get("videos") and not s.get("skipped"):
            return int(s["stage_number"])
    if stages:
        return int(stages[0]["stage_number"])
    raise RuntimeError(f"shooter {slug!r} has no stages -- ingest something first")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--project",
        type=Path,
        required=True,
        help="MatchProject root directory (must have shot detection completed).",
    )
    parser.add_argument(
        "--slug",
        type=str,
        default=None,
        help="Shooter slug to feature. Defaults to the first audited shooter.",
    )
    parser.add_argument(
        "--stage",
        type=int,
        default=None,
        help="Stage number for Audit / Compare / Export. Defaults to the "
        "first audited stage on the default shooter.",
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
            "  uv pip install playwright\n"
            "  uv run playwright install chromium",
            file=sys.stderr,
        )
        return 2

    project = args.project.expanduser().resolve()

    # Read match_id from match.json -- the URL-scoped server does not expose it
    # via /api/health. Legacy single-shooter projects lack match.json entirely.
    match_json = project / "match.json"
    if not match_json.exists():
        print(
            "[capture] /api/health did not return a match_id -- this script "
            "requires a match-bound project (legacy single-shooter projects "
            "are no longer supported by the path-scoped URL scheme).",
            file=sys.stderr,
        )
        return 1
    match_id = json.loads(match_json.read_text())["match_id"]

    args.output.mkdir(parents=True, exist_ok=True)
    port = args.port or find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    # Server stdout goes to a log file so uvicorn access logs don't fill the
    # 64 KB pipe buffer and wedge the process.
    log_path = args.output / "server.log"
    print(f"[capture] server log -> {log_path}")

    with boot_ui(project, port, log_path) as proc:
        print(f"[capture] starting splitsmith ui on {base_url} ...")
        try:
            wait_for_health(base_url)
        except TimeoutError as e:
            # Read the log file (never proc.stdout.read() -- blocks to EOF on
            # a live process and hangs).
            try:
                tail = log_path.read_text(errors="replace")[-4000:]
            except OSError:
                tail = "(log file unreadable)"
            print(f"[capture] {e}\n--- server log tail ---\n{tail}", file=sys.stderr)
            return 1

        slug = args.slug or pick_default_shooter(base_url, match_id)
        if not slug:
            print(
                "[capture] no shooter slug available (project has no registered "
                "shooters). Bind a shooter before running.",
                file=sys.stderr,
            )
            return 1
        print(f"[capture] match_id={match_id}, slug={slug}")

        stage = args.stage if args.stage is not None else pick_default_stage(base_url, match_id, str(slug))
        print(f"[capture] feature stage: {stage}")

        _ = proc  # server is still running; referenced to suppress unused-var lint

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport={"width": args.width, "height": args.height},
                device_scale_factor=2,
            )
            page = context.new_page()
            for route, filename, description, optional in SCREENSHOTS:
                path = route.format(match_id=match_id, slug=slug, stage=stage)
                url = base_url + path
                target = args.output / filename
                print(f"[capture] {filename:22} <- {path}")
                try:
                    # domcontentloaded instead of networkidle: the SPA keeps
                    # SSE connections open forever so networkidle never fires.
                    page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                    page.wait_for_timeout(1500)
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

    print(f"[capture] wrote PNGs to {args.output}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
