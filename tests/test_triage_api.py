"""Tests for the mobile-triage accept-stage endpoint (slice 4, task 2).

Uses the same TestClient/app bootstrap as ``tests/test_ui_server.py`` --
``_match_create_app`` scaffolds a Match folder and ``_MatchClient`` handles
the ``/api/matches/{id}/...`` rewrite -- so these fixtures reuse those
helpers directly rather than re-inventing the pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith import match_model
from splitsmith.automation import AutomationOverride
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from tests.conftest import bound_match_id
from tests.conftest import scaffold_match as _scaffold_match
from tests.test_ui_server import _match_create_app, _MatchClient


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


@pytest.fixture
def seeded_match(client: _MatchClient) -> None:
    """Adds a second shooter, "bob", carrying the same two stages as
    "alice" to the match ``client`` is bound to, then accepts alice's
    stage 1 with a 4.2 s gap between its two shots (both a ``long_pause``
    and a ``stage_time_mismatch`` against the 10 s stage clock) so the
    triage aggregation has a real anomaly to surface. Bob's stages and
    alice's stage 2 stay untouched - "ready" cells with no audit doc.
    """
    match_id = bound_match_id(client.app)
    match_root = client.app.state.splitsmith_state.matches.resolve(match_id)
    match = match_model.Match.load(match_root)
    match.add_shooter(match_root, match_model.Shooter(slug="bob", name="Bob"))

    bob_root = match_model.Match.shooter_root(match_root, "bob")
    MatchProject.init(bob_root, name="Triage Match")
    bob_project = MatchProject.load(bob_root)
    bob_project.stages = [
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
    bob_project.save(bob_root)

    doc = {
        "stage_number": 1,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 400},
            {"shot_number": 2, "ms_after_beep": 4600},
        ],
        "audit_events": [],
    }
    put_resp = client.put("/api/shooters/alice/stages/1/audit", json=doc)
    assert put_resp.status_code == 200
    accept_resp = client.post("/api/shooters/alice/stages/1/audit/accept")
    assert accept_resp.status_code == 200


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


def test_accept_refuses_unclassifiable(client: _MatchClient, seeded_stage_unclassified: dict) -> None:
    resp = client.post("/api/shooters/alice/stages/1/audit/accept")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "not_fully_classified"


def test_accept_unknown_stage_404(client: _MatchClient) -> None:
    resp = client.post("/api/shooters/alice/stages/99/audit/accept")
    assert resp.status_code == 404


def test_flag_unknown_stage_404(client: _MatchClient) -> None:
    resp = client.post("/api/shooters/alice/stages/99/attention", json={"flagged": True})
    assert resp.status_code == 404


def test_flag_sets_needs_attention(client: _MatchClient, seeded_stage: dict) -> None:
    resp = client.post(
        "/api/shooters/alice/stages/1/attention",
        json={"flagged": True, "note": "beep sounds off"},
    )
    assert resp.status_code == 200
    doc = client.get("/api/shooters/alice/stages/1/audit").json()
    na = doc["needs_attention"]
    assert na["flagged"] is True
    assert na["note"] == "beep sounds off"
    assert na["flagged_at"] and na["updated_at"]


def test_unflag_keeps_object_with_timestamp(client: _MatchClient, seeded_stage: dict) -> None:
    client.post("/api/shooters/alice/stages/1/attention", json={"flagged": True})
    resp = client.post("/api/shooters/alice/stages/1/attention", json={"flagged": False})
    assert resp.status_code == 200
    na = client.get("/api/shooters/alice/stages/1/audit").json()["needs_attention"]
    assert na["flagged"] is False and na["note"] is None and na["flagged_at"] is None
    assert na["updated_at"]


def test_flag_stage_without_audit_doc(client: _MatchClient, empty_stage: None) -> None:
    resp = client.post("/api/shooters/alice/stages/2/attention", json={"flagged": True})
    assert resp.status_code == 200
    doc = client.get("/api/shooters/alice/stages/2/audit").json()
    assert doc["needs_attention"]["flagged"] is True


def test_flag_and_unflag_doc_less_stage_keeps_ready_status(client: _MatchClient, empty_stage: None) -> None:
    """A stage with no audit doc reads "ready". Flagging it for desktop
    must not flip that to "in_progress" forever (regression for the
    doc-less-flag status bug: the created doc has to seed the same
    beep-confirm stub shape the beep-review endpoint uses, or
    ``is_stub_audit`` stops recognizing it and status falls through to
    ``in_progress`` even after the flag is cleared)."""

    def status_of(stage_number: int) -> str:
        stages = client.get("/api/shooters/alice/project").json()["stages"]
        return next(s["status"] for s in stages if s["stage_number"] == stage_number)

    assert status_of(2) == "ready"

    resp = client.post("/api/shooters/alice/stages/2/attention", json={"flagged": True})
    assert resp.status_code == 200
    assert status_of(2) == "ready"

    resp = client.post("/api/shooters/alice/stages/2/attention", json={"flagged": False})
    assert resp.status_code == 200
    assert status_of(2) == "ready"


def test_flag_note_too_long_422(client: _MatchClient, seeded_stage: dict) -> None:
    resp = client.post(
        "/api/shooters/alice/stages/1/attention",
        json={"flagged": True, "note": "x" * 281},
    )
    assert resp.status_code == 422


def test_triage_lists_cells_with_status_and_anomalies(client: _MatchClient, seeded_match: None) -> None:
    body = client.get("/api/match/triage").json()
    cells = body["cells"]
    assert [(c["slug"], c["stage_number"]) for c in cells] == [
        ("alice", 1),
        ("bob", 1),
        ("alice", 2),
        ("bob", 2),
    ]
    a1 = cells[0]
    assert a1["status"] == "audited"
    assert any(a["kind"] == "long_pause" for a in a1["anomalies"])
    assert body["flagged_count"] == 0


def test_triage_carries_flag_and_count(client: _MatchClient, seeded_match: None) -> None:
    client.post(
        "/api/shooters/alice/stages/2/attention",
        json={"flagged": True, "note": "recheck"},
    )
    body = client.get("/api/match/triage").json()
    flagged = [c for c in body["cells"] if c["needs_attention"] and c["needs_attention"]["flagged"]]
    assert [(c["slug"], c["stage_number"]) for c in flagged] == [("alice", 2)]
    assert body["flagged_count"] == 1


def test_accept_returns_fresh_triage_list(client: _MatchClient, seeded_match: None) -> None:
    body = client.post("/api/shooters/alice/stages/1/audit/accept").json()
    assert "cells" in body and "flagged_count" in body


def test_triage_excludes_placeholder_stages(client: _MatchClient) -> None:
    """A placeholder stage (no scoreboard data yet) has nothing meaningful
    to triage, so ``_build_triage_response`` skips it via ``if
    stg.placeholder: continue`` (server.py). Add one alongside the
    client fixture's two real stages and confirm it never reaches the
    grid."""
    match_id = bound_match_id(client.app)
    match_root = client.app.state.splitsmith_state.matches.resolve(match_id)
    shooter_root = match_model.Match.shooter_root(match_root, "alice")
    project = MatchProject.load(shooter_root)
    project.stages.append(
        StageEntry(stage_number=3, stage_name="Stage 3", time_seconds=0.0, placeholder=True)
    )
    project.save(shooter_root)

    body = client.get("/api/match/triage").json()
    stage_numbers = {c["stage_number"] for c in body["cells"]}
    assert 3 not in stage_numbers
    assert stage_numbers == {1, 2}


def test_triage_carries_resolved_threshold(client: _MatchClient, seeded_match: None) -> None:
    """The triage payload's threshold is the resolved per-project value
    (same resolution path as ``get_hitl_queue``), not the automation
    default of 0.95."""
    match_id = bound_match_id(client.app)
    match_root = client.app.state.splitsmith_state.matches.resolve(match_id)
    alice_root = match_model.Match.shooter_root(match_root, "alice")
    project = MatchProject.load(alice_root)
    project.automation = AutomationOverride(beep_low_confidence_threshold=0.5)
    project.save(alice_root)

    body = client.get("/api/match/triage").json()
    assert body["beep_low_confidence_threshold"] == 0.5


def test_triage_summary_counts_flags_only(client: _MatchClient, seeded_match: None) -> None:
    resp = client.post(
        "/api/shooters/alice/stages/2/attention",
        json={"flagged": True, "note": "recheck"},
    )
    assert resp.status_code == 200

    body = client.get("/api/match/triage/summary").json()
    assert body == {"flagged_count": 1}

    unflag_resp = client.post("/api/shooters/alice/stages/2/attention", json={"flagged": False})
    assert unflag_resp.status_code == 200

    body2 = client.get("/api/match/triage/summary").json()
    assert body2 == {"flagged_count": 0}


def test_triage_includes_skipped_stages_with_skipped_status(client: _MatchClient) -> None:
    """A skipped stage (shooter DNF'd or the RO called it) still needs a
    triage cell so the coach sees it in the grid - it just carries status
    ``skipped`` instead of ``ready``/``audited``/etc."""
    match_id = bound_match_id(client.app)
    match_root = client.app.state.splitsmith_state.matches.resolve(match_id)
    shooter_root = match_model.Match.shooter_root(match_root, "alice")
    project = MatchProject.load(shooter_root)
    project.stages.append(StageEntry(stage_number=3, stage_name="Stage 3", time_seconds=10.0, skipped=True))
    project.save(shooter_root)

    body = client.get("/api/match/triage").json()
    cell = next(c for c in body["cells"] if c["slug"] == "alice" and c["stage_number"] == 3)
    assert cell["status"] == "skipped"
