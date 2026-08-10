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


def _bootstrap(tmp_path: Path, shots: list[dict[str, Any]]) -> tuple[TestClient, Path, str]:
    """Returns ``(client, audit_file, compare_url)``.

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
    return TestClient(app), audit_file, compare_url


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
    client, audit_file, compare_url = _bootstrap(tmp_path, _legacy_shots())
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


def test_compare_preserves_manual_classes(tmp_path: Path) -> None:
    shots = _legacy_shots()
    shots[3]["interval_class"] = "reload"
    shots[3]["interval_class_source"] = "manual"
    client, _audit, compare_url = _bootstrap(tmp_path, shots)

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
    client, audit_file, compare_url = _bootstrap(tmp_path, shots)
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


def test_compare_leaves_a_cleared_manual_doc_alone(tmp_path: Path) -> None:
    # #780: this route's guard used to omit the ``!= "manual"`` clause the
    # coach GET and the share card carry, so a doc whose only unclassified
    # shot is the "explicitly cleared, do not reclassify" marker triggered
    # a heal here and not on its neighbours -- rewriting the other shots'
    # stale auto classes and reporting different figures for the same run.
    shots = _legacy_shots()
    for s in shots[:3]:
        s["interval_class"] = "movement"  # stale against the rule, on purpose
        s["interval_class_source"] = "auto"
    shots[3]["interval_class_source"] = "manual"  # cleared, class absent

    client, _audit, compare_url = _bootstrap(tmp_path, shots)
    resp = client.get(compare_url)
    assert resp.status_code == 200, resp.text
    (shooter,) = resp.json()["shooters"]
    assert [s["interval_class"] for s in shooter["shots"]] == [
        "movement",
        "movement",
        "movement",
        None,
    ]


def test_compare_shot_without_ms_stays_unclassified(tmp_path: Path) -> None:
    shots = _legacy_shots()
    del shots[2]["ms_after_beep"]
    client, _audit, compare_url = _bootstrap(tmp_path, shots)

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
