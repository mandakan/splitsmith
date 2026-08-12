"""Stable identity for audit-document shots."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, StageEntry
from splitsmith.shot_id import derive_shot_id, ensure_shot_ids
from splitsmith.ui.server import create_app
from tests.conftest import bound_match_id, scaffold_match


def test_detected_shot_keys_off_candidate_number() -> None:
    assert derive_shot_id({"candidate_number": 37, "time": 7.181}) == "cand-37"


def test_manual_shot_keys_off_rounded_time() -> None:
    assert derive_shot_id({"candidate_number": None, "time": 7.1814}) == "manual-t7181"


def test_derivation_is_identical_for_the_same_input() -> None:
    """Both sides must mint the same id without coordinating."""
    shot = {"candidate_number": None, "time": 12.5}
    assert derive_shot_id(shot) == derive_shot_id(dict(shot))


def test_ensure_stamps_only_missing_ids() -> None:
    shots = [
        {"candidate_number": 1, "time": 1.0},
        {"candidate_number": None, "time": 2.0, "id": "manual-already-here"},
    ]
    added = ensure_shot_ids(shots)
    assert added == 1
    assert shots[0]["id"] == "cand-1"
    assert shots[1]["id"] == "manual-already-here"


def test_existing_id_survives_a_nudge() -> None:
    """The whole point: moving a shot is a move, not a delete plus an add."""
    shots = [{"candidate_number": None, "time": 2.0}]
    ensure_shot_ids(shots)
    original = shots[0]["id"]
    shots[0]["time"] = 2.01
    ensure_shot_ids(shots)
    assert shots[0]["id"] == original


def test_colliding_derivations_get_distinct_ids() -> None:
    """Two manual shots on the same millisecond must not share an id."""
    shots = [
        {"candidate_number": None, "time": 3.0},
        {"candidate_number": None, "time": 3.0},
    ]
    ensure_shot_ids(shots)
    assert shots[0]["id"] != shots[1]["id"]


def test_shot_with_no_time_still_gets_an_id() -> None:
    shots = [{"candidate_number": None, "time": None}]
    ensure_shot_ids(shots)
    assert shots[0]["id"]


def test_a_shot_with_no_candidate_and_no_time_has_no_convergent_id() -> None:
    """Documented limitation, not an accident: a shot with neither key has
    nothing stable to derive from, so two independent stamps of equivalent
    no-key shots do not converge on the same id."""
    first = [{"candidate_number": None, "time": None}]
    second = [{"candidate_number": None, "time": None}]
    ensure_shot_ids(first)
    ensure_shot_ids(second)
    assert first[0]["id"] != second[0]["id"]


@pytest.fixture
def local_app_with_stage(tmp_path: Path) -> tuple[TestClient, str]:
    """Local-mode TestClient for a project with one shooter and one stage."""
    root, shooter_root = scaffold_match(tmp_path, name="Shot Id Match")
    project = MatchProject.load(shooter_root)
    project.stages = [StageEntry(stage_number=1, stage_name="Stage One", time_seconds=30.0)]
    project.save(shooter_root)
    app = create_app(project_root=root, project_name="Shot Id Match")
    client = TestClient(app)
    return client, f"/api/matches/{bound_match_id(app)}"


def test_put_audit_stamps_ids_and_keeps_them_across_a_nudge(
    local_app_with_stage: tuple[TestClient, str],
) -> None:
    """A save mints ids; the next save preserves them even though time moved."""
    client, url_base = local_app_with_stage
    doc = {
        "stage_number": 1,
        "beep_time": 5.0,
        "shots": [
            {"shot_number": 1, "candidate_number": 4, "time": 6.687, "ms_after_beep": 1687},
            {"shot_number": 2, "candidate_number": None, "time": 7.181, "ms_after_beep": 2181},
        ],
        "audit_events": [],
    }
    first = client.put(f"{url_base}/shooters/me/stages/1/audit", json=doc)
    assert first.status_code == 200, first.text
    ids = [s["id"] for s in first.json()["shots"]]
    assert ids[0] == "cand-4"
    assert ids[1].startswith("manual-")

    moved = first.json()
    moved["shots"][1]["time"] = 7.201
    second = client.put(f"{url_base}/shooters/me/stages/1/audit", json=moved)
    assert second.status_code == 200, second.text
    assert [s["id"] for s in second.json()["shots"]] == ids
