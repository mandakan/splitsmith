"""The single-shooter MP4 grades footage, never what is drawn over it."""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import numpy as np
import pytest

from splitsmith import grade, mp4_render
from splitsmith.fcpxml_gen import PipPlacement, SecondaryClip
from splitsmith.grade import Lut, ResolvedGrade

from .test_mp4_render import (
    _basic_stage,
    _build_plan,
    _FakeRasterizer,
    _make_video,
    _meta_30fps,
    _render,
    _transitioned_composition,
    _writes_output,
)


def _inverting(tmp_path: Path) -> ResolvedGrade:
    source = tmp_path / "invert.cube"
    source.write_text(grade.write_cube(Lut(size=2, table=1.0 - grade.identity(2).table)))
    return ResolvedGrade(source=source, strength=1.0, label="invert")


def _graph(cmd: tuple[str, ...]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


def test_the_grade_goes_on_every_camera_before_the_overlay(tmp_path: Path) -> None:
    sec = SecondaryClip(
        video_path=_make_video(tmp_path, "cam.mp4"),
        video=_meta_30fps(),
        beep_offset_seconds=5.0,
        label="Cam",
        pip=PipPlacement(corner="top-right"),
    )
    overlay = _make_video(tmp_path, "hud.mov")
    stage = _basic_stage(
        tmp_path=tmp_path, name="A", primary_name="a.mp4", secondaries=(sec,), overlay_path=overlay
    )
    comp, plan = _build_plan(stage)
    lut = tmp_path / "work" / "grade-abc.cube"
    graded = _graph(
        mp4_render._build_stage_command(
            plan, sequence=comp.sequence, output_path=tmp_path / "s.mp4", grade_lut=lut
        )
    )
    plain = _graph(
        mp4_render._build_stage_command(plan, sequence=comp.sequence, output_path=tmp_path / "s.mp4")
    )
    applied = grade.lut_filter(lut)
    assert "lut3d" not in plain, "no grade, no filter: the default render is what it was"
    assert graded.startswith(f"[0:v]setpts=PTS-STARTPTS,{applied},format=yuv420p[base]")
    assert f"[1:v]setpts=PTS-STARTPTS,{applied}" in graded, "the picture-in-picture camera too"
    assert graded.count("lut3d") == 2, "never the HUD overlay"
    assert graded.replace(f",{applied}", "") == plain


def test_a_graded_render_grades_stages_edges_and_backdrops_and_nothing_else(tmp_path: Path) -> None:
    comp = dataclasses.replace(_transitioned_composition(tmp_path, slates=True), grade=_inverting(tmp_path))
    calls: list[list[str]] = []
    _render(tmp_path, comp, name="m", runner=_writes_output(calls), rasterizer=_FakeRasterizer())
    by_name = {Path(argv[-1]).name: argv for argv in calls}
    # stage_000 is item 1 (after its slate); its tail edge is footage, the next slate's head is a card.
    for name in ("stage_000.mp4", "stage_001.mp4", "edge_001_tail.mp4"):
        assert "lut3d" in " ".join(by_name[name]), name
    backdrops = [argv for name, argv in by_name.items() if name.endswith("_backdrop.png")]
    assert backdrops and all("lut3d" in " ".join(argv) for argv in backdrops)
    for name in ("slate_000.mp4", "slate_001.mp4", "edge_002_head.mp4", "boundary_001.mp4", "m.mp4"):
        assert "lut3d" not in " ".join(by_name[name]), f"{name} is not footage"
    # One file, content-named, in the work dir.
    luts = list((tmp_path / "work_m").glob("grade-*.cube"))
    assert len(luts) == 1


def test_an_ungraded_render_names_no_lut(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    _render(
        tmp_path,
        _transitioned_composition(tmp_path, slates=True),
        name="m",
        runner=_writes_output(calls),
        rasterizer=_FakeRasterizer(),
    )
    assert not any("lut3d" in " ".join(argv) for argv in calls)


def test_a_changed_lut_misses_the_segment_cache(tmp_path: Path) -> None:
    """The cache keys on the argv; the LUT's file is named by its content,
    so the same stage graded another way encodes again."""
    from splitsmith.segment_cache import SegmentCache

    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    full = _inverting(tmp_path)
    runs: list[list[list[str]]] = []
    # Built once: re-making it rewrites the stage videos, whose mtimes the key reads.
    base = _transitioned_composition(tmp_path, kind=None)
    for strength in (1.0, 1.0, 0.5):
        calls: list[list[str]] = []
        comp = dataclasses.replace(base, grade=dataclasses.replace(full, strength=strength))
        _render(tmp_path, comp, name=f"m{len(runs)}", runner=_writes_output(calls), segment_cache=cache)
        runs.append(calls)
    encoded = [[a[-1] for a in calls if a[-1].endswith(".part.mp4")] for calls in runs]
    assert encoded[0] and not encoded[1], "the same grade is reused"
    assert encoded[2], "another strength encodes again"


@pytest.mark.integration
def test_ffmpeg_applies_the_lut_to_the_pixels(tmp_path: Path) -> None:
    """A real ffmpeg reads the materialized file through ``lut_filter``: an
    inverting LUT turns a mid-red frame cyan-ish."""
    lut = grade.materialize(_inverting(tmp_path), tmp_path / "work")
    out = tmp_path / "px.rgb"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=0xC03020:s=16x16:d=0.1",
         "-vf", f"{grade.lut_filter(lut)},format=rgb24", "-frames:v", "1", "-f", "rawvideo", str(out)],
        check=True,
    )  # fmt: skip
    r, g, b = np.frombuffer(out.read_bytes(), dtype=np.uint8)[:3]
    assert (
        abs(int(r) - (255 - 0xC0)) <= 6
        and abs(int(g) - (255 - 0x30)) <= 6
        and abs(int(b) - (255 - 0x20)) <= 6
    )
