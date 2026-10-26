"""Get a checkout ready for the fixture review and open the queue.

Idempotent: run it whenever you sit down to review. Each step does nothing
when it has nothing to do.

1. Fetch the fixture WAVs kept in R2 (``fixture_audio.py fetch``: only what
   is missing or differs).
2. Build the SPA when ``dist/`` is older than its sources.
3. Rewrite the queue's order (``fixture_review_inventory.py``).
4. Serve the lab with auto-sync off and open ``/dev/review``.

Saves write ``tests/fixtures/*.json`` in this checkout, so it refuses to run
on ``main``; review on a branch and commit with ``git add -u tests/fixtures``.

With ``--phone`` it serves on the Mac's LAN address behind a token and
prints the URL to open on the phone (same Wi-Fi, or a VPN home). The token is
kept in ``build/review_phone_token`` so the phone stays signed in between
runs; ``--new-token`` replaces it.

Usage:
    uv run python scripts/review_session.py            # prepare and serve
    uv run python scripts/review_session.py --phone    # serve to a phone
    uv run python scripts/review_session.py --no-serve # prepare only
"""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "src" / "splitsmith" / "ui_static"
DIST_INDEX = UI / "dist" / "index.html"
# The desktop app and `splitsmith ui` default to 5174.
DEFAULT_PORT = 5180
TOKEN_FILE = ROOT / "build" / "review_phone_token"


def _run(*cmd: str, cwd: Path = ROOT) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def _branch() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _newest_mtime(paths: list[Path]) -> float:
    newest = 0.0
    for p in paths:
        files = [p] if p.is_file() else (f for f in p.rglob("*") if f.is_file())
        for f in files:
            newest = max(newest, f.stat().st_mtime)
    return newest


def _ensure_spa() -> None:
    modules = UI / "node_modules" / ".modules.yaml"
    if not modules.exists() or modules.stat().st_mtime < (UI / "pnpm-lock.yaml").stat().st_mtime:
        _run("pnpm", "install", "--frozen-lockfile", cwd=UI)
    sources = [
        UI / "src",
        UI / "index.html",
        UI / "package.json",
        UI / "pnpm-lock.yaml",
        UI / "vite.config.ts",
    ]
    if DIST_INDEX.exists() and DIST_INDEX.stat().st_mtime >= _newest_mtime(
        [p for p in sources if p.exists()]
    ):
        print("SPA build is current")
        return
    _run("pnpm", "build", cwd=UI)


def lan_address() -> str:
    """The address the default route leaves from (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed, nothing sent
        return s.getsockname()[0]


def phone_token(path: Path, rotate: bool) -> str:
    """The phone's token, kept between runs so the phone stays signed in."""
    if path.exists() and not rotate:
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    path.write_text(token, encoding="utf-8")
    path.chmod(0o600)
    return token


def phone_url(host: str, port: int, token: str) -> str:
    return f"http://{host}:{port}/dev/review?lab_token={token}"


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        return s.connect_ex((host, port)) == 0


def _serve(port: int, host: str = "127.0.0.1", token: str | None = None) -> int:
    if _port_in_use(port, host):
        print(f"port {port} is already in use; stop that server or pass --port", file=sys.stderr)
        return 1
    # The token goes through the environment, never the command line ``ps`` shows.
    env = {**os.environ, "SPLITSMITH_AUTO_SYNC": "0"}
    if token:
        env["SPLITSMITH_LAB_TOKEN"] = token
    cmd = [
        "splitsmith",
        "ui",
        "--lab",
        "--skip-system-check",
        "--no-browser",
        "--port",
        str(port),
        "--host",
        host,
    ]
    print(f"$ SPLITSMITH_AUTO_SYNC=0 {' '.join(cmd)}", flush=True)
    server = subprocess.Popen(cmd, cwd=ROOT, env=env)
    base = f"http://{host}:{port}"
    health = f"{base}/api/health" + (f"?lab_token={token}" if token else "")
    for _ in range(120):
        if server.poll() is not None:
            return server.returncode
        try:
            urllib.request.urlopen(health, timeout=1)  # noqa: S310
            break
        except urllib.error.HTTPError:
            break  # any HTTP answer means it is up (the token's redirect included)
        except OSError:
            time.sleep(0.5)
    if token:
        print(f"open on the phone: {phone_url(host, port, token)}", flush=True)
        print("  (same Wi-Fi or your VPN home; the token stays valid until --new-token)", flush=True)
        print("Ctrl-C stops the server", flush=True)
    else:
        print(f"review queue: {base}/dev/review  (Ctrl-C stops the server)", flush=True)
        webbrowser.open(f"{base}/dev/review")
    try:
        return server.wait()
    except KeyboardInterrupt:
        server.terminate()
        return server.wait()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-serve", action="store_true", help="prepare only")
    ap.add_argument("--allow-main", action="store_true", help="run on main anyway")
    ap.add_argument("--phone", action="store_true", help="serve on the LAN for a phone, behind a token")
    ap.add_argument("--host", help="LAN address to bind with --phone (default: the default route's)")
    ap.add_argument("--new-token", action="store_true", help="rotate the phone's token")
    args = ap.parse_args()

    if _branch() == "main" and not args.allow_main:
        print(
            "on main: reviews write tests/fixtures/*.json here. Branch first, e.g.\n"
            "  git checkout -b fixtures/rise-foot-review",
            file=sys.stderr,
        )
        return 1
    py = sys.executable
    _run(py, "scripts/fixture_audio.py", "fetch")
    _ensure_spa()
    _run(py, "scripts/fixture_review_inventory.py")
    if args.no_serve:
        return 0
    if args.phone:
        return _serve(args.port, args.host or lan_address(), phone_token(TOKEN_FILE, args.new_token))
    return _serve(args.port)


if __name__ == "__main__":
    sys.exit(main())
