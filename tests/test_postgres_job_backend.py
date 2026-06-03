"""Tests for :class:`PostgresJobBackend` (doc 04, Tier 2 of doc 10).

Runs against file-backed SQLite + aiosqlite. The backend is
engine-agnostic; SQLite suffices to prove the SQL shapes work, the
per-user filter holds, and the restart-sweep behaviour fires.

PR-gamma split enqueue from execution: :meth:`submit` writes a PENDING
row and hands a JSON payload to the injected ``deferrer`` (in
production, the Procrastinate enqueue); a separate worker process pops
it and calls :meth:`run_job`. These tests stand in for that worker with
two fake deferrers:

* an *inline* deferrer that drives :meth:`run_job` immediately, so a
  ``submit`` runs the body to its terminal state in one call (the common
  case -- most tests only care about the end state);
* a *no-op* deferrer that records nothing and runs nothing, leaving the
  row PENDING so tests can exercise the queued window (cancel-before-pickup,
  find_active dedupe) and then drive :meth:`run_job` themselves.

Why file-backed and not ``:memory:``: :meth:`run_job` offloads the body
to a worker thread that opens its own event loop via ``asyncio.run`` for
each DB call, and ``sqlite+aiosqlite:///:memory:`` is per-connection in
aiosqlite -- the worker thread would see a fresh empty DB and the row
it's supposed to update would be invisible. A ``tmp_path``-backed file
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


def _noop_deferrer() -> Callable[..., object]:
    """A deferrer that records nothing and runs nothing.

    Leaves the submitted row PENDING so tests can exercise the queued
    window before a worker pops the job.
    """

    async def _defer(**_payload: object) -> None:
        return None

    return _defer


def _inline_deferrer(box: list[PostgresJobBackend]) -> Callable[..., object]:
    """A deferrer that runs the job inline via :meth:`run_job`.

    Stands in for the worker round-trip: ``submit`` enqueues, this runs
    the body to its terminal state before ``submit`` returns. ``box`` is
    a one-element list holding the backend, populated after construction
    (the deferrer and the backend reference each other).
    """

    async def _defer(*, job_id: str, kind: str, args: dict | None = None, **_rest: object) -> None:
        await box[0].run_job(job_id=job_id, kind=kind, args=args)

    return _defer


def _register(backend: PostgresJobBackend, kind: str, fn: Callable[[object], None]) -> None:
    """Register ``fn`` as the body for ``kind``.

    The adapter swallows any dispatch ``args``; these test callables take
    only the :class:`JobHandle`.
    """
    backend.bodies.register(kind, lambda handle, **_args: fn(handle))


def _build_backend_for_new_user(
    tmp_path,
    *,
    email: str = "jobs@thias.se",
    db_name: str = "jobs.sqlite",
    inline: bool = True,
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
    box: list[PostgresJobBackend] = []
    deferrer = _inline_deferrer(box) if inline else _noop_deferrer()
    backend = PostgresJobBackend(session_factory, user_id=user_id, deferrer=deferrer)
    box.append(backend)
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


def test_submit_without_deferrer_raises(tmp_path) -> None:
    """An execute-only backend (worker side, no deferrer) must refuse to
    enqueue -- enqueuing belongs to the API process."""
    db_path = tmp_path / "no-deferrer.sqlite"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = sessionmaker(engine)

    async def _setup() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            user = User(email="worker@thias.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    user_id = asyncio.run(_setup())
    backend = PostgresJobBackend(session_factory, user_id=user_id, sweep_on_boot=False)
    _register(backend, "probe", lambda _h: None)
    with pytest.raises(RuntimeError, match="needs a deferrer"):
        asyncio.run(backend.submit(kind="probe"))


def test_submit_unknown_kind_fails_fast(tmp_path) -> None:
    """An unknown kind must fail before any row is written or enqueued."""
    from splitsmith.ui.jobs import UnknownJobKindError

    backend, _, _ = _build_backend_for_new_user(tmp_path)
    with pytest.raises(UnknownJobKindError):
        asyncio.run(backend.submit(kind="nope"))
    assert asyncio.run(backend.list()) == []


def test_submit_persists_row_and_runs_to_succeeded(tmp_path) -> None:
    backend, _, _ = _build_backend_for_new_user(tmp_path)
    seen = threading.Event()

    def work(_handle):
        seen.set()

    _register(backend, "test", work)
    job = asyncio.run(backend.submit(kind="test"))
    # The returned snapshot reflects the enqueue moment, before the
    # (inline) worker picks it up.
    assert job.status == JobStatus.PENDING
    assert seen.wait(timeout=2.0)

    assert _wait_until(lambda: asyncio.run(backend.get(job.id)).status == JobStatus.SUCCEEDED)
    final = asyncio.run(backend.get(job.id))
    assert final.progress == 1.0
    assert final.error is None
    assert final.finished_at is not None


def test_failed_job_records_error_string(tmp_path) -> None:
    backend, _, _ = _build_backend_for_new_user(tmp_path)

    def boom(_handle):
        raise ValueError("oh no")

    _register(backend, "test", boom)
    job = asyncio.run(backend.submit(kind="test"))
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
    # No-op deferrers: this test only checks row ownership, not execution.
    alice = PostgresJobBackend(session_factory, user_id=alice_id, deferrer=_noop_deferrer())
    bob = PostgresJobBackend(session_factory, user_id=bob_id, deferrer=_noop_deferrer())
    _register(alice, "probe", lambda _h: None)
    _register(bob, "probe", lambda _h: None)

    a_job = asyncio.run(alice.submit(kind="probe"))
    b_job = asyncio.run(bob.submit(kind="probe"))
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
    """A cancel that arrives before the worker pops the row should flip
    status straight to CANCELLED so the SPA can stop polling.

    With out-of-process dispatch the PENDING window is real: the no-op
    deferrer leaves the row queued, so cancel() races in cleanly without
    needing to block any executor."""
    backend, _, _ = _build_backend_for_new_user(tmp_path, inline=False)
    ran = threading.Event()

    def victim(_h):
        ran.set()

    _register(backend, "victim", victim)
    queued = asyncio.run(backend.submit(kind="victim"))
    assert asyncio.run(backend.get(queued.id)).status == JobStatus.PENDING

    snapshot = asyncio.run(backend.cancel(queued.id))
    assert snapshot is not None
    assert snapshot.status == JobStatus.CANCELLED
    # The body never ran.
    assert not ran.is_set()


def test_cancel_active_for_user_cancels_pending_jobs(tmp_path) -> None:
    """The delete cascade's pre-teardown stop cancels every queued job."""
    backend, _, _ = _build_backend_for_new_user(tmp_path, inline=False)
    _register(backend, "victim", lambda _h: None)
    a = asyncio.run(backend.submit(kind="victim"))
    b = asyncio.run(backend.submit(kind="victim"))

    cancelled = asyncio.run(backend.cancel_active_for_user())
    assert cancelled == 2
    assert asyncio.run(backend.get(a.id)).status == JobStatus.CANCELLED
    assert asyncio.run(backend.get(b.id)).status == JobStatus.CANCELLED


