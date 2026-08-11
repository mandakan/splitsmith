"""Tests for the mobile-triage accept-stage endpoint (slice 4, task 2).

Uses the same TestClient/app bootstrap as ``tests/test_ui_server.py`` --
``_match_create_app`` scaffolds a Match folder and ``_MatchClient`` handles
the ``/api/matches/{id}/...`` rewrite -- so these fixtures reuse those
helpers directly rather than re-inventing the pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from tests.conftest import scaffold_match as _scaffold_match
from tests.test_ui_server import _MatchClient, _match_create_app


@pytest.fixture
def client(tmp_path: Path) -> _MatchClient:
    """A client bound to a fresh match with shooter "alice" and two
    set-up stages (primary video + time, no audit doc yet)."""
    root, shooter_root = _scaffold_match(
        tmp_path, name="Triage Match", shooter_slug="alice", shooter_name="Alice"
    )
    project = MatchProject.load(shooter_root)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="S1",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v1.mp4"), role="primary", beep_time=5.0)],
        ),
        StageEntry(
            stage_number=2,
            stage_name="S2",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v2.mp4"), role="primary", beep_time=5.0)],
        ),
    ]
    project.save(shooter_root)
    app = _match_create_app(project_root=root, project_name="ignored")
    return _MatchClient(app)


@pytest.fixture
def seeded_stage(client: _MatchClient) -> dict:
    """Stage 1's audit doc has two kept shots the auto-classifier fills
    in on save (#775), so both come back classified."""
    doc = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 400},
            {"shot_number": 2, "ms_after_beep": 1600},
        ],
        "audit_events": [],
    }
    resp = client.put("/api/shooters/alice/stages/1/audit", json=doc)
    assert resp.status_code == 200
    return resp.json()


@pytest.fixture
def empty_stage(client: _MatchClient) -> None:
    """Stage 2 exists but has no audit doc yet - nothing to accept."""
    resp = client.get("/api/shooters/alice/stages/2/audit")
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.fixture
def seeded_stage_unclassified(client: _MatchClient) -> dict:
    """Stage 1's audit doc has a kept shot the auto-classifier cannot
    fill in: ``interval_class`` is ``None`` but ``interval_class_source``
    is ``"manual"``, which ``classify_intervals_in_dicts`` always leaves
    untouched (manual entries survive re-classification per coach.py:216)."""
    doc = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 400},
            {
                "shot_number": 2,
                "ms_after_beep": 1600,
                "interval_class": None,
                "interval_class_source": "manual",
            },
        ],
        "audit_events": [],
    }
    resp = client.put("/api/shooters/alice/stages/1/audit", json=doc)
    assert resp.status_code == 200
    return resp.json()


def test_accept_appends_event_and_flips_status(client: _MatchClient, seeded_stage: dict) -> None:
    resp = client.post("/api/shooters/alice/stages/1/audit/accept")
    assert resp.status_code == 200

    doc = client.get("/api/shooters/alice/stages/1/audit").json()
    kinds = [e["kind"] for e in doc["audit_events"]]
    assert "accept" in kinds
    accept = next(e for e in doc["audit_events"] if e["kind"] == "accept")
    assert accept["id"] and accept["ts"]
    assert accept["payload"] == {"source": "triage"}

    # status now audited via the project payload (backend-authoritative)
    stages = client.get("/api/shooters/alice/project").json()["stages"]
    assert stages[0]["status"] == "audited"


def test_accept_clears_needs_attention(client: _MatchClient, seeded_stage: dict) -> None:
    # Task 3 (the /attention endpoint) doesn't exist yet - seed the key
    # directly through the existing PUT so this test has no dependency
    # on it.
    doc = client.get("/api/shooters/alice/stages/1/audit").json()
    doc["needs_attention"] = {
        "flagged": True,
        "flagged_at": "2026-08-01T00:00:00Z",
        "note": "check split 3",
        "updated_at": "2026-08-01T00:00:00Z",
    }
    put_resp = client.put("/api/shooters/alice/stages/1/audit", json=doc)
    assert put_resp.status_code == 200

    resp = client.post("/api/shooters/alice/stages/1/audit/accept")
    assert resp.status_code == 200

    doc2 = client.get("/api/shooters/alice/stages/1/audit").json()
    assert doc2["needs_attention"]["flagged"] is False
    assert doc2["needs_attention"]["updated_at"]


def test_accept_refuses_empty_stage(client: _MatchClient, empty_stage: None) -> None:
    resp = client.post("/api/shooters/alice/stages/2/audit/accept")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "nothing_to_accept"


def test_accept_refuses_unclassifiable(
    client: _MatchClient, seeded_stage_unclassified: dict
) -> None:
    resp = client.post("/api/shooters/alice/stages/1/audit/accept")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "not_fully_classified"


def test_accept_unknown_stage_404(client: _MatchClient) -> None:
    resp = client.post("/api/shooters/alice/stages/99/audit/accept")
    assert resp.status_code == 404
