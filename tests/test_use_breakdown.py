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


def test_a_preset_remembers_the_choice_and_an_older_one_uses_the_breakdown() -> None:
    from splitsmith.export_presets import ExportPresetBody

    assert ExportPresetBody().breakdown is True
    assert ExportPresetBody.model_validate({}).breakdown is True
    assert ExportPresetBody.model_validate({"breakdown": False}).breakdown is False


def test_the_match_export_threads_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui import match_exports as match_exports_mod

    from .ui._helpers import _seed_match_export_project, _stub_match_export_probe, _wait_for_job

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
    from .ui._helpers import (
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
    from .ui._helpers import _match_create_app, _MatchClient

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
    [
        (True, (), True),
        (False, ("--no-breakdown",), True),
        (True, ("--no-breakdown",), False),
        (False, (), False),
    ],
)
def test_the_match_cli_stitches_an_overlay_only_drawn_the_way_it_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drawn_with: bool, args: tuple, stitched: bool
) -> None:
    """Told not to redraw, an overlay drawn with the Breakdown when the run
    asks for none (or the other way round) is left out, and the note says
    which, and the flag that stitches it. (By default it is drawn again:
    ``test_a_stale_overlay_is_drawn_again_with_its_recorded_style_and_stitched``.)"""
    from .test_match_cli_export import _overlay_on_disk

    stage, output = _overlay_on_disk(
        tmp_path,
        monkeypatch,
        record="current",
        change="nothing",
        drawn_with_breakdown=drawn_with,
        args=(*args, "--keep-stale-overlays"),
    )
    assert (stage.overlay is not None) is stitched
    if not stitched:
        flag = "--breakdown" if drawn_with else "--no-breakdown"
        assert ("drawn with the Breakdown" if drawn_with else "drawn without the Breakdown") in output
        assert flag in output and "older audit" not in output


# --- the MCP tools ----------------------------------------------------------------------


def test_the_mcp_stage_export_threads_the_choice(tmp_path: Path) -> None:
    from unittest.mock import patch

    from splitsmith.mcp import export_tools

    from .test_mcp_export_tools import _make_audit_json, _seed_export_project

    root, _src = _seed_export_project(tmp_path)
    _make_audit_json(root / "audit", 1)
    fields = ("trimmed_video_path", "csv_path", "events_csv_path", "fcpxml_path", "report_path")
    fake = type(
        "R",
        (),
        {
            **dict.fromkeys((*fields, "overlay_path")),
            "stage_number": 1,
            "shots_written": 0,
            "anomalies": [],
            "secondary_trimmed_paths": {},
        },
    )()
    seen: list = []
    with patch("splitsmith.mcp.export_tools.export_helpers.export_stage", return_value=fake) as mock_export:
        for breakdown in (True, False):
            export_tools.export_stage_tool(str(root), stage_number=1, breakdown=breakdown)
            seen.append(mock_export.call_args.kwargs["request"].breakdown)
    assert seen == [True, False]


@pytest.mark.parametrize(
    ("drawn_with", "breakdown", "stitched"),
    [(True, True, True), (False, False, True), (True, False, False), (False, True, False)],
)
def test_the_mcp_match_export_stitches_an_overlay_only_drawn_the_way_it_asks(
    tmp_path: Path, drawn_with: bool, breakdown: bool, stitched: bool
) -> None:
    from .test_mcp_export_tools import _export_match_with_overlay_on_disk

    overlay, stage_input, _result = _export_match_with_overlay_on_disk(
        tmp_path, record="current", breakdown=breakdown, drawn_with_breakdown=drawn_with
    )
    assert (stage_input.overlay_path == overlay) is stitched


