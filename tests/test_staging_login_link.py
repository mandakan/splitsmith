"""``scripts/staging_login_link.py``: reading the magic link back from Cloud Logging."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "staging_login_link.py"


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("staging_login_link", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entry(message: str) -> dict:
    return {"jsonPayload": {"logger": "splitsmith.db.email", "message": message}}


def test_newest_link_takes_the_first_entry_for_the_email() -> None:
    """gcloud logging read returns newest first; another address's link and a
    non-link line are skipped."""
    script = _script()
    base = "https://my.staging.splitsmith.app/auth/callback?token="
    entries = [
        _entry(f"MAGIC_LINK other@example.com {base}otherTok"),
        _entry("console email backend ready"),
        _entry(f"MAGIC_LINK me@example.com {base}newTok"),
        _entry(f"MAGIC_LINK me@example.com {base}oldTok"),
    ]
    assert script.newest_link(entries, "me@example.com") == f"{base}newTok"
    assert script.newest_link(entries, "nobody@example.com") is None


def test_log_filter_targets_staging_serve_email_lines() -> None:
    query = _script().log_filter("me@example.com")
    assert 'resource.labels.service_name="splitsmith-serve-staging"' in query
    assert 'jsonPayload.logger="splitsmith.db.email"' in query
    assert 'jsonPayload.message:"MAGIC_LINK me@example.com"' in query
