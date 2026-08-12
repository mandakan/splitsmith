"""Shot annotations must not land on a neighbour after a renumber."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry
from splitsmith.ui.server import create_app
from tests.conftest import bound_match_id, scaffold_match
from tests.hosted_helpers import _CapturingSender, login
from tests.mirror_helpers import alias_url, seed_mirror


@pytest.fixture
def local_app_with_stage_root(tmp_path: Path) -> tuple[TestClient, str, Path]:
    """As :func:`local_app_with_stage`, plus the shooter root on disk.

    The extra path is only for the tests that have to hand-write an audit
    doc the save boundary would otherwise normalise away.
    """
    root, shooter_root = scaffold_match(tmp_path, name="Coach Identity Match")
    project = MatchProject.load(shooter_root)
    project.stages = [StageEntry(stage_number=1, stage_name="Stage One", time_seconds=30.0)]
    project.save(shooter_root)
    app = create_app(project_root=root, project_name="Coach Identity Match")
    client = TestClient(app)
    return client, f"/api/matches/{bound_match_id(app)}", shooter_root


@pytest.fixture
def local_app_with_stage(
    local_app_with_stage_root: tuple[TestClient, str, Path],
) -> tuple[TestClient, str]:
    """Local-mode TestClient for a project with one shooter and one stage."""
    client, url_base, _shooter_root = local_app_with_stage_root
    return client, url_base


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
# surface ``seed_mirror`` already uses, and issues the PATCH itself through
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
    seed_mirror(client, match_id, name)

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
        alias_url(match_id, "shooters/alice/stages/1/shots/2/coach"),
        json={"coaching_note": "meant for the old shot 2", "expected_version": held_version},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "version_conflict"


# --- #844: the client can only use the by-id route if the coach payload
# carries the id, and can only send ``expected_version`` on the positional
# fallback if the coach payload carries the version. Both were absent, which
# is why the SPA still addressed shots positionally with no guard at all.


def test_coach_response_exposes_each_shot_id(
    local_app_with_stage: tuple[TestClient, str],
) -> None:
    client, url_base = local_app_with_stage
    saved = client.put(f"{url_base}/shooters/me/stages/1/audit", json=_doc(list(_TWO_SHOTS)))
    assert saved.status_code == 200, saved.text

    coach = client.get(f"{url_base}/shooters/me/stages/1/coach")
    assert coach.status_code == 200, coach.text
    assert [s["id"] for s in coach.json()["shots"]] == ["cand-4", "cand-9"]


def test_coach_response_reports_no_id_for_an_unusable_one(
    local_app_with_stage_root: tuple[TestClient, str, Path],
) -> None:
    """A non-string ``id`` must reach the client as ``null``, not as itself.

    ``shot_id.has_usable_id`` already rules an int id out everywhere else --
    the by-id route matches ``s.get("id") == shot_id`` against a *string*
    path segment, so handing the client a ``7`` would send it to a route
    that can only 404. ``null`` is what routes it to the positional
    fallback instead. Written to disk directly because the save boundary
    would stamp a derived id over the junk one.
    """
    client, url_base, shooter_root = local_app_with_stage_root
    audit_file = shooter_root / "audit" / "stage1.json"
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_file.write_text(
        json.dumps(_doc([{"shot_number": 1, "time": 6.0, "ms_after_beep": 1000, "id": 7}])),
        encoding="utf-8",
    )

    coach = client.get(f"{url_base}/shooters/me/stages/1/coach")
    assert coach.status_code == 200, coach.text
    assert [s["id"] for s in coach.json()["shots"]] == [None]


def test_coach_patch_response_carries_the_version_a_follow_up_patch_needs(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """Two positional patches in a row must both land.

    The PATCH handler saves under the version it read, so the *new* version
    is the one the client now holds. Serving the pre-save version instead
    would make the response's own guard value stale on arrival: every
    second annotation on a stage would 409 with nothing having changed.
    Hosted, because local hard-codes version 0 on every read and write and
    so cannot tell a fresh version from a stale one.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    match_id = "01JCOACHPATCHVERSION00001"
    name = "coach-patch-version"
    seed_mirror(client, match_id, name)

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

    put_audit = client.put(
        f"/api/sync/matches/{match_id}/docs/audit/alice/1",
        params={"expected_version": 0},
        json=_doc(list(_TWO_SHOTS)),
    )
    assert put_audit.status_code == 200, put_audit.text

    read = client.get(alias_url(match_id, "shooters/alice/stages/1/coach"))
    assert read.status_code == 200, read.text
    held_version = read.json()["version"]

    first = client.patch(
        alias_url(match_id, "shooters/alice/stages/1/shots/1/coach"),
        json={"coaching_note": "first", "expected_version": held_version},
    )
    assert first.status_code == 200, first.text
    next_version = first.json()["version"]
    assert next_version != held_version, "a save that changed the doc must move the version"

    second = client.patch(
        alias_url(match_id, "shooters/alice/stages/1/shots/1/coach"),
        json={"coaching_note": "second", "expected_version": next_version},
    )
    assert second.status_code == 200, second.text
