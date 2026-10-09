"""Region figures on ``stages[].figures`` (spec 2026-10-08, part 2, "Share
figures"): ``moving_shots``, ``reloads``, ``reload_avg_s`` and
``overhang_s`` come from *confirmed* regions only (``source == "manual"``,
``events.confirmed_from_doc``), and are all ``null`` when the stage has
none. An auto proposal nobody looked at never reaches a shared figure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app

REGION_KEYS = ("moving_shots", "reloads", "reload_avg_s", "overhang_s")


@pytest.fixture(autouse=True)
def _disable_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


def _figures(tmp_path: Path, events: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Shots at 1.5, 1.8, 3.3 and 5.9 s from the beep; ``events`` written
    straight into the audit doc (``events_seeded`` so nothing seeds)."""
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Figures Match")
    project = MatchProject.load(shooter_root)
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
    doc: dict[str, Any] = {
        "stage_number": 1,
        "beep_time": 5.0,
        "shots": [
            {"shot_number": i + 1, "ms_after_beep": ms, "source": "detected"}
            for i, ms in enumerate([1500, 1800, 3300, 5900])
        ],
        "events_seeded": True,
    }
    if events is not None:
        doc["events"] = events
    (audit_dir / "stage1.json").write_text(json.dumps(doc) + "\n", encoding="utf-8")

    app = create_app(project_root=root, project_name="Figures Match")
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    client = TestClient(app)
    resp = client.get(f"/api/matches/{match_id}/shooters/me/project")
    assert resp.status_code == 200, resp.text
    stage = next(s for s in resp.json()["stages"] if s["stage_number"] == 1)
    assert stage["figures"] is not None
    return stage["figures"]


def test_no_regions_reports_null_region_figures(tmp_path: Path) -> None:
    figures = _figures(tmp_path, None)
    assert {k: figures[k] for k in REGION_KEYS} == dict.fromkeys(REGION_KEYS)
    assert figures["shot_count"] == 4


def test_auto_proposals_alone_report_null_region_figures(tmp_path: Path) -> None:
    figures = _figures(
        tmp_path,
        [
            {"id": "evt-1", "kind": "reload", "start": 3.4, "end": 5.2, "source": "auto"},
            {"id": "evt-2", "kind": "movement", "start": 1.6, "end": 3.5, "source": "auto"},
        ],
    )
    assert {k: figures[k] for k in REGION_KEYS} == dict.fromkeys(REGION_KEYS)


def test_confirmed_reload_on_the_move_reports_figures(tmp_path: Path) -> None:
    """Movement 1.6-3.5 covers shots 2 and 3; the reload 3.4-5.2 overlaps
    it, so it overhangs by 5.2 - 3.5. An auto reload beside them is not
    counted."""
    figures = _figures(
        tmp_path,
        [
            {"id": "evt-1", "kind": "movement", "start": 1.6, "end": 3.5, "source": "manual"},
            {"id": "evt-2", "kind": "reload", "start": 3.4, "end": 5.2, "source": "manual"},
            {"id": "evt-3", "kind": "reload", "start": 6.0, "end": 7.0, "source": "auto"},
        ],
    )
    assert figures["moving_shots"] == 2
    assert figures["reloads"] == 1
    assert figures["reload_avg_s"] == pytest.approx(1.8)
    assert figures["overhang_s"] == pytest.approx(1.7)


def test_standing_reload_has_no_overhang_figure(tmp_path: Path) -> None:
    """A standing reload measures no overhang: ``null``, never ``0.0``
    (the summary card omits it on the same condition)."""
    figures = _figures(
        tmp_path,
        [{"id": "evt-1", "kind": "reload", "start": 3.4, "end": 5.2, "source": "manual"}],
    )
    assert figures["moving_shots"] == 0
    assert figures["reloads"] == 1
    assert figures["reload_avg_s"] == pytest.approx(1.8)
    assert figures["overhang_s"] is None
