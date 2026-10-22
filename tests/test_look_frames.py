"""The frames sting's pure contract (issue #1535)."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.look import frames as look_frames
from splitsmith.look.sandbox import ORIGIN
from splitsmith.look.template import TemplateContext


def _template(tmp_path: Path, head: str) -> Path:
    path = tmp_path / "sting.html"
    path.write_text(f"<!doctype html><html><head>{head}</head><body></body></html>", encoding="utf-8")
    return path


def test_the_tag_opts_in_and_only_with_content_edges(tmp_path: Path) -> None:
    assert look_frames.wants_frames(_template(tmp_path, '<meta name="splitsmith-frames" content="edges">'))
    assert look_frames.wants_frames(_template(tmp_path, "<meta content='edges' name='splitsmith-frames'>"))
    assert not look_frames.wants_frames(
        _template(tmp_path, '<meta name="splitsmith-frames" content="cards">')
    )
    assert not look_frames.wants_frames(_template(tmp_path, '<meta name="splitsmith-data" content="edges">'))
    assert not look_frames.wants_frames(_template(tmp_path, ""))


@pytest.mark.parametrize(
    "head",
    [
        '<meta data-name="splitsmith-frames" content="edges">',
        '<meta name="splitsmith-frames" data-content="edges">',
        '<!-- <meta name="splitsmith-frames" content="edges"> -->',
        '<meta name="splitsmith-frames" content="EDGES">',
    ],
)
def test_a_tag_that_is_not_an_edges_opt_in_is_not_wanted(tmp_path: Path, head: str) -> None:
    assert not look_frames.wants_frames(_template(tmp_path, head))


def test_a_commented_out_tag_is_not_misplaced_either(tmp_path: Path) -> None:
    assert not look_frames.misplaced_frames_tag(
        _template(tmp_path, '<!-- <meta name="splitsmith-frames" content="cards"> -->')
    )


def test_a_tag_with_another_content_is_misplaced_not_wanted(tmp_path: Path) -> None:
    other = _template(tmp_path, '<meta name="splitsmith-frames" content="cards">')
    assert look_frames.misplaced_frames_tag(other)
    assert not look_frames.misplaced_frames_tag(_template(tmp_path, ""))


def test_extraction_is_one_ffmpeg_call_at_the_boundary_rate(tmp_path: Path) -> None:
    cmd = look_frames.extraction_command(
        tmp_path / "edge.mp4", tmp_path / "from", rate="30000/1001", ffmpeg_binary="ff"
    )
    assert cmd == (
        "ff", "-hide_banner", "-y", "-loglevel", "error", "-i", str(tmp_path / "edge.mp4"),
        "-vf", "fps=30000/1001", "-q:v", "2", "-start_number", "0", str(tmp_path / "from" / "%04d.jpg"),
    )  # fmt: skip


def _files(tmp_path: Path, n: int) -> list[Path]:
    return [tmp_path / f"{i:04d}.jpg" for i in range(n)]


def test_a_full_edge_maps_frame_for_frame(tmp_path: Path) -> None:
    urls = look_frames.edge_frame_urls(
        _files(tmp_path, 30), count=30, fps=30.0, pad_seconds=0.0, end="tail", key="k"
    )
    assert urls[0] == f"{ORIGIN}/frames/k/from/0000.jpg"
    assert urls[-1] == f"{ORIGIN}/frames/k/from/0029.jpg"


def test_a_tail_pad_repeats_the_last_frame_after_the_footage(tmp_path: Path) -> None:
    # 0.5 s of a 1 s boundary is held: 15 real frames, then the 15th again.
    urls = look_frames.edge_frame_urls(
        _files(tmp_path, 15), count=30, fps=30.0, pad_seconds=0.5, end="tail", key="k"
    )
    assert len(urls) == 30
    assert urls[:15] == [f"{ORIGIN}/frames/k/from/{i:04d}.jpg" for i in range(15)]
    # Every held index repeats the last file; a wrap (n % len) would not.
    assert urls[15:] == [f"{ORIGIN}/frames/k/from/0014.jpg"] * 15


def test_a_head_pad_repeats_the_first_frame_before_the_footage(tmp_path: Path) -> None:
    urls = look_frames.edge_frame_urls(
        _files(tmp_path, 15), count=30, fps=30.0, pad_seconds=0.5, end="head", key="k"
    )
    assert urls[:16] == [f"{ORIGIN}/frames/k/to/0000.jpg"] * 16
    assert urls[16] == f"{ORIGIN}/frames/k/to/0001.jpg"
    assert urls[29] == f"{ORIGIN}/frames/k/to/0014.jpg"


def test_a_tail_with_more_footage_than_the_boundary_is_not_capped_by_its_pad(tmp_path: Path) -> None:
    # 20 files at count 30 with a 0.5 s pad: the pad is the footage running
    # out, not a cap. Boundary frames 0..19 are footage, 20..29 hold frame 19.
    urls = look_frames.edge_frame_urls(
        _files(tmp_path, 20), count=30, fps=30.0, pad_seconds=0.5, end="tail", key="k"
    )
    assert urls[:20] == [f"{ORIGIN}/frames/k/from/{i:04d}.jpg" for i in range(20)]
    assert urls[20:] == [f"{ORIGIN}/frames/k/from/0019.jpg"] * 10


def test_a_head_pad_rounds_half_up_to_whole_frames(tmp_path: Path) -> None:
    # 30000/1001 fps: 0.5 s is 14.985 frames, which is 15 held.
    urls = look_frames.edge_frame_urls(
        _files(tmp_path, 15), count=30, fps=30000 / 1001, pad_seconds=0.5, end="head", key="k"
    )
    assert urls[:16] == [f"{ORIGIN}/frames/k/to/0000.jpg"] * 16
    assert urls[16] == f"{ORIGIN}/frames/k/to/0001.jpg"
    # 25 fps: 0.5 s is exactly 12.5 frames; half-up gives 13 (round() gives 12).
    urls = look_frames.edge_frame_urls(
        _files(tmp_path, 15), count=30, fps=25.0, pad_seconds=0.5, end="head", key="k"
    )
    assert urls[:14] == [f"{ORIGIN}/frames/k/to/0000.jpg"] * 14
    assert urls[14] == f"{ORIGIN}/frames/k/to/0001.jpg"


def test_an_edge_with_more_frames_than_the_boundary_is_cut_to_count(tmp_path: Path) -> None:
    urls = look_frames.edge_frame_urls(
        _files(tmp_path, 31), count=30, fps=30.0, pad_seconds=0.0, end="tail", key="k"
    )
    assert len(urls) == 30 and urls[-1].endswith("/0029.jpg")


def test_no_frames_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no frames"):
        look_frames.edge_frame_urls([], count=30, fps=30.0, pad_seconds=0.0, end="tail", key="k")


def test_the_key_moves_with_either_edge() -> None:
    base = look_frames.frames_key("d", "tail-a", "head-a")
    assert len(base) == 16
    assert look_frames.frames_key("d", "tail-b", "head-a") != base
    assert look_frames.frames_key("d", "tail-a", "head-b") != base
    assert look_frames.frames_key("e", "tail-a", "head-a") != base


def test_with_frames_adds_the_block_and_nothing_else() -> None:
    ctx = TemplateContext(
        theme={},
        data={"transition": {"kind": "sting:push"}, "shooters": []},
        size={"width": 2, "height": 2},
        fps=30.0,
        engine={},
        assets={},
    )
    out = look_frames.with_frames(ctx, fps=30.0, count=1, from_urls=["a"], to_urls=["b"])
    assert out.data["transition"]["frames"] == {"fps": 30.0, "count": 1, "from": ["a"], "to": ["b"]}
    assert out.data["shooters"] == [] and "frames" not in ctx.data["transition"]
