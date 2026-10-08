"""The compare grid's match summary card (spec 2026-10-08-grid-match-summary-design)."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from splitsmith.compare import mp4_grid
from splitsmith.compare.overlay_sprites import SpriteGeometry
from splitsmith.compare.overlay_summary import (
    _placements_for_plan,
    extract_match_summary_freezes,
    grid_match_summaries,
    match_summary_strip_height,
)
from splitsmith.composition import MatchTitle
from splitsmith.match_project import StageScorecard
from splitsmith.match_summary import build_match_summary, match_summary_groups
from splitsmith.overlay_layout import Anchor, Emphasis, Role
from splitsmith.overlay_summary_cell import summary_scale
from splitsmith.stage_summary_data import TileShot, TileStageData

COUNT_TAGS = ("A", "C", "D", "M", "NS", "P")


def _shots(*splits: float) -> tuple[TileShot, ...]:
    out, t = [], 0.0
    for i, s in enumerate(splits):
        t += s
        out.append(TileShot(time_from_beep=t, split=s, interval_class="draw" if i == 0 else "split"))
    return tuple(out)


def _tile(n: int, *, shots=(), time=None, card=None, label="Anna") -> TileStageData:
    return TileStageData(label=label, stage_number=n, shots=shots, stage_time_seconds=time, scorecard=card)


CARD = StageScorecard(
    hit_factor=6.12, stage_pct=88.4, alphas=10, charlies=2, deltas=1, misses=1, no_shoots=0, procedurals=0
)


def _groups(summary, label="Anna"):
    scale = summary_scale(540)
    return match_summary_groups(summary, label, scale=scale, cell_width=960, cell_height=540)


def _texts(groups) -> list[str]:
    return [e.text for g in groups for e in g.elements]


def _counts(groups) -> list[str]:
    return [
        e.text
        for g in groups
        for e in g.elements
        if e.role is Role.DETAIL and e.text.rstrip("0123456789") in COUNT_TAGS
    ]


def test_a_full_match_tile_carries_both_bands_and_no_coverage_note() -> None:
    summary = build_match_summary(
        [
            ("One", _tile(1, shots=_shots(1.0, 0.2, 0.3), card=CARD)),
            ("Two", _tile(2, shots=_shots(0.9, 0.25), card=CARD)),
        ],
        title="M",
        label="Anna",
    )
    groups = _groups(summary)
    assert groups[0].anchor is Anchor.TOP_CENTER and _texts(groups[:1]) == ["Anna"]
    texts = _texts(groups)
    assert texts[1] == "Scoring"
    assert _counts(groups) == ["A20", "C4", "D2", "M2", "NS0", "P0"]
    by_caption = {e.caption: e.text for g in groups for e in g.elements if e.caption}
    assert by_caption == {"Avg": "0.25", "Best draw": "0.90", "Rounds": "5"}
    assert not any("of 2" in t for t in texts)
    # A real miss plates, as on the stage hold.
    assert next(e for g in groups for e in g.elements if e.text == "M2").emphasis is Emphasis.PLATE


def test_partial_coverage_is_said_beside_each_band() -> None:
    summary = build_match_summary(
        [("One", _tile(1, shots=_shots(1.0, 0.2), card=CARD)), ("Two", _tile(2)), ("Three", _tile(3))],
        title="M",
        label="Anna",
    )
    assert _texts(_groups(summary)).count("(1 of 3 stages)") == 2


def test_a_split_figure_never_read_is_a_dash_so_the_columns_line_up() -> None:
    summary = build_match_summary([("One", _tile(1, shots=_shots(1.1)))], title="M", label="Anna")
    by_caption = {e.caption: e.text for g in _groups(summary) for e in g.elements if e.caption}
    assert by_caption == {"Avg": "-", "Best draw": "1.10", "Rounds": "1"}


def test_a_count_no_stage_reported_is_left_out_not_zero() -> None:
    partial = StageScorecard(hit_factor=6.0, alphas=10, charlies=2)
    summary = build_match_summary([("One", _tile(1, card=partial))], title="M", label="Anna")
    assert _counts(_groups(summary)) == ["A10", "C2"]


def test_a_dq_anywhere_plates_the_name_row() -> None:
    dq = CARD.model_copy(update={"dq": True})
    summary = build_match_summary(
        [("One", _tile(1, card=CARD)), ("Two", _tile(2, card=dq))], title="M", label="Anna"
    )
    name_row = _groups(summary)[0].elements
    assert [e.text for e in name_row] == ["Anna", "DQ"]
    assert name_row[1].role is Role.VERDICT and name_row[1].emphasis is Emphasis.PLATE


def test_a_shooter_with_nothing_recorded_is_a_name_alone() -> None:
    summary = build_match_summary([("One", _tile(1)), ("Two", _tile(2))], title="M", label="Anna")
    assert _texts(_groups(summary)) == ["Anna"]


# --- the spine and the render ----------------------------------------------------


def test_the_spine_puts_the_match_summary_after_the_last_stage_before_the_closing_card() -> None:
    from tests.test_compare_mp4_grid_hold import _plan

    plans = [_plan(), replace(_plan(), stage_number=4, stage_name="Stage 4")]
    kwargs = {
        "title_page": None,
        "closing": MatchTitle(text="Bromma", duration_seconds=2.0),
        "stage_titles": "none",
        "title_duration_seconds": 1.5,
        "transitions": (),
        "tail_pad_seconds": 0.5,
    }
    spine = mp4_grid.plan_grid_spine(plans, match_summary_seconds=6.0, **kwargs)
    assert [item.name for item in spine.items] == ["stage3", "stage4", "match_summary", "closing"]
    summary = spine.items[2]
    assert summary.duration_seconds == 6.0 and summary.stage_index == 1
    assert "match_summary" not in [item.name for item in mp4_grid.plan_grid_spine(plans, **kwargs).items]


def test_the_render_encodes_the_match_summary_as_a_card_without_the_overlay(tmp_path: Path) -> None:
    from tests.test_compare_mp4_grid_cards import CANVAS, _concat_names, _FakeRasterizer, _ok_runner
    from tests.test_compare_mp4_grid_hold import _driver_shooters, _still_runner

    calls: list[tuple[str, ...]] = []
    cards: list[tuple[str, ...]] = []
    stills: list[tuple[str, ...]] = []
    work = tmp_path / "work"
    fake = _FakeRasterizer()
    result = mp4_grid.render_grid_mp4(
        _driver_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=_ok_runner(calls),
        card_runner=_ok_runner(cards),
        still_runner=_still_runner(stills),
        rasterizer=fake,
        work_dir=work,
        ffmpeg_binary="/bin/ffmpeg",
        closing=MatchTitle(text="Bromma Open", duration_seconds=2.0),
        match_name="Bromma Open",
        match_summary_seconds=6.0,
    )
    assert _concat_names(work) == ["stage1.mov", "match_summary.mov", "closing.mov"]
    # The progress runner saw the stage and the stitch only.
    assert len(calls) == 2
    summary_cmd = next(c for c in cards if c[-1].endswith("match_summary.mov"))
    png = work / "match_summary.png"
    assert summary_cmd == tuple(
        str(c)
        for c in mp4_grid.build_card_segment_command(
            png,
            seconds=6.0,
            canvas=CANVAS,
            shooter_labels=("Anders", "Mathias"),
            output_path=Path(summary_cmd[-1]),
            ffmpeg_binary="/bin/ffmpeg",
        )
    )
    with Image.open(png) as image:
        assert image.size == (640, 360)
    cells = next(html for html in fake.calls if "Splits" in html)
    # Anders's audit reached his tile; both shooters are named.
    assert "Anders" in cells and "Mathias" in cells and "Rounds" in cells
    assert any("Match summary" in html and "Bromma Open" in html for html in fake.calls)
    # One tail freeze per shooter, on the still hook.
    assert sum(1 for c in stills if "match-summary" in c[-1]) == 2
    assert result.degradations == ()


def test_a_shooter_missing_the_last_stage_sits_on_their_latest_footage(tmp_path: Path) -> None:
    from tests.test_compare_mp4_grid_cards import _ok_runner
    from tests.test_compare_mp4_grid_hold import _plan, _still_runner

    first = _plan(fillers=0)
    last = replace(_plan(fillers=1), stage_number=4, stage_name="Stage 4")
    stills: list[tuple[str, ...]] = []
    freezes = extract_match_summary_freezes(
        [first, last], work_dir=tmp_path, ffmpeg_binary="/bin/ffmpeg", runner=_still_runner(stills)
    )
    assert freezes["Ann"].name.startswith("freeze-stage3-")
    assert freezes["Bo"].name.startswith("freeze-stage4-") and freezes["Cy"].name.startswith("freeze-stage4-")
    nobody = replace(first, tiles=tuple(replace(t, trim_path=None) for t in first.tiles))
    assert (
        extract_match_summary_freezes([nobody], work_dir=tmp_path, ffmpeg_binary="x", runner=_ok_runner([]))
        == {}
    )


def test_every_shooter_counts_every_rendered_stage() -> None:
    from tests.test_compare_mp4_grid_hold import _plan

    plans = [_plan(), replace(_plan(), stage_number=4, stage_name="Stage 4")]
    data = {("Ann", 3): _tile(3, shots=_shots(1.0, 0.2), card=CARD, label="Ann")}
    summaries = grid_match_summaries(plans, data, title="M", duration_seconds=6.0)
    assert set(summaries) == {"Ann", "Bo", "Cy"}
    assert summaries["Ann"].stage_count == 2 and summaries["Ann"].split_stages == 1
    assert summaries["Bo"].stage_count == 2 and summaries["Bo"].split_stages == 0


# --- pixels -----------------------------------------------------------------------

_PROBE = """
() => {
  const out = [];
  for (const cell of document.querySelectorAll('.cell')) {
    const box = cell.getBoundingClientRect();
    for (const value of cell.querySelectorAll('.value')) {
      const el = value.closest('.el');
      const shown = getComputedStyle(el).display !== 'none';
      const caption = el.querySelector('.caption');
      const range = document.createRange();
      range.selectNodeContents(value);
      const r = range.getBoundingClientRect();
      const inside = r.left >= box.left - 0.5 && r.right <= box.right + 0.5
        && r.top >= box.top - 0.5 && r.bottom <= box.bottom + 0.5;
      out.push({text: value.textContent, caption: caption ? caption.textContent : null, shown, inside,
                clipped: value.scrollWidth > value.clientWidth + 1});
    }
  }
  return out;
}
"""


@pytest.mark.parametrize(("labels", "rows", "cols"), [(2, 1, 2), (4, 2, 2), (16, 4, 4)])
def test_tiles_keep_their_splits_inside_the_cell_at_every_grid_size(
    labels: int, rows: int, cols: int
) -> None:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    from splitsmith.match_summary import match_summary_groups
    from splitsmith.overlay_html import grid_html
    from splitsmith.overlay_raster import CHROMIUM_CHANNEL, DEVICE_SCALE_FACTOR
    from splitsmith.overlay_theme import load_theme
    from tests.test_compare_mp4_grid_hold import _plan

    names = tuple(f"Shooter Number {i} Longname" for i in range(labels))
    plan = _plan(names, fillers=0, rows=rows, cols=cols)
    width, height = 1920, 1080
    strip = match_summary_strip_height(height, rows)
    geometry = SpriteGeometry(canvas_width=width, canvas_height=height - strip, rows=rows, cols=cols)
    many = StageScorecard(
        hit_factor=6.1, alphas=1234, charlies=345, deltas=67, misses=12, no_shoots=3, procedurals=2
    )
    data = {
        (name, n): _tile(n, shots=_shots(1.234, 0.21, 0.3), card=many if n % 2 else None, label=name)
        for name in names
        for n in range(1, 13)
    }
    plans = [replace(plan, stage_number=n, stage_name=f"Stage {n}") for n in range(1, 13)]
    summaries = grid_match_summaries(plans, data, title="M", duration_seconds=6.0)
    scale = summary_scale(geometry.cell_height)
    cells = [
        (
            p,
            match_summary_groups(
                summaries[p.label],
                p.label,
                scale=scale,
                cell_width=geometry.cell_width,
                cell_height=geometry.cell_height,
            ),
        )
        for p in _placements_for_plan(plan)
    ]
    html = grid_html(cells, geometry=geometry, scale=scale, theme=load_theme("splitsmith"))
    try:
        with sync_playwright() as playwright, tempfile.TemporaryDirectory() as tmp:
            browser = playwright.chromium.launch(channel=CHROMIUM_CHANNEL, headless=True)
            page = browser.new_context(
                viewport={"width": width, "height": height - strip}, device_scale_factor=DEVICE_SCALE_FACTOR
            ).new_page()
            path = Path(tmp) / "cells.html"
            path.write_text(html, encoding="utf-8")
            page.goto(path.as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
            page.evaluate("window.__splitsmithFit && window.__splitsmithFit()")
            values = page.evaluate(_PROBE)
            browser.close()
    except PlaywrightError as exc:
        pytest.skip(f"no Chromium: {exc}")
    assert values
    splits = [v for v in values if v["caption"] in ("Avg", "Best draw", "Rounds")]
    assert len(splits) == 3 * labels
    # The splits are never dropped and nothing shown leaves its cell or is cut.
    assert all(v["shown"] for v in splits), splits
    shown = [v for v in values if v["shown"]]
    assert all(v["inside"] and not v["clipped"] for v in shown), [
        v for v in shown if not v["inside"] or v["clipped"]
    ]


# --- the request, the job and the CLI ---------------------------------------------


def test_the_grid_request_defaults_off_and_bounds_the_hold() -> None:
    from pydantic import ValidationError

    from splitsmith.ui.exports_api import CompareGridRequest

    req = CompareGridRequest(stage_numbers=[1], audio_from="a")
    assert (req.match_summary, req.match_summary_seconds) == (False, 6.0)
    for bad in (0.0, 0.4, 31.0):
        with pytest.raises(ValidationError):
            CompareGridRequest(
                stage_numbers=[1], audio_from="a", match_summary=True, match_summary_seconds=bad
            )


def test_the_grid_job_threads_the_match_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import Any

    from tests.test_compare_grid_endpoint import (
        _fake_probe,
        _fake_render_grid_mp4,
        _match_create_app,
        _MatchClient,
        _seed_match,
        _wait_for_job,
        _write_trims,
        mp4_grid_mod,
        pl_mod,
    )

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
    for extra in ({}, {"match_summary": True, "match_summary_seconds": 8}):
        response = client.post("/api/match/compare-export", json={**body, **extra})
        assert response.status_code == 200
        assert _wait_for_job(client, response.json()["id"])["status"] == "succeeded"
    assert [c["match_summary_seconds"] for c in captured[-2:]] == [0.0, 8.0]


def test_the_compare_cli_threads_the_match_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_compare_cli_mp4 import _capture_render, _invoke_mp4, _patch_probe, _seed_match_with_stages

    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    _patch_probe(monkeypatch)
    captured = _capture_render(monkeypatch)
    result = _invoke_mp4(match_root, tmp_path / "out.mp4")
    assert result.exit_code == 0, result.output
    assert captured["match_summary_seconds"] == 0.0
    result = _invoke_mp4(match_root, tmp_path / "out.mp4", "--match-summary", "--match-summary-seconds", "9")
    assert result.exit_code == 0, result.output
    assert captured["match_summary_seconds"] == 9.0
    assert captured["match_name"] == "Compare Match"


@pytest.mark.parametrize("seconds", ["0", "-1", "31"])
def test_the_compare_cli_refuses_a_hold_the_encode_cannot_make(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seconds: str
) -> None:
    from tests.test_compare_cli_mp4 import _capture_render, _invoke_mp4, _patch_probe, _seed_match_with_stages

    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    _patch_probe(monkeypatch)
    captured = _capture_render(monkeypatch)
    result = _invoke_mp4(
        match_root, tmp_path / "out.mp4", "--match-summary", "--match-summary-seconds", seconds
    )
    assert result.exit_code == 2
    assert captured == {}


def test_the_compare_cli_refuses_the_match_summary_on_fcpxml(tmp_path: Path) -> None:
    from splitsmith.cli import app
    from tests.test_compare_cli_mp4 import _seed_match_with_stages, runner

    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    args = ["compare", "export", str(match_root), "--audio-from", "mathias", "--match-summary"]
    result = runner.invoke(app, [*args, "-o", str(tmp_path / "out.fcpxml")])
    assert result.exit_code == 2
    assert "--match-summary" in result.output
