"""The export-run record's persistence seam (#629).

Local mode writes ``<shooter_root>/export_runs.json``; hosted writes a
``state_docs`` row. The append re-loads on a version conflict so two
concurrent export jobs never lose a run, and it never fails the export.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from splitsmith import export_runs
from splitsmith.ui import server as server_mod

from .test_ui_server import _seed_match_export_project


def _run(run_id: str, stage: int) -> export_runs.ExportRun:
    return export_runs.ExportRun(
        run_id=run_id,
        kind="stage",
        finished_at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        duration_seconds=1.0,
        stage_numbers=[stage],
        formats=["trim"],
        anomaly_count=0,
        artifacts=[],
    )


@contextmanager
def _match_context(project_root: Path, match_id: str | None = None):
    """Bind the ContextVars the alias middleware sets per request.

    ``AppState.shooter_root`` reads ``current_match_root`` and the hosted
    branch of every state accessor reads ``current_match_id``; neither has
    a process-level fallback. A test that calls a state accessor outside a
    request has to set them itself.
    """
    tok_root = server_mod.current_match_root.set(project_root)
    tok_id = server_mod.current_match_id.set(match_id)
    try:
        yield
    finally:
        server_mod.current_match_root.reset(tok_root)
        server_mod.current_match_id.reset(tok_id)


def test_local_mode_appends_to_a_file_in_the_shooter_root(tmp_path: Path) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    shooter_root = project_root / "shooters" / "me"

    with _match_context(project_root):
        server_mod._record_export_run(state, "me", _run("a" * 32, 1))
        server_mod._record_export_run(state, "me", _run("b" * 32, 2))

    doc = json.loads((shooter_root / "export_runs.json").read_text(encoding="utf-8"))
    assert [r["run_id"] for r in doc["runs"]] == ["b" * 32, "a" * 32]
    # Never inside exports/ -- everything there is offered as a deliverable.
    assert not (shooter_root / "exports" / "export_runs.json").exists()


def test_a_write_failure_does_not_raise(tmp_path: Path, monkeypatch, caplog) -> None:
    """The deliverables are the product; the history is bookkeeping. A
    failed record write logs and returns -- a red job row over files that
    wrote correctly is a worse lie than a missing history line."""
    import logging

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(type(state), "save_export_runs", boom, raising=True)
    with caplog.at_level(logging.WARNING), _match_context(project_root):
        server_mod._record_export_run(state, "me", _run("c" * 32, 1))  # must not raise
    assert "export run record" in caplog.text


def test_hosted_mode_appends_to_state_docs(tmp_path: Path) -> None:
    import asyncio as _asyncio

    from splitsmith import match_model as _match_model
    from splitsmith.db import Base, ProjectStateStore, User, create_engine, sessionmaker

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    sf = sessionmaker(engine)

    async def _setup_db() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            user = User(email="export-runs@test.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    uid = _asyncio.run(_setup_db())
    store = ProjectStateStore(sf, user_id=uid)

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    local_match = _match_model.Match.load(project_root)
    match_id = local_match.match_id
    _asyncio.run(store.save_match(match_id, local_match.model_dump(mode="json"), expected_version=0))

    state = client.app.state.splitsmith_state
    old = state._project_state
    state._project_state = store
    try:
        with _match_context(project_root, match_id):
            server_mod._record_export_run(state, "me", _run("d" * 32, 1))
    finally:
        state._project_state = old

    doc, version = _asyncio.run(store.load_export_runs(match_id, "me"))
    assert version == 1
    assert [r["run_id"] for r in doc["runs"]] == ["d" * 32]
    # Local mode must not have been used as a fallback.
    assert not (project_root / "shooters" / "me" / "export_runs.json").exists()


def test_conflict_retry_reloads_and_reappends_onto_the_winner(tmp_path: Path, monkeypatch) -> None:
    """A concurrent export job's save landing between this helper's load and
    its own save must not be blindly overwritten.

    ``save_export_runs`` is wrapped so its *first* call plants a competing
    run directly in the store -- simulating a second export job's write
    landing first -- and then raises the real conflict exception the store
    would raise in that situation. The wrapper's later calls delegate to
    the unpatched implementation. If ``_record_export_run`` only retried
    the *same* save instead of re-loading, the competing run would never
    appear in the final document; if it re-loaded but then overwrote
    rather than re-appended, the competing run would still be lost.
    """
    import asyncio as _asyncio

    from splitsmith import match_model as _match_model
    from splitsmith.db import Base, ProjectStateStore, StateConflictError, User, create_engine, sessionmaker

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    sf = sessionmaker(engine)

    async def _setup_db() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            user = User(email="export-runs-race@test.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    uid = _asyncio.run(_setup_db())
    store = ProjectStateStore(sf, user_id=uid)

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    local_match = _match_model.Match.load(project_root)
    match_id = local_match.match_id
    _asyncio.run(store.save_match(match_id, local_match.model_dump(mode="json"), expected_version=0))

    state = client.app.state.splitsmith_state
    old = state._project_state
    state._project_state = store

    real_save = type(state).save_export_runs
    calls = {"n": 0}

    def racing_save(self, slug: str, doc: dict, *, version: int) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            # A concurrent export job wins the race: its run lands in the
            # store between this helper's load and its own save.
            winner_doc = export_runs.append_run(None, _run("e" * 32, 9))
            _asyncio.run(store.save_export_runs(match_id, slug, winner_doc, expected_version=0))
            raise StateConflictError("stale version")
        return real_save(self, slug, doc, version=version)

    monkeypatch.setattr(type(state), "save_export_runs", racing_save, raising=True)
    try:
        with _match_context(project_root, match_id):
            server_mod._record_export_run(state, "me", _run("d" * 32, 1))
    finally:
        state._project_state = old

    doc, version = _asyncio.run(store.load_export_runs(match_id, "me"))
    assert calls["n"] == 2, "expected exactly one conflict + one successful retry"
    assert version == 2
    # Newest-first, and both runs survive: the retry re-loaded the winner's
    # doc and appended onto it rather than overwriting it.
    assert [r["run_id"] for r in doc["runs"]] == ["d" * 32, "e" * 32]


def test_a_stage_export_records_a_run(tmp_path: Path, monkeypatch) -> None:
    from splitsmith import trim

    from .test_ui_server import _wait_for_job

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)

    def fake_trim_video(source, output_path, **kwargs):  # type: ignore[no-untyped-def]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"TRIMMED")
        return trim.TrimResult(output_path=Path(output_path), start_time=0.0, end_time=10.0)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)
    assert client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 10.0}).status_code == 200

    resp = client.post(
        "/api/shooters/me/stages/1/export",
        json={
            "write_trim": True,
            "write_csv": False,
            "write_fcpxml": False,
            "write_report": False,
            "write_overlay": False,
        },
    )
    assert resp.status_code == 200, resp.text
    assert _wait_for_job(client, resp.json()["id"])["status"] == "succeeded"

    doc = json.loads((project_root / "shooters" / "me" / "export_runs.json").read_text(encoding="utf-8"))
    assert len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["kind"] == "stage"
    assert run["stage_numbers"] == [1]
    # Requested formats, not produced files: the run asked for a trim only.
    assert run["formats"] == ["trim"]
    assert run["anomaly_count"] == 0
    assert [a["kind"] for a in run["artifacts"]] == ["trim"]
    assert run["artifacts"][0]["filename"].endswith("_trimmed.mp4")
    assert "/" not in run["artifacts"][0]["filename"]
    # Wall clock, not a timeline length -- and a real measurement, so it is
    # positive and small for a mocked trim.
    assert 0.0 < run["duration_seconds"] < 60.0


def test_a_failed_stage_export_records_nothing(tmp_path: Path, monkeypatch) -> None:
    """The record describes a completed run. A job that raises (here: the
    trim writer produces no clip at all) must leave no history line."""
    from splitsmith import trim

    from .test_ui_server import _wait_for_job

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)

    # ``_seed_match_export_project`` pre-populates a stale ``_trimmed.mp4``
    # so match-export tests have something to build an FCPXML from. Remove
    # it here: this test's premise is that the trim writer produces no clip
    # at all, and the exporter's stale-artefact fallback would otherwise
    # paper over the failure and let the job succeed.
    for stale in (project_root / "shooters" / "me" / "exports").iterdir():
        stale.unlink()

    def failing_trim(source, output_path, **kwargs):  # type: ignore[no-untyped-def]
        raise trim.FFmpegError("ffmpeg exploded")

    monkeypatch.setattr(trim, "trim_video", failing_trim)
    assert client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 10.0}).status_code == 200

    resp = client.post(
        "/api/shooters/me/stages/1/export",
        json={
            "write_trim": True,
            "write_csv": False,
            "write_fcpxml": False,
            "write_report": False,
            "write_overlay": False,
        },
    )
    assert _wait_for_job(client, resp.json()["id"])["status"] == "failed"
    assert not (project_root / "shooters" / "me" / "export_runs.json").exists()
