"""End-to-end tests for the Coach HTTP endpoints (issue #161).

Bootstraps a minimal project + an audit JSON, then exercises GET /coach,
POST /coach/reclassify, and PATCH /shots/{n}/coach. Mirrors the test
style in ``test_ui_server.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from splitsmith.ui.project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app


@pytest.fixture(autouse=True)
def _disable_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


def _bootstrap(tmp_path: Path) -> tuple[TestClient, Path]:
    root = tmp_path / "match"
    project = MatchProject.init(root, name="Coach Match")
    project.competitor_name = "Tester"
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="K-vallen",
            time_seconds=30.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    project.save(root)

    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / "stage1.json"
    payload = {
        "stage_number": 1,
        "stage_name": "K-vallen",
        "beep_time": 5.0,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 1500, "source": "detected"},
            {"shot_number": 2, "ms_after_beep": 1800, "source": "detected"},  # 0.30 -> split
            {"shot_number": 3, "ms_after_beep": 2700, "source": "detected"},  # 0.90 -> transition
            {"shot_number": 4, "ms_after_beep": 5300, "source": "detected"},  # 2.60 -> movement
        ],
    }
    audit_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    app = create_app(project_root=root, project_name="Coach Match")
    return TestClient(app), audit_file


def _read(audit_file: Path) -> dict[str, Any]:
    return json.loads(audit_file.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


def test_get_coach_returns_shots_with_stale_when_unset(tmp_path: Path) -> None:
    client, _audit = _bootstrap(tmp_path)
    resp = client.get("/api/stages/1/coach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stage_number"] == 1
    assert body["beep_time"] == 5.0
    assert len(body["shots"]) == 4

    # Nothing classified yet -> stored class is None and stale=False per
    # the spec (unannotated shots aren't stale).
    for s in body["shots"]:
        assert s["interval_class"] is None
        assert s["interval_class_source"] is None
        assert s["stale"] is False
        assert s["improvement_flag"] is False

    # time_absolute = beep_time + ms/1000 so the SPA can seek videos.
    assert body["shots"][0]["time_absolute"] == pytest.approx(5.0 + 1.5)
    # First shot's "split" is the draw.
    assert body["shots"][0]["split"] == pytest.approx(1.5)
    assert body["shots"][1]["split"] == pytest.approx(0.3)
    # Reload-hint flag fires on the long gap.
    assert body["shots"][3]["reload_hint"] is True


def test_get_coach_returns_videos_with_beep_in_clip(tmp_path: Path) -> None:
    client, _audit = _bootstrap(tmp_path)
    resp = client.get("/api/stages/1/coach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "videos" in body
    assert len(body["videos"]) == 1
    primary_entry = body["videos"][0]
    assert primary_entry["role"] == "primary"
    # No trimmed clip on disk in the bootstrap; beep_in_clip == beep in
    # source so clip coords match source coords for this fixture.
    assert primary_entry["beep_in_clip"] == pytest.approx(5.0)


def test_get_coach_clamps_beep_to_pre_buffer_when_trimmed(tmp_path: Path) -> None:
    """Beep at 8 s in source + a trimmed clip on disk -> the SPA must
    seek inside the trimmed clip, where the beep sits at min(8, 5)=5 s.
    All shot ``time_absolute`` values follow the same anchor.
    """
    root = tmp_path / "match"
    project = MatchProject.init(root, name="Trimmed Match")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="K-vallen",
            time_seconds=30.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=8.0)],
        )
    ]
    project.save(root)

    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_name": "K-vallen",
                "shots": [
                    {"shot_number": 1, "ms_after_beep": 1500, "source": "detected"},
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Place a non-empty file at the trimmed-clip path so the helper
    # detects "trim exists" without running ffmpeg.
    trimmed_dir = project.trimmed_path(root)
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "stage1_trimmed.mp4").write_bytes(b"not really a video, but non-empty")

    app = create_app(project_root=root, project_name="Trimmed Match")
    client = TestClient(app)
    resp = client.get("/api/stages/1/coach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Default trim_pre_buffer_seconds is 5.0; beep clamps to that.
    assert body["beep_time"] == pytest.approx(5.0)
    assert body["videos"][0]["beep_in_clip"] == pytest.approx(5.0)
    assert body["shots"][0]["time_absolute"] == pytest.approx(5.0 + 1.5)


def test_get_stage_distributions(tmp_path: Path) -> None:
    client, _audit = _bootstrap(tmp_path)
    resp = client.get("/api/stages/1/coach/distributions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stage_number"] == 1
    classes = {d["interval_class"] for d in body["distributions"]}
    assert classes == {"split", "transition", "movement", "reload"}
    by_class = {d["interval_class"]: d for d in body["distributions"]}
    assert by_class["split"]["count"] == 1
    assert by_class["transition"]["count"] == 1
    assert by_class["movement"]["count"] == 1
    assert by_class["reload"]["count"] == 0
    assert body["first_shot_s"] == pytest.approx(1.5)


def test_get_match_distributions_aggregates(tmp_path: Path) -> None:
    client, _audit = _bootstrap(tmp_path)
    resp = client.get("/api/coach/distributions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stage_count"] == 1
    by_class = {d["interval_class"]: d for d in body["distributions"]}
    assert by_class["split"]["count"] == 1
    assert by_class["transition"]["count"] == 1
    assert body["first_shot_seconds"] == pytest.approx([1.5])


def test_get_coach_404_when_no_audit(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = MatchProject.init(root, name="Empty")
    project.stages = [StageEntry(stage_number=1, stage_name="x", time_seconds=10.0, videos=[])]
    project.save(root)
    app = create_app(project_root=root, project_name="Empty")
    client = TestClient(app)
    resp = client.get("/api/stages/1/coach")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Reclassify
# ---------------------------------------------------------------------------


def test_reclassify_persists_auto_classes(tmp_path: Path) -> None:
    client, audit_file = _bootstrap(tmp_path)

    resp = client.post("/api/stages/1/coach/reclassify")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    classes = [s["interval_class"] for s in body["shots"]]
    assert classes == ["first_shot", "split", "transition", "movement"]
    assert all(s["interval_class_source"] == "auto" for s in body["shots"])
    assert all(s["stale"] is False for s in body["shots"])

    # Persisted on disk.
    saved = _read(audit_file)
    persisted = [s["interval_class"] for s in saved["shots"]]
    assert persisted == ["first_shot", "split", "transition", "movement"]
    # audit_events appended.
    events = saved.get("audit_events", [])
    assert any(e.get("kind") == "coach_reclassify" for e in events)


def test_reclassify_preserves_manual(tmp_path: Path) -> None:
    client, audit_file = _bootstrap(tmp_path)

    # Set a manual override on shot 4 first.
    resp = client.patch(
        "/api/stages/1/shots/4/coach",
        json={"interval_class": "reload", "interval_class_source": "manual"},
    )
    assert resp.status_code == 200, resp.text

    # Reclassify -- should leave shot 4 alone.
    resp = client.post("/api/stages/1/coach/reclassify")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["shots"][3]["interval_class"] == "reload"
    assert body["shots"][3]["interval_class_source"] == "manual"
    # Auto would say "movement" -> stale flag fires on the manual entry.
    assert body["shots"][3]["stale"] is True


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


def test_patch_set_class_and_note_and_flag(tmp_path: Path) -> None:
    client, audit_file = _bootstrap(tmp_path)

    resp = client.patch(
        "/api/stages/1/shots/2/coach",
        json={
            "interval_class": "split",
            "interval_class_source": "manual",
            "improvement_flag": True,
            "coaching_note": "second A was slow",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    s = body["shots"][1]
    assert s["interval_class"] == "split"
    assert s["interval_class_source"] == "manual"
    assert s["improvement_flag"] is True
    assert s["coaching_note"] == "second A was slow"

    saved = _read(audit_file)
    target = next(x for x in saved["shots"] if x["shot_number"] == 2)
    assert target["interval_class"] == "split"
    assert target["coaching_note"] == "second A was slow"


def test_patch_clear_class_drops_pair(tmp_path: Path) -> None:
    client, audit_file = _bootstrap(tmp_path)
    client.patch(
        "/api/stages/1/shots/2/coach",
        json={"interval_class": "reload", "interval_class_source": "manual"},
    )
    resp = client.patch("/api/stages/1/shots/2/coach", json={"clear_class": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["shots"][1]["interval_class"] is None
    assert body["shots"][1]["interval_class_source"] is None


def test_patch_class_without_source_rejected(tmp_path: Path) -> None:
    client, _audit = _bootstrap(tmp_path)
    resp = client.patch(
        "/api/stages/1/shots/2/coach",
        json={"interval_class": "split"},
    )
    assert resp.status_code == 400


def test_patch_unknown_shot_404(tmp_path: Path) -> None:
    client, _audit = _bootstrap(tmp_path)
    resp = client.patch(
        "/api/stages/1/shots/999/coach",
        json={"improvement_flag": True},
    )
    assert resp.status_code == 404


def test_patch_clear_note(tmp_path: Path) -> None:
    client, _audit = _bootstrap(tmp_path)
    client.patch(
        "/api/stages/1/shots/2/coach",
        json={"coaching_note": "old note"},
    )
    resp = client.patch("/api/stages/1/shots/2/coach", json={"clear_note": True})
    assert resp.status_code == 200
    assert resp.json()["shots"][1]["coaching_note"] is None


def test_patch_emits_audit_event(tmp_path: Path) -> None:
    client, audit_file = _bootstrap(tmp_path)
    client.patch(
        "/api/stages/1/shots/2/coach",
        json={"improvement_flag": True, "coaching_note": "fix me"},
    )
    saved = _read(audit_file)
    events = saved.get("audit_events", [])
    coach_events = [e for e in events if e.get("kind") == "coach_patch"]
    assert len(coach_events) == 1
    assert coach_events[0]["payload"]["shot_number"] == 2


# ---------------------------------------------------------------------------
# Stale recompute on Audit-style timestamp drift
# ---------------------------------------------------------------------------


def test_stale_after_audit_edit(tmp_path: Path) -> None:
    client, audit_file = _bootstrap(tmp_path)
    # First persist auto classes.
    client.post("/api/stages/1/coach/reclassify")

    # Simulate an Audit-side timestamp move: shot 2 drifts from 0.30 -> 0.70 s gap.
    saved = _read(audit_file)
    for s in saved["shots"]:
        if s["shot_number"] == 2:
            s["ms_after_beep"] = 2200  # 1500 + 700 ms
    audit_file.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")

    # GET surfaces stale=True; the stored class is "split" but the rule
    # would now say "transition".
    resp = client.get("/api/stages/1/coach")
    assert resp.status_code == 200
    body = resp.json()
    moved = body["shots"][1]
    assert moved["interval_class"] == "split"
    assert moved["stale"] is True

    # Reclassify clears stale.
    resp = client.post("/api/stages/1/coach/reclassify")
    body = resp.json()
    moved = body["shots"][1]
    assert moved["interval_class"] == "transition"
    assert moved["stale"] is False
