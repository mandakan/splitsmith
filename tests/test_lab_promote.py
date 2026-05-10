"""``promote_stage_to_fixture`` provenance enforcement.

The audit JSON the SPA writes only carries shot/beep data -- source_video,
fixture_window_in_source, and the camera block are project-level facts
that the promote endpoint must derive from project context. Without
``require_provenance`` the corpus silently accumulates half-labelled
fixtures (which is what landed s36ed6e4e + the tallmilan iPhone
secondaries with empty provenance and bit the calibrator).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.lab import core as lab_core
from splitsmith.lab.core import PromoteRequest, promote_stage_to_fixture
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app


def _write_audit_pair(
    *,
    fixtures_root: Path,
    audit_dir: Path,
    payload: dict,
) -> tuple[Path, Path]:
    """Drop a stage<N>.json + matching audit WAV stub on disk."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_json = audit_dir / "stage1.json"
    audit_json.write_text(json.dumps(payload), encoding="utf-8")
    audit_wav = audit_dir / "stage1.wav"
    audit_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake")
    fixtures_root.mkdir(parents=True, exist_ok=True)
    return audit_json, audit_wav


_BASE_AUDIT_PAYLOAD = {
    "stage_number": 1,
    "stage_name": "Test Stage",
    "stage_time_seconds": 12.5,
    "beep_time": 1.0,
    "shots": [],
    "stage_rounds": {"expected": 0},
}


def test_promote_writes_supplied_provenance_fields(tmp_path: Path) -> None:
    """When the caller supplies source_video / window / camera, they
    land on the published fixture."""
    fixtures_root = tmp_path / "fixtures"
    audit_json, audit_wav = _write_audit_pair(
        fixtures_root=fixtures_root,
        audit_dir=tmp_path / "audit",
        payload=dict(_BASE_AUDIT_PAYLOAD),
    )

    rec = promote_stage_to_fixture(
        PromoteRequest(
            audit_json_path=audit_json,
            audit_wav_path=audit_wav,
            fixture_slug="stage-shots-test-2026-stage1-stestabcd",
            fixtures_root=fixtures_root,
            source_video="/Users/x/matches/test/raw/clip.MOV",
            fixture_window_in_source=(2.5, 18.5),
            camera={
                "id": "unknown",
                "mount": "hand",
                "position": "shooter",
                "audio_source": "internal",
                "agc_state": "unknown",
            },
        )
    )

    written = json.loads(Path(rec.audit_path).read_text())
    # ``scrub_local_path`` strips ``/Users/<name>/matches/<match>/`` from
    # source_video for PII safety, leaving the project-relative tail.
    assert written["source_video"] == "raw/clip.MOV"
    assert written["fixture_window_in_source"] == [2.5, 18.5]
    assert written["camera"]["mount"] == "hand"


def test_promote_refuses_when_camera_missing(tmp_path: Path) -> None:
    fixtures_root = tmp_path / "fixtures"
    audit_json, audit_wav = _write_audit_pair(
        fixtures_root=fixtures_root,
        audit_dir=tmp_path / "audit",
        payload=dict(_BASE_AUDIT_PAYLOAD),
    )
    with pytest.raises(ValueError, match="camera.mount"):
        promote_stage_to_fixture(
            PromoteRequest(
                audit_json_path=audit_json,
                audit_wav_path=audit_wav,
                fixture_slug="stage-shots-test-2026-stage1-stestabcd",
                fixtures_root=fixtures_root,
                source_video="/x.mov",
                fixture_window_in_source=(0.0, 1.0),
            )
        )


def test_promote_refuses_when_window_missing(tmp_path: Path) -> None:
    fixtures_root = tmp_path / "fixtures"
    audit_json, audit_wav = _write_audit_pair(
        fixtures_root=fixtures_root,
        audit_dir=tmp_path / "audit",
        payload=dict(_BASE_AUDIT_PAYLOAD),
    )
    with pytest.raises(ValueError, match="fixture_window_in_source"):
        promote_stage_to_fixture(
            PromoteRequest(
                audit_json_path=audit_json,
                audit_wav_path=audit_wav,
                fixture_slug="stage-shots-test-2026-stage1-stestabcd",
                fixtures_root=fixtures_root,
                source_video="/x.mov",
                camera={"mount": "hand"},
            )
        )


