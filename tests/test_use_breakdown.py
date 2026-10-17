"""The export's "Use Breakdown" switch: on by default, and off an export
reads a stage as one nobody broke down. No region reaches any output
(markers, summaries, the overlay, the Draw), confirmed or not; proposals
never do either way. The engine half is pinned beside each renderer; this
file pins that the choice reaches them from every place a user asks."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.ui.exports import StageExportRequest
from splitsmith.ui.exports_api import ExportStageRequest, MatchExportRequest
from splitsmith.ui.match_exports import MatchExportRequestData


def test_the_breakdown_is_used_by_default_everywhere_a_user_starts() -> None:
    assert MatchExportRequest(stage_numbers=[1]).breakdown is True
    assert ExportStageRequest().breakdown is True
    assert MatchExportRequestData.__dataclass_fields__["breakdown"].default is True
    assert StageExportRequest.__dataclass_fields__["breakdown"].default is True


def test_the_match_export_threads_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui import match_exports as match_exports_mod

    from .test_ui_server import _seed_match_export_project, _stub_match_export_probe, _wait_for_job

    http, _root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)
    seen: list = []
    real = match_exports_mod.export_match

    def capture(*args: object, **kwargs: object) -> object:
        seen.append(kwargs["request"])
        return real(*args, **kwargs)

    monkeypatch.setattr(match_exports_mod, "export_match", capture)
    body = {"stage_numbers": [1], "include_overlay": False}
    for extra in ({}, {"breakdown": False}):
        r = http.post("/api/shooters/me/export/match", json={**body, **extra})
        assert r.status_code == 200, r.text
        assert _wait_for_job(http, r.json()["id"])["status"] == "succeeded"
    assert [req.breakdown for req in seen] == [True, False]


def test_an_overlay_drawn_with_the_breakdown_is_drawn_again_without_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The match export reuses an overlay only when it was drawn the way
    this export asks: switching the breakdown off draws it again, through
    a stage export told so, and the next export like it reuses that one."""
    from .test_ui_server import (
        _run_overlay_match_export,
        _seed_match_export_project,
        _stub_match_export_probe,
    )

    client, _root = _seed_match_export_project(tmp_path, stage_count=1)
    _stub_match_export_probe(monkeypatch)
    style = {"overlay_variant": "plate"}
    assert [c.breakdown for c in _run_overlay_match_export(client, monkeypatch, style)] == [True]
    off = {**style, "breakdown": False}
    assert [c.breakdown for c in _run_overlay_match_export(client, monkeypatch, off)] == [False]
    assert _run_overlay_match_export(client, monkeypatch, off) == []
    assert [c.breakdown for c in _run_overlay_match_export(client, monkeypatch, style)] == [True]
