"""The match summary card (spec 2026-10-07-match-summary-design): one card
after the last stage with match-wide figures and a row per stage. Only what
is real: an absent figure is "-", never a zero, and nothing is summed that
the sport does not sum (no total stage time, no match percentage)."""

from __future__ import annotations

import pytest

from splitsmith.match_project import StageScorecard
from splitsmith.match_summary import build_match_summary, coverage_lines, row_cells
from splitsmith.stage_summary_data import TileShot, TileStageData


def _shots(*splits: float) -> tuple[TileShot, ...]:
    out, t = [], 0.0
    for i, s in enumerate(splits):
        t += s
        out.append(TileShot(time_from_beep=t, split=s, interval_class="draw" if i == 0 else "split"))
    return tuple(out)


def _tile(n: int, *, shots=(), time=None, card=None) -> TileStageData:
    return TileStageData(
        label="Mathias", stage_number=n, shots=shots, stage_time_seconds=time, scorecard=card
    )


CARD = StageScorecard(
    hit_factor=6.12, stage_pct=88.4, alphas=10, charlies=2, deltas=1, misses=0, no_shoots=0, procedurals=0
)


def test_the_headline_is_the_whole_match_by_the_stage_summarys_own_rules() -> None:
    summary = build_match_summary(
        [
            ("Up the hill", _tile(1, shots=_shots(1.40, 0.20, 0.30), time=14.21, card=CARD)),
            ("Long range", _tile(2, shots=_shots(1.12, 0.40), time=22.80, card=CARD)),
        ],
        title="Stockholm Open",
        label="Mathias",
    )
    # Splits 0.20, 0.30, 0.40 weighted by count, not a mean of stage means.
    assert summary.avg_split == pytest.approx(0.30)
    assert summary.best_draw == pytest.approx(1.12)
    assert summary.rounds == 5
    assert summary.hits == {"A": 20, "C": 4, "D": 2, "M": 0, "NS": 0, "P": 0}
    assert coverage_lines(summary) == []


def test_absent_is_a_dash_and_coverage_says_how_much_is_behind_the_figures() -> None:
    summary = build_match_summary(
        [
            ("Up the hill", _tile(1, shots=_shots(1.40, 0.20), time=14.21, card=CARD)),
            ("Bare", _tile(2, time=9.5)),
            ("Nothing", _tile(3)),
        ],
        title="Stockholm Open",
        label="Mathias",
    )
    assert row_cells(summary.rows[1]) == ["02", "Bare", "9.50", "-", "-", "-", "-"]
    assert row_cells(summary.rows[2]) == ["03", "Nothing", "-", "-", "-", "-", "-"]
    assert row_cells(summary.rows[0]) == ["01", "Up the hill", "14.21", "6.12", "88.4", "1.40", "0.20"]
    assert coverage_lines(summary) == ["Splits from 1 of 3 stages", "Scores from 1 of 3 stages"]


def test_a_match_with_nothing_audited_has_no_split_figures_at_all() -> None:
    summary = build_match_summary([("Bare", _tile(1, time=9.5))], title="M", label="Mathias")
    assert summary.avg_split is None and summary.best_draw is None and summary.rounds is None
    assert summary.hits is None


def test_a_dq_stage_says_dq_and_its_scoring_stays_out_of_the_hits() -> None:
    dq = CARD.model_copy(update={"dq": True})
    summary = build_match_summary(
        [("One", _tile(1, time=10.0, card=CARD)), ("Two", _tile(2, time=11.0, card=dq))],
        title="M",
        label="Mathias",
    )
    assert summary.hits == {"A": 10, "C": 2, "D": 1, "M": 0, "NS": 0, "P": 0}
    assert row_cells(summary.rows[1])[3] == "DQ"
    assert summary.dq is True


def test_nothing_sums_the_stage_times() -> None:
    summary = build_match_summary(
        [("One", _tile(1, time=10.0)), ("Two", _tile(2, time=11.0))], title="M", label="Mathias"
    )
    assert not any(hasattr(summary, name) for name in ("total_time", "match_pct", "total_seconds"))


