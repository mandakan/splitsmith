"""SQLite crash-recovery journal for the local job queue (issue #665).

The local :class:`~splitsmith.ui.jobs.JobRegistry` keeps job state in
memory; killing the process used to lose everything queued, and with no
batch-start there was no cheap way to rebuild the queue by hand. This
module gives local mode the persistence hosted mode gets from
``compute_jobs``: every active job is mirrored into a small SQLite file,
rows are discarded on any terminal transition, and boot re-enqueues
whatever a dead process left behind (:func:`resume_journaled_jobs`).

Design
------
- The journal stores only what re-submission needs: ``kind``, the
  JSON-projected ``args``, the dedupe keys (``stage_number`` /
  ``shooter_slug`` / ``video_id``), and the match context that local
  submit otherwise carries implicitly in the copied ``contextvars``
  (``current_match_root`` / ``current_match_id``). This mirrors the
  hosted queue payload, which ships ``match_id`` next to ``args`` and
  re-binds the ContextVars in the worker before the body runs.
- RUNNING jobs are journaled exactly like PENDING ones and restart from
  scratch on resume: every job kind is idempotent, so re-running a job
  the crash interrupted is correct, just not free.
- Journal writes are best-effort: a failed insert/delete logs and never
  fails the job it mirrors. The journal is a recovery aid; the in-memory
  registry stays the source of truth for a live process.
- One journal file per machine (``<user-config>/jobs.sqlite3``). Several
  live journal instances may share it (two local servers, or a second
  ``create_app`` in one process), so ownership must be exact: each
  instance holds an exclusive advisory lock on a per-instance token file
  (``jobs.sqlite3.locks/<token>.lock``) for its lifetime, and rows carry
  that token. Resume only takes over rows whose token lock is no longer
  held - the kernel drops the lock on any death (SIGKILL, power loss,
  reboot), and unlike a pid check this can't be fooled by pid reuse or
  by a sibling instance inside the same process. Concurrent resumers
  race on an atomic per-row claim (DELETE rowcount), so a row is
  re-enqueued exactly once.

The wire-args helpers (:func:`to_wire_args` / :func:`rehydrate_args`)
are the single source of truth for how a Pydantic ``req`` crosses a
persistence boundary; the hosted layer (``db.job_backend`` /
``splitsmith.queue``) delegates here.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from .jobs import Job, JobBackend

logger = logging.getLogger(__name__)


def to_wire_args(args: dict[str, Any]) -> dict[str, Any]:
    """Project ``args`` to a JSON-serialisable dict for persistence.

    Pydantic models (the ``req`` carried by ``export`` / ``match_export``
    and the compare-grid request) become ``model_dump(mode="json")``
    dicts; :func:`rehydrate_args` rebuilds the typed model before the
    body runs. Everything else (slug, stage_number, flags) is already
    JSON-native and passes through untouched.
    """
    return {k: (v.model_dump(mode="json") if isinstance(v, BaseModel) else v) for k, v in args.items()}


def rehydrate_args(kind: str, args: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the typed ``req`` Pydantic model dropped to a dict by
    :func:`to_wire_args`.

    Only ``export`` / ``match_export`` carry a ``req``; every other kind
    passes through. The request models moved to ``exports_api`` under
    #919's lift-as-you-go rule, and the import stays lazy under the same
    cycle rule that governs that module: ``server`` imports the models
    back from it, so nothing on the export-router side may be pulled in
    eagerly from a module ``server`` itself imports -- this one included.
    """
    if kind not in ("export", "match_export") or "req" not in args:
        return args
    from .exports_api import ExportStageRequest, MatchExportRequest

    model = ExportStageRequest if kind == "export" else MatchExportRequest
    out = dict(args)
    out["req"] = model.model_validate(args["req"])
    return out


class JournaledJob(BaseModel):
    """One recoverable job as read back from the journal."""

    id: str
    kind: str
    args: dict[str, Any]
    stage_number: int | None = None
    shooter_slug: str | None = None
    video_id: str | None = None
    match_id: str | None = None
    match_root: str | None = None
    # Liveness token of the journal instance that recorded the row: the
    # instance holds an exclusive lock on ``<locks-dir>/<token>.lock``
    # for its lifetime. Resume takes the row over only once that lock is
    # gone. ``owner_pid`` rides along purely for humans reading logs.
    owner_token: str | None = None
    owner_pid: int | None = None
    created_at: datetime


_SCHEMA = """
CREATE TABLE IF NOT EXISTS active_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    args_json TEXT NOT NULL,
    stage_number INTEGER,
    shooter_slug TEXT,
    video_id TEXT,
    match_id TEXT,
    match_root TEXT,
    owner_token TEXT,
    owner_pid INTEGER,
    created_at TEXT NOT NULL
)
"""


def _try_lock(fh: Any) -> bool:
    """Take an exclusive, non-blocking advisory lock on ``fh``.

    The kernel releases these on any process death - SIGKILL, power
    loss, reboot - which is what makes them a truthful liveness signal
    where a pid check is not (pid reuse; and a second open() in the SAME
    process is denied too, so a sibling journal instance in-process
    reads as alive - verified on darwin, documented in flock(2)).
    """
    try:
        if sys.platform == "win32":  # pragma: no cover - POSIX dev/CI
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


