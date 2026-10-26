"""Mint a staging magic-link and print the login URL.

Staging's email transport is console-only (see
docs/saas-readiness/11-environment-strategy.md), so magic links land in
the staging serve service's logs as ``MAGIC_LINK <email> <url>`` lines
instead of a mailbox. This script triggers ``POST /api/v1/auth/begin``
against staging and reads the freshest link back from Cloud Logging
(``gcloud logging read``; serve runs on Cloud Run as
``splitsmith-serve-staging`` in project ``splitsmith-hosting``).

Links are single-use and expire after 15 minutes. Requires an
authenticated gcloud CLI (``gcloud auth login``) with read access to the
project's logs.

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

BASE_URL = "https://my.staging.splitsmith.app"
GCP_PROJECT = "splitsmith-hosting"
SERVICE = "splitsmith-serve-staging"
MARKER = "MAGIC_LINK"
DEFAULT_EMAIL = "m@thias.se"
LINK_RE = re.compile(r"https://\S+/auth/callback\?token=[A-Za-z0-9._~-]+")


def _gcloud_bin() -> str:
    found = shutil.which("gcloud")
    if found:
        return found
    raise SystemExit("gcloud CLI not found on PATH -- install it and run `gcloud auth login` first")


def begin_login(email: str) -> None:
    req = urllib.request.Request(
        f"{BASE_URL}/api/v1/auth/begin",
        data=json.dumps({"email": email}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Generous: staging scales to zero, and a cold start (boot migration
    # check, queue re-check) can take most of a minute.
    with urllib.request.urlopen(req, timeout=90) as resp:
        if resp.status != 200:
            raise SystemExit(f"auth/begin returned HTTP {resp.status}")


def log_filter(email: str) -> str:
    """The Cloud Logging filter for this email's MAGIC_LINK lines on staging serve.

    serve logs one JSON object per line, which Cloud Logging stores as
    ``jsonPayload`` with the text in ``message`` (``StructuredJsonFormatter``).
    """
    return " AND ".join(
        [
            'resource.type="cloud_run_revision"',
            f'resource.labels.service_name="{SERVICE}"',
            'jsonPayload.logger="splitsmith.db.email"',
            f'jsonPayload.message:"{MARKER} {email}"',
        ]
    )


def newest_link(entries: list[dict], email: str) -> str | None:
    """The link in the newest MAGIC_LINK entry for ``email``, if any.

    ``gcloud logging read`` returns newest first.
    """
    for entry in entries:
        message = (entry.get("jsonPayload") or {}).get("message", "")
        if MARKER in message and email in message:
            match = LINK_RE.search(message)
            if match:
                return match.group(0)
    return None


def fish_link(gcloud: str, email: str, attempts: int = 8) -> str | None:
    """Poll Cloud Logging for the link; ingestion lags the request by seconds."""
    for _ in range(attempts):
        proc = subprocess.run(
            [
                gcloud,
                "logging",
                "read",
                log_filter(email),
                "--project",
                GCP_PROJECT,
                "--freshness",
                "15m",
                "--limit",
                "20",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        try:
            entries = json.loads(proc.stdout or "[]")
        except ValueError:
            entries = []
        link = newest_link(entries, email)
        if link is not None:
            return link
        time.sleep(4)
    return None


def main() -> None:
    email = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EMAIL
    gcloud = _gcloud_bin()
    begin_login(email)
    link = fish_link(gcloud, email)
    if link is None:
        raise SystemExit("no MAGIC_LINK line found in the staging logs -- try again")
    print(link)


if __name__ == "__main__":
    main()
