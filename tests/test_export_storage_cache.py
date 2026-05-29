"""Tests for the hosted-mode export-deliverable cache push/pull (PR-epsilon
part 2).

The ``export`` / ``match_export`` job bodies write their deliverables
(lossless ``exports/*_trimmed.mp4``, FCPXML, CSV, report, overlay, per-cam
trims, the stitched match FCPXML) to a local ``exports/`` dir. On a worker
fleet those are produced out-of-process, so they round-trip through storage
the same way the audit trim already does: push on produce, pull on download.

These tests build a :class:`StageExportResult` directly and use
``FilesystemStorage`` against ``tmp_path`` (Protocol-equivalent to
``S3Storage`` per ``test_s3_storage.py``) -- no ffmpeg, no real exporter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.storage import FilesystemStorage
from splitsmith.ui import export_storage
from splitsmith.ui.exports import StageExportResult
from splitsmith.ui.project import MatchProject

SCOPE = "matches/m1/shooters/me"


def _project(tmp_path: Path, *, scope: str | None = SCOPE, with_storage: bool = True) -> MatchProject:
    root = tmp_path / "p"
    project = MatchProject.init(root, name="export-test")
    if with_storage:
        backing = tmp_path / "tenant"
        backing.mkdir(exist_ok=True)
        project.bind_storage(FilesystemStorage(backing), scope=scope)
    return project


def _exports_dir(tmp_path: Path) -> Path:
    d = tmp_path / "p" / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_storage_export_key_is_none_in_local_mode(tmp_path: Path) -> None:
    project = _project(tmp_path, with_storage=False)
    assert export_storage._storage_export_key(project, Path("a_trimmed.mp4")) is None
    assert export_storage._storage_export_key(None, Path("a.fcpxml")) is None


def test_storage_export_key_uses_scope_and_basename(tmp_path: Path) -> None:
    project = _project(tmp_path)
    key = export_storage._storage_export_key(project, tmp_path / "p" / "exports" / "stage1_x.fcpxml")
    assert key == f"{SCOPE}/exports/stage1_x.fcpxml"


def test_local_mode_push_is_noop(tmp_path: Path) -> None:
    """No storage bound: push writes nothing, raises nothing."""
    project = _project(tmp_path, with_storage=False)
    f = _write(_exports_dir(tmp_path) / "stage1_x.fcpxml")
    export_storage.push_export_file(project, f)  # no-op, no error


def test_scope_none_disables_cache(tmp_path: Path) -> None:
    """Storage bound but scope None (non-match request): cache stays off."""
    project = _project(tmp_path, scope=None)
    f = _write(_exports_dir(tmp_path) / "stage1_x.fcpxml")
    export_storage.push_export_file(project, f)
    assert list(project._storage.list("")) == []  # type: ignore[union-attr]


def test_push_text_uses_write_bytes_not_upload_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    storage = project._storage
    calls: list[str] = []
    monkeypatch.setattr(storage, "upload_stream", lambda *a, **k: calls.append("stream") or 0)
    monkeypatch.setattr(storage, "write_bytes", lambda *a, **k: calls.append("bytes"))

    export_storage.push_export_file(project, _write(_exports_dir(tmp_path) / "s1.fcpxml"))
    export_storage.push_export_file(project, _write(_exports_dir(tmp_path) / "s1_splits.csv"))
    export_storage.push_export_file(project, _write(_exports_dir(tmp_path) / "s1_report.txt"))
    assert calls == ["bytes", "bytes", "bytes"]


def test_push_binary_uses_upload_stream_not_write_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multi-GB overlays / trims must stream, never buffer whole via
    write_bytes."""
    project = _project(tmp_path)
    storage = project._storage
    calls: list[str] = []
    monkeypatch.setattr(storage, "upload_stream", lambda *a, **k: calls.append("stream") or 0)
    monkeypatch.setattr(storage, "write_bytes", lambda *a, **k: calls.append("bytes"))

    export_storage.push_export_file(project, _write(_exports_dir(tmp_path) / "s1_trimmed.mp4"))
    export_storage.push_export_file(project, _write(_exports_dir(tmp_path) / "s1_overlay.mov"))
    assert calls == ["stream", "stream"]