def test_the_mcp_match_export_threads_the_choice(tmp_path: Path) -> None:
    from unittest.mock import patch

    from splitsmith.mcp import export_tools

    from .test_mcp_export_tools import _make_audit_json, _seed_export_project

    root, _src = _seed_export_project(tmp_path)
    _make_audit_json(root / "audit", 1)
    (root / "exports").mkdir(exist_ok=True)
    (root / "exports" / "stage1_k-vallen_trimmed.mp4").write_bytes(b"x")
    fake = type(
        "R",
        (),
        {"fcpxml_path": root / "m.fcpxml", "stage_count": 1, "duration_seconds": 1.0, "anomalies": []},
    )()
    seen: list = []
    with patch("splitsmith.mcp.export_tools.match_export_helpers.export_match", return_value=fake) as mock:
        for breakdown in (True, False):
            export_tools.export_match_tool(str(root), stage_numbers=[1], breakdown=breakdown)
            seen.append(mock.call_args.kwargs["request"].breakdown)
    assert seen == [True, False]


# --- the preview ------------------------------------------------------------------------

_REGIONS = [
    {"id": "evt-1", "kind": "reload", "start": 1.45, "end": 1.65, "source": "manual"},
    {"id": "evt-2", "kind": "draw", "start": 0.5, "end": 1.42, "source": "manual"},
]


def test_the_summary_preview_follows_the_switch(tmp_path: Path) -> None:
    from splitsmith import export_preview as ep

    from .test_export_preview import AUDIT, _render, _StubRasterizer

    htmls = []
    for breakdown in (True, False):
        raster = _StubRasterizer()
        (tmp_path / str(breakdown)).mkdir()
        spec = ep.PreviewSpec(card="summary", stage_number=3, breakdown=breakdown)
        _render(tmp_path / str(breakdown), spec, audit={**AUDIT, "events": _REGIONS}, raster=raster)
        htmls.append(raster.htmls[-1])
    used, unused = htmls
    # The Draw is the timer draw, switch or not (#1528).
    assert ">Reloads<" in used and ">1.42<" in used
    assert ">Reloads<" not in unused and ">1.42<" in unused


def test_the_hud_preview_follows_the_switch(tmp_path: Path) -> None:
    from splitsmith import export_preview as ep

    from .test_export_preview import AUDIT, _HudRasterizer, _render

    raster = _HudRasterizer()
    spec = ep.PreviewSpec(card="overlay", stage_number=3, width=480, overlay_variant="plate", breakdown=False)
    _render(tmp_path, spec, audit={**AUDIT, "events": _REGIONS}, raster=raster)
    (timeline,) = raster.timelines
    stage = timeline["data"]["stage"]
    assert stage["reloads"] == [] and stage["events"] == []


def test_the_previews_match_summary_follows_the_switch(tmp_path: Path) -> None:
    from splitsmith import export_preview as ep

    from .test_export_preview import AUDIT, _project

    project, _root = _project(tmp_path)
    docs = {3: {**AUDIT, "events": _REGIONS}}
    used = ep.match_summary_for(project, docs, title="T", label="M", work_dir=tmp_path / "a")
    unused = ep.match_summary_for(
        project, docs, title="T", label="M", work_dir=tmp_path / "b", breakdown=False
    )
    # The Draw is the timer draw, switch or not (#1528).
    assert used.rows[0].draw == pytest.approx(1.42)
    assert unused.rows[0].draw == pytest.approx(1.42)


def test_the_preview_key_moves_only_when_the_breakdown_is_off() -> None:
    from dataclasses import replace

    from splitsmith import export_preview as ep

    spec = ep.PreviewSpec(card="summary", stage_number=3)

    def key(s: ep.PreviewSpec) -> str:
        return ep.preview_key(s, slug="me", project_updated_at="t1", audit="a1")

    assert key(spec) == key(replace(spec, breakdown=True))
    assert key(spec) != key(replace(spec, breakdown=False))


