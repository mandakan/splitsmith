"""A grid reads a stage's tiles only while that stage is acquired (#1691).

A hosted worker's tiles live in storage and are pulled one stage at a time:
a whole match of 4K trims is gigabytes, and on Cloud Run the disk is
memory. ``render_grid_mp4(tile_access=)`` brackets every read of a stage's
trims (its encode, its edges, its summary hold, a card's backdrop, the
match summary's freezes) with that stage's ``acquire``, and a stage whose
segments are all cached is never acquired.

These never shell out: every runner is a fake that writes its output.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

from PIL import Image

from splitsmith import composition
from splitsmith.compare import mp4_grid
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle
from splitsmith.composition import MatchTitle
from splitsmith.segment_cache import SegmentCache
from tests.conftest import fake_ffmpeg_probe
from tests.test_compare_mp4_grid_cards import _FakeRasterizer

CANVAS = mp4_grid.GridCanvas(640, 360, 25, 1)
STAGES = (1, 2, 3)
LABELS = ("Anders", "Mathias")


def _shooters(tmp_path: Path) -> tuple[list[CompareShooterBundle], dict[str, int]]:
    """Two shooters over three stages, each tile its own (empty) trim, and
    the stage each trim belongs to."""
    stage_of: dict[str, int] = {}
    bundles = []
    for label in LABELS:
        audit = tmp_path / f"{label}-audit.json"
        audit.write_text(
            json.dumps({"shots": [{"shot_number": 1, "candidate_number": 1, "ms_after_beep": 900}]}),
            encoding="utf-8",
        )
        stages = {}
        for n in STAGES:
            trim = tmp_path / f"{label}-stage{n}.mov"
            if not trim.exists():  # rewriting it would re-date it and miss the cache
                trim.write_bytes(b"")
            stage_of[str(trim)] = n
            stages[n] = CompareStageBundle(
                stage_number=n,
                stage_name=f"Stage {n}",
                trim_path=trim,
                audit_path=audit,
                beep_offset_in_clip=2.0,
                duration_seconds=10.0,
                width=1920,
                height=1080,
                frame_rate_num=25,
                frame_rate_den=1,
            )
        bundles.append(
            CompareShooterBundle(label=label, project_root=tmp_path / label, stages_by_number=stages)
        )
    return bundles, stage_of


class _Access:
    """Records what is acquired, and checks every runner's argv against it."""

    def __init__(self, stage_of: dict[str, int]) -> None:
        self.stage_of = stage_of
        self.held: list[int] = []
        self.acquired: list[int] = []
        self.max_held = 0
        self.reads_outside_acquire: list[str] = []
        self.still_reads = 0

    @contextlib.contextmanager
    def acquire(self, stage_number: int) -> Iterator[None]:
        self.acquired.append(stage_number)
        self.held.append(stage_number)
        self.max_held = max(self.max_held, len(set(self.held)))
        try:
            yield
        finally:
            self.held.remove(stage_number)

    def check(self, argv: tuple[str, ...]) -> None:
        for token in argv:
            stage = self.stage_of.get(token)
            if stage is not None and stage not in self.held:
                self.reads_outside_acquire.append(token)


def _runners(access: _Access | None) -> dict[str, object]:
    def check(cmd) -> tuple[str, ...]:
        argv = tuple(str(c) for c in cmd)
        if access is not None:
            access.check(argv)
        return argv

    def writing(cmd, **_kwargs):
        argv = check(cmd)
        Path(argv[-1]).write_bytes(b"segment " + argv[-1].encode())
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    def still(cmd, **_kwargs):
        argv = check(cmd)
        if access is not None:
            access.still_reads += sum(token in access.stage_of for token in argv)
        Image.new("RGB", (64, 36), (7, 9, 11)).save(argv[-1])
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    return {
        "runner": writing,
        "card_runner": writing,
        "boundary_runner": writing,
        "still_runner": still,
        "probe_runner": fake_ffmpeg_probe(),
    }


def _render(tmp_path: Path, work: str, cache: SegmentCache, access: _Access | None, *, dressed: bool):
    shooters, _ = _shooters(tmp_path)
    extra: dict[str, object] = {}
    if dressed:
        extra = {
            "overlay": True,
            "summary_hold_seconds": 2.0,
            "stage_titles": "slate",
            "title_page": MatchTitle(text="HFO", duration_seconds=2.0),
            "match_summary_seconds": 3.0,
            "transitions": composition.uniform_transitions("fade", 0.5, len(STAGES)),
            "rasterizer": _FakeRasterizer(),
        }
    return mp4_grid.render_grid_mp4(
        shooters,
        audio_label="Anders",
        output_path=tmp_path / f"{work}.mp4",
        canvas=CANVAS,
        ffmpeg_binary="/bin/ffmpeg",
        work_dir=tmp_path / work,
        segment_cache=cache,
        tile_access=access,
        **_runners(access),
        **extra,
    )


def test_a_fully_cached_render_acquires_no_tiles(tmp_path: Path) -> None:
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)
    _render(tmp_path, "warm", cache, None, dressed=False)
    _, stage_of = _shooters(tmp_path)
    access = _Access(stage_of)
    _render(tmp_path, "again", cache, access, dressed=False)
    assert access.acquired == []


def test_a_cold_render_holds_at_most_one_stage_at_a_time(tmp_path: Path) -> None:
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)
    _, stage_of = _shooters(tmp_path)
    access = _Access(stage_of)
    result = _render(tmp_path, "cold", cache, access, dressed=False)
    assert [s.ok for s in result.stages] == [True, True, True]
    assert access.max_held == 1
    assert sorted(set(access.acquired)) == [1, 2, 3]
    assert access.reads_outside_acquire == []


def test_a_cold_dressed_render_acquires_around_cards_and_freezes(tmp_path: Path) -> None:
    """Slates, summary holds, a title page, the match summary and a fade at
    every boundary: every tile read happens inside its own stage's acquire."""
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)
    _, stage_of = _shooters(tmp_path)
    access = _Access(stage_of)
    result = _render(tmp_path, "dressed", cache, access, dressed=True)
    assert [s.ok for s in result.stages] == [True, True, True]
    # The holds, the backdrops and the freezes did read tiles.
    assert access.still_reads >= len(STAGES) * len(LABELS)
    assert access.reads_outside_acquire == []
    assert access.max_held == 1