def _long_match(n: int):
    names = ["Up the hill", "Long range speed and accuracy challenge", "Speed shoot", "Classifier 99-11"]
    return [
        (
            names[i % len(names)],
            _tile(i + 1, shots=_shots(1.2 + i / 100, 0.21, 0.25, 0.3), time=12.0 + i, card=CARD),
        )
        for i in range(n)
    ]


@pytest.mark.parametrize("stages", [12, 20])
def test_the_card_keeps_inside_the_frame_at_any_match_length(stages: int) -> None:
    import io

    from PIL import Image

    from splitsmith.match_summary import match_summary_html
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError
    from splitsmith.overlay_theme import load_theme

    summary = build_match_summary(
        _long_match(stages), title="Stockholm IPSC Open 2026", label="Mathias Axell"
    )
    html = match_summary_html(summary, width=1280, height=720, theme=load_theme("splitsmith"))
    try:
        with ChromiumRasterizer() as raster:
            png = raster.png(html, width=1280, height=720)
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    px = im.load()
    margin = 20
    edges = [
        (x, y) for x in range(1280) for y in (*range(margin), *range(720 - margin, 720)) if px[x, y][3] > 0
    ]
    edges += [
        (x, y) for y in range(720) for x in (*range(margin), *range(1280 - margin, 1280)) if px[x, y][3] > 0
    ]
    assert edges == [], edges[:5]
    # Past twelve stages the table takes two columns.
    assert html.count("<table>") == (2 if stages > 12 else 1)


# --- the export -------------------------------------------------------------------------


