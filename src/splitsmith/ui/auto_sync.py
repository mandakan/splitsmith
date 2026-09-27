"""Desktop auto-sync driver (spec 2026-09-27 s2).

Feeds :class:`splitsmith.sync.auto.AutoSyncCore` from the outside world:
fingerprint polls against hosted, local writes (the dirty middleware in
``server.py``), job outcomes (a terminal listener), and submits the
``auto_sync`` job the core picks. Local mode only; ``create_app`` never
builds it hosted.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from .. import user_config
from ..sync.auto import AutoSyncCore, Fingerprint
from ..sync.auto_state import auto_sync_effective, load_auto_prefs
from ..sync.client import HostedSyncClient
from ..sync.plan import doc_identity_key
from ..sync.pull import PULLABLE_DOC_KINDS
from ..sync.push import removable_full_trims
from ..sync.state import load_sync_state, local_fingerprint, versions_digest
from .job_journal import try_lock
from .jobs import Job, JobStatus

logger = logging.getLogger(__name__)

AUTO_SYNC_ENV = "SPLITSMITH_AUTO_SYNC"
SYNC_KINDS = frozenset({"sync_match", "auto_sync"})
#: Job kinds that encode video. One on any match holds every automatic
#: sync (``AutoSyncCore.pick``); a manual sync is the user's call.
RENDER_KINDS = frozenset({"match_export", "export", "compare-grid", "generate_proxy"})
#: Job kinds whose success writes nothing that syncs (export history,
#: renders, proxies, downloads), so they must not schedule a push.
_UNSYNCED_JOB_KINDS = frozenset(
    {"match_export", "export", "youtube_upload", "compare-grid", "generate_proxy", "model_download"}
)
#: POST routes under /api/matches/{id}/ that compute or dry-run and write
#: nothing; each false dirty mark costs one pointless sync 45 s later.
_READ_ONLY_WRITES = (
    "match/sync",
    "jobs",
    "match/merge/plan",
)
_READ_ONLY_SUFFIXES = ("/export-preview", "/videos/suggest-coverage", "/videos/relink/scan")
_AUTH_REASON = "sign in again in hosted sync settings"
_OTHER_OWNER_REASON = "another splitsmith window on this computer is syncing"
#: Held by the one process on this machine that runs auto-sync (#1076).
OWNER_LOCK_FILE = "auto_sync.lock"
#: Held under a match root for the length of one sync job, manual or auto.
MATCH_SYNC_LOCK_FILE = ".sync.lock"
_OFFLINE_REASON = "offline: could not reach the hosted server"


class FileLock:
    """An exclusive, non-blocking advisory lock on ``path`` (#1076).

    The desktop app and a ``splitsmith ui`` share ``~/.splitsmith`` on
    purpose, so both would run auto-sync over the same matches; the
    one-sync-at-a-time rule is per process. ``flock`` is released by the
    kernel on any process death, so a crashed holder never strands it.
    A lock file that cannot be opened (read-only home) counts as held:
    a guard must not be the reason sync stops working.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: Any = None

    def acquire(self) -> bool:
        if self._fh is not None:
            return True
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fh = self._path.open("a+b")  # noqa: SIM115 - held until release()
        except OSError:
            logger.warning("could not open sync lock %s; running unguarded", self._path)
            return True
        if not try_lock(fh):
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is not None:
            self._fh.close()  # closing the descriptor drops the flock
            self._fh = None


def write_marks_dirty(rest: str) -> bool:
    """Whether a successful write to ``/api/matches/{id}/<rest>`` leaves
    the match with something to push."""
    if rest.startswith(_READ_ONLY_WRITES):
        return False
    return not rest.endswith(_READ_ONLY_SUFFIXES)


def auto_sync_disabled_by_env() -> bool:
    return os.environ.get(AUTO_SYNC_ENV, "").strip() in ("0", "false", "False")


def _http(prefs: user_config.GlobalPrefs) -> httpx.Client:
    return httpx.Client(
        base_url=prefs.hosted_base_url or "",
        headers={"Authorization": f"Bearer {prefs.hosted_token}"},
        timeout=15.0,
    )


def _fetch_fingerprints(prefs: user_config.GlobalPrefs) -> dict[str, Fingerprint]:
    with _http(prefs) as http:
        return HostedSyncClient(http=http).get_fingerprints()


def _fetch_manifest(prefs: user_config.GlobalPrefs, match_id: str) -> list[dict] | None:
    """The match's doc manifest, or None when hosted has no such match."""
    with _http(prefs) as http:
        return HostedSyncClient(http=http).find_doc_manifest(match_id)


def manifest_fingerprint(manifest: list[dict]) -> Fingerprint:
    """The ``/fingerprints`` triple computed from one match's doc manifest,
    for a hosted too old to serve the route (#1071). Filtered to the
    pullable kinds here because an older server's manifest may not be;
    the manifest carries the identities, so the digest (#1072) comes too."""
    versions = {
        doc_identity_key(d["doc_kind"], d.get("slug"), d.get("stage_number")): int(d["version"])
        for d in manifest
        if d["doc_kind"] in PULLABLE_DOC_KINDS
    }
    return len(versions), sum(versions.values()), versions_digest(versions)


class AutoSyncService:
    """One per local server process. Inputs arrive from request handlers
    and job worker threads, so every core mutation takes ``_lock``."""

    def __init__(
        self,
        *,
        jobs: Any,
        matches: Any,
        submit_auto_sync: Callable[[str, Path], Awaitable[None]],
        load_prefs: Callable[[], user_config.GlobalPrefs] = user_config.load_global_prefs,
        fetch_fingerprints: Callable[[user_config.GlobalPrefs], dict[str, Fingerprint]] | None = None,
        fetch_manifest: Callable[[user_config.GlobalPrefs, str], list[dict] | None] | None = None,
        clock: Callable[[], float] = time.time,
        owner_lock: FileLock | None = None,
    ) -> None:
        self.core = AutoSyncCore()
        #: ``None`` = no cross-process guard (tests); the server passes one.
        self._owner_lock = owner_lock
        self._owner_blocked = False
        self._jobs = jobs
        self._matches = matches
        self._submit = submit_auto_sync
        self._load_prefs = load_prefs
        self._fetch = fetch_fingerprints or _fetch_fingerprints
        self._fetch_manifest = fetch_manifest or _fetch_manifest
        self._clock = clock
        self._lock = threading.Lock()
        self._started = False
        self._auth_failed_token: str | None = None

    # -- thread-safe inputs ------------------------------------------------

    def mark_dirty(self, match_id: str) -> None:
        with self._lock:
            self.core.mark_dirty(match_id, self._clock())

    def on_job_terminal(self, job: Job) -> None:
        if job.match_id is None:
            return
        with self._lock:
            if job.kind in SYNC_KINDS:
                started = job.started_at.timestamp() if job.started_at is not None else None
                self.core.on_sync_done(
                    job.match_id,
                    self._clock(),
                    ok=job.status == JobStatus.SUCCEEDED,
                    started_at=started,
                )
            elif job.status == JobStatus.SUCCEEDED and job.kind not in _UNSYNCED_JOB_KINDS:
                self.core.mark_dirty(job.match_id, self._clock())

    # -- scheduling --------------------------------------------------------

    def _enabled_roots(self, prefs: user_config.GlobalPrefs) -> dict[str, Path]:
        self._matches.refresh_from_recent_projects()
        roots: dict[str, Path] = {}
        for match_id in self._matches.known_ids():
            try:
                root = self._matches.resolve(match_id)
            except KeyError:
                continue
            if auto_sync_effective(
                load_auto_prefs(root), load_sync_state(root), global_enabled=prefs.auto_sync_enabled
            ):
                roots[match_id] = root
        return roots

    async def tick(self) -> None:
        """One scheduling pass: maybe poll, then maybe submit one sync."""
        prefs = self._load_prefs()
        if not prefs.hosted_base_url or not prefs.hosted_token:
            return
        if self._owner_lock is not None:
            owner = self._owner_lock.acquire()
            with self._lock:
                if not owner:
                    self._owner_blocked = True
                    self.core.paused_reason = _OTHER_OWNER_REASON
                    return
                if self._owner_blocked:  # the other process quit: take over
                    self._owner_blocked = False
                    self.core.paused_reason = None
                    self.core.next_poll_at = 0.0
        now = self._clock()
        if self.core.auth_blocked() and prefs.hosted_token != self._auth_failed_token:
            with self._lock:
                self.core.clear_auth_block()
        roots = await asyncio.to_thread(self._enabled_roots, prefs)
        if not self._started:
            # Startup: pull every watched match once, so the reconciler
            # runs over anything that changed while the app was closed.
            self._started = True
            with self._lock:
                for match_id in roots:
                    self.core.mark_pull_due(match_id)
        if now >= self.core.next_poll_at and not self.core.auth_blocked():
            await self._poll(prefs, roots, now)
        jobs = await self._jobs.list()
        active = [j for j in jobs if j.status in (JobStatus.PENDING, JobStatus.RUNNING)]
        busy = {j.match_id for j in active if j.match_id}
        sync_active = any(j.kind in SYNC_KINDS for j in active)
        render_active = any(j.kind in RENDER_KINDS for j in active)
        with self._lock:
            match_id = self.core.pick(
                now, enabled=roots, busy=busy, sync_active=sync_active, render_active=render_active
            )
            if match_id is not None:
                self.core.on_sync_started(match_id, now)
        if match_id is not None:
            await self._submit(match_id, roots[match_id])

    def _fetch_server(self, prefs: user_config.GlobalPrefs, roots: dict[str, Path]) -> dict[str, Fingerprint]:
        try:
            return self._fetch(prefs)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
        # A hosted older than /fingerprints (#1071): one manifest per
        # watched match, the per-match diff the route replaced. Tried
        # again every poll, so an upgraded hosted is picked up at once.
        server: dict[str, Fingerprint] = {}
        for match_id in roots:
            manifest = self._fetch_manifest(prefs, match_id)
            if manifest is not None:
                server[match_id] = manifest_fingerprint(manifest)
        return server

    async def _poll(self, prefs: user_config.GlobalPrefs, roots: dict[str, Path], now: float) -> None:
        try:
            server = await asyncio.to_thread(self._fetch_server, prefs, roots)
        except httpx.HTTPStatusError as exc:
            auth = exc.response.status_code in (401, 403)
            if auth:
                self._auth_failed_token = prefs.hosted_token
            reason = _AUTH_REASON if auth else f"hosted returned {exc.response.status_code}"
            with self._lock:
                self.core.on_poll_error(now, reason, auth=auth)
            return
        except (httpx.HTTPError, OSError) as exc:
            logger.info("auto-sync poll failed: %s", exc)
            with self._lock:
                self.core.on_poll_error(now, _OFFLINE_REASON, auth=False)
            return
        local: dict[str, Fingerprint] = {}
        for mid, root in roots.items():
            fp = local_fingerprint(load_sync_state(root))
            # A hosted older than #1072 sends no digest: compare the pair.
            local[mid] = fp if len(server.get(mid, fp)) == 3 else fp[:2]
        with self._lock:
            self.core.on_poll_ok(now, server, local)

    def close(self) -> None:
        """Hand auto-sync to another process on this machine, if any."""
        if self._owner_lock is not None:
            self._owner_lock.release()

    async def run(self, interval_s: float = 5.0) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must outlive one bad tick
                logger.exception("auto-sync tick failed")
            await asyncio.sleep(interval_s)

    # -- status ------------------------------------------------------------

    def _match_id_for(self, match_root: Path) -> str | None:
        for match_id in self._matches.known_ids():
            try:
                if self._matches.resolve(match_id) == match_root:
                    return match_id
            except KeyError:
                continue
        return None

    def status_for(self, match_root: Path) -> dict[str, Any]:
        prefs = self._load_prefs()
        auto = load_auto_prefs(match_root)
        sync_state = load_sync_state(match_root)
        last = auto.last_auto
        if (
            last is not None
            and not last.ok
            and sync_state.last_synced_at is not None
            and sync_state.last_synced_at > last.at
        ):
            last = None  # a later sync (manual) succeeded; the failure is stale
        match_id = self._match_id_for(match_root)
        with self._lock:
            waiting = match_id is not None and self.core.waiting_for_change(match_id)
        return {
            "enabled": auto_sync_effective(auto, sync_state, global_enabled=prefs.auto_sync_enabled),
            "setting": auto.enabled,
            "global_enabled": prefs.auto_sync_enabled,
            "full_media": auto.full_media,
            # Full trims still on hosted; with full media off the next push
            # removes those whose rendition is there (spec v1.1).
            "full_trims_on_hosted": len(removable_full_trims(match_root, sync_state)),
            "paused_reason": self.core.paused_reason,
            "last_auto": last.model_dump(mode="json") if last else None,
            # Parked after a failure (#1070): no retry until a local write,
            # a hosted change, hosted coming back, or a manual sync.
            "waiting_for_change": waiting,
            # Synced before, then deleted on hosted: sync stays off until
            # the user publishes it again (POST .../match/sync/republish).
            "hosted_deleted": auto.hosted_deleted_at is not None,
        }