def test_the_preview_route_threads_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import export_preview as ep
    from splitsmith import runtime as runtime_module
    from splitsmith.ui import export_preview_api

    from .test_look_editor_api import _factory
    from .ui._helpers import _seed_match_export_project

    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _factory)
    monkeypatch.setenv(runtime_module.ENV_CACHE_DIR, str(tmp_path / "cache"))
    runtime_module._clear_runtime_cache()
    specs: list = []
    summaries: list = []
    real_render, real_summary = ep.render_preview, ep.match_summary_for

    def render(spec, **kwargs):  # noqa: ANN001, ANN003, ANN202
        specs.append(spec)
        return real_render(spec, **kwargs)

    def summary(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        summaries.append(kwargs.get("breakdown", True))
        return real_summary(*args, **kwargs)

    monkeypatch.setattr(export_preview_api, "render_preview", render)
    monkeypatch.setattr(export_preview_api, "match_summary_for", summary)
    http, _root = _seed_match_export_project(tmp_path, stage_count=1)
    for card in ("summary", "match_summary"):
        for extra in ({}, {"breakdown": False}):
            body = {"card": card, "stage_number": 1, "width": 480, **extra}
            assert http.post("/api/shooters/me/export-preview", json=body).status_code == 200
    assert [s.breakdown for s in specs] == [True, False, True, False]
    assert summaries == [True, False]
    runtime_module._clear_runtime_cache()


def test_the_overlay_cli_threads_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``splitsmith overlay --no-breakdown``: the overlay and the summary
    card it writes beside it read no region."""
    import json
    from typing import Any

    from typer.testing import CliRunner

    from splitsmith import overlay_render, summary_card
    from splitsmith.cli import app

    from .test_overlay_render import _write_audit

    audit = _write_audit(tmp_path)
    doc = json.loads(audit.read_text())
    doc["events"] = [{"id": "evt-1", "kind": "reload", "start": 0.1, "end": 0.4, "source": "manual"}]
    audit.write_text(json.dumps(doc))
    seen: dict[str, Any] = {}
    tiles: list = []

    def fake_render(**kwargs: Any) -> Path:
        seen.update(kwargs)
        return kwargs["output_path"]

    def fake_card(**kwargs: Any) -> Any:
        tiles.append(kwargs["data"])
        raise summary_card.SummaryCardError("stub")

    monkeypatch.setattr(overlay_render, "render_overlay", fake_render)
    monkeypatch.setattr(summary_card, "render_summary_card", fake_card)
    base = [
        "overlay",
        "--audit", str(audit),
        "--video", str(tmp_path / "t.mp4"),
        "--output", str(tmp_path / "o.mov"),
        "--summary-card",
    ]  # fmt: skip
    CliRunner().invoke(app, base)
    assert seen["breakdown"] is True and tiles[-1].reloads
    CliRunner().invoke(app, [*base, "--no-breakdown"])
    assert seen["breakdown"] is False and tiles[-1].reloads == () and tiles[-1].events == ()


def test_the_stage_export_route_threads_the_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui import exports as exports_mod

    from .ui._helpers import _seed_match_export_project, _wait_for_job

    client, _root = _seed_match_export_project(tmp_path, stage_count=1)
    seen: list = []
    real = exports_mod.export_stage

    def capture(**kwargs: object) -> object:
        seen.append(kwargs["request"])
        return real(**kwargs)

    monkeypatch.setattr(exports_mod, "export_stage", capture)
    body = {"write_trim": False, "write_fcpxml": False, "write_report": False, "write_overlay": False}
    for extra in ({}, {"breakdown": False}):
        resp = client.post("/api/shooters/me/stages/1/export", json={**body, **extra})
        assert resp.status_code == 200, resp.text
        assert _wait_for_job(client, resp.json()["id"])["status"] == "succeeded"
    assert [req.breakdown for req in seen] == [True, False]


@pytest.mark.parametrize("tool", ["export_stage", "export_match"])
def test_the_mcp_wrappers_pass_the_choice_on(tool: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The FastMCP wrapper is what an agent calls; the tool functions are
    tested directly elsewhere, so a wrapper that dropped the argument would
    pass them."""
    import asyncio

    from splitsmith.mcp import create_server, export_tools

    seen: dict = {}

    def capture(*_args: object, **kwargs: object) -> dict:
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(export_tools, f"{tool}_tool", capture)
    args = {"project_root": "/nowhere", "breakdown": False}
    args.update({"stage_number": 1} if tool == "export_stage" else {"stage_numbers": [1]})
    asyncio.run(create_server().call_tool(tool, args))
    assert seen["breakdown"] is False
