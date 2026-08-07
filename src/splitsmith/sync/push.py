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

import contextlib
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from ..observability import PhaseTimer
from .client import HostedSyncClient, SyncClientError
from .plan import build_push_plan
from .state import SyncedItem, load_sync_state, save_sync_state


class MediaItemTiming(BaseModel):
    """Size and upload duration of one uploaded media item.

    Skipped items (unchanged size+mtime since the last push) never get an
    entry - there is nothing to measure for a file that wasn't touched.
    """

    remote_key: str
    bytes: int
    seconds: float


class PushReport(BaseModel):
    """Summary of one push: how much moved and how much was skipped."""

    uploaded: int
    skipped: int
    docs: int
    #: Phase name -> elapsed seconds, always ``plan``/``ensure_match``/
    #: ``media``/``docs`` on a successful push (observability tier-1
    #: pattern, PR #485 - mirrored here via ``timer`` below onto
    #: ``Job.timings`` when this runs as a job body).
    timings: dict[str, float] = Field(default_factory=dict)
    bytes_uploaded: int = 0
    media_items: list[MediaItemTiming] = Field(default_factory=list)


@contextlib.contextmanager
def _timed_phase(timings: dict[str, float], timer: PhaseTimer | None, name: str) -> Iterator[None]:
    """Time one push phase into ``timings[name]`` (seconds), mirrored onto
    ``timer`` (the job's :class:`~splitsmith.observability.PhaseTimer`)
    when provided - same phase name lands on ``Job.timings`` the way every
    other job body's phases do. Elapsed time is recorded in a ``finally``
    so a raised exception is still timed and always propagates unchanged;
    this never swallows or replaces the body's exception.
    """
    start = time.monotonic()
    try:
        if timer is not None:
            with timer.phase(name):
                yield
        else:
            yield
    finally:
        timings[name] = time.monotonic() - start


def run_push(
    match_root: Path,
    *,
    client: HostedSyncClient,
    on_progress: Callable[[float, str], None] = lambda p, m: None,
    timer: PhaseTimer | None = None,
) -> PushReport:
    """Push ``match_root`` to the hosted mirror via ``client``.

    Raises :class:`~splitsmith.sync.client.SyncClientError` before any
    network call when the plan itself isn't pushable (a legacy project,
    an unsyncable absolute video path, a corrupt audit doc, ...) - the
    planner's ``errors`` are surfaced verbatim, joined by newlines.

    ``timer``, when given, is a job's :class:`PhaseTimer`: each of the
    four phases below (``plan``, ``ensure_match``, ``media``, ``docs``)
    opens a ``timer.phase(...)`` block in addition to the internal
    ``time.monotonic()`` accounting that always lands on
    :attr:`PushReport.timings`, so a caller running this as a job body
    gets the same numbers on ``Job.timings``.
    """
    timings: dict[str, float] = {}
    bytes_uploaded = 0
    media_items: list[MediaItemTiming] = []

    with _timed_phase(timings, timer, "plan"):
        sync_state = load_sync_state(match_root)
        plan = build_push_plan(match_root, sync_state=sync_state)
        if plan.errors:
            raise SyncClientError("\n".join(plan.errors))

    with _timed_phase(timings, timer, "ensure_match"):
        client.ensure_match(plan.match_id, plan.match_name)

    total_bytes = sum(item.size for item in plan.media)
    bytes_done = 0

    with _timed_phase(timings, timer, "media"):
        for item in plan.media:

            def _progress(delta: int, item=item) -> None:
                nonlocal bytes_done
                bytes_done += delta
                fraction = bytes_done / total_bytes if total_bytes else 1.0
                on_progress(fraction, f"uploading {item.remote_key}")

            item_start = time.monotonic()
            sha256 = client.upload_media(plan.match_id, item, progress=_progress)
            item_seconds = time.monotonic() - item_start
            sync_state.items[item.remote_key] = SyncedItem(
                sha256=sha256, size=item.size, mtime_ns=item.mtime_ns
            )
            save_sync_state(match_root, sync_state)
            bytes_uploaded += item.size
            media_items.append(
                MediaItemTiming(remote_key=item.remote_key, bytes=item.size, seconds=item_seconds)
            )

    with _timed_phase(timings, timer, "docs"):
        for doc in plan.docs:
            label = doc.kind if doc.slug is None else f"{doc.kind} ({doc.slug})"
            on_progress(1.0, f"syncing {label}")
            client.put_doc(plan.match_id, doc)

    sync_state.last_synced_at = datetime.now(UTC)
    save_sync_state(match_root, sync_state)

    return PushReport(
        uploaded=len(plan.media),
        skipped=plan.media_skipped,
        docs=len(plan.docs),
        timings=timings,
        bytes_uploaded=bytes_uploaded,
        media_items=media_items,
    )