class JobJournal:
    """Mirror of the registry's active set in a SQLite file.

    Satisfies the :class:`~splitsmith.ui.jobs.JobJournalSink` protocol.
    ``record`` / ``discard`` are called from the event-loop thread
    (submit, cancel) and from executor worker threads (terminal
    transitions), so every statement runs on one shared connection
    behind a lock. Both are best-effort: failures log and return, they
    never propagate into the job they mirror.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = self._open()
        except sqlite3.DatabaseError:
            # The journal is expendable crash-recovery state: a corrupt
            # file (e.g. a torn write from a hard power-off) must not
            # brick the app. Start over once; a second failure is a real
            # environment problem and should surface.
            logger.exception("job journal at %s is unreadable; recreating it", path)
            path.unlink(missing_ok=True)
            self._conn = self._open()
        # Liveness anchor: hold an exclusive lock on a per-instance token
        # file for as long as this journal (i.e. this server) is alive.
        # Rows we record carry the token; ``owner_is_alive`` on another
        # instance probes the lock. Unique filename, so acquisition can
        # only fail on environmental grounds - then rows we record just
        # look dead to a sibling, which at worst duplicates an idempotent
        # job, never strands one.
        self._locks_dir = path.with_name(path.name + ".locks")
        self._locks_dir.mkdir(parents=True, exist_ok=True)
        self._owner_token = uuid.uuid4().hex
        self._owner_fh = (self._locks_dir / f"{self._owner_token}.lock").open("w")
        if not _try_lock(self._owner_fh):  # pragma: no cover - unique name; environmental only
            logger.warning("could not lock job-journal owner file; sibling servers may resume our jobs")

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_SCHEMA)
        conn.commit()
        return conn

    def record(self, job: Job, args: dict[str, Any]) -> None:
        """Persist ``job`` as recoverable, capturing the match context.

        Reads ``current_match_root`` / ``current_match_id`` from the
        submitting request's context - the same values ``submit``
        captures into the job's ``contextvars`` copy - so a resume in a
        fresh process can re-bind them (the local analogue of the hosted
        queue shipping ``match_id`` in its payload).
        """
        from .server import current_match_id, current_match_root

        match_root = current_match_root.get()
        match_id = current_match_id.get()
        try:
            args_json = json.dumps(to_wire_args(args))
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO active_jobs "
                    "(id, kind, args_json, stage_number, shooter_slug, video_id, "
                    " match_id, match_root, owner_token, owner_pid, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job.id,
                        job.kind,
                        args_json,
                        job.stage_number,
                        job.shooter_slug,
                        job.video_id,
                        match_id,
                        str(match_root) if match_root is not None else None,
                        self._owner_token,
                        os.getpid(),
                        job.created_at.isoformat(),
                    ),
                )
                self._conn.commit()
        except Exception:  # noqa: BLE001 - best-effort by contract; the job must still run
            logger.exception("failed to journal job %s (%s); it will not survive a crash", job.id, job.kind)

    def discard(self, job_id: str) -> None:
        """Drop the row for a job that reached a terminal state. Idempotent."""
        try:
            with self._lock:
                self._conn.execute("DELETE FROM active_jobs WHERE id = ?", (job_id,))
                self._conn.commit()
        except Exception:  # noqa: BLE001 - best-effort by contract
            logger.exception("failed to discard journaled job %s", job_id)

    def load_active(self) -> list[JournaledJob]:
        """All recoverable rows, oldest first (original submission order)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, kind, args_json, stage_number, shooter_slug, video_id, "
                "match_id, match_root, owner_token, owner_pid, created_at "
                "FROM active_jobs ORDER BY rowid"
            ).fetchall()
        return [
            JournaledJob(
                id=r[0],
                kind=r[1],
                args=json.loads(r[2]),
                stage_number=r[3],
                shooter_slug=r[4],
                video_id=r[5],
                match_id=r[6],
                match_root=r[7],
                owner_token=r[8],
                owner_pid=r[9],
                created_at=datetime.fromisoformat(r[10]),
            )
            for r in rows
        ]

    def claim(self, job_id: str) -> bool:
        """Atomically consume a row; True iff this caller won it.

        Two servers booting off the same crash race their resumes here:
        the DELETE's rowcount decides a single winner, so a row is
        re-enqueued exactly once. Errors count as "lost" - not claiming
        a row is always safe (it stays for the next boot).
        """
        try:
            with self._lock:
                cur = self._conn.execute("DELETE FROM active_jobs WHERE id = ?", (job_id,))
                self._conn.commit()
                return cur.rowcount > 0
        except Exception:  # noqa: BLE001 - best-effort by contract
            logger.exception("failed to claim journaled job %s", job_id)
            return False

    def owner_is_alive(self, token: str) -> bool:
        """Whether the journal instance that minted ``token`` still runs.

        Probes the token's lock file: missing or lockable means the owner
        is gone (its lock died with it); a denied lock means it is alive
        - including a sibling instance inside this same process, since a
        second open() is denied too. A dead owner's file is cleaned up on
        the way out.
        """
        lock_path = self._locks_dir / f"{token}.lock"
        try:
            fh = lock_path.open("r+")
        except OSError:
            return False
        with fh:
            if not _try_lock(fh):
                return True
            # Acquired: the owner is dead. Unlink while still holding the
            # lock so a concurrent prober sees either the locked file
            # (waits for next boot) or no file at all.
            lock_path.unlink(missing_ok=True)
            return False

    def sweep_stale_owner_locks(self) -> None:
        """Delete token files whose owners are gone. Called after resume;
        keeps the locks dir from accruing one file per crashed process."""
        try:
            for lock_path in self._locks_dir.glob("*.lock"):
                self.owner_is_alive(lock_path.stem)
        except OSError:  # pragma: no cover - directory races are ignorable
            pass

    def close(self) -> None:
        """Release the liveness lock and the connection.

        Production never calls this (the lock is meant to die with the
        process); it exists so tests can simulate an owner's death and
        so short-lived tooling doesn't leave token files behind.
        """
        with self._lock:
            self._conn.close()
            self._owner_fh.close()
        (self._locks_dir / f"{self._owner_token}.lock").unlink(missing_ok=True)


