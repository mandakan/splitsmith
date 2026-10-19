"""Write the server's OpenAPI document to ``src/splitsmith/ui_static/openapi.json``.

The SPA generates ``src/lib/api/types.gen.ts`` from that file (``pnpm gen:api``),
so the request and response types the pages see are the server's own.
Built offline from ``create_app(lab_enabled=True)``: no server, no browser,
no database. ``tests/test_openapi_export.py`` fails when the committed file
is behind the routes; run this script and commit the result.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "splitsmith" / "ui_static" / "openapi.json"


def build_document() -> dict:
    """The OpenAPI document of the local-mode app with the lab routes on."""
    os.environ.setdefault("SPLITSMITH_AUTO_SYNC", "0")
    os.environ.setdefault("SPLITSMITH_HOME", tempfile.mkdtemp(prefix="splitsmith-openapi-"))
    from splitsmith.ui.server import create_app

    return create_app(lab_enabled=True).openapi()


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    text = render(build_document())
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT} is stale; run: uv run python scripts/export_openapi.py", file=sys.stderr)
            return 1
        print("openapi.json is current")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