def test_promote_refuses_when_source_video_missing(tmp_path: Path) -> None:
    fixtures_root = tmp_path / "fixtures"
    audit_json, audit_wav = _write_audit_pair(
        fixtures_root=fixtures_root,
        audit_dir=tmp_path / "audit",
        payload=dict(_BASE_AUDIT_PAYLOAD),
    )
    with pytest.raises(ValueError, match="source_video"):
        promote_stage_to_fixture(
            PromoteRequest(
                audit_json_path=audit_json,
                audit_wav_path=audit_wav,
                fixture_slug="stage-shots-test-2026-stage1-stestabcd",
                fixtures_root=fixtures_root,
                fixture_window_in_source=(0.0, 1.0),
                camera={"mount": "hand"},
            )
        )


def test_promote_legacy_escape_hatch_skips_provenance_check(tmp_path: Path) -> None:
    """``require_provenance=False`` keeps the pre-#220 promote behaviour
    for the lab CLI and migration scripts that don't have project context."""
    fixtures_root = tmp_path / "fixtures"
    audit_json, audit_wav = _write_audit_pair(
        fixtures_root=fixtures_root,
        audit_dir=tmp_path / "audit",
        payload=dict(_BASE_AUDIT_PAYLOAD),
    )
    rec = promote_stage_to_fixture(
        PromoteRequest(
            audit_json_path=audit_json,
            audit_wav_path=audit_wav,
            fixture_slug="stage-shots-test-2026-stage1-stestabcd",
            fixtures_root=fixtures_root,
            require_provenance=False,
        )
    )
    written = json.loads(Path(rec.audit_path).read_text())
    # No provenance fields end up on the fixture; that's the contract
    # for legacy / migration callers.
    assert "source_video" not in written or written["source_video"] is None
    assert "fixture_window_in_source" not in written or written["fixture_window_in_source"] is None


def _setup_promote_endpoint_project(
    root: Path,
    *,
    camera_mount: str | None,
    beep_time: float | None = 1.0,
) -> tuple[MatchProject, Path]:
    """Build a project + stub audit WAV/trimmed MP4 so /api/lab/promote
    runs without invoking ffmpeg. Returns the project and the fixtures
    root the test should point promote at."""
    project = MatchProject.init(root, name="Promote Test Match")
    project.selected_shooter_id = 12345
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="Stage One",
            time_seconds=10.0,
            videos=[
                StageVideo(
                    path=Path("raw/v.mp4"),
                    role="primary",
                    beep_time=beep_time,
                    beep_source="manual",
                    beep_reviewed=True,
                    camera_mount=camera_mount,
                )
            ],
        )
    ]
    project.save(root)
    # Drop a primary stub + trimmed MP4 stub + audit WAV stub so
    # ``_resolve_audit_audio`` finds the trimmed-video branch and skips
    # ffmpeg re-extraction (audit WAV mtime newer than trim).
    raw = root / "raw"
    raw.mkdir(exist_ok=True)
    (raw / "v.mp4").write_bytes(b"\x00" * 16)
    trimmed = root / "trimmed"
    trimmed.mkdir(exist_ok=True)
    trim_path = trimmed / "stage1_trimmed.mp4"
    trim_path.write_bytes(b"\x00" * 16)
    audio = root / "audio"
    audio.mkdir(exist_ok=True)
    audit_wav = audio / "stage1_audit.wav"
    audit_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake")
    # Make audit WAV newer than the trimmed MP4 so ffmpeg-extract is skipped.
    future = time.time() + 10
    os.utime(audit_wav, (future, future))
    audit_dir = root / "audit"
    audit_dir.mkdir(exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_name": "Stage One",
                "stage_time_seconds": 10.0,
                "beep_time": 1.0,
                "shots": [],
                "stage_rounds": {"expected": 0},
            }
        ),
        encoding="utf-8",
    )
    return project, root / "fixtures-out"


