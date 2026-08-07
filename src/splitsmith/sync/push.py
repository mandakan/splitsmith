"""Push executor for the desktop-to-hosted sync MVP (#631).

``run_push`` drives one local match through a full push: build the plan
(Task 7), adopt the hosted mirror, upload every changed media item
before any doc - the consistency invariant a hosted reader depends on,
since a doc can reference a clip that must already be resolvable - then
upsert every doc. Sync state is saved after each media item lands
(crash-safe incrementality: a killed process re-uploads only what didn't
finish), and once more at the end with ``last_synced_at`` stamped.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from .client import HostedSyncClient, SyncClientError
from .plan import build_push_plan
from .state import SyncedItem, load_sync_state, save_sync_state


class PushReport(BaseModel):
    """Summary of one push: how much moved and how much was skipped."""

    uploaded: int
    skipped: int
    docs: int


def run_push(
    match_root: Path,
    *,
    client: HostedSyncClient,
    on_progress: Callable[[float, str], None] = lambda p, m: None,
) -> PushReport:
    """Push ``match_root`` to the hosted mirror via ``client``.

    Raises :class:`~splitsmith.sync.client.SyncClientError` before any
    network call when the plan itself isn't pushable (a legacy project,
    an unsyncable absolute video path, a corrupt audit doc, ...) - the
    planner's ``errors`` are surfaced verbatim, joined by newlines.
    """
    sync_state = load_sync_state(match_root)
    plan = build_push_plan(match_root, sync_state=sync_state)
    if plan.errors:
        raise SyncClientError("\n".join(plan.errors))

    client.ensure_match(plan.match_id, plan.match_name)

    total_bytes = sum(item.size for item in plan.media)
    bytes_done = 0

    for item in plan.media:

        def _progress(delta: int, item=item) -> None:
            nonlocal bytes_done
            bytes_done += delta
            fraction = bytes_done / total_bytes if total_bytes else 1.0
            on_progress(fraction, f"uploading {item.remote_key}")

        sha256 = client.upload_media(plan.match_id, item, progress=_progress)
        sync_state.items[item.remote_key] = SyncedItem(sha256=sha256, size=item.size, mtime_ns=item.mtime_ns)
        save_sync_state(match_root, sync_state)

    for doc in plan.docs:
        label = doc.kind if doc.slug is None else f"{doc.kind} ({doc.slug})"
        on_progress(1.0, f"syncing {label}")
        client.put_doc(plan.match_id, doc)

    sync_state.last_synced_at = datetime.now(UTC)
    save_sync_state(match_root, sync_state)

    return PushReport(uploaded=len(plan.media), skipped=plan.media_skipped, docs=len(plan.docs))
