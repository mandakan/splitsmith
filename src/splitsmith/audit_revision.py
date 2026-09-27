"""Audit document revisions (spec 2026-09-27 s5).

A content hash, identical in local and hosted mode, that rides the audit
GET/PUT as ``_version`` so a save made from a stale copy is refused
instead of overwriting whatever a sync pull (or another tab) wrote.
Deliberately outside ``splitsmith.db``: the slim local install raises and
maps it (#1057 import-surface rule).
"""

from __future__ import annotations

import hashlib
import json

REVISION_FIELD = "_version"


class AuditRevisionConflictError(Exception):
    """A PUT carried a ``_version`` that no longer matches the stored doc."""


def audit_revision(doc: dict | None) -> str:
    if doc is None:
        return "none"
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
