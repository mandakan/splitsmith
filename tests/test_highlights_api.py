"""The highlight reel over the API (part 2 plan, 2026-10-18)."""

from __future__ import annotations

import json
from pathlib import Path

from splitsmith.config import DEFAULT_MOMENTS
from splitsmith.highlights import MOMENT_GROUPS, MOMENT_KINDS, readiness_for_project
from splitsmith.match_project import MatchProject, StageStatus

from .test_ui_server import _seed_match_export_project

SHOOTER = Path("shooters") / "me"


def _broken_down(root: Path, n: int, *, sources: tuple[str, ...] = ("manual",)) -> None:
    path = root / SHOOTER / "audit" / f"stage{n}.json"
    doc = json.loads(path.read_text())
    doc["audit_events"] = [{"kind": "save"}]  # what makes a stage audited
    doc["events_seeded"] = 4
    doc["events"] = [
        {"id": f"evt-{i}", "kind": "movement", "start": 0.6 + i, "end": 0.9 + i, "source": s}
        for i, s in enumerate(sources)
    ]
    path.write_text(json.dumps(doc))


def _trimmed(root: Path) -> None:
    project = MatchProject.load(root / SHOOTER)
    for stage in project.stages:
        stage.primary().processed["trim"] = True
    project.save(root / SHOOTER)


def test_readiness_names_each_stage_and_counts_proposals(tmp_path):
    client, root = _seed_match_export_project(tmp_path, stage_count=2)
    _trimmed(root)
    _broken_down(root, 1)
    _broken_down(root, 2, sources=("auto", "manual", "auto"))
    body = client.get("/api/shooters/me/highlights").json()
    assert body["unlocked"] is False
    assert (body["ready"], body["total"]) == (1, 2)
    assert [(s["stage_number"], s["state"], s["proposals"]) for s in body["stages"]] == [
        (1, "ready", 0),
        (2, "proposals_left", 2),
    ]


def test_an_untrimmed_stage_reads_as_not_ready_from_the_request(tmp_path):
    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    _broken_down(root, 1)
    body = client.get("/api/shooters/me/highlights").json()
    assert [s["state"] for s in body["stages"]] == ["trim_missing"]


def test_the_menu_lists_every_moment_once_with_its_group_and_default(tmp_path):
    client, _root = _seed_match_export_project(tmp_path, stage_count=1)
    menu = client.get("/api/shooters/me/highlights").json()["menu"]
    assert [m["kind"] for m in menu] == [k for k in menu_order()]
    assert {m["kind"] for m in menu} == set(MOMENT_KINDS)
    assert {m["kind"] for m in menu if m["default"]} == set(DEFAULT_MOMENTS)
    assert all(m["group"] == MOMENT_GROUPS[m["kind"]] for m in menu)
    assert not any(m["default"] for m in menu if m["group"] == "lolz")
    assert next(m for m in menu if m["kind"] == "thinker")["label"] == "THE THINKER"


def menu_order() -> tuple[str, ...]:
    from splitsmith.highlights import BEAT_ORDER

    return BEAT_ORDER


def test_readiness_reads_docs_handed_in_like_hosted(tmp_path):
    _client, root = _seed_match_export_project(tmp_path, stage_count=2)
    _trimmed(root)
    project = MatchProject.load(root / SHOOTER)
    docs = {1: {"shots": [], "events_seeded": 4, "events": []}, 2: None}
    statuses = {1: StageStatus.audited, 2: StageStatus.ready}
    rows = readiness_for_project(project, statuses, docs)
    assert [(r.stage_number, r.state) for r in rows] == [(1, "ready"), (2, "not_audited")]
