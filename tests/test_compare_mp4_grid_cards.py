"""Generated cards in the compare grid MP4 (issue #973, part 2).

A title page, a slate before each stage and a closing card are each a
segment of their own -- one still held for its duration, with the grid's
own stream layout (video plus the mix and one silent track per shooter)
so the cross-stage stitch stays a stream copy. A lower-third rides the
stage's own filter graph and adds no time. The card still itself is
``splitsmith.overlay_card``'s, sized to the *composed* grid (#691).
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from splitsmith.compare import mp4_grid
from splitsmith.composition import MatchTitle, TitleCard
from splitsmith.overlay_raster import RasterizerUnavailableError
from tests.conftest import fake_ffmpeg_probe
from tests.test_compare_mp4_grid_hold import _command, _driver_shooters, _graph_of, _plan, _still_runner

CANVAS = mp4_grid.GridCanvas(640, 360, 25, 1)


class _FakeRasterizer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append(html)
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()


def _ok_runner(calls: list[tuple[str, ...]]):
    def runner(cmd, **_kwargs):
        calls.append(tuple(str(c) for c in cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    return runner


def _concat_names(work: Path) -> list[str]:
    return [line.rsplit("/", 1)[-1].rstrip("'") for line in (work / "concat.txt").read_text().splitlines()]


# --- the card segment ---------------------------------------------------------


def test_card_segment_carries_the_grid_stream_layout() -> None:
    """One video plus N+1 silent PCM tracks, named and flagged exactly as
    a stage segment's are, so the concat demuxer sees one uniform layout."""
    cmd = mp4_grid.build_card_segment_command(
        Path("/w/card.png"),
        seconds=1.5,
        canvas=CANVAS,
        shooter_labels=("Ann", "Bo", "Cy"),
        output_path=Path("/w/card.mov"),
        ffmpeg_binary="/bin/ffmpeg",
    )
    assert cmd[cmd.index("-loop") + 1] == "1"
    assert cmd[cmd.index("-framerate") + 1] == "25/1"
    assert any(a.startswith("anullsrc=") for a in cmd)
    assert cmd[cmd.index("-t") + 1] == "1.5"
    graph = _graph_of(cmd)
    assert "[0:v]format=yuv420p,setsar=1[final]" in graph
    assert "asplit=4[amix][a0][a1][a2]" in graph
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert maps == ["[final]", "[amix]", "[a0]", "[a1]", "[a2]"]
    labels = mp4_grid.audio_track_labels(("Ann", "Bo", "Cy"))
    for arg in mp4_grid._track_naming_args(labels):
        assert arg in cmd
    assert cmd[cmd.index("-r") + 1] == "25/1"
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-c:a") + 1] == mp4_grid.SEGMENT_AUDIO_CODEC
    assert cmd[-1] == "/w/card.mov"


# --- the lower-third in the stage graph ------------------------------------------


def test_lower_third_is_the_last_input_and_fades_before_the_tail(tmp_path: Path) -> None:
    plan = _plan(hold=3.0)
    card = mp4_grid.LowerThirdInput(path=Path("/w/lt.png"), seconds=2.0)
    cmd = _command(plan)
    with_lt = mp4_grid.build_stage_command(
        plan,
        canvas=mp4_grid.GridCanvas(1920, 1080, 25, 1),
        output_path=Path("/w/s3.mov"),
        ffmpeg_binary="/bin/ffmpeg",
        hold_still_path=Path("/w/summary-stage3.png"),
        lower_third=card,
    )
    inputs = [with_lt[i + 1] for i, a in enumerate(with_lt) if a == "-i"]
    assert inputs[-1] == "/w/lt.png"
    index = len(inputs) - 1
    graph = _graph_of(with_lt)
    assert f"[{index}:v]format=rgba,fade=t=out:st=1.5:d=0.5:alpha=1[lt]" in graph
    assert "overlay=0:0:enable='lt(t,2)'[withlt]" in graph
    # Composited on the action, upstream of the hold's concat: the card
    # cannot reach a frame of the summary.
    assert "[withlt][hold]concat=n=2:v=1:a=0[joined]" in graph
    # Without the card the argv is what it was.
    assert (
        mp4_grid.build_stage_command(
            plan,
            canvas=mp4_grid.GridCanvas(1920, 1080, 25, 1),
            output_path=Path("/w/s3.mov"),
            ffmpeg_binary="/bin/ffmpeg",
            hold_still_path=Path("/w/summary-stage3.png"),
            lower_third=None,
        )
        == cmd
    )


