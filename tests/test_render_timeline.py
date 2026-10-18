"""The render's timeline sidecar: where each spine item sits in the written
MP4, so a pass after the export (the audio dress) can place sound on a card
or a beep without re-planning the render."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from splitsmith import composition, mp4_render
from splitsmith.render_timeline import TimelineEntry, read_timeline, timeline_path

from .test_mp4_render import (
    _carded_composition,
    _ok,
    _render,
    _transitioned_composition,
    _writes_output,
)


def _spans(entries: tuple[TimelineEntry, ...]) -> list[tuple[str, float, float]]:
    return [(e.name, round(e.start, 3), round(e.end, 3)) for e in entries]


def test_a_transition_keeps_each_item_on_its_cut(tmp_path: Path) -> None:
    """A 1 s fade is centred on the cut, so each stage's nominal span is the
    cut's: stage A 0 .. 14.3, stage B 14.3 .. 28.6, the beep 3 s in (the
    head pad), whatever the boundary segment took off either end."""
    result = _render(tmp_path, _transitioned_composition(tmp_path), name="m", runner=_writes_output([]))
    assert _spans(result.timeline) == [
        ("stage_000", 0.0, 14.3),
        ("boundary_000", 13.8, 14.8),
        ("stage_001", 14.3, 28.6),
    ]
    a, fade, b = result.timeline
    assert (fade.kind, fade.stage_index, fade.cues) == ("transition", None, ())
    assert (a.kind, a.stage_index, a.beep) == ("stage", 0, pytest.approx(3.0))
    assert (b.kind, b.stage_index, b.beep) == ("stage", 1, pytest.approx(17.3))
    assert read_timeline(timeline_path(tmp_path / "m.mp4")) == result.timeline


def test_a_skipped_card_is_absent_and_moves_what_follows(tmp_path: Path, monkeypatch) -> None:
    """No browser: the title page and closing card were never written, so
    they are not on the timeline and the stages start where the video has
    them."""
    from splitsmith.overlay_raster import RasterizerUnavailableError

    class _NoBrowser:
        def __enter__(self):  # type: ignore[no-untyped-def]
            raise RasterizerUnavailableError("no browser", "boom")

        def __exit__(self, *exc: object) -> None:
            pass

    monkeypatch.setattr(mp4_render, "ChromiumRasterizer", _NoBrowser)
    result = mp4_render.render_mp4(
        _carded_composition(tmp_path),
        output_path=tmp_path / "m.mp4",
        work_dir=tmp_path / "work",
        runner=MagicMock(side_effect=_ok),
    )
    assert _spans(result.timeline) == [
        ("intro", 0.0, 4.0),
        ("stage_000", 4.0, 24.0),
        ("stage_001", 24.0, 44.0),
        ("outro", 44.0, 48.0),
    ]
    assert result.timeline[0].stage_index is None and result.timeline[0].beep is None


def test_the_sidecar_is_plain_json_beside_the_video(tmp_path: Path) -> None:
    _render(tmp_path, _transitioned_composition(tmp_path, kind=None), name="m", runner=_writes_output([]))
    path = tmp_path / "m-timeline.json"
    assert timeline_path(tmp_path / "m.mp4") == path
    doc = json.loads(path.read_text())
    assert doc["version"] == 1
    assert doc["items"][0] == {
        "kind": "stage",
        "name": "stage_000",
        "stage_index": 0,
        "start": 0.0,
        "end": pytest.approx(14.3),
        "beep": 3.0,
        "cues": [],
        "overlay": False,
    }


def test_a_cards_template_sounds_ride_its_entry(tmp_path: Path) -> None:
    """What a card's template declares through ``window.sounds()`` is
    written on that card's entry, for the dress pass to place."""
    from .test_mp4_render import _FakeRasterizer

    cues = ({"play": "click.wav", "at": 0.5},)

    class _Sounding(_FakeRasterizer):
        def render_template_frames(self, template, **kwargs):  # type: ignore[no-untyped-def]
            from dataclasses import replace

            return replace(super().render_template_frames(template, **kwargs), sounds=cues)

    result = mp4_render.render_mp4(
        _carded_composition(tmp_path),
        output_path=tmp_path / "m.mp4",
        work_dir=tmp_path / "work",
        runner=MagicMock(side_effect=_ok),
        rasterizer=_Sounding(),
    )
    by_kind = {e.kind: e for e in result.timeline}
    assert by_kind["title_page"].cues == cues and by_kind["closing"].cues == cues
    assert by_kind["stage"].cues == () and by_kind["intro"].cues == ()
    assert read_timeline(timeline_path(tmp_path / "m.mp4")) == result.timeline


def test_a_stage_entry_says_whether_it_carried_a_hud(tmp_path: Path) -> None:
    """A stage entry records whether its segment had an overlay, so the dress
    pass can say a stage went out without its HUD; other items say nothing."""
    import dataclasses

    comp = _transitioned_composition(tmp_path, kind=None)
    hud = composition.ConnectedClip(asset=comp.stages[0].primary, label="HUD", role="overlay")
    comp = dataclasses.replace(
        comp, stages=(dataclasses.replace(comp.stages[0], overlay=hud), comp.stages[1])
    )
    result = _render(tmp_path, comp, name="m", runner=_writes_output([]))
    assert [(e.name, e.overlay) for e in result.timeline] == [("stage_000", True), ("stage_001", False)]
