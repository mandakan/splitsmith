"""Import the marketing-site waitlist into ``access_requests`` (spec
2026-10-03, Task 10).

The marketing site's old `/api/waitlist` Cloudflare Function stored every
signup directly in a Workers KV namespace (binding ``WAITLIST``), keyed
``email:<lowercased-email>`` with a JSON value shaped
``{ts, ip_hash, ua, source}``. That endpoint is retired -- the site form
now posts straight to the hosted app's ``/api/v1/access-requests`` -- but
the KV namespace still holds every name collected before the cutover.
This script is the one-time migration of that KV data into the hosted
database's ``access_requests`` table, run by hand against production.

Producing the dump
-------------------

``package.json`` (in ``site/``) has ``waitlist:list`` and ``waitlist:get``
scripts, but neither alone emits the ``{email, ts}`` shape this script
reads: ``waitlist:list`` only lists key names, and ``waitlist:get`` fetches
one key's value at a time. Combine them with a shell loop and ``jq``, run
from ``site/``, to build ``dump.json``:

    pnpm run waitlist:list -- --remote | jq -r '.[].name' | while read -r key; do
      email="${key#email:}"
      value="$(pnpm run --silent waitlist:get -- "$key" --remote)"
      jq -n --arg email "$email" --argjson value "$value" \\
        '{email: $email, ts: $value.ts}'
    done | jq -s '.' > dump.json

``--remote`` is required on both commands -- without it, wrangler reads the
local dev KV namespace, not the deployed one.

Running the import
-------------------

    SPLITSMITH_DATABASE_URL=postgresql+asyncpg://... \\
        uv run python scripts/import_waitlist.py dump.json

Each entry's email is validated with the same rule the intake route uses
(``valid_request_email``: non-empty, has an ``@``, no whitespace/control
characters, at most 320 characters); invalid entries are skipped and
counted. ``ts`` is accepted as either an int/float of milliseconds since
the epoch (``Date.now()``, what the KV writer actually stores) or an ISO
8601 string, in case an older dump used that shape. Entries are imported
earliest-``ts``-first per email, so where the dump has more than one
timestamp for the same address (a resubmission before the cutover), the
earliest one is the one kept; an email that already has a pending request
or an existing account (including a soft-deleted one) is skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from splitsmith.db import create_engine, sessionmaker
from splitsmith.db.access_requests import AccessRequestStore, valid_request_email

_MAX_EMAIL_LENGTH = 320


def _parse_ts(ts: int | float | str) -> datetime:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, tz=UTC)
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _load_entries(path: Path) -> tuple[list[tuple[str, datetime]], int]:
    """Read the dump, validate each email, and sort earliest-``ts``-first
    so that, per email, the first entry handed to ``import_entry`` is the
    oldest one -- later duplicates for the same address are then skipped
    as "already known" by the store itself, rather than by any dedup
    logic here. Returns ``(entries, invalid_count)``."""
    raw: list[dict[str, Any]] = json.loads(path.read_text())
    entries: list[tuple[str, datetime]] = []
    invalid = 0
    for item in raw:
        email = str(item.get("email", ""))
        if not valid_request_email(email) or len(email) > _MAX_EMAIL_LENGTH:
            invalid += 1
            continue
        entries.append((email, _parse_ts(item["ts"])))
    entries.sort(key=lambda pair: pair[1])
    return entries, invalid


async def _run(path: Path) -> None:
    entries, invalid = _load_entries(path)

    url = os.environ["SPLITSMITH_DATABASE_URL"]
    engine = create_engine(url)
    store = AccessRequestStore(sessionmaker(engine))

    imported = 0
    skipped = invalid
    for email, requested_at in entries:
        inserted = await store.import_entry(email, requested_at=requested_at, source="import")
        if inserted:
            imported += 1
        else:
            skipped += 1

    print(f"imported {imported}, skipped {skipped} ({invalid} invalid)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path, help="path to the {email, ts}[] JSON dump")
    args = parser.parse_args(argv)
    asyncio.run(_run(args.dump))


if __name__ == "__main__":
    main(sys.argv[1:])
