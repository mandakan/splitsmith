"""GET /api/match/stage/{n}/compare carries interval classes (#781).

Bootstraps a minimal match + audit JSON (mirrors tests/test_coach_api.py),
then asserts the compare payload heals legacy classifications in memory
without ever writing back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from splitsmith.match_model import Match, MatchStageDefinition
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app


@pytest.fixture(autouse=True)
def _disable_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


def _bootstrap(tmp_path: Path, shots: list[dict[str, Any]]) -> tuple[TestClient, Path, str, Path]:
    """Returns ``(client, audit_file, compare_url, shooter_root)``.

    ``compare_url`` carries the ``/api/matches/{match_id}/`` prefix the
    alias middleware needs to bind ``current_match_root`` - a bare
    ``/api/match/stage/{n}/compare`` request 409s ``no_project`` (mirrors
    ``tests/test_coach_api.py::_bootstrap``).
    """
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Compare Match")
    match = Match.load(root)
    match.stages = [MatchStageDefinition(stage_number=1, stage_name="K-vallen")]
    match.save(root)
    project = MatchProject.load(shooter_root)
    project.competitor_name = "Tester"
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="K-vallen",
            time_seconds=30.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    project.save(shooter_root)

    audit_dir = shooter_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / "stage1.json"
    payload = {
        "stage_number": 1,
        "stage_name": "K-vallen",
        "beep_time": 5.0,
        "shots": shots,
    }
    audit_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    app = create_app(project_root=root, project_name="Compare Match")
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    compare_url = f"/api/matches/{match_id}/match/stage/1/compare"
    return TestClient(app), audit_file, compare_url, shooter_root


def _legacy_shots() -> list[dict[str, Any]]:
    # time = beep_time + ms/1000, both present as in real audit docs.
    # Gaps: 0.30 -> split, 0.90 -> transition, 2.60 -> movement.
    return [
        {"shot_number": 1, "time": 6.5, "ms_after_beep": 1500, "source": "detected"},
        {"shot_number": 2, "time": 6.8, "ms_after_beep": 1800, "source": "detected"},
        {"shot_number": 3, "time": 7.7, "ms_after_beep": 2700, "source": "detected"},
        {"shot_number": 4, "time": 10.3, "ms_after_beep": 5300, "source": "detected"},
    ]


def test_compare_heals_legacy_doc_in_memory_only(tmp_path: Path) -> None:
    client, audit_file, compare_url, _shooter_root = _bootstrap(tmp_path, _legacy_shots())
    before = audit_file.read_text(encoding="utf-8")

    resp = client.get(compare_url)
    assert resp.status_code == 200, resp.text
    (shooter,) = resp.json()["shooters"]
    assert [s["interval_class"] for s in shooter["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "movement",
    ]
    # Unlike the coach GET, the compare read never persists the heal -
    # share requests impersonate the owner tenant (#778), so this path
    # must stay read-only in code.
    assert audit_file.read_text(encoding="utf-8") == before
    # No trim files exist in this fixture, so the ref is None - but the
    # field itself must be the renamed ``video_ref``, not ``video_path``.
    assert shooter["video_ref"] is None
    assert "video_path" not in shooter


def test_compare_preserves_manual_classes(tmp_path: Path) -> None:
    shots = _legacy_shots()
    shots[3]["interval_class"] = "reload"
    shots[3]["interval_class_source"] = "manual"
    client, _audit, compare_url, _shooter_root = _bootstrap(tmp_path, shots)

    resp = client.get(compare_url)
    assert resp.status_code == 200, resp.text
    (shooter,) = resp.json()["shooters"]
    assert [s["interval_class"] for s in shooter["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "reload",
    ]


def test_compare_junk_class_degrades_to_none(tmp_path: Path) -> None:
    shots = _legacy_shots()
    for s in shots:
        s["interval_class"] = "split"  # fully classified: heal must not run
    shots[2]["interval_class"] = "banana"
    client, audit_file, compare_url, _shooter_root = _bootstrap(tmp_path, shots)
    before = audit_file.read_text(encoding="utf-8")

    resp = client.get(compare_url)
    assert resp.status_code == 200, resp.text
    (shooter,) = resp.json()["shooters"]
    assert [s["interval_class"] for s in shooter["shots"]] == [
        "split",
        "split",
        None,
        "split",
    ]
    assert audit_file.read_text(encoding="utf-8") == before


def test_compare_shot_without_ms_stays_unclassified(tmp_path: Path) -> None:
    shots = _legacy_shots()
    del shots[2]["ms_after_beep"]
    client, _audit, compare_url, _shooter_root = _bootstrap(tmp_path, shots)

    resp = client.get(compare_url)
    assert resp.status_code == 200, resp.text
    (shooter,) = resp.json()["shooters"]
    # The heal skips ms-less shots; the others classify around it
    # (5300 - 1800 = 3.5s -> movement).
    assert [s["interval_class"] for s in shooter["shots"]] == [
        "first_shot",
        "split",
        None,
        "movement",
    ]


def test_compare_resolves_local_trim_and_prefers_exports(tmp_path: Path) -> None:
    """Local mode: no trim -> None; audit-cache trim resolves; a lossless
    export alongside it wins (lossless-first resolution order)."""
    from splitsmith.export_naming import stage_file_base

    client, _audit, compare_url, shooter_root = _bootstrap(tmp_path, _legacy_shots())
    project = MatchProject.load(shooter_root)
    stage = next(s for s in project.stages if s.stage_number == 1)
    primary = next(v for v in stage.videos if v.role == "primary")
    video_id = primary.video_id

    resp = client.get(compare_url)
    assert resp.status_code == 200, resp.text
    (shooter,) = resp.json()["shooters"]
    assert shooter["video_ref"] is None

    trimmed_dir = shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    cache_name = f"stage1_cam_{video_id}_trimmed.mp4"
    (trimmed_dir / cache_name).write_bytes(b"")

    resp = client.get(compare_url)
    assert resp.status_code == 200, resp.text
    (shooter,) = resp.json()["shooters"]
    assert shooter["video_ref"] == f"trimmed/{cache_name}"

    exports_dir = shooter_root / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    base = stage_file_base(1, "K-vallen")
    lossless_name = f"{base}_trimmed.mp4"
    (exports_dir / lossless_name).write_bytes(b"")

    resp = client.get(compare_url)
    assert resp.status_code == 200, resp.text
    (shooter,) = resp.json()["shooters"]
    assert shooter["video_ref"] == f"exports/{lossless_name}"


class _FakeStorage:
    """Minimal storage double: presign-capable, existence checked in-memory."""

    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.supports_presigned_get = True

    def exists(self, key: str) -> bool:
        return key in self.existing


def _hosted_shaped_project() -> tuple[MatchProject, Path, StageVideo]:
    """A ``MatchProject`` with a hosted ``_storage_scope`` bound, one stage,
    and one primary video - minimal enough to drive ``_resolve_compare_trim``
    without a full app/DB fixture (mirrors test_media_presign_serving.py's
    hosted-shaped construction)."""
    video = StageVideo(path=Path("raw/clip.mp4"), role="primary", beep_time=5.0)
    project = MatchProject(
        name="Hosted Shooter",
        stages=[StageEntry(stage_number=1, stage_name="K-vallen", time_seconds=30.0, videos=[video])],
    )
    project.bind_storage(None, scope="matches/m1/shooters/me")
    primary = project.stages[0].videos[0]
    return project, Path("/unused/shooter/root"), primary


def test_resolve_compare_trim_hosted_prefers_exports_over_trimmed() -> None:
    from splitsmith.export_naming import stage_file_base
    from splitsmith.ui.server import _resolve_compare_trim

    project, shooter_root, primary = _hosted_shaped_project()
    video_id = primary.video_id
    scope = "matches/m1/shooters/me"
    base = stage_file_base(1, "K-vallen")
    lossless_name = f"{base}_trimmed.mp4"
    cache_name = f"stage1_cam_{video_id}_trimmed.mp4"

    # Neither exists -> None.
    storage = _FakeStorage(set())
    assert _resolve_compare_trim(project, shooter_root, 1, "K-vallen", primary, storage) is None

    # Only the audit-cache trim exists -> trimmed ref.
    storage = _FakeStorage({f"{scope}/trimmed/{cache_name}"})
    ref = _resolve_compare_trim(project, shooter_root, 1, "K-vallen", primary, storage)
    assert ref == f"trimmed/{cache_name}"

    # Both exist -> lossless export wins.
    storage = _FakeStorage({f"{scope}/trimmed/{cache_name}", f"{scope}/exports/{lossless_name}"})
    ref = _resolve_compare_trim(project, shooter_root, 1, "K-vallen", primary, storage)
    assert ref == f"exports/{lossless_name}"
