"""Hosted-mode worker<->API seam (state refactor).

The small JSON state (match / project / audit docs) now lives in the
``state_docs`` Postgres table via :class:`ProjectStateStore`, not in
S3-mirrored files. The unit-level coverage of that round-trip lives in
``test_project_state_store.py``; the live-Postgres RLS + serve->worker
proof lives in ``test_hosted_docker_smoke.py``.

What stays specific to this file is the **media** seam: the heavy binary
artifacts (audit-trim MP4s, export deliverables) still round-trip through
S3, keyed by the per-shooter ``scope`` that ``state.shooter_project``
binds. This file proves a worker can cut media on one working root, push
it, and the API mirrors it down on another -- with the project doc itself
resolved from a shared Postgres store rather than a mirrored file.

Uses an in-memory SQLite ``ProjectStateStore`` (the state docs) +
:class:`FilesystemStorage` against a temp dir (the S3 media stand-in), so
the seam exercises real reads/writes, not mocks.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from splitsmith.db import Base, ProjectStateStore, User, create_engine, sessionmaker
from splitsmith.match_model import Match
from splitsmith.storage import FilesystemStorage
from splitsmith.ui import audio as audio_helpers
from splitsmith.ui.project import PROJECT_FILE, MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import AppState, current_match_id, current_match_root


def _store_with_match(slug: str = "alpha") -> tuple[ProjectStateStore, str]:
    """Build an in-memory state store seeded with a one-shooter match +
    its (empty) project doc. Returns ``(store, match_id)``."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)
    match_id = "m_seam_test"

    async def _setup() -> ProjectStateStore:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            user = User(email="seam@thias.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            uid = user.id
        store = ProjectStateStore(session_factory, user_id=uid)
        await store.save_match(match_id, {"name": "Seam", "shooters": [slug]}, expected_version=0)
        await store.save_project(
            match_id, slug, MatchProject(name=slug).model_dump(mode="json"), expected_version=0
        )
        return store

    return asyncio.run(_setup()), match_id


class _BoundMatch:
    """Context manager: set the per-request match ContextVars."""

    def __init__(self, root: Path, match_id: str) -> None:
        self._root = root
        self._match_id = match_id

    def __enter__(self) -> None:
        self._rt = current_match_root.set(self._root)
        self._it = current_match_id.set(self._match_id)

    def __exit__(self, *exc: object) -> None:
        current_match_id.reset(self._it)
        current_match_root.reset(self._rt)


def test_save_is_noop_in_local_mode(tmp_path: Path) -> None:
    """No state store bound (desktop) -> save just writes locally."""
    import json

    shooter_root = tmp_path / "shooters" / "alpha"
    proj = MatchProject.init(shooter_root, name="alpha")
    proj.competitor_name = "Local"
    proj.save(shooter_root)  # _state_store is None -> file write
    assert json.loads((shooter_root / PROJECT_FILE).read_text())["competitor_name"] == "Local"


def test_project_doc_round_trips_api_worker_api(tmp_path: Path) -> None:
    """API write -> worker read+write -> API re-read sees the worker's
    value, with the project doc resolved from a shared state store across
    two distinct working roots (no file mirror involved)."""
    store, match_id = _store_with_match()
    state = AppState()
    state.project_state = store

    api_root = tmp_path / "api"
    api_root.mkdir()
    with _BoundMatch(api_root, match_id):
        proj = state.shooter_project("alpha")
        proj.competitor_name = "ApiWritten"
        proj.save(state.shooter_root("alpha"))

    worker_root = tmp_path / "worker"
    worker_root.mkdir()
    with _BoundMatch(worker_root, match_id):
        wproj = state.shooter_project("alpha")
        assert wproj.competitor_name == "ApiWritten"
        wproj.competitor_name = "WorkerWritten"
        wproj.save(state.shooter_root("alpha"))

    with _BoundMatch(api_root, match_id):
        reread = state.shooter_project("alpha")
        assert reread.competitor_name == "WorkerWritten"
    # No project.json was written on either root -- state lives in Postgres.
    assert not (api_root / "shooters" / "alpha" / PROJECT_FILE).exists()
    assert not (worker_root / "shooters" / "alpha" / PROJECT_FILE).exists()


def test_audit_doc_round_trips_api_worker_api(tmp_path: Path) -> None:
    """shot_detect's result (audit doc) written on the worker is visible to
    the API via the shared state store."""
    store, match_id = _store_with_match()
    state = AppState()
    state.project_state = store

    worker_root = tmp_path / "worker"
    worker_root.mkdir()
    with _BoundMatch(worker_root, match_id):
        state.save_audit("alpha", 1, {"shots": [0.21, 0.55]}, version=0)

    api_root = tmp_path / "api"
    api_root.mkdir()
    with _BoundMatch(api_root, match_id):
        doc, version = state.load_audit("alpha", 1)
        assert doc == {"shots": [0.21, 0.55]}
        assert version == 1


def test_audit_trim_mp4_round_trips_worker_to_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The heavy binary seam: a worker cuts the audit-trim MP4 and pushes
    it to storage; the API mirrors it down to serve the scrub clip. The
    project doc resolves from the shared state store; only the MP4 rides
    S3. Proves the scope wiring (``state.shooter_project`` bind) produces
    matching push/pull keys across two working roots."""
    monkeypatch.setattr("splitsmith.trim.select_audit_encoder", lambda *a, **k: "libx264")

    def fake_trim_video(**kwargs: object) -> None:
        dest = Path(kwargs["output_path"])  # type: ignore[arg-type]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"MP4DATA")

    monkeypatch.setattr("splitsmith.trim.trim_video", fake_trim_video)

    store, match_id = _store_with_match()
    storage = FilesystemStorage(tmp_path / "s3")
    state = AppState()
    state.project_state = store
    state.storage = storage

    worker_root = tmp_path / "worker"
    worker_root.mkdir()
    with _BoundMatch(worker_root, match_id):
        wproj = state.shooter_project("alpha")
        video = StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=3.5, stage_number=1)
        wproj.stages = [StageEntry(stage_number=1, stage_name="One", time_seconds=10.0, videos=[video])]
        wshooter = state.shooter_root("alpha")
        wproj.save(wshooter)
        source = wshooter / "raw" / "v.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video bytes")
        audio_helpers.ensure_video_audit_trim(wshooter, 1, video, source, 3.5, 10.0, project=wproj)

    api_root = tmp_path / "api"
    api_root.mkdir()
    with _BoundMatch(api_root, match_id):
        aproj = state.shooter_project("alpha")
        prim = aproj.stage(1).primary()
        assert prim is not None
        ashooter = state.shooter_root("alpha")
        assert not audio_helpers.trimmed_video_path(ashooter, 1, prim, project=aproj).exists()
        pulled = audio_helpers.pull_trimmed_video(ashooter, 1, prim, project=aproj)
        assert pulled.exists()
        assert pulled.read_bytes() == b"MP4DATA"


def test_export_media_round_trips_worker_to_api(tmp_path: Path) -> None:
    """A worker produces export deliverables and pushes them to storage;
    the API mirrors them down via ``pull_export_file``. Project doc from
    the shared state store; deliverables ride S3."""
    from splitsmith.ui import export_storage
    from splitsmith.ui.exports import StageExportResult

    store, match_id = _store_with_match()
    storage = FilesystemStorage(tmp_path / "s3")
    state = AppState()
    state.project_state = store
    state.storage = storage

    worker_root = tmp_path / "worker"
    worker_root.mkdir()
    with _BoundMatch(worker_root, match_id):
        wproj = state.shooter_project("alpha")
        ed = wproj.exports_path(state.shooter_root("alpha"))
        ed.mkdir(parents=True, exist_ok=True)
        (ed / "stage1_one_trimmed.mp4").write_bytes(b"TRIM")
        (ed / "stage1_one.fcpxml").write_bytes(b"<fcpxml/>")
        (ed / "stage1_one_report.txt").write_bytes(b"REPORT")
        result = StageExportResult(
            stage_number=1,
            trimmed_video_path=ed / "stage1_one_trimmed.mp4",
            csv_path=None,
            fcpxml_path=ed / "stage1_one.fcpxml",
            report_path=ed / "stage1_one_report.txt",
            overlay_path=None,
            shots_written=2,
            anomalies=[],
        )
        export_storage.push_stage_export_outputs(wproj, result)

    api_root = tmp_path / "api"
    api_root.mkdir()
    with _BoundMatch(api_root, match_id):
        aproj = state.shooter_project("alpha")
        aed = aproj.exports_path(state.shooter_root("alpha"))
        for name, data in [
            ("stage1_one_trimmed.mp4", b"TRIM"),
            ("stage1_one.fcpxml", b"<fcpxml/>"),
            ("stage1_one_report.txt", b"REPORT"),
        ]:
            target = aed / name
            assert not target.exists()
            assert export_storage.pull_export_file(aproj, target) is True
            assert target.read_bytes() == data


def test_seam_is_noop_without_storage(tmp_path: Path) -> None:
    """Local mode (no state store, no storage): audit save/load go to disk."""
    state = AppState()  # project_state + storage stay None
    api_root = tmp_path / "api"
    Match.init(api_root, name="Local")
    (Match.shooter_root(api_root, "alpha")).mkdir(parents=True, exist_ok=True)
    with _BoundMatch(api_root, "m_local"):
        assert state.load_audit("alpha", 1) == (None, 0)
        state.save_audit("alpha", 1, {"shots": []}, version=0)
        doc, version = state.load_audit("alpha", 1)
        assert doc == {"shots": []}
        assert version == 0  # files have no optimistic-lock version
