"""Shot annotations must not land on a neighbour after a renumber."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry
from splitsmith.ui.server import create_app
from tests.conftest import bound_match_id, scaffold_match
from tests.hosted_helpers import _CapturingSender, login
from tests.test_mirror_read_only import _alias_url, _seed_mirror


@pytest.fixture
def local_app_with_stage(tmp_path: Path) -> tuple[TestClient, str]:
    """Local-mode TestClient for a project with one shooter and one stage."""
    root, shooter_root = scaffold_match(tmp_path, name="Coach Identity Match")
    project = MatchProject.load(shooter_root)
    project.stages = [StageEntry(stage_number=1, stage_name="Stage One", time_seconds=30.0)]
    project.save(shooter_root)
    app = create_app(project_root=root, project_name="Coach Identity Match")
    client = TestClient(app)
    return client, f"/api/matches/{bound_match_id(app)}"


def _doc(shots: list[dict]) -> dict:
    return {"stage_number": 1, "beep_time": 5.0, "shots": shots, "audit_events": []}


_TWO_SHOTS = [
    {"shot_number": 1, "candidate_number": 4, "time": 6.0, "ms_after_beep": 1000},
    {"shot_number": 2, "candidate_number": 9, "time": 6.5, "ms_after_beep": 1500},
]


def test_patch_by_id_targets_the_right_shot(
    local_app_with_stage: tuple[TestClient, str],
) -> None:
    client, url_base = local_app_with_stage
    saved = client.put(f"{url_base}/shooters/me/stages/1/audit", json=_doc(_TWO_SHOTS))
    assert saved.status_code == 200, saved.text

    resp = client.patch(
        f"{url_base}/shooters/me/stages/1/shots/by-id/cand-9/coach",
        json={"coaching_note": "tight transition"},
    )
    assert resp.status_code == 200, resp.text

    doc = client.get(f"{url_base}/shooters/me/stages/1/audit").json()
    by_id = {s["id"]: s for s in doc["shots"]}
    assert by_id["cand-9"].get("coaching_note") == "tight transition"
    assert by_id["cand-4"].get("coaching_note") in (None, "")


# The stale-shot-number guard cannot be exercised locally: local-mode
# ``load_audit``/``save_audit`` (server.py's ``AppState``) hard-code
# version 0 on every read and write -- there is no locking on plain files,
# so the version genuinely never moves. Verified empirically before
# writing this test (see task-3-report.md): a spy on ``AppState.load_audit``
# recorded six consecutive reads across two audit PUTs (one of them an
# insert) and every single one reported version 0. The guard can only be
# tripped where the version is real, i.e. hosted's ``state_docs`` optimistic
# lock -- so this one test is ported to the hosted fixtures instead of the
# local ``local_app_with_stage`` fixture used above. It seeds through the
# desktop-sync doc routes (``/api/sync/matches/.../docs/...``), the same
# surface ``_seed_mirror`` already uses, and issues the PATCH itself through
# the ``/api/matches/{id}/...`` alias -- the coach-by-number PATCH is on the
# mirror's review capability (``capabilities._REVIEW_ROUTES``), so it reaches the
# handler under test even though the match is a read-only mirror.
def test_stale_shot_number_patch_is_refused_after_an_insert(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The corruption this task exists to prevent.

    A client reads the stage, someone inserts a shot ahead of shot 2, and the
    first client then patches "shot 2" holding the version it originally
    read. Without the guard that annotation lands on what is now a
    different shot.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JCOACHPATCHSTALE0000001"
    name = "coach-patch-stale"
    _seed_mirror(client, match_id, name)

    roster_doc = match_model.Match(match_id=match_id, name=name, shooters=["alice"], stages=[]).model_dump(
        mode="json"
    )
    put_roster = client.put(
        f"/api/sync/matches/{match_id}/docs/match", params={"expected_version": 1}, json=roster_doc
    )
    assert put_roster.status_code == 200, put_roster.text

    stage = StageEntry(stage_number=1, stage_name="Stage One", time_seconds=30.0)
    project_doc = MatchProject(name=name, competitor_name="Alice", stages=[stage]).model_dump(mode="json")
    put_project = client.put(
        f"/api/sync/matches/{match_id}/docs/project/alice",
        params={"expected_version": 0},
        json=project_doc,
    )
    assert put_project.status_code == 200, put_project.text

    audit_url = f"/api/sync/matches/{match_id}/docs/audit/alice/1"
    doc = _doc(list(_TWO_SHOTS))
    first = client.put(audit_url, params={"expected_version": 0}, json=doc)
    assert first.status_code == 200, first.text
    held_version = first.json()["version"]

    doc["shots"].insert(1, {"shot_number": 2, "candidate_number": 7, "time": 6.2, "ms_after_beep": 1200})
    bumped = client.put(audit_url, params={"expected_version": held_version}, json=doc)
    assert bumped.status_code == 200, bumped.text
    assert bumped.json()["version"] != held_version

    resp = client.patch(
        _alias_url(match_id, "shooters/alice/stages/1/shots/2/coach"),
        json={"coaching_note": "meant for the old shot 2", "expected_version": held_version},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "version_conflict"