# --- the render driver -------------------------------------------------------------


def test_cards_become_segments_in_spine_order_on_their_own_runner(tmp_path: Path) -> None:
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
        title_page=MatchTitle(text="Bromma", info=("2026-05-01",), duration_seconds=3.0),
        stage_titles="slate",
        title_duration_seconds=1.5,
        closing=MatchTitle(text="Bromma", duration_seconds=2.0),
    )
    assert _concat_names(work) == ["title_page.mov", "slate-stage1.mov", "stage1.mov", "closing.mov"]
    # The progress runner saw one stage and one stitch, nothing else.
    assert len(calls) == 2
    # Three card encodes on their own hook, each from a PNG at the
    # composed size.
    assert len(cards) == 3
    for name in ("title_page", "slate-stage1", "closing"):
        with Image.open(work / f"{name}.png") as image:
            assert image.size == (640, 360)
    assert [c[-1].rsplit("/", 1)[-1] for c in cards] == ["title_page.mov", "slate-stage1.mov", "closing.mov"]
    # Backdrop grabs went through the still hook, never the progress one:
    # a first frame for the title page and the slate, a last for the closing.
    assert len(stills) == 3
    assert all(cmd[-1].endswith("_backdrop.png") for cmd in stills)
    assert len(fake.calls) == 3
    assert "2026-05-01" in fake.calls[0]
    assert result.degradations == ()
    assert result.stages[0].ok


def test_no_browser_skips_every_card_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoBrowser:
        def __enter__(self):
            raise RasterizerUnavailableError("no browser", "Chromium could not be launched: boom")

        def __exit__(self, *exc: object) -> None:
            pass

    monkeypatch.setattr(mp4_grid, "ChromiumRasterizer", _NoBrowser)
    calls: list[tuple[str, ...]] = []
    cards: list[tuple[str, ...]] = []
    work = tmp_path / "work"
    result = mp4_grid.render_grid_mp4(
        _driver_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=_ok_runner(calls),
        card_runner=_ok_runner(cards),
        work_dir=work,
        ffmpeg_binary="/bin/ffmpeg",
        title_page=MatchTitle(text="Bromma"),
        stage_titles="slate",
    )
    assert _concat_names(work) == ["stage1.mov"]
    assert cards == []
    assert len(result.degradations) == 1
    assert "boom" in result.degradations[0].detail
    assert result.stages[0].ok


