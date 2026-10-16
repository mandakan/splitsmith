"""The stage and match summaries drawn by a Look's template (spec
2026-10-16-summary-templates-design)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from splitsmith import composition, looks, mp4_render
from splitsmith.config import StageEvent
from splitsmith.hud_extras import HudExtras
from splitsmith.match_project import StageScorecard
from splitsmith.overlay_theme import load_theme
from splitsmith.stage_summary_data import TileShot, TileStageData
from splitsmith.summary_template import (
    match_summary_payload,
    stage_summary_payload,
    summary_context,
)
from tests.test_mp4_render import (
    _command_writing,
    _fake_clip_writer,
    _FakeRasterizer,
    _ok,
    _summarised_composition,
    _transitioned_composition,
)

_MOVE = StageEvent(id="evt-1", kind="movement", start=1.1, end=1.25, source="manual")
_PROPOSAL = StageEvent(id="evt-2", kind="reload", start=0.2, end=0.9, source="auto")


def _tile() -> TileStageData:
    return TileStageData(
        label="Me",
        stage_number=2,
        shots=(TileShot(1.0, 1.0), TileShot(1.3, 0.3, interval_class="split", moving=False)),
        stage_time_seconds=4.5,
        scorecard=StageScorecard(hit_factor=12.0, alphas=10, charlies=1),
        events=(_MOVE, _PROPOSAL),
    )


@pytest.fixture
def retro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A user Look with a summary and a match summary template."""
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path / "home"))
    manifest = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    manifest.update(
        name="retro",
        slots={"summary": {"finished": "sum.html"}, "match_summary": {"tally": "match.html"}},
    )
    root = tmp_path / "home" / "looks" / "retro"
    root.mkdir(parents=True)
    (root / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "sum.html").write_text(
        '<!doctype html><meta name="splitsmith-data" content="scoring">', encoding="utf-8"
    )
    (root / "match.html").write_text("<!doctype html>", encoding="utf-8")
    return root


# -- the payload -----------------------------------------------------------


def test_the_stage_payload_is_hud_stage_data_from_the_beep_with_figures() -> None:
    payload = stage_summary_payload(_tile(), extras=None)
    assert set(payload) == {"stage", "figures"}
    assert [s["t"] for s in payload["stage"]["shots"]] == [1.0, 1.3]
    assert payload["stage"]["events"] == [{"kind": "movement", "start": 1.1, "end": 1.25}], "no proposal"
    assert payload["figures"]["hit_factor"] == 12.0 and payload["figures"]["draw"] == 1.0


def test_declared_providers_join_the_stage_payload() -> None:
    extras = HudExtras(stage_name="Bay 2", scorecard=_tile().scorecard)
    payload = stage_summary_payload(_tile(), extras=extras, keys=("scoring",))
    assert payload["scoring"]["name"] == "Bay 2"
    assert payload["scoring"]["spreads"]["even"][-1]["totals"]["A"] == 10


def test_the_match_payload_is_the_match_summary_as_data() -> None:
    from splitsmith.match_summary import MatchSummary, MatchSummaryRow

    row = MatchSummaryRow(1, "Bay", 10.0, False, 4.2, 55.0, 1.1, 0.4)
    summary = MatchSummary("M", "Me", (row,), 0.4, 1.1, 20, {"A": 10}, 1, 1)
    match = match_summary_payload(summary)["match"]
    assert match["rows"][0]["name"] == "Bay" and match["hits"] == {"A": 10}
    assert "duration_seconds" not in match


@pytest.mark.parametrize(("size", "upright"), [((1920, 1080), False), ((1080, 1920), True)])
def test_the_context_carries_the_safe_area_only_upright(size: tuple[int, int], upright: bool) -> None:
    context = summary_context(
        slot="summary",
        variant="finished",
        text="Bay 2",
        payload={"figures": {}},
        duration_seconds=3.0,
        width=size[0],
        height=size[1],
        fps=30.0,
        theme=load_theme("splitsmith"),
    )
    assert context.data["card"] == {
        "slot": "summary",
        "variant": "finished",
        "text": "Bay 2",
        "info": [],
        "duration_seconds": 3.0,
    }
    assert ("safe_area" in context.data) is upright


# -- the render ------------------------------------------------------------


def _with_variant(comp: composition.Composition, variant: str) -> composition.Composition:
    stages = tuple(
        dataclasses.replace(s, summary=dataclasses.replace(s.summary, variant=variant)) if s.summary else s
        for s in comp.stages
    )
    return dataclasses.replace(comp, stages=stages)


def _render(tmp_path: Path, comp: composition.Composition, fake: _FakeRasterizer, look: str) -> tuple:
    runner = MagicMock(side_effect=_ok)
    work = tmp_path / "work"
    result = mp4_render.render_mp4(
        comp,
        output_path=tmp_path / "m.mp4",
        work_dir=work,
        runner=runner,
        rasterizer=fake,
        overlay_theme=look,
    )
    return result, runner, work


def _summary_contexts(fake: _FakeRasterizer) -> list[dict]:
    return [
        r[1]["data"] for r in fake.frame_requests if r[1]["data"].get("card", {}).get("slot") == "summary"
    ]


def test_the_default_summary_draws_the_engine_card(tmp_path: Path, retro: Path) -> None:
    fake = _FakeRasterizer()
    _render(tmp_path, _summarised_composition(tmp_path), fake, "retro")
    assert _summary_contexts(fake) == []
    assert [c for c in fake.calls if c.startswith("<!doctype html>")], "the engine's summary HTML"


