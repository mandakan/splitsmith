"""Crash-recovery journal for the local job queue (issue #665).

The local :class:`~splitsmith.ui.jobs.JobRegistry` is in-memory; killing
the process used to lose everything queued. These tests cover the SQLite
journal that records active jobs and the boot-time resume that re-enqueues
whatever a previous process left behind.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from splitsmith.ui import server as server_mod
from splitsmith.ui.job_journal import (
    JobJournal,
    rehydrate_args,
    resume_journaled_jobs,
    to_wire_args,
)
from splitsmith.ui.jobs import JobRegistry, JobStatus


def _wait_until(predicate, *, timeout=5.0, poll=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


def _submit(reg: JobRegistry, **kwargs):
    return asyncio.run(reg.submit(**kwargs))


def _job(reg: JobRegistry, job_id: str):
    return asyncio.run(reg.get(job_id))


class _MatchContext:
    """Set the match ContextVars the way the alias middleware does."""

    def __init__(self, root: Path, match_id: str) -> None:
        self._root = root
        self._match_id = match_id

    def __enter__(self) -> _MatchContext:
        self._root_token = server_mod.current_match_root.set(self._root)
        self._id_token = server_mod.current_match_id.set(self._match_id)
        return self

    def __exit__(self, *exc: object) -> None:
        server_mod.current_match_root.reset(self._root_token)
        server_mod.current_match_id.reset(self._id_token)


def test_journal_row_lives_and_dies_with_the_job(tmp_path: Path) -> None:
    journal = JobJournal(tmp_path / "jobs.sqlite3")
    reg = JobRegistry(max_concurrent=1, journal=journal)
    release = threading.Event()
    reg.bodies.register("hold", lambda handle, **_a: release.wait(timeout=10))
    try:
        job = _submit(reg, kind="hold", args={}, stage_number=3, shooter_slug="anna", video_id="v1")
        rows = journal.load_active()
        assert [(r.kind, r.stage_number, r.shooter_slug, r.video_id) for r in rows] == [
            ("hold", 3, "anna", "v1")
        ]
        assert rows[0].id == job.id
    finally:
        release.set()
    assert _wait_until(lambda: _job(reg, job.id).status == JobStatus.SUCCEEDED)
    assert journal.load_active() == []


def test_failed_job_is_discarded_from_journal(tmp_path: Path) -> None:
    journal = JobJournal(tmp_path / "jobs.sqlite3")
    reg = JobRegistry(max_concurrent=1, journal=journal)

    def boom(_handle, **_a):
        raise ValueError("oh no")

    reg.bodies.register("boom", boom)
    job = _submit(reg, kind="boom", args={})
    assert _wait_until(lambda: _job(reg, job.id).status == JobStatus.FAILED)
    assert journal.load_active() == []


def test_journal_captures_match_context_and_args(tmp_path: Path) -> None:
    match_root = tmp_path / "match"
    match_root.mkdir()
    journal = JobJournal(tmp_path / "jobs.sqlite3")
    reg = JobRegistry(max_concurrent=1, journal=journal)
    release = threading.Event()
    reg.bodies.register("hold", lambda handle, **_a: release.wait(timeout=10))
    try:
        with _MatchContext(match_root, "m1"):
            _submit(reg, kind="hold", args={"x": 1, "flag": True})
        (row,) = journal.load_active()
        assert row.match_root == str(match_root)
        assert row.match_id == "m1"
        assert row.args == {"x": 1, "flag": True}
    finally:
        release.set()


def test_rows_survive_process_restart(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    journal = JobJournal(path)
    reg = JobRegistry(max_concurrent=1, journal=journal)
    release = threading.Event()
    reg.bodies.register("hold", lambda handle, **_a: release.wait(timeout=10))
    try:
        _submit(reg, kind="hold", args={})  # occupies the only worker slot
        _submit(reg, kind="hold", args={})  # stays PENDING
        # Simulate the crash: a fresh journal on the same path is what a
        # new process would open. Both the running and the pending job
        # must still be there.
        fresh = JobJournal(path)
        assert len(fresh.load_active()) == 2
        fresh.close()
    finally:
        release.set()


def test_resume_reenqueues_with_args_and_match_context(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    match_root = tmp_path / "match"
    match_root.mkdir()
    journal1 = JobJournal(path)
    reg1 = JobRegistry(max_concurrent=1, journal=journal1)
    release = threading.Event()
    reg1.bodies.register("hold", lambda handle, **_a: release.wait(timeout=10))
    try:
        with _MatchContext(match_root, "m1"):
            _submit(reg1, kind="hold", args={"x": 1}, stage_number=2, shooter_slug="anna")

        # "Restart": the old owner's liveness lock dies with it, then a
        # fresh journal + fresh registry boot with the body registered anew.
        journal1.close()
        journal2 = JobJournal(path)
        reg2 = JobRegistry(max_concurrent=1, journal=journal2)
        seen: dict[str, object] = {}
        done = threading.Event()

        def body(_handle, **args):
            seen["args"] = args
            seen["root"] = server_mod.current_match_root.get()
            seen["match_id"] = server_mod.current_match_id.get()
            done.set()

        reg2.bodies.register("hold", body)
        resumed = asyncio.run(resume_journaled_jobs(reg2, journal2))
        assert resumed == 1
        assert done.wait(timeout=5.0)
        assert seen == {"args": {"x": 1}, "root": match_root, "match_id": "m1"}
        (job,) = asyncio.run(reg2.list())
        assert (job.kind, job.stage_number, job.shooter_slug) == ("hold", 2, "anna")
        assert _wait_until(lambda: _job(reg2, job.id).status == JobStatus.SUCCEEDED)
        assert journal2.load_active() == []
    finally:
        release.set()


def test_resume_drops_unknown_kinds_and_vanished_matches(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    gone_root = tmp_path / "gone"
    gone_root.mkdir()
    journal1 = JobJournal(path)
    reg1 = JobRegistry(max_concurrent=1, journal=journal1)
    release = threading.Event()
    reg1.bodies.register("plug", lambda handle, **_a: release.wait(timeout=10))
    reg1.bodies.register("known", lambda handle, **_a: release.wait(timeout=10))
    try:
        _submit(reg1, kind="plug", args={})  # occupies the slot
        with _MatchContext(gone_root, "gone"):
            _submit(reg1, kind="known", args={})  # pending; its match will vanish
        gone_root.rmdir()

        journal1.close()
        journal2 = JobJournal(path)
        reg2 = JobRegistry(max_concurrent=1, journal=journal2)
        reg2.bodies.register("known", lambda handle, **_a: None)
        # "plug" is not registered on the new registry (a dev-only kind);
        # "known" points at a match root that no longer exists. Both rows
        # are dropped, nothing is submitted, and the journal drains.
        resumed = asyncio.run(resume_journaled_jobs(reg2, journal2))
        assert resumed == 0
        assert asyncio.run(reg2.list()) == []
        assert journal2.load_active() == []
    finally:
        release.set()


def test_resume_dedupes_identical_rows(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    journal1 = JobJournal(path)
    reg1 = JobRegistry(max_concurrent=1, journal=journal1)
    release = threading.Event()
    reg1.bodies.register("plug", lambda handle, **_a: release.wait(timeout=10))
    reg1.bodies.register("work", lambda handle, **_a: release.wait(timeout=10))
    try:
        _submit(reg1, kind="plug", args={})  # occupies the slot
        _submit(reg1, kind="work", args={"x": 1}, stage_number=1, shooter_slug="anna")
        _submit(reg1, kind="work", args={"x": 1}, stage_number=1, shooter_slug="anna")

        journal1.close()
        journal2 = JobJournal(path)
        reg2 = JobRegistry(max_concurrent=1, journal=journal2)
        reg2.bodies.register("work", lambda handle, **_a: None)
        resumed = asyncio.run(resume_journaled_jobs(reg2, journal2))
        assert resumed == 1
    finally:
        release.set()


def test_resume_leaves_rows_owned_by_a_live_sibling(tmp_path: Path) -> None:
    """A sibling journal instance still holds its liveness lock (another
    local server, or an earlier app in this same process): a boot must
    not steal - and double-run - the jobs it is running right now."""
    path = tmp_path / "jobs.sqlite3"
    journal1 = JobJournal(path)
    reg1 = JobRegistry(max_concurrent=1, journal=journal1)
    release = threading.Event()
    reg1.bodies.register("hold", lambda handle, **_a: release.wait(timeout=10))
    try:
        _submit(reg1, kind="hold", args={})
        # journal1 stays open: its owner lock is held, so its row is alive.
        journal2 = JobJournal(path)
        reg2 = JobRegistry(max_concurrent=1, journal=journal2)
        reg2.bodies.register("hold", lambda handle, **_a: None)
        resumed = asyncio.run(resume_journaled_jobs(reg2, journal2))
        assert resumed == 0
        assert asyncio.run(reg2.list()) == []
        # The row still belongs to its live owner: not consumed.
        assert len(journal2.load_active()) == 1
    finally:
        release.set()


def test_resume_takes_over_rows_once_the_owner_lock_is_released(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    journal1 = JobJournal(path)
    reg1 = JobRegistry(max_concurrent=1, journal=journal1)
    release = threading.Event()
    reg1.bodies.register("hold", lambda handle, **_a: release.wait(timeout=10))
    try:
        _submit(reg1, kind="hold", args={})
        # Owner death: the kernel would drop the lock on SIGKILL/reboot;
        # close() is the in-process equivalent.
        journal1.close()

        journal2 = JobJournal(path)
        reg2 = JobRegistry(max_concurrent=1, journal=journal2)
        done = threading.Event()
        reg2.bodies.register("hold", lambda handle, **_a: done.set())
        resumed = asyncio.run(resume_journaled_jobs(reg2, journal2))
        assert resumed == 1
        assert done.wait(timeout=5.0)
    finally:
        release.set()


def test_cancel_discards_journal_row(tmp_path: Path) -> None:
    journal = JobJournal(tmp_path / "jobs.sqlite3")
    reg = JobRegistry(max_concurrent=1, journal=journal)
    release = threading.Event()
    reg.bodies.register("hold", lambda handle, **_a: release.wait(timeout=10))
    try:
        running = _submit(reg, kind="hold", args={})
        pending = _submit(reg, kind="hold", args={})
        assert len(journal.load_active()) == 2
        asyncio.run(reg.cancel(pending.id))
        assert [r.id for r in journal.load_active()] == [running.id]
        # Cancelling the running job discards its row at request time -
        # a job the user aborted must not resurrect on the next boot.
        asyncio.run(reg.cancel(running.id))
        assert journal.load_active() == []
    finally:
        release.set()


def test_wire_args_roundtrip_for_export_req() -> None:
    from splitsmith.ui.server import ExportStageRequest

    req = ExportStageRequest(write_overlay=True, overlay_max_height=720)
    wire = to_wire_args({"slug": "anna", "stage_number": 2, "req": req})
    json.dumps(wire)  # must be JSON-serialisable as stored
    back = rehydrate_args("export", wire)
    assert back["slug"] == "anna"
    assert back["req"] == req


def test_create_app_resumes_journal_on_local_boot(empty_match: Path) -> None:
    """End-to-end: a journal row left by a dead process is re-enqueued by
    ``create_app`` in local mode. The resumed detect_beep will fail (the
    fixture has no video) - the guarantee under test is re-enqueue, not
    success."""
    from splitsmith.ui.job_journal import default_journal_path
    from splitsmith.ui.server import create_app

    path = default_journal_path()
    assert path is not None
    journal = JobJournal(path)
    reg = JobRegistry(max_concurrent=1, journal=journal)
    release = threading.Event()
    reg.bodies.register("plug", lambda handle, **_a: release.wait(timeout=10))
    reg.bodies.register("detect_beep", lambda handle, **_a: release.wait(timeout=10))
    try:
        _submit(reg, kind="plug", args={})  # occupies the slot
        with _MatchContext(empty_match, "m1"):
            _submit(
                reg,
                kind="detect_beep",
                args={"slug": "solo", "stage_number": 1, "video_id": "v1"},
                stage_number=1,
                shooter_slug="solo",
                video_id="v1",
            )
        journal.close()

        app = create_app()
        state = app.state.splitsmith_state
        jobs = asyncio.run(state.jobs.list())
        resumed = [j for j in jobs if j.kind == "detect_beep"]
        assert len(resumed) == 1
        assert (resumed[0].stage_number, resumed[0].shooter_slug, resumed[0].video_id) == (1, "solo", "v1")
    finally:
        release.set()