def default_journal_path() -> Path | None:
    """The per-machine journal location, or ``None`` when user config is
    disabled (``SPLITSMITH_DISABLE_USER_CONFIG``) or the dir can't be
    created - in which case local mode simply runs journal-less, which
    is the pre-#665 behaviour."""
    # Same resolution as projects.json / scoreboard.json; ``_ensure_dir``
    # is the one place that knows about the disable env + creation.
    from ..user_config import _ensure_dir

    base = _ensure_dir()
    return None if base is None else base / "jobs.sqlite3"


async def resume_journaled_jobs(backend: JobBackend, journal: JobJournal) -> int:
    """Re-enqueue every recoverable row onto ``backend``; return the count.

    Called once at local boot, after ``register_job_bodies``. Rows whose
    owner still holds its liveness lock are left alone - they belong to
    a running sibling server. Everything else is claimed atomically up
    front (single winner across concurrently-booting siblings); a
    successful re-submit records a fresh row under the new job id, so
    nothing is lost, and a claimed row we then choose to drop doesn't
    come back on the next boot either. Dropped with a warning:

    - kinds with no registered body (dev-only kinds register lazily at
      their route callsites and can't run before someone hits the route);
    - rows whose ``match_root`` no longer exists on disk;
    - exact duplicates of a row already resumed (the HTTP layer's
      ``find_active`` dedupe has no memory across restarts).

    The match ContextVars are re-bound around each submit so the body's
    ``state.shooter_root(...)`` calls resolve exactly as they would have
    in the original request - the local analogue of the hosted worker's
    ``_bind_match``.
    """
    from .server import current_match_id, current_match_root

    resumed = 0
    seen: set[tuple[Any, ...]] = set()
    for row in journal.load_active():
        if row.owner_token is not None and journal.owner_is_alive(row.owner_token):
            # A sibling journal instance (another local server, or an
            # earlier app in this same process) is alive and running this
            # row; it will discard it on completion. Not ours to consume.
            logger.info(
                "leaving journaled job %s (%s) to its live owner (pid %s)",
                row.id,
                row.kind,
                row.owner_pid,
            )
            continue
        if not journal.claim(row.id):
            # A concurrently-booting sibling won the row.
            continue
        if row.kind not in backend.bodies:
            logger.warning("dropping journaled job %s: no body registered for kind %r", row.id, row.kind)
            continue
        if row.match_root is not None and not Path(row.match_root).exists():
            logger.warning(
                "dropping journaled job %s (%s): match root %s no longer exists",
                row.id,
                row.kind,
                row.match_root,
            )
            continue
        key = (
            row.kind,
            json.dumps(row.args, sort_keys=True),
            row.stage_number,
            row.shooter_slug,
            row.video_id,
            row.match_id,
        )
        if key in seen:
            logger.warning("dropping journaled job %s (%s): duplicate of a resumed row", row.id, row.kind)
            continue
        seen.add(key)
        root_token = current_match_root.set(Path(row.match_root)) if row.match_root is not None else None
        id_token = current_match_id.set(row.match_id) if row.match_id is not None else None
        try:
            await backend.submit(
                kind=row.kind,
                args=rehydrate_args(row.kind, row.args),
                stage_number=row.stage_number,
                shooter_slug=row.shooter_slug,
                video_id=row.video_id,
            )
        finally:
            if root_token is not None:
                current_match_root.reset(root_token)
            if id_token is not None:
                current_match_id.reset(id_token)
        resumed += 1
    journal.sweep_stale_owner_locks()
    if resumed:
        logger.info("resumed %d job(s) queued by a previous run", resumed)
    return resumed


__all__ = [
    "JobJournal",
    "JournaledJob",
    "default_journal_path",
    "rehydrate_args",
    "resume_journaled_jobs",
    "to_wire_args",
]
