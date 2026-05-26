"""Tests for :class:`PostgresJobBackend` (doc 04, Tier 2 of doc 10).

Runs against file-backed SQLite + aiosqlite. The backend is
engine-agnostic; SQLite suffices to prove the SQL shapes work, the
per-user filter holds, and the restart-sweep behaviour fires.

Why file-backed and not ``:memory:``: the in-process worker thread
opens its own event loop via ``asyncio.run`` for each DB call, and
``sqlite+aiosqlite:///:memory:`` is per-connection in aiosqlite --
the worker thread would see a fresh empty DB and the row it's
supposed to update would be invisible. A ``tmp_path``-backed file
shares state across connections without any extra setup.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

import pytest

from splitsmith.db import (
    Base,
    ComputeJobRow,
    PostgresJobBackend,
    User,
    create_engine,
    sessionmaker,
)
from splitsmith.ui.jobs import JobBackend, JobStatus


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0, poll: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


def _build_backend_for_new_user(
    tmp_path,
    *,
    email: str = "jobs@thias.se",
    max_concurrent: int = 2,
    db_name: str = "jobs.sqlite",
) -> tuple[PostgresJobBackend, sessionmaker, str]:
    db_path = tmp_path / db_name
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = sessionmaker(engine)

    async def _setup() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            user = User(email=email)
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    user_id = asyncio.run(_setup())
    backend = PostgresJobBackend(session_factory, user_id=user_id, max_concurrent=max_concurrent)
    return backend, session_factory, user_id


def test_satisfies_job_backend_protocol(tmp_path) -> None:
    backend, _, _ = _build_backend_for_new_user(tmp_path)
    typed: JobBackend = backend
    assert typed is backend


@pytest.mark.parametrize("bad", ["", None, 0, b"abc"])
def test_construction_rejects_empty_or_non_string_user_id(bad, tmp_path) -> None:
    from splitsmith.db import sessionmaker as smaker
    from splitsmith.db.engine import create_engine as _ce

    engine = _ce("sqlite+aiosqlite:///:memory:")
    factory = smaker(engine)
    with pytest.raises(ValueError, match="non-empty user_id"):
        PostgresJobBackend(factory, user_id=bad)  # type: ignore[arg-type]


def test_submit_persists_row_and_runs_to_succeeded(tmp_path) -> None:
    backend, _, _ = _build_backend_for_new_user(tmp_path)
    seen = threading.Event()

    def work(_handle):
        seen.set()

    job = asyncio.run(backend.submit(kind="test", fn=work))
    assert job.status == JobStatus.PENDING
    assert seen.wait(timeout=2.0)

    assert _wait_until(lambda: asyncio.run(backend.get(job.id)).status == JobStatus.SUCCEEDED)
    final = asyncio.run(backend.get(job.id))
    assert final.progress == 1.0
    assert final.error is None
    assert final.finished_at is not None


def test_failed_job_records_error_string(tmp_path) -> None:
    backend, _, _ = _build_backend_for_new_user(tmp_path, max_concurrent=1)

    def boom(_handle):
        raise ValueError("oh no")

    job = asyncio.run(backend.submit(kind="test", fn=boom))
    assert _wait_until(lambda: asyncio.run(backend.get(job.id)).status == JobStatus.FAILED)
    final = asyncio.run(backend.get(job.id))
    assert final.error == "oh no"
    assert final.finished_at is not None


def test_list_returns_only_this_users_jobs(tmp_path) -> None:
    """Per-user isolation: two backends bound to two users see disjoint
    job lists even when they submit identical kinds at the same time."""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/multi.sqlite")
    session_factory = sessionmaker(engine)

    async def _setup() -> tuple[str, str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            alice = User(email="alice-jobs@thias.se")
            bob = User(email="bob-jobs@thias.se")
            s.add_all([alice, bob])
            await s.commit()
            await s.refresh(alice)
            await s.refresh(bob)
            return alice.id, bob.id

    alice_id, bob_id = asyncio.run(_setup())
    alice = PostgresJobBackend(session_factory, user_id=alice_id, max_concurrent=2)
    bob = PostgresJobBackend(session_factory, user_id=bob_id, max_concurrent=2)

    a_job = asyncio.run(alice.submit(kind="probe", fn=lambda _h: None))
    b_job = asyncio.run(bob.submit(kind="probe", fn=lambda _h: None))
    assert a_job.id != b_job.id

    # Each backend sees only its user's row, even though both rows
    # share kind / stage / video_id.
    alice_jobs = asyncio.run(alice.list())
    bob_jobs = asyncio.run(bob.list())
    assert {j.id for j in alice_jobs} == {a_job.id}
    assert {j.id for j in bob_jobs} == {b_job.id}

    # Cross-user get/cancel/acknowledge return None.
    assert asyncio.run(bob.get(a_job.id)) is None
    assert asyncio.run(bob.cancel(a_job.id)) is None
    assert asyncio.run(bob.acknowledge(a_job.id)) is None


def test_cancel_pending_short_circuits_to_cancelled(tmp_path) -> None:
    """A cancel that arrives before the worker picks the row up should
    flip status straight to CANCELLED so the SPA can stop polling."""
    backend, _, _ = _build_backend_for_new_user(tmp_path, max_concurrent=1)
    proceed = threading.Event()
    started = threading.Event()

    def hold(_h):
        started.set()
        proceed.wait(timeout=2.0)

    # Block the executor with one slow job so a second submission
    # stays PENDING long enough for cancel() to race in.
    asyncio.run(backend.submit(kind="hold", fn=hold))
    assert started.wait(timeout=2.0)

    ran = threading.Event()

    def victim(_h):
        ran.set()

    queued = asyncio.run(backend.submit(kind="victim", fn=victim))
    assert asyncio.run(backend.get(queued.id)).status == JobStatus.PENDING
    snapshot = asyncio.run(backend.cancel(queued.id))
    assert snapshot is not None
    assert snapshot.status == JobStatus.CANCELLED
    proceed.set()
    # The worker should never have run the victim.
    assert not ran.is_set()


def test_acknowledge_and_acknowledge_all_failures(tmp_path) -> None:
    backend, _, _ = _build_backend_for_new_user(tmp_path, max_concurrent=1)

    def boom(_h):
        raise ValueError("kaboom")

    a = asyncio.run(backend.submit(kind="a", fn=boom))
    b = asyncio.run(backend.submit(kind="b", fn=boom))
    assert _wait_until(
        lambda: all(asyncio.run(backend.get(jid)).status == JobStatus.FAILED for jid in (a.id, b.id))
    )

    affected = asyncio.run(backend.acknowledge_all_failures())
    assert {j.id for j in affected} == {a.id, b.id}
    # Second call is a no-op since both are already acknowledged.
    again = asyncio.run(backend.acknowledge_all_failures())
    assert again == []

    # acknowledge on a non-failed job is a no-op snapshot return.
    succeeded = asyncio.run(backend.submit(kind="ok", fn=lambda _h: None))
    assert _wait_until(lambda: asyncio.run(backend.get(succeeded.id)).status == JobStatus.SUCCEEDED)
    snap = asyncio.run(backend.acknowledge(succeeded.id))
    assert snap is not None
    assert snap.acknowledged is False


def test_find_active_dedupe_by_kind_and_stage(tmp_path) -> None:
    backend, _, _ = _build_backend_for_new_user(tmp_path, max_concurrent=1)
    proceed = threading.Event()

    def slow(_h):
        proceed.wait(timeout=2.0)

    job = asyncio.run(backend.submit(kind="trim", stage_number=4, fn=slow))
    # While running, find_active matches on (kind, stage).
    assert _wait_until(lambda: asyncio.run(backend.get(job.id)).status == JobStatus.RUNNING)
    found = asyncio.run(backend.find_active(kind="trim", stage_number=4))
    assert found is not None and found.id == job.id

    # Different stage: no match.
    assert asyncio.run(backend.find_active(kind="trim", stage_number=5)) is None
    # Different kind: no match.
    assert asyncio.run(backend.find_active(kind="detect_beep", stage_number=4)) is None

    proceed.set()
    assert _wait_until(lambda: asyncio.run(backend.get(job.id)).status == JobStatus.SUCCEEDED)
    # Terminal jobs don't match find_active anymore.
    assert asyncio.run(backend.find_active(kind="trim", stage_number=4)) is None


def test_restart_sweep_marks_stuck_rows_failed(tmp_path) -> None:
    """A backend constructed against a DB with pre-existing PENDING /
    RUNNING rows (belonging to this user, from a prior server process)
    must flip them to FAILED on boot so the SPA doesn't see ghosts."""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/isolation.sqlite")
    session_factory = sessionmaker(engine)

    async def _setup() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            user = User(email="restart@thias.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            uid = user.id
            # Insert stuck rows directly as if a previous process left
            # them behind.
            s.add_all(
                [
                    ComputeJobRow(
                        id="stuck-pending",
                        user_id=uid,
                        kind="trim",
                        status=JobStatus.PENDING.value,
                        cancel_requested=False,
                        acknowledged=False,
                    ),
                    ComputeJobRow(
                        id="stuck-running",
                        user_id=uid,
                        kind="export",
                        status=JobStatus.RUNNING.value,
                        cancel_requested=False,
                        acknowledged=False,
                    ),
                    ComputeJobRow(
                        id="already-done",
                        user_id=uid,
                        kind="trim",
                        status=JobStatus.SUCCEEDED.value,
                        cancel_requested=False,
                        acknowledged=False,
                    ),
                ]
            )
            await s.commit()
            return uid

    user_id = asyncio.run(_setup())
    backend = PostgresJobBackend(session_factory, user_id=user_id, max_concurrent=1)

    pending_snap = asyncio.run(backend.get("stuck-pending"))
    running_snap = asyncio.run(backend.get("stuck-running"))
    done_snap = asyncio.run(backend.get("already-done"))
    assert pending_snap.status == JobStatus.FAILED
    assert running_snap.status == JobStatus.FAILED
    assert pending_snap.error == "server restarted before this job finished"
    assert running_snap.error == "server restarted before this job finished"
    # Already-terminal rows are untouched.
    assert done_snap.status == JobStatus.SUCCEEDED


def test_restart_sweep_only_touches_this_users_rows(tmp_path) -> None:
    """Alice booting her backend must not flip Bob's stuck rows --
    each user owns their own sweep boundary."""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/restart.sqlite")
    session_factory = sessionmaker(engine)

    async def _setup() -> tuple[str, str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            alice = User(email="alice-restart@thias.se")
            bob = User(email="bob-restart@thias.se")
            s.add_all([alice, bob])
            await s.commit()
            await s.refresh(alice)
            await s.refresh(bob)
            s.add_all(
                [
                    ComputeJobRow(
                        id="alice-stuck",
                        user_id=alice.id,
                        kind="trim",
                        status=JobStatus.PENDING.value,
                        cancel_requested=False,
                        acknowledged=False,
                    ),
                    ComputeJobRow(
                        id="bob-stuck",
                        user_id=bob.id,
                        kind="trim",
                        status=JobStatus.PENDING.value,
                        cancel_requested=False,
                        acknowledged=False,
                    ),
                ]
            )
            await s.commit()
            return alice.id, bob.id

    alice_id, bob_id = asyncio.run(_setup())
    # Only Alice's backend is constructed; Bob's row should stay
    # untouched.
    PostgresJobBackend(session_factory, user_id=alice_id, max_concurrent=1)

    async def _statuses() -> tuple[str, str]:
        async with session_factory() as s:
            from sqlalchemy import select

            a_status = (
                await s.execute(select(ComputeJobRow.status).where(ComputeJobRow.id == "alice-stuck"))
            ).scalar_one()
            b_status = (
                await s.execute(select(ComputeJobRow.status).where(ComputeJobRow.id == "bob-stuck"))
            ).scalar_one()
            return a_status, b_status

    alice_status, bob_status = asyncio.run(_statuses())
    assert alice_status == JobStatus.FAILED.value
    assert bob_status == JobStatus.PENDING.value


def test_progress_message_round_trip_via_handle(tmp_path) -> None:
    """Worker writes via :class:`JobHandle.update`; the persisted row
    reflects the latest progress + message."""
    backend, _, _ = _build_backend_for_new_user(tmp_path, max_concurrent=1)
    proceed = threading.Event()

    def slow(handle):
        handle.update(progress=0.25, message="phase 1")
        proceed.wait(timeout=2.0)

    job = asyncio.run(backend.submit(kind="test", fn=slow))
    assert _wait_until(lambda: asyncio.run(backend.get(job.id)).message == "phase 1")
    snap = asyncio.run(backend.get(job.id))
    assert snap.progress == 0.25
    proceed.set()


def test_shutdown_blocks_new_submissions(tmp_path) -> None:
    backend, _, _ = _build_backend_for_new_user(tmp_path, max_concurrent=1)
    backend.begin_shutdown()
    from splitsmith.ui.jobs import ShutdownInProgressError

    with pytest.raises(ShutdownInProgressError):
        asyncio.run(backend.submit(kind="probe", fn=lambda _h: None))