def test_push_missing_or_empty_file_is_skipped(tmp_path: Path) -> None:
    project = _project(tmp_path)
    storage = project._storage
    # Missing file
    export_storage.push_export_file(project, _exports_dir(tmp_path) / "gone.fcpxml")
    # Empty file
    empty = _write(_exports_dir(tmp_path) / "empty.csv", b"")
    export_storage.push_export_file(project, empty)
    assert list(storage.list("")) == []  # type: ignore[union-attr]


def test_push_failure_is_best_effort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A push outage must not raise -- the local file is the source of
    truth for the current job."""
    project = _project(tmp_path)
    monkeypatch.setattr(
        project._storage,
        "upload_stream",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated R2 outage")),
    )
    export_storage.push_export_file(project, _write(_exports_dir(tmp_path) / "s1_trimmed.mp4"))


def test_push_stage_outputs_pushes_every_artifact(tmp_path: Path) -> None:
    project = _project(tmp_path)
    storage = project._storage
    ed = _exports_dir(tmp_path)
    result = StageExportResult(
        stage_number=1,
        trimmed_video_path=_write(ed / "s1_trimmed.mp4", b"TRIM"),
        csv_path=_write(ed / "s1_splits.csv", b"CSV"),
        fcpxml_path=_write(ed / "s1.fcpxml", b"<xml/>"),
        report_path=_write(ed / "s1_report.txt", b"REPORT"),
        overlay_path=_write(ed / "s1_overlay.mov", b"OVL"),
        shots_written=2,
        anomalies=[],
        secondary_trimmed_paths={"cam2": _write(ed / "s1_cam_cam2_trimmed.mp4", b"SEC")},
    )

    export_storage.push_stage_export_outputs(project, result)

    for name, data in [
        ("s1_trimmed.mp4", b"TRIM"),
        ("s1_splits.csv", b"CSV"),
        ("s1.fcpxml", b"<xml/>"),
        ("s1_report.txt", b"REPORT"),
        ("s1_overlay.mov", b"OVL"),
        ("s1_cam_cam2_trimmed.mp4", b"SEC"),
    ]:
        assert storage.read_bytes(f"{SCOPE}/exports/{name}") == data  # type: ignore[union-attr]


def test_push_stage_outputs_skips_none_artifacts(tmp_path: Path) -> None:
    """CSV/report-only export (no trim/fcpxml/overlay) pushes just the
    populated paths."""
    project = _project(tmp_path)
    storage = project._storage
    ed = _exports_dir(tmp_path)
    result = StageExportResult(
        stage_number=1,
        trimmed_video_path=None,
        csv_path=_write(ed / "s1_splits.csv", b"CSV"),
        fcpxml_path=None,
        report_path=_write(ed / "s1_report.txt", b"REPORT"),
        overlay_path=None,
        shots_written=2,
        anomalies=[],
    )

    export_storage.push_stage_export_outputs(project, result)

    keys = sorted(o.path for o in storage.list(f"{SCOPE}/exports/"))  # type: ignore[union-attr]
    assert keys == [f"{SCOPE}/exports/s1_report.txt", f"{SCOPE}/exports/s1_splits.csv"]


def test_pull_returns_true_when_already_local(tmp_path: Path) -> None:
    project = _project(tmp_path)
    f = _write(_exports_dir(tmp_path) / "s1.fcpxml", b"local")
    assert export_storage.pull_export_file(project, f) is True


def test_pull_mirrors_from_storage(tmp_path: Path) -> None:
    project = _project(tmp_path)
    storage = project._storage
    target = _exports_dir(tmp_path) / "s1_trimmed.mp4"
    target.unlink(missing_ok=True)
    storage.write_bytes(f"{SCOPE}/exports/s1_trimmed.mp4", b"PUSHED")  # type: ignore[union-attr]

    assert not target.exists()
    assert export_storage.pull_export_file(project, target) is True
    assert target.read_bytes() == b"PUSHED"


def test_pull_returns_false_on_absent_key(tmp_path: Path) -> None:
    project = _project(tmp_path)
    target = _exports_dir(tmp_path) / "never_made.fcpxml"
    assert export_storage.pull_export_file(project, target) is False
    assert not target.exists()


def test_pull_is_false_in_local_mode(tmp_path: Path) -> None:
    project = _project(tmp_path, with_storage=False)
    target = _exports_dir(tmp_path) / "s1.fcpxml"
    assert export_storage.pull_export_file(project, target) is False
