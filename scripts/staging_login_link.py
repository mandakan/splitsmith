"""Mint a staging magic-link and print the login URL.

Staging's email transport is console-only (see
docs/saas-readiness/11-environment-strategy.md), so magic links land in
the serve service's Railway logs as ``MAGIC_LINK <email> <url>`` lines
instead of a mailbox. This script triggers ``POST /api/v1/auth/begin``
against staging and fishes the freshest link out of ``railway logs``.

Links are single-use and expire after 15 minutes. Requires an
authenticated Railway CLI linked to the splitsmith project (``railway``
on PATH, or ``~/.railway/bin/railway``).

Usage:
    python3 scripts/staging_login_link.py [email]

Default email: m@thias.se.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE_URL = "https://my.staging.splitsmith.app"
MARKER = "MAGIC_LINK"
DEFAULT_EMAIL = "m@thias.se"
LINK_RE = re.compile(r"https://\S+/auth/callback\?token=[A-Za-z0-9._~-]+")


def _railway_bin() -> str:
    found = shutil.which("railway")
    if found:
        return found
    fallback = Path.home() / ".railway" / "bin" / "railway"
    if fallback.exists():
        return str(fallback)
    raise SystemExit("railway CLI not found (PATH or ~/.railway/bin) -- run `railway login` first")


def begin_login(email: str) -> None:
    req = urllib.request.Request(
        f"{BASE_URL}/api/v1/auth/begin",
        data=json.dumps({"email": email}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise SystemExit(f"auth/begin returned HTTP {resp.status}")


def fish_link(railway: str, email: str, attempts: int = 6, log_window_s: int = 15) -> str | None:
    """Scan staging serve logs for the newest MAGIC_LINK line for ``email``.

    ``railway logs`` streams and never exits on its own; each attempt
    lets it run for ``log_window_s`` seconds and recovers whatever it
    printed from the TimeoutExpired exception.
    """
    for _ in range(attempts):
        try:
            proc = subprocess.run(
                [railway, "logs", "--service", "serve", "--environment", "staging"],
                capture_output=True,
                text=True,
                timeout=log_window_s,
                check=False,
            )
            out = proc.stdout or ""
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        hits = [line for line in out.splitlines() if MARKER in line and email in line]
        if hits:
            match = LINK_RE.search(hits[-1])
            if match:
                return match.group(0)
        time.sleep(3)
    return None


def main() -> None:
    email = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EMAIL
    railway = _railway_bin()
    begin_login(email)
    link = fish_link(railway, email)
    if link is None:
        raise SystemExit("no MAGIC_LINK line found in staging logs -- try again")
    print(link)


if __name__ == "__main__":
    main()
