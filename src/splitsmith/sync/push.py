"""Push executor for the desktop-to-hosted sync MVP (#631).

``run_push`` drives one local match through a full push: build the plan
(Task 7), adopt the hosted mirror, upload every changed media item
before any doc - the consistency invariant a hosted reader depends on,
since a doc can reference a clip that must already be resolvable - then
garbage-collect remote beep_review objects whose local file is gone
(#821 a: a confirmed video's snippets are swept pre-push, and the
pushed copy must follow or ``snippet_ready`` lies forever for reopened
items), then upsert every changed doc (#797: unchanged docs are already
filtered out of ``plan.docs`` by the planner). Sync state is saved
after each media item, each gc deletion, and each doc lands (crash-safe
incrementality: a killed process re-uploads/re-pushes only what didn't
finish), and once more at the end with ``last_synced_at`` stamped.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .. import match_model
from ..observability import PhaseTimer
from .base import save_base_doc
from .beep_snippets import generate_beep_snippets
from .client import HostedSyncClient, SyncClientError
from .plan import build_push_plan, doc_identity_key, hash_doc_body
from .state import SyncedItem, SyncState, load_sync_state, save_sync_state

logger = logging.getLogger(__name__)

#: Maps a ``sync_state.items`` remote key back to the local file it came
#: from (#821 gc phase below). Mirrors the ``trimmed``/``beep_review``
#: subdirs ``_SYNC_MEDIA_KEY_RE`` in ``sync_api.py`` admits.
_MEDIA_KEY_LOCAL_RE = re.compile(
    r"^matches/[^/]+/shooters/(?P<slug>[^/]+)/(?P<subdir>trimmed|beep_review)/(?P<name>[^/]+)$"
)


def _local_media_path(match_root: Path, remote_key: str) -> Path:
    """The local file ``remote_key`` was uploaded from, or ``match_root``
    itself for a key shape gc does not recognize.

    ``match_root`` always exists, so returning it makes an unrecognized
    key look "still present" and never a gc-deletion candidate - the
    guard fails closed rather than mapping an unknown shape onto some
    real (and wrong) path that might not exist.
    """
    m = _MEDIA_KEY_LOCAL_RE.match(remote_key)
    if m is None:
        return match_root
    shooter_root = match_model.Match.shooter_root(match_root, m.group("slug"))
    return shooter_root / m.group("subdir") / m.group("name")


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
    #: Docs whose canonical-JSON hash matched what was already recorded
    #: (#797) - not PUT this push. ``docs`` above stays "docs pushed".
    docs_skipped: int = 0
    #: Remote beep_review objects removed by the gc phase this push
    #: (#821) - their local files were swept because the video is now
    #: reviewed, so the remote copy (and its sync_state entry) followed.
    media_deleted: int = 0


def format_push_message(report: PushReport) -> str:
    """The one-line "Synced: ..." summary a sync job reports as its final
    progress message. Names the unchanged-doc count only when it's
    nonzero, so a first push (everything new) reads exactly as it did
    before #797."""
    message = f"Synced: {report.uploaded} uploaded, {report.skipped} skipped, {report.docs} docs"
    if report.docs_skipped:
        message += f" ({report.docs_skipped} unchanged)"
    return message


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
    sync_state: SyncState | None = None,
) -> PushReport:
    """Push ``match_root`` to the hosted mirror via ``client``.

    Raises :class:`~splitsmith.sync.client.SyncClientError` before any
    network call when the plan itself isn't pushable (a legacy project,
    an unsyncable absolute video path, a corrupt audit doc, ...) - the
    planner's ``errors`` are surfaced verbatim, joined by newlines.

    ``timer``, when given, is a job's :class:`PhaseTimer`: each of the
    five phases below (``plan``, ``ensure_match``, ``media``, ``gc``,
    ``docs``) opens a ``timer.phase(...)`` block in addition to the
    internal ``time.monotonic()`` accounting that always lands on
    :attr:`PushReport.timings`, so a caller running this as a job body
    gets the same numbers on ``Job.timings``.

    ``sync_state``, when given, is used as-is instead of loading it fresh
    - ``run_sync`` hands in the state it already loaded and mutated
    during its pull/merge phase, so this push sees those doc_versions and
    doc_hashes rather than a stale on-disk copy. ``None`` (the default)
    keeps ``run_push`` callable standalone, exactly as before.
    """
    timings: dict[str, float] = {}
    bytes_uploaded = 0
    media_items: list[MediaItemTiming] = []

    with _timed_phase(timings, timer, "plan"):
        sync_state = sync_state or load_sync_state(match_root)
        snippet_report = generate_beep_snippets(match_root)
        for err in snippet_report.errors:
            logger.warning("beep snippet generation: %s", err)
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

    media_deleted = 0
    with _timed_phase(timings, timer, "gc"):
        # Remote snippet GC (#821): sync_state remembers every key we
        # ever uploaded. A beep_review key whose local file is gone was
        # swept by generate_beep_snippets because the video is now
        # reviewed - the remote copy must follow or snippet_ready lies
        # forever for reopened items. Failures keep the key in
        # sync_state so the next push retries; gc must never fail a
        # push that already moved the operator's data.
        stale = [
            key
            for key in list(sync_state.items)
            if "/beep_review/" in key and not _local_media_path(match_root, key).exists()
        ]
        for key in stale:
            try:
                client.delete_media(plan.match_id, key)
            except Exception as exc:  # noqa: BLE001 - gc must never fail a push, see above
                logger.warning("beep_review gc: could not delete %s: %s", key, exc)
                continue
            del sync_state.items[key]
            media_deleted += 1
        if stale:
            save_sync_state(match_root, sync_state)

    with _timed_phase(timings, timer, "docs"):
        for doc in plan.docs:
            label = doc.kind if doc.slug is None else f"{doc.kind} ({doc.slug})"
            on_progress(1.0, f"syncing {label}")
            key = doc_identity_key(doc.kind, doc.slug, doc.stage_number)
            new_version = client.put_doc(
                plan.match_id, doc, expected_version=sync_state.doc_versions.get(key, 0)
            )
            # Record hash + version + base only after the PUT succeeds -
            # same crash-safety invariant as media: a failed push must
            # retry this doc next time, not skip it forever.
            sync_state.doc_hashes[key] = hash_doc_body(doc.body)
            sync_state.doc_versions[key] = new_version
            save_base_doc(match_root, key, doc.body)
            save_sync_state(match_root, sync_state)

    sync_state.last_synced_at = datetime.now(UTC)
    save_sync_state(match_root, sync_state)

    return PushReport(
        uploaded=len(plan.media),
        skipped=plan.media_skipped,
        docs=len(plan.docs),
        timings=timings,
        bytes_uploaded=bytes_uploaded,
        media_items=media_items,
        docs_skipped=plan.docs_skipped,
        media_deleted=media_deleted,
    )