def test_lab_promote_endpoint_400s_when_camera_mount_missing(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _setup_promote_endpoint_project(root, camera_mount=None)
    app = create_app(project_root=root, project_name="ignored", lab_enabled=True)
    client = TestClient(app)

    resp = client.post(
        "/api/lab/promote",
        json={"stage_number": 1, "slug": "stage-shots-promote-test-2026-stage1"},
    )
    assert resp.status_code == 400
    assert "camera_mount" in resp.json()["detail"]


def test_lab_promote_endpoint_writes_full_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: project has primary + camera_mount + beep -> the
    fixture lands with source_video / fixture_window_in_source / camera
    derived from project context."""
    root = tmp_path / "match"
    project, _ = _setup_promote_endpoint_project(root, camera_mount="hand")
    fixtures_root = tmp_path / "fixtures-out"
    fixtures_root.mkdir()

    # Redirect lab promote output away from the repo's tests/fixtures/.
    monkeypatch.setattr(lab_core, "DEFAULT_FIXTURES_ROOT", fixtures_root)

    app = create_app(project_root=root, project_name="ignored", lab_enabled=True)
    client = TestClient(app)

    slug = "stage-shots-promote-test-2026-stage1"
    resp = client.post(
        "/api/lab/promote",
        json={"stage_number": 1, "slug": slug},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    fixture_json = Path(body["audit_path"])
    written = json.loads(fixture_json.read_text())

    # source_video lands and resolves to the project's primary. Under
    # tmp_path (macOS /private/var/folders/...) ``scrub_local_path``
    # leaves the absolute path intact -- scrub coverage is already in
    # the unit-level test above. Here we just confirm derivation.
    assert written["source_video"].endswith("/raw/v.mp4")
    # fixture_window_in_source = [max(0, beep - pre), beep + stage_time + post]
    # beep=1.0, pre=5.0, stage=10.0, post=5.0  ->  [0.0, 16.0]
    assert written["fixture_window_in_source"] == [0.0, 16.0]
    assert written["camera"]["mount"] == "hand"
    assert written["camera"]["position"] == "shooter"
    assert written["camera"]["audio_source"] == "internal"


def test_promote_overrides_audit_json_provenance_with_request(tmp_path: Path) -> None:
    """When the audit JSON ALSO has source/window/camera (e.g.
    promote-from-anchor wrote them), the request still wins. This
    matters because the project knows the live primary path; the audit
    JSON might carry a stale copy from a pre-rename state."""
    fixtures_root = tmp_path / "fixtures"
    payload = dict(_BASE_AUDIT_PAYLOAD)
    payload["source_video"] = "/old/stale/path.mov"
    payload["fixture_window_in_source"] = [99.0, 100.0]
    payload["camera"] = {"mount": "head"}
    audit_json, audit_wav = _write_audit_pair(
        fixtures_root=fixtures_root,
        audit_dir=tmp_path / "audit",
        payload=payload,
    )
    rec = promote_stage_to_fixture(
        PromoteRequest(
            audit_json_path=audit_json,
            audit_wav_path=audit_wav,
            fixture_slug="stage-shots-test-2026-stage1-stestabcd",
            fixtures_root=fixtures_root,
            source_video="/new/correct/path.mov",
            fixture_window_in_source=(0.5, 13.5),
            camera={"mount": "hand", "position": "shooter", "audio_source": "internal"},
        )
    )
    written = json.loads(Path(rec.audit_path).read_text())
    # ``/new/correct/path.mov`` doesn't match the user-home prefix so
    # scrub_local_path leaves it intact.
    assert written["source_video"] == "/new/correct/path.mov"
    assert written["fixture_window_in_source"] == [0.5, 13.5]
    assert written["camera"]["mount"] == "hand"
