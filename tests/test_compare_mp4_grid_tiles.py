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

import pytest
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


def _render(
    tmp_path: Path,
    work: str,
    cache: SegmentCache | None,
    access: _Access | None,
    *,
    dressed: bool,
    runners: dict[str, object] | None = None,
    shooters: list[CompareShooterBundle] | None = None,
    **kwargs: object,
):
    if shooters is None:
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
        **(runners if runners is not None else _runners(access)),
        **extra,
        **kwargs,
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


# --- one stage at a time --------------------------------------------------


def _tile_reading_runners(stage_of: dict[str, int]) -> tuple[dict[str, object], list[str]]:
    """Runners that write their outputs and note every encode (not a still
    grab) that reads a tile: a stage or edge encode."""
    reads: list[str] = []
    base = _runners(None)

    def noting(inner):
        def run(cmd, **kwargs):
            reads.extend(token for token in (str(c) for c in cmd) if token in stage_of)
            return inner(cmd, **kwargs)

        return run

    return {
        **base,
        "runner": noting(base["runner"]),
        "boundary_runner": noting(base["boundary_runner"]),
        "card_runner": noting(base["card_runner"]),
    }, reads


def _concat_keys(work: Path) -> list[str]:
    listed = (work / "concat.txt").read_text(encoding="utf-8").splitlines()
    return [Path(line[len("file '") : -1]).name for line in listed]


@pytest.mark.parametrize("dressed", [False, True])
def test_stage_renders_then_a_full_render_is_all_hits(tmp_path: Path, dressed: bool) -> None:
    """Every stage rendered on its own, then the whole grid: it encodes no
    stage and no edge (nothing reads a tile but still grabs), and stitches
    exactly the segments a one-process render of the same grid stitches."""
    split = SegmentCache(root=tmp_path / "split-cache", max_bytes=1 << 30)
    for n in STAGES:
        result = _render(tmp_path, f"stage{n}", split, None, dressed=dressed, only_stage=n)
        assert [(s.stage_number, s.ok) for s in result.stages] == [(n, True)]
        assert not (tmp_path / f"stage{n}.mp4").exists()

    _, stage_of = _shooters(tmp_path)
    runners, reads = _tile_reading_runners(stage_of)
    _render(tmp_path, "stitch", split, None, dressed=dressed, runners=runners)
    assert reads == []

    whole = SegmentCache(root=tmp_path / "whole-cache", max_bytes=1 << 30)
    _render(tmp_path, "whole", whole, None, dressed=dressed)
    assert _concat_keys(tmp_path / "stitch") == _concat_keys(tmp_path / "whole")


def test_a_stage_with_a_filler_tile_renders_alone(tmp_path: Path) -> None:
    shooters, stage_of = _shooters(tmp_path)
    del shooters[1].stages_by_number[2]  # Mathias has no trim on stage 2
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)
    for n in STAGES:
        result = _render(tmp_path, f"stage{n}", cache, None, dressed=False, shooters=shooters, only_stage=n)
        assert [s.ok for s in result.stages] == [True]
    runners, reads = _tile_reading_runners(stage_of)
    _render(tmp_path, "stitch", cache, None, dressed=False, runners=runners, shooters=shooters)
    assert reads == []


def test_only_stage_without_a_cache_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="segment_cache"):
        _render(tmp_path, "w", None, None, dressed=False, only_stage=1)


def test_only_stage_names_a_stage_in_the_grid(tmp_path: Path) -> None:
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)
    with pytest.raises(ValueError, match="stage 9"):
        _render(tmp_path, "w", cache, None, dressed=False, only_stage=9)


class _UrlCache(SegmentCache):
    """A cache whose stage segments the stitch reads over a URL."""

    def concat_source(self, path: Path) -> str:
        return f"https://bucket.example/{path.name}?X-Amz-Signature=s3cret"


def test_a_stitch_over_urls_reads_quietly_or_fails(tmp_path: Path) -> None:
    """Reading segments over URLs, the stitch whitelists the protocols and
    trusts only a quiet read: ffmpeg exits 0 on a dropped connection with a
    short file. The URL never reaches the error."""
    cache = _UrlCache(root=tmp_path / "cache", max_bytes=1 << 30)
    _render(tmp_path, "warm", cache, None, dressed=False)
    stitches: list[tuple[str, ...]] = []
    base = _runners(None)

    def noisy(cmd, **kwargs):  # type: ignore[no-untyped-def]
        argv = tuple(str(c) for c in cmd)
        if "concat" in argv:
            stitches.append(argv)
            Path(argv[-1]).write_bytes(b"short")
            return subprocess.CompletedProcess(
                cmd, 0, "", "https://bucket.example/x.mov?X-Amz-Signature=s3cret: Connection reset\n"
            )
        return base["runner"](cmd, **kwargs)

    with pytest.raises(mp4_grid.GridRenderError) as err:
        _render(tmp_path, "stitch", cache, None, dressed=False, runners={**base, "runner": noisy})
    (stitch,) = stitches
    assert "-protocol_whitelist" in stitch and stitch[stitch.index("-loglevel") + 1] == "error"
    assert "s3cret" not in str(err.value)
    listed = (tmp_path / "stitch" / "concat.txt").read_text(encoding="utf-8")
    assert "https://bucket.example/" in listed


@pytest.mark.parametrize("dressed", [False, True])
def test_stage_segments_from_other_workers_are_found_by_the_stitch(tmp_path: Path, dressed: bool) -> None:
    """Each stage on its own "worker": its own scratch cache root over one
    shared store, its own work dir, and its own copy of the tiles (rewritten,
    so a new mtime). With the tiles keyed by their stored identity, a stitch
    from a third worker encodes no stage and reads no tile."""
    import os

    from splitsmith.storage import FilesystemStorage
    from splitsmith.stored_segment_cache import StoredSegmentCache

    store = FilesystemStorage(tmp_path / "store")
    shooters, stage_of = _shooters(tmp_path)
    identities = {path: f"storage:exports/{Path(path).name}:0:v1" for path in stage_of}

    def worker(name: str) -> StoredSegmentCache:
        for path in stage_of:  # this worker's own copy of every tile
            Path(path).write_bytes(b"")
            os.utime(path, (hash(name) % 10**6, hash(name) % 10**6))
        return StoredSegmentCache(
            tmp_path / f"{name}-cache", 1 << 30, storage=store, prefix="matches/m/render-segments"
        )

    for n in STAGES:
        _render(
            tmp_path,
            f"stage{n}",
            worker(f"w{n}"),
            None,
            dressed=dressed,
            shooters=shooters,
            only_stage=n,
            tile_identities=identities,
        )
    runners, reads = _tile_reading_runners(stage_of)
    _render(
        tmp_path,
        "stitch",
        worker("stitcher"),
        None,
        dressed=dressed,
        shooters=shooters,
        runners=runners,
        tile_identities=identities,
    )
    assert reads == []