def test_lower_third_adds_no_segment_and_reaches_the_stage_command(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    cards: list[tuple[str, ...]] = []
    work = tmp_path / "work"
    mp4_grid.render_grid_mp4(
        _driver_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=_ok_runner(calls),
        card_runner=_ok_runner(cards),
        rasterizer=_FakeRasterizer(),
        work_dir=work,
        ffmpeg_binary="/bin/ffmpeg",
        stage_titles="lower-third",
        title_duration_seconds=2.0,
    )
    assert _concat_names(work) == ["stage1.mov"]
    assert cards == []
    assert str(work / "lower-third-stage1.png") in calls[0]
    assert "enable='lt(t,2)'" in _graph_of(calls[0])


def test_no_cards_requested_is_the_render_it_always_was(tmp_path: Path) -> None:
    """With nothing requested the rasterizer is never opened for a card
    and the concat list is the stages alone."""
    calls: list[tuple[str, ...]] = []
    cards: list[tuple[str, ...]] = []
    work = tmp_path / "work"
    mp4_grid.render_grid_mp4(
        _driver_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=_ok_runner(calls),
        card_runner=_ok_runner(cards),
        work_dir=work,
        ffmpeg_binary="/bin/ffmpeg",
    )
    assert _concat_names(work) == ["stage1.mov"]
    assert cards == []


def test_stage_card_text_is_the_stage_name_with_its_round_count(tmp_path: Path) -> None:
    fake = _FakeRasterizer()
    mp4_grid.render_grid_mp4(
        _driver_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=_ok_runner([]),
        card_runner=_ok_runner([]),
        still_runner=_still_runner([]),
        rasterizer=fake,
        work_dir=tmp_path / "work",
        ffmpeg_binary="/bin/ffmpeg",
        stage_titles="slate",
    )
    assert len(fake.calls) == 1
    assert "Stage 1" in fake.calls[0]


def test_stage_cards_for_the_grid_come_from_the_plan() -> None:
    card = mp4_grid.stage_card(_plan(), style="slate", seconds=1.5, expected_rounds=24)
    assert card == TitleCard(text="Stage 3", duration_seconds=1.5, style="slate", info=("24 rounds",))
    bare = mp4_grid.stage_card(_plan(), style="lower-third", seconds=2.0, expected_rounds=None)
    assert bare.info == ()


def test_probe_is_still_only_asked_when_the_overlay_is_on(tmp_path: Path) -> None:
    """Cards need a browser, not ``drawtext``; the ffmpeg capability probe
    stays gated on the overlay alone."""
    probes: list[tuple[str, ...]] = []

    def probe(cmd, **_kwargs):
        probes.append(tuple(str(c) for c in cmd))
        return fake_ffmpeg_probe()(cmd)

    mp4_grid.render_grid_mp4(
        _driver_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=_ok_runner([]),
        card_runner=_ok_runner([]),
        still_runner=_still_runner([]),
        probe_runner=probe,
        rasterizer=_FakeRasterizer(),
        work_dir=tmp_path / "work",
        ffmpeg_binary="/bin/ffmpeg",
        title_page=MatchTitle(text="Bromma"),
    )
    assert probes == []


def test_round_count_shows_on_a_slate_without_the_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count comes from project.json, not from the overlay's data, so
    ``--titles slate`` alone prints it (a review caught it silently absent)."""
    from splitsmith.compare import overlay_data

    monkeypatch.setattr(mp4_grid, "load_expected_rounds", lambda shooters: {1: 24})
    fake = _FakeRasterizer()
    mp4_grid.render_grid_mp4(
        _driver_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=_ok_runner([]),
        card_runner=_ok_runner([]),
        still_runner=_still_runner([]),
        rasterizer=fake,
        work_dir=tmp_path / "work",
        ffmpeg_binary="/bin/ffmpeg",
        overlay=False,
        stage_titles="slate",
    )
    assert "24 rounds" in fake.calls[0]
    assert overlay_data.load_expected_rounds is not None


def test_head_backdrop_is_the_first_frame_and_tail_backdrop_the_last(tmp_path: Path) -> None:
    stills: list[tuple[str, ...]] = []
    mp4_grid.render_grid_mp4(
        _driver_shooters(tmp_path),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=_ok_runner([]),
        card_runner=_ok_runner([]),
        still_runner=_still_runner(stills),
        rasterizer=_FakeRasterizer(),
        work_dir=tmp_path / "work",
        ffmpeg_binary="/bin/ffmpeg",
        title_page=MatchTitle(text="Bromma"),
        closing=MatchTitle(text="Bromma"),
    )
    head, tail = stills
    assert head[head.index("-frames:v") + 1] == "1" and "-update" not in head
    assert "-update" in tail and "-t" in tail
