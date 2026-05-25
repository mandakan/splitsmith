"""Round-trip tests for ``splitsmith.backup`` export/import."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.backup import (
    DEFAULT_DIRS,
    MANIFEST_NAME,
    BackupError,
    export_project,
    import_project,
)
from splitsmith.ui.project import PROJECT_FILE, MatchProject
from splitsmith.ui.server import create_app


def _seed_project(root: Path, *, name: str = "Test Match") -> MatchProject:
    project = MatchProject.init(root, name=name)
    (project.audit_path(root) / "stage1.json").write_text('{"shots": []}')
    (root / "scoreboard" / "match.json").write_text('{"id": "abc"}')
    (project.trimmed_path(root) / "stage1_trimmed.mp4").write_bytes(b"\x00" * 1024)
    (project.exports_path(root) / "stage1.fcpxml").write_text("<fcpxml/>")
    (project.raw_path(root) / "source.mp4").write_bytes(b"\x00" * 4096)
    (project.audio_path(root) / "stage1.wav").write_bytes(b"\x00" * 2048)
    (project.probes_path(root) / "cache.json").write_text('{"probed": true}')
    (project.thumbs_path(root) / "x.jpg").write_bytes(b"\x00" * 128)
    return project


def test_export_includes_defaults_only(tmp_path: Path) -> None:
    src = tmp_path / "match"
    _seed_project(src)

    result = export_project(src, tmp_path / "out")

    assert result.archive_path.exists()
    assert set(result.included) == {PROJECT_FILE, *DEFAULT_DIRS}
    with tarfile.open(result.archive_path) as tf:
        names = tf.getnames()
    # Defaults are present: project.json + audit + scoreboard only.
    assert f"match/{PROJECT_FILE}" in names
    assert any(n.startswith("match/audit/") for n in names)
    assert any(n.startswith("match/scoreboard/") for n in names)
    # Everything else (caches + regeneratable video) is excluded by default.
    assert not any(n.startswith("match/trimmed/") for n in names)
    assert not any(n.startswith("match/exports/") for n in names)
    assert not any(n.startswith("match/probes/") for n in names)
    assert not any(n.startswith("match/thumbs/") for n in names)
    assert not any(n.startswith("match/raw/") for n in names)
    assert not any(n.startswith("match/audio/") for n in names)
    # Manifest is written.
    assert f"match/{MANIFEST_NAME}" in names


def test_export_opts_in_trimmed_and_exports(tmp_path: Path) -> None:
    src = tmp_path / "match"
    _seed_project(src)

    result = export_project(
        src,
        tmp_path / "out",
        include_trimmed=True,
        include_exports=True,
    )
    with tarfile.open(result.archive_path) as tf:
        names = tf.getnames()
    assert any(n.startswith("match/trimmed/") for n in names)
    assert any(n.startswith("match/exports/") for n in names)
    assert {"trimmed", "exports"} <= set(result.included)


def test_export_with_raw_and_audio(tmp_path: Path) -> None:
    src = tmp_path / "match"
    _seed_project(src)

    result = export_project(src, tmp_path / "out", include_raw=True, include_audio=True)

    with tarfile.open(result.archive_path) as tf:
        names = tf.getnames()
    assert any(n.startswith("match/raw/") for n in names)
    assert any(n.startswith("match/audio/") for n in names)
    assert "raw" in result.included
    assert "audio" in result.included


def test_round_trip_restores_project(tmp_path: Path) -> None:
    src = tmp_path / "match"
    _seed_project(src, name="Restored Match")

    result = export_project(src, tmp_path / "out")

    dest_root = tmp_path / "imported"
    imported = import_project(result.archive_path, dest_root)

    assert imported.project_root == dest_root / "match"
    assert imported.project_name == "Restored Match"
    assert (imported.project_root / PROJECT_FILE).exists()
    assert (imported.project_root / "audit" / "stage1.json").read_text() == '{"shots": []}'
    assert (imported.project_root / "scoreboard" / "match.json").read_text() == '{"id": "abc"}'
    # Regeneratable dirs and caches were intentionally excluded from the
    # default archive, so they don't reappear after restore.
    assert not (imported.project_root / "trimmed").exists()
    assert not (imported.project_root / "exports").exists()
    assert not (imported.project_root / "probes").exists()
    assert not (imported.project_root / "raw").exists()


def test_import_refuses_overwrite_by_default(tmp_path: Path) -> None:
    src = tmp_path / "match"
    _seed_project(src)
    result = export_project(src, tmp_path / "out")

    dest = tmp_path / "imported"
    import_project(result.archive_path, dest)

    with pytest.raises(BackupError, match="already exists"):
        import_project(result.archive_path, dest)


def test_import_overwrite_replaces_existing(tmp_path: Path) -> None:
    src = tmp_path / "match"
    _seed_project(src)
    result = export_project(src, tmp_path / "out")

    dest = tmp_path / "imported"
    import_project(result.archive_path, dest)
    stray = dest / "match" / "audit" / "stale.json"
    stray.write_text("stale")
    assert stray.exists()

    import_project(result.archive_path, dest, overwrite=True)
    assert not stray.exists()


def test_skips_subdir_overridden_outside_project_root(tmp_path: Path) -> None:
    src = tmp_path / "match"
    project = _seed_project(src)

    external = tmp_path / "external-raw"
    external.mkdir()
    (external / "huge.mp4").write_bytes(b"\x00" * 32)
    project.raw_dir = str(external)
    project.save(src)

    result = export_project(src, tmp_path / "out", include_raw=True)

    assert "raw" not in result.included
    reasons = {s.name: s.reason for s in result.skipped}
    assert reasons.get("raw") == "outside_project_root"
    with tarfile.open(result.archive_path) as tf:
        assert not any(n.startswith("match/raw") for n in tf.getnames())


def test_manifest_contents(tmp_path: Path) -> None:
    src = tmp_path / "match"
    _seed_project(src, name="Manifest Test")
    result = export_project(src, tmp_path / "out", include_audio=True)

    with tarfile.open(result.archive_path) as tf:
        member = tf.extractfile(f"match/{MANIFEST_NAME}")
        assert member is not None
        manifest = json.loads(member.read().decode())

    assert manifest["project_name"] == "Manifest Test"
    assert manifest["options"] == {
        "include_trimmed": False,
        "include_exports": False,
        "include_raw": False,
        "include_audio": True,
    }
    assert "audio" in manifest["included"]
    assert "raw" not in manifest["included"]
    assert "trimmed" not in manifest["included"]


def test_export_to_explicit_file_path(tmp_path: Path) -> None:
    src = tmp_path / "match"
    _seed_project(src)
    target = tmp_path / "custom.tar.gz"
    result = export_project(src, target)
    assert result.archive_path == target
    assert target.exists()


def test_export_missing_project_file_raises(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-project"
    empty.mkdir()
    with pytest.raises(BackupError, match="no project.json"):
        export_project(empty, tmp_path / "out")


# ---------------------------------------------------------------------------
# UI endpoint integration tests
# ---------------------------------------------------------------------------


def test_export_endpoint_streams_archive(tmp_path: Path) -> None:
    # Per Tier 1 step 3 of doc 10: the export endpoint streams a
    # single shooter's directory from inside a Match folder; tarball
    # entries are keyed on the shooter slug, not the match root name.
    from splitsmith import match_model
    from tests.conftest import bound_match_id

    root = tmp_path / "match"
    match = match_model.Match.init(root, name="HTTP Match")
    match.add_shooter(root, match_model.Shooter(slug="me", name="Me"))
    shooter_root = match_model.Match.shooter_root(root, "me")
    _seed_project(shooter_root, name="HTTP Match")

    app = create_app(project_root=root, project_name="HTTP Match")
    client = TestClient(app)
    match_id = bound_match_id(app)

    resp = client.get(f"/api/matches/{match_id}/shooters/me/project/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/gzip"
    assert "me-backup-" in resp.headers.get("content-disposition", "") or "me.tar.gz" in resp.headers.get(
        "content-disposition", ""
    )
    with tarfile.open(fileobj=io.BytesIO(resp.content)) as tf:
        names = tf.getnames()
    assert f"me/{PROJECT_FILE}" in names
    assert any(n.startswith("me/audit/") for n in names)
    assert any(n.startswith("me/scoreboard/") for n in names)
    # Defaults exclude raw/audio/trimmed/exports.
    assert not any(n.startswith("me/raw/") for n in names)
    assert not any(n.startswith("me/trimmed/") for n in names)
    assert not any(n.startswith("me/exports/") for n in names)


# ``test_import_endpoint_extracts_and_optionally_binds`` was deleted
# in Tier 1 step 3 of doc 10. The /api/me/projects/import endpoint
# imports a per-shooter MatchProject archive and (with bind=true)
# bound it as a standalone project. With the legacy single-shooter
# bind path retired, the imported archive needs to land inside a
# Match folder; that's a backup/import redesign question, not a
# tier-1 demolition concern. The endpoint's no-bind behaviour is
# still exercised by ``test_import_endpoint_refuses_overwrite``
# below.


def test_import_endpoint_refuses_overwrite(tmp_path: Path) -> None:
    src = tmp_path / "match"
    _seed_project(src)
    archive = export_project(src, tmp_path / "out").archive_path

    app = create_app()
    client = TestClient(app)
    dest = tmp_path / "incoming"

    # First import succeeds.
    with archive.open("rb") as fh:
        first = client.post(
            "/api/me/projects/import",
            files={"archive": ("backup.tar.gz", fh, "application/gzip")},
            data={"dest_root": str(dest)},
        )
    assert first.status_code == 200

    # Second one without overwrite=true is rejected.
    with archive.open("rb") as fh:
        second = client.post(
            "/api/me/projects/import",
            files={"archive": ("backup.tar.gz", fh, "application/gzip")},
            data={"dest_root": str(dest)},
        )
    assert second.status_code == 400
    assert "already exists" in second.json()["detail"]
