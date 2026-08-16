"""Pull planning for the bidirectional sync (docs only - media never
flows hosted-to-desktop; desktop re-derives instead, see the slice spec).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .plan import doc_identity_key
from .state import SyncState

#: Doc kinds this client knows how to merge locally. ``list_doc_meta``
#: returns every kind in the match, including hosted-only ones like
#: ``export_runs`` (#629) -- and ``SyncClient._doc_path`` maps anything
#: it does not recognise onto the audit URL shape, so pulling an unknown
#: kind fails the whole sync rather than that one doc. An allowlist keeps
#: the next kind added inert by default.
PULLABLE_DOC_KINDS = frozenset({"match", "project", "audit"})


@dataclass(frozen=True)
class RemoteDoc:
    """One remotely-changed doc identity from the hosted manifest."""

    kind: str
    slug: str | None
    stage_number: int | None
    version: int
    updated_at: datetime


def remote_doc_key(rd: RemoteDoc) -> str:
    return doc_identity_key(rd.kind, rd.slug, rd.stage_number)


def plan_pull(manifest: list[dict], sync_state: SyncState) -> list[RemoteDoc]:
    """Manifest entries whose version differs from the recorded one.

    A key absent from ``doc_versions`` means "never seen" - pull it.
    Equality (not less-than) is deliberate: versions only move forward,
    and a recorded version that is somehow *ahead* of the manifest means
    local state is confused - re-pulling and re-merging is the safe
    answer either way.
    """
    changed: list[RemoteDoc] = []
    for entry in manifest:
        if entry["doc_kind"] not in PULLABLE_DOC_KINDS:
            continue
        rd = RemoteDoc(
            kind=entry["doc_kind"],
            slug=entry.get("slug"),
            stage_number=entry.get("stage_number"),
            version=entry["version"],
            updated_at=datetime.fromisoformat(entry["updated_at"]),
        )
        if sync_state.doc_versions.get(remote_doc_key(rd)) != rd.version:
            changed.append(rd)
    return changed