def test_cancel_active_for_user_ignores_terminal_jobs(tmp_path) -> None:
    """Already-finished jobs aren't re-cancelled (nothing active to stop)."""
    backend, _, _ = _build_backend_for_new_user(tmp_path, inline=True)
    _register(backend, "quick", lambda _h: None)
    done = asyncio.run(backend.submit(kind="quick"))  # inline -> SUCCEEDED
    assert asyncio.run(backend.get(done.id)).status == JobStatus.SUCCEEDED

    assert asyncio.run(backend.cancel_active_for_user()) == 0
    assert asyncio.run(backend.get(done.id)).status == JobStatus.SUCCEEDED


def test_cancel_active_for_user_isolation(tmp_path) -> None:
    """One user's bulk cancel never touches another user's active jobs."""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/cancel-iso.sqlite")
    session_factory = sessionmaker(engine)

    async def _setup() -> tuple[str, str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            alice = User(email="alice-cancel@thias.se")
            bob = User(email="bob-cancel@thias.se")
            s.add_all([alice, bob])
            await s.commit()
            await s.refresh(alice)
            await s.refresh(bob)
            return alice.id, bob.id

    alice_id, bob_id = asyncio.run(_setup())
    alice = PostgresJobBackend(session_factory, user_id=alice_id, deferrer=_noop_deferrer())
    bob = PostgresJobBackend(session_factory, user_id=bob_id, deferrer=_noop_deferrer())
    _register(alice, "probe", lambda _h: None)
    _register(bob, "probe", lambda _h: None)
    a_job = asyncio.run(alice.submit(kind="probe"))
    b_job = asyncio.run(bob.submit(kind="probe"))

    assert asyncio.run(alice.cancel_active_for_user()) == 1
    assert asyncio.run(alice.get(a_job.id)).status == JobStatus.CANCELLED
    # Bob's job is untouched.
    assert asyncio.run(bob.get(b_job.id)).status == JobStatus.PENDING


def test_run_job_skips_a_cancelled_row(tmp_path) -> None:
    """If a worker pops a job that was cancelled while PENDING, the body
    must not run and the row stays CANCELLED."""
    backend, _, _ = _build_backend_for_new_user(tmp_path, inline=False)
    ran = threading.Event()

    def victim(_h):
        ran.set()

    _register(backend, "victim", victim)
    queued = asyncio.run(backend.submit(kind="victim"))
    asyncio.run(backend.cancel(queued.id))

    # Worker pops it after the cancel landed.
    asyncio.run(backend.run_job(job_id=queued.id, kind="victim"))
    assert not ran.is_set()
    assert asyncio.run(backend.get(queued.id)).status == JobStatus.CANCELLED


def test_acknowledge_and_acknowledge_all_failures(tmp_path) -> None:
    backend, _, _ = _build_backend_for_new_user(tmp_path)

    def boom(_h):
        raise ValueError("kaboom")

    _register(backend, "a", boom)
    _register(backend, "b", boom)
    a = asyncio.run(backend.submit(kind="a"))
    b = asyncio.run(backend.submit(kind="b"))
    assert _wait_until(
        lambda: all(asyncio.run(backend.get(jid)).status == JobStatus.FAILED for jid in (a.id, b.id))
    )

    affected = asyncio.run(backend.acknowledge_all_failures())
    assert {j.id for j in affected} == {a.id, b.id}
    # Second call is a no-op since both are already acknowledged.
    again = asyncio.run(backend.acknowledge_all_failures())
    assert again == []

    # acknowledge on a non-failed job is a no-op snapshot return.
    _register(backend, "ok", lambda _h: None)
    succeeded = asyncio.run(backend.submit(kind="ok"))
    assert _wait_until(lambda: asyncio.run(backend.get(succeeded.id)).status == JobStatus.SUCCEEDED)
    snap = asyncio.run(backend.acknowledge(succeeded.id))
    assert snap is not None
    assert snap.acknowledged is False


def test_find_active_dedupe_by_kind_and_stage(tmp_path) -> None:
    """find_active matches a queued (PENDING) or running job so the SPA
    can dedupe a resubmission; terminal jobs stop matching."""
    backend, _, _ = _build_backend_for_new_user(tmp_path, inline=False)
    _register(backend, "trim", lambda _h: None)

    job = asyncio.run(backend.submit(kind="trim", stage_number=4))
    assert asyncio.run(backend.get(job.id)).status == JobStatus.PENDING

    # While queued, find_active matches on (kind, stage).
    found = asyncio.run(backend.find_active(kind="trim", stage_number=4))
    assert found is not None and found.id == job.id

    # Different stage: no match.
    assert asyncio.run(backend.find_active(kind="trim", stage_number=5)) is None
    # Different kind: no match.
    assert asyncio.run(backend.find_active(kind="detect_beep", stage_number=4)) is None

    # Drive it to completion; terminal jobs don't match find_active.
    asyncio.run(backend.run_job(job_id=job.id, kind="trim"))
    assert asyncio.run(backend.get(job.id)).status == JobStatus.SUCCEEDED
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
    backend = PostgresJobBackend(session_factory, user_id=user_id)

    pending_snap = asyncio.run(backend.get("stuck-pending"))
    running_snap = asyncio.run(backend.get("stuck-running"))
    done_snap = asyncio.run(backend.get("already-done"))
    assert pending_snap.status == JobStatus.FAILED
    assert running_snap.status == JobStatus.FAILED
    assert pending_snap.error == "server restarted before this job finished"
    assert running_snap.error == "server restarted before this job finished"
    # Already-terminal rows are untouched.
    assert done_snap.status == JobStatus.SUCCEEDED


def test_worker_backend_does_not_sweep_on_boot(tmp_path) -> None:
    """The worker passes ``sweep_on_boot=False`` so it never fails jobs
    that were queued for it to run."""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/worker-sweep.sqlite")
    session_factory = sessionmaker(engine)

    async def _setup() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            user = User(email="worker-sweep@thias.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            uid = user.id
            s.add(
                ComputeJobRow(
                    id="queued-for-worker",
                    user_id=uid,
                    kind="trim",
                    status=JobStatus.PENDING.value,
                    cancel_requested=False,
                    acknowledged=False,
                )
            )
            await s.commit()
            return uid

    user_id = asyncio.run(_setup())
    backend = PostgresJobBackend(session_factory, user_id=user_id, sweep_on_boot=False)
    snap = asyncio.run(backend.get("queued-for-worker"))
    assert snap.status == JobStatus.PENDING


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
    PostgresJobBackend(session_factory, user_id=alice_id)

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
    reflects the latest progress + message.

    Drives :meth:`run_job` on a background thread with a body that blocks
    after its first update, so the main thread can observe the mid-run
    state before the job finalises."""
    backend, _, _ = _build_backend_for_new_user(tmp_path, inline=False)
    proceed = threading.Event()

    def slow(handle):
        handle.update(progress=0.25, message="phase 1")
        proceed.wait(timeout=2.0)

    _register(backend, "test", slow)
    job = asyncio.run(backend.submit(kind="test"))

    worker = threading.Thread(target=lambda: asyncio.run(backend.run_job(job_id=job.id, kind="test")))
    worker.start()
    try:
        assert _wait_until(lambda: asyncio.run(backend.get(job.id)).message == "phase 1")
        snap = asyncio.run(backend.get(job.id))
        assert snap.progress == 0.25
    finally:
        proceed.set()
        worker.join(timeout=2.0)


def test_shutdown_blocks_new_submissions(tmp_path) -> None:
    backend, _, _ = _build_backend_for_new_user(tmp_path)
    backend.begin_shutdown()
    from splitsmith.ui.jobs import ShutdownInProgressError

    with pytest.raises(ShutdownInProgressError):
        asyncio.run(backend.submit(kind="probe"))


def test_args_reach_the_body_through_submit(tmp_path) -> None:
    """A non-empty ``args`` payload survives submit -> deferrer -> run_job
    and arrives at the body as keyword arguments.

    The other tests register through ``_register``, which swallows
    dispatch args; this one keeps them so a regression that dropped or
    mangled ``**call_args`` on the way to the body would fail here.
    """
    backend, _, _ = _build_backend_for_new_user(tmp_path)
    seen: dict[str, object] = {}

    def body(handle, **kwargs):
        seen.update(kwargs)

    backend.bodies.register("with_args", body)
    asyncio.run(backend.submit(kind="with_args", args={"slug": "stage-3", "chain": False}))

    assert seen == {"slug": "stage-3", "chain": False}


def test_before_body_failure_fails_the_job_not_strands_it(tmp_path) -> None:
    """A ``before_body`` that raises (e.g. the worker failing to resolve a
    match cross-process) must mark the row FAILED with the error message,
    not let the exception escape and strand the row at PENDING.

    This is the worker's match-binding guard: ``register_compute_task``
    resolves the match inside ``before_body`` precisely so this capture
    path applies.
    """
    backend, _, _ = _build_backend_for_new_user(tmp_path, inline=False)
    ran = {"body": False}

    def body(handle, **_kwargs):
        ran["body"] = True

    backend.bodies.register("needs_match", body)
    job = asyncio.run(backend.submit(kind="needs_match"))

    def before_body():
        raise RuntimeError("hosted worker cannot resolve match 'abc123'")

    asyncio.run(backend.run_job(job_id=job.id, kind="needs_match", before_body=before_body))

    snap = asyncio.run(backend.get(job.id))
    assert snap.status == JobStatus.FAILED.value
    assert "cannot resolve match 'abc123'" in (snap.error or "")
    assert ran["body"] is False  # body never ran -- binding failed first


def test_wire_and_rehydrate_round_trip_a_pydantic_req() -> None:
    """``_to_wire_args`` projects the Pydantic ``req`` (carried by
    ``export`` / ``match_export``) to a JSON-native dict so it can ride
    the queue, and ``_rehydrate_args`` reconstructs the typed model on
    the worker side. A closure can't be pickled onto a queue, so this
    JSON round-trip is the whole reason PR-gamma reshaped dispatch.
    """
    from splitsmith.db.job_backend import _to_wire_args
    from splitsmith.queue import _rehydrate_args
    from splitsmith.ui.server import ExportStageRequest

    req = ExportStageRequest(write_csv=False, overlay_theme="clean")
    wire = _to_wire_args({"req": req, "video_id": "v1"})

    # On the wire the model is a plain JSON dict, not a BaseModel.
    assert isinstance(wire["req"], dict)
    assert wire["req"]["write_csv"] is False
    assert wire["video_id"] == "v1"

    rehydrated = _rehydrate_args("export", wire)
    assert isinstance(rehydrated["req"], ExportStageRequest)
    assert rehydrated["req"] == req
    # Pass-through kinds are returned untouched (no ``req`` to rebuild).
    assert _rehydrate_args("trim", {"video_id": "v1"}) == {"video_id": "v1"}
