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

import pytest

from splitsmith import export_runs, trim
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


def _fake_trim_video(source, output_path, **kwargs):  # type: ignore[no-untyped-def]
    """Stand in for ``trim.trim_video``: no ffmpeg, just a placeholder file.

    Shared by every test that needs a trim to "succeed" without caring what
    bytes land on disk -- the CSV/FCPXML/report writers this file exercises
    never inspect the trim's contents, only its existence and timing.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"TRIMMED")
    return trim.TrimResult(output_path=Path(output_path), start_time=0.0, end_time=10.0)


def _export_stage_trim_only(client, stage_number: int):
    """Submit a trim-only export and wait for it. The trim writer must
    already be monkeypatched by the caller."""
    from .test_ui_server import _wait_for_job

    resp = client.post(
        f"/api/shooters/me/stages/{stage_number}/export",
        json={
            "write_trim": True,
            "write_csv": False,
            "write_fcpxml": False,
            "write_report": False,
            "write_overlay": False,
        },
    )
    assert resp.status_code == 200, resp.text
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    return final


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


def test_concurrent_stage_exports_all_reach_the_desktop_history(tmp_path: Path, monkeypatch) -> None:
    """Six stage exports submitted at once leave six history lines.

    This is the Export page's own bundle button: it submits one export job
    per selected stage, and ``JobRegistry`` runs two of them at a time
    against the single ``export_runs.json`` for that shooter. Desktop's
    ``load_export_runs`` always reports version 0 and ``save_export_runs``
    ignores the version it is handed, so nothing in the optimistic-lock
    retry protects that read-modify-write -- only
    ``AppState.export_run_lock`` does.

    ``load_export_runs`` is wrapped to sleep after reading so the window
    between load and save is wide enough to hit every time rather than
    one round in ten. The sleep changes no behaviour; it only makes an
    unserialised implementation lose runs deterministically. With the
    lock held across load/append/save the sleeps serialise and all six
    survive.
    """
    import time

    from .test_ui_server import _wait_for_job

    stages = 6
    client, project_root = _seed_match_export_project(tmp_path, stage_count=stages)
    monkeypatch.setattr(trim, "trim_video", _fake_trim_video)

    state = client.app.state.splitsmith_state
    real_load = type(state).load_export_runs

    def slow_load(self, slug: str):  # type: ignore[no-untyped-def]
        out = real_load(self, slug)
        time.sleep(0.05)
        return out

    monkeypatch.setattr(type(state), "load_export_runs", slow_load, raising=True)

    for n in range(1, stages + 1):
        assert (
            client.post(f"/api/shooters/me/stages/{n}/time", json={"time_seconds": 10.0}).status_code == 200
        )
    job_ids = []
    for n in range(1, stages + 1):
        resp = client.post(
            f"/api/shooters/me/stages/{n}/export",
            json={
                "write_trim": True,
                "write_csv": False,
                "write_fcpxml": False,
                "write_report": False,
                "write_overlay": False,
            },
        )
        assert resp.status_code == 200, resp.text
        job_ids.append(resp.json()["id"])
    for job_id in job_ids:
        assert _wait_for_job(client, job_id, timeout=30.0)["status"] == "succeeded"

    doc = json.loads((project_root / "shooters" / "me" / "export_runs.json").read_text(encoding="utf-8"))
    # Every stage, exactly once: a lost update drops a row, and a temp-file
    # collision drops one too (that one at least logs; a lost update does
    # not log anything at all).
    assert sorted(r["stage_numbers"][0] for r in doc["runs"]) == list(range(1, stages + 1))
    assert len(doc["runs"]) == stages
    # No stray temp files left behind by the unique-name writer.
    assert not list((project_root / "shooters" / "me").glob("*.tmp"))


def test_two_concurrent_desktop_writers_do_not_collide_on_a_temp_file(tmp_path: Path, monkeypatch) -> None:
    """``save_export_runs`` is safe to call from two threads at once.

    Separate from the lock: a temp file named after the destination is
    shared by every writer to that shooter, so two writers write the one
    file and the second ``replace`` finds nothing there -- ENOENT, and a
    lost run. ``_record_export_run`` serialises desktop writers so this
    cannot be reached through the job bodies today, but the method's own
    contract should not depend on its only caller holding a lock.

    ``Path.replace`` is wrapped with a barrier so both writers have
    written their temp file before either renames. With a unique
    ``mkstemp`` name they rename different files and both succeed; with a
    shared name the second raises.
    """
    import threading
    import time

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state

    real_replace = Path.replace
    both_written = threading.Barrier(2)

    def slow_replace(self: Path, target):  # type: ignore[no-untyped-def]
        if self.name.startswith("export_runs.json"):
            both_written.wait(timeout=10)
            time.sleep(0.05)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", slow_replace, raising=True)

    errors: list[BaseException] = []

    def writer(n: int) -> None:
        with _match_context(project_root):
            try:
                state.save_export_runs("me", export_runs.append_run(None, _run(str(n) * 32, n)), version=0)
            except BaseException as exc:  # noqa: BLE001 -- reported, not swallowed
                errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == [], f"concurrent save_export_runs raised: {errors!r}"
    assert not list((project_root / "shooters" / "me").glob("*.tmp"))


def test_a_stage_export_records_a_run(tmp_path: Path, monkeypatch) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)

    monkeypatch.setattr(trim, "trim_video", _fake_trim_video)
    assert client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 10.0}).status_code == 200
    _export_stage_trim_only(client, 1)

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
    # positive and small for a mocked trim. 5s comfortably bounds a job
    # that does no real ffmpeg work, while still excluding a wrongly-wired
    # ``TrimResult.end_time - start_time`` (10.0, per the mock above).
    assert 0.0 < run["duration_seconds"] < 5.0


def test_a_failed_stage_export_records_nothing(tmp_path: Path, monkeypatch) -> None:
    """The record describes a completed run. A job that raises (here: the
    trim writer produces no clip at all) must leave no history line."""
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


def test_a_stage_export_records_requested_formats_separately_from_produced_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    """``formats`` is what was asked for; ``artifacts`` is what was written.

    Request a trim (which succeeds) alongside a CSV (which the exporter
    skips because the audit has no shots), so the two fields provably
    diverge -- an implementation that derived ``formats`` from the
    produced artifact kinds instead of the request flags would collapse
    them to the same list, and this test would not tell the difference
    from ``test_a_stage_export_records_a_run`` alone (there both fields
    happen to agree).
    """
    from .test_ui_server import _wait_for_job

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    shooter_root = project_root / "shooters" / "me"

    # ``_seed_match_export_project`` ships the audit doc with one shot;
    # overwrite with an empty ``shots[]`` so ``export_stage`` skips the CSV
    # ("csv not written: no shots audited") while the trim still succeeds
    # via the mock below.
    audit_path = shooter_root / "audit" / "stage1.json"
    audit_doc = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit_doc["shots"], "fixture no longer seeds a shot -- update this test's premise"
    audit_doc["shots"] = []
    audit_path.write_text(json.dumps(audit_doc), encoding="utf-8")

    monkeypatch.setattr(trim, "trim_video", _fake_trim_video)
    assert client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 10.0}).status_code == 200

    resp = client.post(
        "/api/shooters/me/stages/1/export",
        json={
            "write_trim": True,
            "write_csv": True,
            "write_fcpxml": False,
            "write_report": False,
            "write_overlay": False,
        },
    )
    assert resp.status_code == 200, resp.text
    assert _wait_for_job(client, resp.json()["id"])["status"] == "succeeded"

    doc = json.loads((shooter_root / "export_runs.json").read_text(encoding="utf-8"))
    run = doc["runs"][0]
    # Both were requested...
    assert run["formats"] == ["trim", "csv"]
    # ...but the csv never produced a file. Both assertions in one test:
    # a ``formats`` derived from ``artifacts`` would pass the first and
    # fail the second, or vice versa, only if the two fields differ here.
    assert "csv" in run["formats"]
    assert "csv" not in [a["kind"] for a in run["artifacts"]]
    assert [a["kind"] for a in run["artifacts"]] == ["trim"]


def test_a_match_export_records_one_run_spanning_its_stages(tmp_path: Path, monkeypatch) -> None:
    """Run *grouping* is the point: one match export over four stages is
    one history line, not four.

    Stage numbers are gapped (1, 2, 4, 5 -- stage 3 removed, #521's shape)
    rather than contiguous, so ``run["stage_numbers"]`` asserting equal to
    the request only passes for a verbatim passthrough: a ``range(1,
    n+1)`` reconstruction would produce ``[1, 2, 3, 4]`` instead and this
    test would catch it (#629 review finding 4).
    """
    from .test_ui_server import _stub_match_export_probe, _wait_for_job

    client, project_root = _seed_match_export_project(tmp_path, stage_numbers=[1, 2, 4, 5])
    _stub_match_export_probe(monkeypatch)

    resp = client.post(
        "/api/shooters/me/export/match",
        json={
            "stage_numbers": [1, 2, 4, 5],
            "head_pad_seconds": 0.5,
            "tail_pad_seconds": 1.0,
            "include_secondaries": True,
            # Trims are pre-staged and no overlay is asked for, so the
            # worker stays on the "skip the per-stage exporter" branch and
            # never shells out to ffmpeg.
            "include_overlay": False,
        },
    )
    assert resp.status_code == 200, resp.text
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final

    doc = json.loads((project_root / "shooters" / "me" / "export_runs.json").read_text(encoding="utf-8"))
    assert len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["kind"] == "match"
    assert run["stage_numbers"] == [1, 2, 4, 5]
    assert run["formats"] == ["fcpxml"]
    assert [a["kind"] for a in run["artifacts"]] == ["fcpxml"]
    assert run["artifacts"][0]["filename"].endswith("-match.fcpxml")

    # The wall-clock trap, pinned rather than described:
    # ``MatchExportResult.duration_seconds`` is the *stitched timeline's*
    # length, which this fixture makes 8.0s (4 stages x 2.0s effective --
    # same per-stage arithmetic as
    # ``test_export_over_a_gapped_stage_list_covers_every_remaining_stage``
    # in test_ui_server.py). The run's duration is how long the job took,
    # which for a fully mocked export is a fraction of a second. Asserting
    # both is what makes the second assertion discriminating -- wire the
    # wrong field in and it reads 8.0.
    assert final["result"]["duration_seconds"] == pytest.approx(8.0, abs=0.1)
    assert run["duration_seconds"] < 2.0


def test_a_match_export_with_youtube_sidecar_records_both_sidecar_files(tmp_path: Path, monkeypatch) -> None:
    """A run that asks for the YouTube sidecar writes two extra files next
    to the composed FCPXML: a captions ``.srt`` and a metadata JSON named
    ``"<stem>-youtube.json"`` (see ``ui/match_exports.export_match``'s
    ``if request.youtube_sidecar:`` block) -- not ``"<stem>.json"``. That
    naming is easy to get wrong (#629 review finding 1 found it wrong both
    in the record block and in the hosted push three lines above it), and
    nothing exercised the sidecar-present branch before this test (finding
    2). Both files must show up in the recorded artifacts, under their
    real filenames, with ``kind == "sidecar"``.
    """
    from .test_ui_server import _stub_match_export_probe, _wait_for_job

    client, project_root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)

    resp = client.post(
        "/api/shooters/me/export/match",
        json={
            "stage_numbers": [1, 2],
            "head_pad_seconds": 0.5,
            "tail_pad_seconds": 1.0,
            "include_secondaries": True,
            "include_overlay": False,
            "youtube_sidecar": True,
        },
    )
    assert resp.status_code == 200, resp.text
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final

    shooter_root = project_root / "shooters" / "me"
    doc = json.loads((shooter_root / "export_runs.json").read_text(encoding="utf-8"))
    run = doc["runs"][0]
    assert run["formats"] == ["fcpxml", "youtube-sidecar"]

    fcpxml_name = next(a["filename"] for a in run["artifacts"] if a["kind"] == "fcpxml")
    stem = fcpxml_name.removesuffix(".fcpxml")
    expected_srt = f"{stem}.srt"
    expected_json = f"{stem}-youtube.json"

    sidecar_filenames = {a["filename"] for a in run["artifacts"] if a["kind"] == "sidecar"}
    assert sidecar_filenames == {expected_srt, expected_json}

    # The record must not promise a file the download path can't serve --
    # both are real files on disk, not just claimed in the record.
    exports_dir = shooter_root / "exports"
    assert (exports_dir / expected_srt).exists()
    assert (exports_dir / expected_json).exists()


def test_a_match_export_pushes_the_youtube_sidecar_json_under_its_real_name(
    tmp_path: Path, monkeypatch
) -> None:
    """The hosted upload phase must push the metadata file the renderer
    actually wrote.

    ``_run_match_export``'s ``r2_upload`` phase pushed
    ``fcpxml_path.with_suffix(".json")``, a name nothing ever writes:
    ``match_exports.export_match`` writes ``<stem>-youtube.json``. On
    hosted that made the metadata sidecar undownloadable -- it never
    reached object storage, and the API container serving the link has
    no other copy. ``push_export_file`` silently skips a file that is not
    there, so the job stayed green and nothing logged.

    The push is observed rather than driven through real storage: local
    mode has no bound storage, so ``push_export_file`` would no-op and
    prove nothing. What is asserted is which *paths the job asks to
    push*, cross-checked against the files on disk -- so a wrong name
    fails here whether it is skipped later or not.
    """
    from splitsmith.ui import export_storage

    from .test_ui_server import _stub_match_export_probe, _wait_for_job

    pushed: list[Path] = []
    real_push = export_storage.push_export_file

    def spy_push(project, local_file: Path) -> None:  # type: ignore[no-untyped-def]
        pushed.append(local_file)
        real_push(project, local_file)

    monkeypatch.setattr(export_storage, "push_export_file", spy_push)

    client, project_root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)

    resp = client.post(
        "/api/shooters/me/export/match",
        json={
            "stage_numbers": [1, 2],
            "head_pad_seconds": 0.5,
            "tail_pad_seconds": 1.0,
            "include_secondaries": True,
            "include_overlay": False,
            "youtube_sidecar": True,
        },
    )
    assert resp.status_code == 200, resp.text
    assert _wait_for_job(client, resp.json()["id"])["status"] == "succeeded"

    by_name = {p.name: p for p in pushed}
    fcpxml_name = next(n for n in by_name if n.endswith("-match.fcpxml"))
    stem = fcpxml_name.removesuffix(".fcpxml")

    exports_dir = project_root / "shooters" / "me" / "exports"
    assert (exports_dir / f"{stem}-youtube.json").exists(), "the fixture no longer writes the sidecar"

    # All three match-level deliverables, each under the name on disk.
    assert set(by_name) == {fcpxml_name, f"{stem}.srt", f"{stem}-youtube.json"}
    # ...and specifically not the name the bug used, which no writer produces.
    assert not (exports_dir / f"{stem}.json").exists()
    for path in pushed:
        assert path.exists(), f"asked to push a file that does not exist: {path}"


def test_a_match_export_to_mp4_records_a_match_video_artifact(tmp_path: Path, monkeypatch) -> None:
    """``output_format="mp4"`` produces a single stitched deliverable, and
    the artefact kind must reflect that it's a video, not an FCPXML --
    the only other test in this file uses the default ``fcpxml`` format,
    so this branch of the kind ternary was previously untested (#629
    review finding 3).

    Stubs ``mp4_render.render_mp4`` the same way
    ``test_youtube_preset_threads_through_to_mp4_renderer`` in
    test_ui_match_exports.py does, so no real ffmpeg render happens.
    """
    from splitsmith.ui import match_exports as match_exports_mod

    from .test_ui_server import _stub_match_export_probe, _wait_for_job

    def fake_render_mp4(comp, *, output_path, **kwargs):  # type: ignore[no-untyped-def]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")

    monkeypatch.setattr(match_exports_mod.mp4_render, "render_mp4", fake_render_mp4)

    client, project_root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)

    resp = client.post(
        "/api/shooters/me/export/match",
        json={
            "stage_numbers": [1, 2],
            "head_pad_seconds": 0.5,
            "tail_pad_seconds": 1.0,
            "include_secondaries": True,
            "include_overlay": False,
            "output_format": "mp4",
        },
    )
    assert resp.status_code == 200, resp.text
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final

    doc = json.loads((project_root / "shooters" / "me" / "export_runs.json").read_text(encoding="utf-8"))
    run = doc["runs"][0]
    assert run["formats"] == ["mp4"]
    assert [a["kind"] for a in run["artifacts"]] == ["match_video"]
    assert run["artifacts"][0]["filename"].endswith("-match.mp4")


def test_export_runs_endpoint_serves_the_history_newest_first(tmp_path: Path, monkeypatch) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=2)

    monkeypatch.setattr(trim, "trim_video", _fake_trim_video)
    for n in (1, 2):
        assert (
            client.post(f"/api/shooters/me/stages/{n}/time", json={"time_seconds": 10.0}).status_code == 200
        )
        _export_stage_trim_only(client, n)

    resp = client.get("/api/shooters/me/exports/runs")
    assert resp.status_code == 200, resp.text
    runs = resp.json()["runs"]
    assert len(runs) == 2
    # Newest first is the stored order; the client must never have to sort.
    assert runs[0]["stage_numbers"] == [2]
    assert runs[1]["stage_numbers"] == [1]
    assert runs[0]["artifacts"][0]["filename"].endswith("_trimmed.mp4")


def test_export_runs_endpoint_marks_a_deleted_artifact_unavailable(tmp_path: Path, monkeypatch) -> None:
    """A run whose file the user has since deleted must not be offered as
    a download.

    Reachable on the very page that renders the history: it also renders
    the cleanup dialog, and cleanup deletes export files while leaving
    ``export_runs`` alone (the history is durable by design). The link
    carries ``download``, so a click on a dead one saves the JSON 404 body
    to disk under the video's own filename.

    ``available`` is a property of the response, not of the record: the
    stored run still names the file it produced -- that is what happened
    -- and only the flag moves.
    """
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)

    monkeypatch.setattr(trim, "trim_video", _fake_trim_video)
    assert client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 10.0}).status_code == 200
    _export_stage_trim_only(client, 1)

    shooter_root = project_root / "shooters" / "me"
    before = client.get("/api/shooters/me/exports/runs").json()["runs"]
    filename = before[0]["artifacts"][0]["filename"]
    assert before[0]["artifacts"][0]["available"] is True

    (shooter_root / "exports" / filename).unlink()

    after = client.get("/api/shooters/me/exports/runs").json()["runs"]
    assert after[0]["artifacts"][0]["filename"] == filename, "the record itself must not change"
    assert after[0]["artifacts"][0]["available"] is False
    # ...and the stored document is untouched: no ``available`` key was
    # written into it, and the run is still there.
    stored = json.loads((shooter_root / "export_runs.json").read_text(encoding="utf-8"))
    assert len(stored["runs"]) == 1
    assert "available" not in stored["runs"][0]["artifacts"][0]
    # The link the SPA would have rendered really is dead -- this is the
    # 404 body that would otherwise be saved under the video's filename.
    assert client.get(f"/api/shooters/me/exports/file/{filename}").status_code == 404


def test_export_runs_endpoint_is_empty_before_any_export(tmp_path: Path) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    resp = client.get("/api/shooters/me/exports/runs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


def test_export_runs_endpoint_survives_a_corrupt_log(tmp_path: Path) -> None:
    """Bookkeeping must not 500 a page. A truncated document reads as an
    empty history, not an error."""
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    (project_root / "shooters" / "me" / "export_runs.json").write_text("{not json", encoding="utf-8")
    resp = client.get("/api/shooters/me/exports/runs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}
