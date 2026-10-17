"""The export's "Use Breakdown" switch: on by default, and off an export
reads a stage as one nobody broke down. No region reaches any output
(markers, summaries, the overlay, the Draw), confirmed or not; proposals
never do either way. The engine half is pinned beside each renderer; this
file pins that the choice reaches them from every place a user asks."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.ui.exports import StageExportRequest
from splitsmith.ui.exports_api import CompareGridRequest, ExportStageRequest, MatchExportRequest
from splitsmith.ui.match_exports import MatchExportRequestData


def test_the_breakdown_is_used_by_default_everywhere_a_user_starts() -> None:
    assert MatchExportRequest(stage_numbers=[1]).breakdown is True
    assert ExportStageRequest().breakdown is True
    assert CompareGridRequest(stage_numbers=[1], audio_from="a").breakdown is True
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


def test_the_compare_grid_threads_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import Any

    from splitsmith.compare import mp4_grid as mp4_grid_mod
    from splitsmith.compare import project_loader as pl_mod

    from .test_compare_grid_endpoint import (
        _fake_probe,
        _fake_render_grid_mp4,
        _seed_match,
        _wait_for_job,
        _write_trims,
    )
    from .test_ui_server import _match_create_app, _MatchClient

    captured: list[dict[str, Any]] = []

    def fake_render(shooters: Any, *, audio_label: str, output_path: Path, **kwargs: Any) -> Any:
        captured.append(kwargs)
        return _fake_render_grid_mp4(shooters, audio_label=audio_label, output_path=output_path)

    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", fake_render)
    match_root = _seed_match(tmp_path, shooters=["mathias"], stage_numbers=[1])
    _write_trims(match_root, slug="mathias", stage_numbers=[1])
    client = _MatchClient(_match_create_app(project_root=match_root, project_name="Compare Match"))
    body = {"stage_numbers": [1], "audio_from": "mathias"}
    for extra in ({}, {"breakdown": False}):
        response = client.post("/api/match/compare-export", json={**body, **extra})
        assert response.status_code == 200
        assert _wait_for_job(client, response.json()["id"])["status"] == "succeeded"
    assert [c["breakdown"] for c in captured[-2:]] == [True, False]


# --- the CLIs ---------------------------------------------------------------------------


def test_the_compare_cli_threads_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_compare_cli_mp4 import _capture_render, _invoke_mp4, _patch_probe, _seed_match_with_stages

    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    _patch_probe(monkeypatch)
    captured = _capture_render(monkeypatch)
    assert _invoke_mp4(match_root, tmp_path / "a.mp4").exit_code == 0
    assert captured["breakdown"] is True
    result = _invoke_mp4(match_root, tmp_path / "b.mp4", "--no-breakdown")
    assert result.exit_code == 0, result.output
    assert captured["breakdown"] is False


def test_the_match_cli_threads_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.cli import app
    from splitsmith.ui import match_exports as match_exports_mod

    from .test_match_cli_export import _capture_mp4, _seed, runner

    root = _seed(tmp_path)
    _capture_mp4(monkeypatch)
    seen: list = []
    real = match_exports_mod.export_match

    def capture(*args: object, **kwargs: object) -> object:
        seen.append(kwargs["request"])
        return real(*args, **kwargs)

    monkeypatch.setattr(match_exports_mod, "export_match", capture)
    args = ["match", "export", str(root), "--shooter", "me", "--format", "mp4"]
    assert runner.invoke(app, [*args, "-o", str(tmp_path / "a.mp4")]).exit_code == 0
    result = runner.invoke(app, [*args, "--no-breakdown", "-o", str(tmp_path / "b.mp4")])
    assert result.exit_code == 0, result.output
    assert [req.breakdown for req in seen] == [True, False]


@pytest.mark.parametrize(
    ("drawn_with", "args", "stitched"),
    [(True, (), True), (False, ("--no-breakdown",), True), (True, ("--no-breakdown",), False), (False, (), False)],
)
def test_the_match_cli_stitches_an_overlay_only_drawn_the_way_it_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drawn_with: bool, args: tuple, stitched: bool
) -> None:
    """The CLI cannot redraw: an overlay drawn with the Breakdown when the
    run asks for none (or the other way round) is left out, and the note
    says which, and the flag that stitches it."""
    from .test_match_cli_export import _overlay_on_disk

    stage, output = _overlay_on_disk(
        tmp_path, monkeypatch, record="current", change="nothing", drawn_with_breakdown=drawn_with, args=args
    )
    assert (stage.overlay is not None) is stitched
    if not stitched:
        flag = "--breakdown" if drawn_with else "--no-breakdown"
        assert ("drawn with the Breakdown" if drawn_with else "drawn without the Breakdown") in output
        assert flag in output and "older audit" not in output
