"""Desktop command runner (#1100 S2, spec 2026-09-28).

Owned by :class:`splitsmith.ui.auto_sync.AutoSyncService`, so it runs only
in the process that holds the per-machine owner lock (#1076). Each tick:

1. **Claim** for every match whose sync just succeeded with commands due
   (the poll saw ``pending_commands``; the sync that followed pulled the
   state the command acts on). A command this desktop refuses (the
   revision guard in :mod:`splitsmith.sync.commands`) completes as
   ``failed`` with the reason; otherwise it becomes a local job.
2. **Heartbeat** every tracked command, which keeps its lease. A
   ``cancel_requested`` reply cancels the local job; a 409 (another desktop
   took it, or it finished) cancels ours and forgets it.
3. **Finish**: a failed or cancelled job completes the command at once. A
   succeeded job whose kind is in ``SYNCED_RESULT_KINDS`` (its result
   travels in a synced doc, e.g. ``shot_detect``'s stage audit) asks for
   an immediate sync and completes as ``succeeded`` only after a sync
   that started after the job ended succeeds, so by the time the phone
   reads "done" hosted has the result; if that sync fails, the command
   fails with the sync's reason (the result still reaches hosted with a
   later sync). Any other kind's result travels in the completion itself
   (e.g. ``render_upload``'s video record), so it completes as
   ``succeeded`` on the very tick its job succeeds -- no sync is awaited.

State is in memory. A restart forgets it; the lease lapses, the command is
claimable again, and the revision guard refuses it if the first run landed.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..sync.commands import prior_result, refuse_reason
from .jobs import JobStatus

logger = logging.getLogger(__name__)

#: Starts the local job for a claimed command; returns the job id, or a
#: reason the command cannot start.
StartCommand = Callable[[str, Path, dict], Awaitable[tuple[str | None, str | None]]]

#: Kinds whose result travels in a synced doc (the stage audit), so the
#: command completes only after a sync that carried it. Any other kind's
#: result travels in the completion itself.
SYNCED_RESULT_KINDS = frozenset({"shot_detect"})


class CommandApi(Protocol):
    def claim(self, match_ids: list[str]) -> list[dict]: ...
    def heartbeat(self, command_id: str, message: str | None) -> dict | None: ...
    def complete(
        self, command_id: str, *, status: str, error: str | None = None, result: dict | None = None
    ) -> None: ...


@dataclass
class _Tracked:
    command: dict
    match_id: str
    job_id: str
    #: Set when the job succeeded: completion waits for a sync that started
    #: after this moment.
    finished_at: float | None = None
    result: dict = field(default_factory=dict)
    #: ``(status, error)`` once the command's outcome is known. Kept until
    #: hosted accepts the completion: the sync event that decided it is
    #: consumed once, so a failed completion call must not lose it.
    outcome: tuple[str, str | None] | None = None


class CommandRunner:
    def __init__(
        self,
        *,
        jobs: Any,
        start: StartCommand,
        request_sync_now: Callable[[str], None],
        clock: Callable[[], float],
    ) -> None:
        self._jobs = jobs
        self._start = start
        self._request_sync_now = request_sync_now
        self._clock = clock
        self._lock = threading.Lock()
        self._claim_due: set[str] = set()
        self._syncs_done: list[tuple[str, bool, float | None, str | None]] = []
        self._tracked: dict[str, _Tracked] = {}

    # -- thread-safe inputs (job listener thread) -------------------------

    def claim_after_sync(self, match_id: str) -> None:
        with self._lock:
            self._claim_due.add(match_id)

    def on_sync_done(self, match_id: str, *, ok: bool, started_at: float | None, error: str | None) -> None:
        with self._lock:
            self._syncs_done.append((match_id, ok, started_at, error))

    def tracked_ids(self) -> list[str]:
        with self._lock:
            return list(self._tracked)

    # -- the tick ---------------------------------------------------------

    async def tick(self, api: CommandApi, roots: dict[str, Path]) -> None:
        with self._lock:
            claim_for = sorted(self._claim_due & set(roots))
            self._claim_due -= set(claim_for)
            syncs_done, self._syncs_done = self._syncs_done, []
        for match_id in claim_for:
            await self._claim(api, match_id, roots[match_id])
        await self._settle_syncs(api, syncs_done)
        await self._heartbeat_and_finish(api)

    async def _claim(self, api: CommandApi, match_id: str, root: Path) -> None:
        try:
            commands = await asyncio.to_thread(api.claim, [match_id])
        except Exception as exc:  # noqa: BLE001 - the next due poll retries
            logger.info("desktop commands: claim for %s failed: %s", match_id, exc)
            return
        for command in commands:
            with self._lock:
                running_here = command["id"] in self._tracked
            if running_here:
                # This desktop already runs it (the lease lapsed while its
                # heartbeats failed); the claim renewed the lease. Starting
                # it again would fail as busy and cancel the running job.
                continue
            done = await asyncio.to_thread(prior_result, root, command)
            if done is not None:
                await self._complete(api, command["id"], "succeeded", result=done)
                continue
            reason = await asyncio.to_thread(refuse_reason, root, command)
            job_id: str | None = None
            if reason is None:
                job_id, reason = await self._start(match_id, root, command)
            if job_id is None:
                await self._complete(api, command["id"], "failed", error=reason or "could not start")
                continue
            with self._lock:
                self._tracked[command["id"]] = _Tracked(command=command, match_id=match_id, job_id=job_id)

    async def _settle_syncs(self, api: CommandApi, syncs_done: list) -> None:
        for match_id, ok, started_at, error in syncs_done:
            with self._lock:
                pending = [
                    t for t in self._tracked.values() if t.match_id == match_id and t.finished_at is not None
                ]
            waiting = [t for t in pending if started_at is None or started_at >= t.finished_at]
            if ok and len(waiting) < len(pending):
                # A sync that started before a result existed succeeded, and
                # the core may count it as the push the result asked for (it
                # dates syncs by its own clock, this runner by the jobs'), so
                # ask again or the command waits for an unrelated change.
                self._request_sync_now(match_id)
            for tracked in waiting:
                tracked.outcome = (
                    ("succeeded", None)
                    if ok
                    else (
                        "failed",
                        f"detection ran, but its result could not be synced: {error or 'sync failed'}",
                    )
                )

    async def _heartbeat_and_finish(self, api: CommandApi) -> None:
        with self._lock:
            tracked = list(self._tracked.values())
        for t in tracked:
            if t.outcome is not None:
                status, error = t.outcome
                result = t.result if status == "succeeded" else None
                await self._complete(api, t.command["id"], status, error=error, result=result)
                continue
            job = await self._jobs.get(t.job_id)
            if job is None:
                await self._complete(api, t.command["id"], "failed", error="the desktop job disappeared")
                continue
            if t.finished_at is None and job.status == JobStatus.SUCCEEDED:
                if t.command.get("kind") not in SYNCED_RESULT_KINDS:
                    # Kept on the entry so a retried completion never reads
                    # the job again: the registry may have evicted it by
                    # then, and the video it uploaded must still be reported.
                    t.outcome = ("succeeded", None)
                    t.result = dict(job.result or {})
                    await self._complete(api, t.command["id"], "succeeded", result=t.result)
                    continue
                t.finished_at = job.finished_at.timestamp() if job.finished_at else self._clock()
                t.result = {"job_id": job.id, "message": job.message}
                self._request_sync_now(t.match_id)
            elif job.status == JobStatus.FAILED:
                fallback = (
                    "detection failed" if t.command.get("kind") == "shot_detect" else "the desktop job failed"
                )
                await self._complete(api, t.command["id"], "failed", error=job.error or fallback)
                continue
            elif job.status == JobStatus.CANCELLED:
                await self._complete(api, t.command["id"], "cancelled")
                continue
            message = job.message if t.finished_at is None else "syncing the result"
            try:
                reply = await asyncio.to_thread(api.heartbeat, t.command["id"], message)
            except Exception as exc:  # noqa: BLE001 - keep trying; the lease is 10 min
                logger.info("desktop commands: heartbeat failed: %s", exc)
                continue
            if reply is None:
                # Finished elsewhere or taken by another desktop: stop ours.
                await self._cancel_job(t.job_id, job)
                self._forget(t.command["id"])
            elif reply.get("cancel_requested"):
                await self._cancel_job(t.job_id, job)

    async def _cancel_job(self, job_id: str, job: Any) -> None:
        if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
            await self._jobs.cancel(job_id)

    async def _complete(
        self,
        api: CommandApi,
        command_id: str,
        status: str,
        *,
        error: str | None = None,
        result: dict | None = None,
    ) -> None:
        try:
            await asyncio.to_thread(api.complete, command_id, status=status, error=error, result=result)
        except Exception as exc:  # noqa: BLE001 - keep it tracked; the next tick retries
            logger.info("desktop commands: complete %s failed: %s", command_id, exc)
            return
        self._forget(command_id)

    def _forget(self, command_id: str) -> None:
        with self._lock:
            self._tracked.pop(command_id, None)
