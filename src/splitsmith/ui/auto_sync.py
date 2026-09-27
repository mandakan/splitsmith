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
from ..sync.push import removable_full_trims
from ..sync.state import load_sync_state, local_fingerprint
from .jobs import Job, JobStatus

logger = logging.getLogger(__name__)

AUTO_SYNC_ENV = "SPLITSMITH_AUTO_SYNC"
SYNC_KINDS = frozenset({"sync_match", "auto_sync"})
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
_OFFLINE_REASON = "offline: could not reach the hosted server"


def write_marks_dirty(rest: str) -> bool:
    """Whether a successful write to ``/api/matches/{id}/<rest>`` leaves
    the match with something to push."""
    if rest.startswith(_READ_ONLY_WRITES):
        return False
    return not rest.endswith(_READ_ONLY_SUFFIXES)


def auto_sync_disabled_by_env() -> bool:
    return os.environ.get(AUTO_SYNC_ENV, "").strip() in ("0", "false", "False")


def _fetch_fingerprints(prefs: user_config.GlobalPrefs) -> dict[str, Fingerprint]:
    http = httpx.Client(
        base_url=prefs.hosted_base_url or "",
        headers={"Authorization": f"Bearer {prefs.hosted_token}"},
        timeout=15.0,
    )
    try:
        return HostedSyncClient(http=http).get_fingerprints()
    finally:
        http.close()


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
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.core = AutoSyncCore()
        self._jobs = jobs
        self._matches = matches
        self._submit = submit_auto_sync
        self._load_prefs = load_prefs
        self._fetch = fetch_fingerprints or _fetch_fingerprints
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
        with self._lock:
            match_id = self.core.pick(now, enabled=roots, busy=busy, sync_active=sync_active)
            if match_id is not None:
                self.core.on_sync_started(match_id, now)
        if match_id is not None:
            await self._submit(match_id, roots[match_id])

    async def _poll(self, prefs: user_config.GlobalPrefs, roots: dict[str, Path], now: float) -> None:
        try:
            server = await asyncio.to_thread(self._fetch, prefs)
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
        local = {mid: local_fingerprint(load_sync_state(root)) for mid, root in roots.items()}
        with self._lock:
            self.core.on_poll_ok(now, server, local)

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
        }