def test_the_match_summary_reaches_the_mp4_and_sits_before_the_closing_card(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from splitsmith import mp4_render
    from splitsmith.config import OutputConfig
    from splitsmith.ui import match_exports as match_exports_mod

    from .test_ui_match_exports import _capture_mp4, _card_request, _one_stage_input, _stub_probe

    captured = _capture_mp4(monkeypatch)
    stage = _one_stage_input(tmp_path)
    stage = match_exports_mod.MatchStageInput(
        **{**stage.__dict__, "scorecard": CARD, "stage_time_seconds": 4.5}
    )
    result = match_exports_mod.export_match(
        stages=[stage],
        request=_card_request(
            match_summary=True,
            match_summary_seconds=7.0,
            summary_hold_seconds=2.0,
            closing_card=True,
            shooter_label="M. Axell",
        ),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    comp = captured["comp"]
    summary = comp.match_summary
    assert summary is not None and summary.duration_seconds == 7.0
    assert summary.label == "M. Axell" and summary.title == "Bromma Classifier"
    assert [r.name for r in summary.rows] == [stage.stage_name]
    assert summary.rows[0].hit_factor == 6.12 and summary.rows[0].time_seconds == 4.5
    kinds = [item.kind for item in mp4_render.plan_timeline(comp).items]
    assert kinds[-3:] == ["summary", "match_summary", "closing"]
    assert not any("match summary" in a for a in result.anomalies)


def test_the_match_summary_is_off_by_default_and_an_anomaly_on_the_xml_renderers(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from splitsmith.config import OutputConfig
    from splitsmith.ui import match_exports as match_exports_mod

    from .test_ui_match_exports import _capture_mp4, _card_request, _one_stage_input, _stub_probe

    captured = _capture_mp4(monkeypatch)
    match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(),
        exports_dir=tmp_path / "exports",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert captured["comp"].match_summary is None
    result = match_exports_mod.export_match(
        stages=[_one_stage_input(tmp_path)],
        request=_card_request(output_format="fcpxml", match_summary=True),
        exports_dir=tmp_path / "exports2",
        config=OutputConfig(),
        probe=_stub_probe,
    )
    assert any("match summary ignored" in a for a in result.anomalies)


def test_the_outro_chapter_starts_after_the_match_summary(tmp_path) -> None:
    from dataclasses import replace

    from splitsmith import composition, youtube_sidecar
    from splitsmith.fcpxml_gen import StageComposition

    from .test_youtube_sidecar import _make_video, _meta_30fps, _shot

    outro = composition.Segment(
        asset=composition.Asset(path=_make_video(tmp_path, "outro.mp4"), metadata=_meta_30fps(duration=5.0)),
        name="Outro",
    )
    stage = StageComposition(
        stage_name="Stage 1",
        video_path=_make_video(tmp_path, "a.mp4"),
        video=_meta_30fps(),
        shots=[_shot(1, 1.0, 1.0)],
        beep_offset_seconds=5.0,
        head_pad_seconds=5.0,
        tail_pad_seconds=5.0,
    )
    comp = composition.from_stage_compositions([stage], project_name="m", outro=outro)
    plain = youtube_sidecar.compute_chapters(comp)
    summary = build_match_summary([("One", _tile(1, time=10.0))], title="M", label="X", duration_seconds=6.0)
    with_summary = youtube_sidecar.compute_chapters(replace(comp, match_summary=summary))
    assert [c.title for c in with_summary] == [c.title for c in plain]
    assert with_summary[-1].start_seconds == pytest.approx(plain[-1].start_seconds + 6.0)


def test_the_request_and_the_preset_carry_it_off_by_default() -> None:
    from pydantic import ValidationError

    from splitsmith.export_presets import ExportPresetBody
    from splitsmith.ui.exports_api import MatchExportRequest

    assert ExportPresetBody().match_summary is False and ExportPresetBody().match_summary_seconds == 6.0
    req = MatchExportRequest(stage_numbers=[1])
    assert req.match_summary is False and req.match_summary_seconds == 6.0
    with pytest.raises(ValidationError):
        MatchExportRequest(stage_numbers=[1], match_summary_seconds=0)


def test_the_server_threads_the_match_summary_into_the_export(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    body = {"stage_numbers": [1], "include_overlay": False, "match_summary": True, "match_summary_seconds": 8}
    r = http.post("/api/shooters/me/export/match", json=body)
    assert r.status_code == 200, r.text
    assert _wait_for_job(http, r.json()["id"])["status"] == "succeeded"
    assert seen[-1].match_summary is True and seen[-1].match_summary_seconds == 8.0


def test_the_match_cli_takes_the_match_summary(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.cli import app

    from .test_match_cli_export import _capture_mp4, _seed, runner

    root = _seed(tmp_path)
    captured = _capture_mp4(monkeypatch)
    args = ["match", "export", str(root), "--shooter", "me", "--format", "mp4"]
    result = runner.invoke(app, [*args, "--match-summary", "--match-summary-seconds", "5"])
    assert result.exit_code == 0, result.output
    summary = captured["comp"].match_summary
    assert summary is not None and summary.duration_seconds == 5.0 and summary.label == "M. Axell"
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert captured["comp"].match_summary is None


def test_the_preview_draws_the_whole_match_summary(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    from splitsmith import export_preview
    from splitsmith import runtime as runtime_module
    from splitsmith.ui import export_preview_api

    from .test_look_editor_api import _factory
    from .test_ui_server import _seed_match_export_project

    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _factory)
    monkeypatch.setenv(runtime_module.ENV_CACHE_DIR, str(tmp_path / "cache"))
    runtime_module._clear_runtime_cache()
    drawn: list = []

    def fake_still(summary, **kwargs):
        drawn.append(summary)
        return Image.new("RGB", (kwargs["width"], kwargs["height"]))

    monkeypatch.setattr(export_preview, "build_match_summary_still", fake_still)
    http, _root = _seed_match_export_project(tmp_path, stage_count=2)
    body = {"card": "match_summary", "stage_number": 1, "width": 480, "project_name": "Final Cut"}
    r = http.post("/api/shooters/me/export-preview", json=body)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert [row.number for row in drawn[-1].rows] == [1, 2]
    assert drawn[-1].title == "Final Cut"
    runtime_module._clear_runtime_cache()


def test_the_preview_key_moves_with_the_summary() -> None:
    from splitsmith.export_preview import PreviewSpec, preview_key

    base = PreviewSpec(card="match_summary", stage_number=2)
    keys = {
        preview_key(
            PreviewSpec(card="match_summary", stage_number=2, summary_digest=d),
            slug="me",
            project_updated_at="t",
            audit="x",
        )
        for d in ("a", "b")
    }
    assert len(keys) == 2
    assert preview_key(base, slug="me", project_updated_at="t", audit="x") not in keys