def test_a_still_summary_template_composes_one_png(tmp_path: Path, retro: Path) -> None:
    fake = _FakeRasterizer()
    result, runner, work = _render(
        tmp_path, _with_variant(_summarised_composition(tmp_path), "finished"), fake, "retro"
    )
    contexts = _summary_contexts(fake)
    assert len(contexts) == 2 and contexts[0]["card"]["text"] == "Me"
    assert "figures" in contexts[0] and "scoring" in contexts[0]
    assert not [c for c in fake.calls if c.startswith("<!doctype html>")], "no engine summary drawn"
    cmd = _command_writing(runner, "summary_000.mp4")
    assert str(work / "summary_000.png") in cmd
    assert result.degradations == ()


def test_an_animated_summary_template_is_a_motion_segment(
    tmp_path: Path, retro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mp4_render, "write_motion_clip", _fake_clip_writer)
    fake = _FakeRasterizer(motion_seconds=0.5)
    _r, runner, work = _render(
        tmp_path, _with_variant(_summarised_composition(tmp_path), "finished"), fake, "retro"
    )
    cmd = _command_writing(runner, "summary_000.mp4")
    assert str(work / "summary_000_motion.mov") in cmd and str(work / "summary_000_backdrop.png") in cmd


def test_a_failing_template_draws_the_engine_card_and_says_so(tmp_path: Path, retro: Path) -> None:
    class _Broken(_FakeRasterizer):
        def render_template_frames(self, template, **kwargs):  # noqa: ANN001, ANN003
            if template.name == "sum.html":
                raise RuntimeError("boom")
            return super().render_template_frames(template, **kwargs)

    fake = _Broken()
    result, runner, work = _render(
        tmp_path, _with_variant(_summarised_composition(tmp_path), "finished"), fake, "retro"
    )
    assert [c for c in fake.calls if c.startswith("<!doctype html>")], "the engine card stood in"
    assert any("stage summary drew the built-in card" in d and "sum.html" in d for d in result.degradations)
    assert str(work / "summary_000.png") in _command_writing(runner, "summary_000.mp4")


def test_a_summary_style_the_look_lacks_is_noted(tmp_path: Path, retro: Path) -> None:
    fake = _FakeRasterizer()
    result, _r, _w = _render(tmp_path, _with_variant(_summarised_composition(tmp_path), "nope"), fake, "retro")
    assert any("'nope' is not in Look 'retro'" in d for d in result.degradations)


def test_a_match_summary_template_draws_the_match_as_data(tmp_path: Path, retro: Path) -> None:
    from splitsmith.match_summary import MatchSummary, MatchSummaryRow

    row = MatchSummaryRow(1, "Bay", 10.0, False, 4.2, 55.0, 1.1, 0.4)
    summary = MatchSummary("Little Trouble", "Me", (row,), 0.4, 1.1, 20, {"A": 10}, 1, 1)
    comp = dataclasses.replace(
        _summarised_composition(tmp_path), match_summary=summary, match_summary_variant="tally"
    )
    fake = _FakeRasterizer()
    _result, runner, work = _render(tmp_path, comp, fake, "retro")
    (context,) = [
        r[1]["data"] for r in fake.frame_requests if r[1]["data"]["card"]["slot"] == "match_summary"
    ]
    assert context["card"]["text"] == "Little Trouble" and context["match"]["rows"][0]["name"] == "Bay"
    assert str(work / "match_summary.png") in _command_writing(runner, "match_summary.mp4")


# -- review findings: transitions and failures -----------------------------


def test_an_animated_summary_beside_a_transition_renders_its_edge(
    tmp_path: Path, retro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fade out of an animated summary is an edge of its clip, offset to
    the clip's last ``half`` seconds, as an animated card's tail edge is."""
    monkeypatch.setattr(mp4_render, "write_motion_clip", _fake_clip_writer)
    comp = _with_variant(_transitioned_composition(tmp_path, summaries=True), "finished")
    fake = _FakeRasterizer(motion_seconds=0.5)
    result, runner, work = _render(tmp_path, comp, fake, "retro")
    edge = _command_writing(runner, "_tail.mp4")
    assert str(work / "summary_000_motion.mov") in edge
    assert not any("transition" in d for d in result.degradations), result.degradations


def test_a_summary_clip_that_fails_draws_the_built_in_card_and_says_so(
    tmp_path: Path, retro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith.look_motion import MotionClipError

    def broken(frames, *, out: Path, fps: float, ffmpeg_binary: str):  # noqa: ANN001, ANN202
        raise MotionClipError("frame 12 failed")

    monkeypatch.setattr(mp4_render, "write_motion_clip", broken)
    fake = _FakeRasterizer(motion_seconds=0.5)
    result, runner, work = _render(
        tmp_path, _with_variant(_summarised_composition(tmp_path), "finished"), fake, "retro"
    )
    assert str(work / "summary_000.png") in _command_writing(runner, "summary_000.mp4")
    assert [c for c in fake.calls if c.startswith("<!doctype html>")], "the engine card stood in"
    assert any("summary_000 drew the built-in card" in d for d in result.degradations)


def test_a_still_template_whose_frame_fails_says_the_built_in_card_drew(tmp_path: Path, retro: Path) -> None:
    from splitsmith.overlay_raster import TemplateFrames

    class _BadFrame(_FakeRasterizer):
        def render_template_frames(self, template, **kwargs):  # noqa: ANN001, ANN003
            frames = super().render_template_frames(template, **kwargs)

            def boom():
                raise RuntimeError("bad frame")
                yield b""  # pragma: no cover

            return TemplateFrames(
                duration=frames.duration,
                frame_count=frames.frame_count,
                width=frames.width,
                height=frames.height,
                frames=boom(),
            )

    result, _r, _w = _render(
        tmp_path, _with_variant(_summarised_composition(tmp_path), "finished"), _BadFrame(), "retro"
    )
    assert any("drew the built-in card" in d for d in result.degradations)
    assert not any("was left out" in d for d in result.degradations)
