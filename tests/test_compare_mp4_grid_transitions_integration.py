"""A real ffmpeg render of a two-shooter grid with a 1 s fade (#1244): the
boundary segment is d long and carries the grid's N+1 tracks, the stitched
file keeps the cut's length, and every track survives with its name."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from splitsmith import composition
from splitsmith.compare import mp4_grid
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle
from splitsmith.fcpxml_gen import probe_video
from splitsmith.segment_cache import SegmentCache
from tests.compare_fixture import cut_clip, probe_seconds, write_audit
from tests.look_fixtures import PUSH_GAP, PushRasterizer, install_frames_look
from tests.synthetic_media import (
    SYNTHETIC_FPS_DEN,
    SYNTHETIC_FPS_NUM,
    build_color_video,
    build_synthetic_video,
    ffmpeg_available,
)

pytestmark = pytest.mark.integration

FRAME = SYNTHETIC_FPS_DEN / SYNTHETIC_FPS_NUM


def _streams(path: Path, *, ffprobe: str) -> list[tuple[str, str]]:
    """``(codec_type, handler name)`` per stream, in file order: an MP4 keeps a
    track's name in its handler, which is where the stitch restates it."""
    done = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    streams = json.loads(done.stdout)["streams"]
    return [(stream["codec_type"], stream.get("tags", {}).get("handler_name", "")) for stream in streams]


def _two_shooters(tmp_path: Path, *, ffmpeg: str) -> list[CompareShooterBundle]:
    """Two shooters, two 4 s stages each, the beep at 1 s."""
    source = tmp_path / "source.mp4"
    build_synthetic_video(source)
    shooters: list[CompareShooterBundle] = []
    for label in ("Anders", "Mathias"):
        stages: dict[int, CompareStageBundle] = {}
        for number in (1, 2):
            trim = tmp_path / f"{label}-{number}.mp4"
            cut_clip(source, trim, 120, ffmpeg=ffmpeg)  # 4 s, beep at 1 s
            audit = tmp_path / f"{label}-{number}.json"
            write_audit(audit, (1100, 1320))
            meta = probe_video(trim)
            stages[number] = CompareStageBundle(
                stage_number=number,
                stage_name=f"Stage {number}",
                trim_path=trim,
                audit_path=audit,
                beep_offset_in_clip=1.0,
                duration_seconds=meta.duration_seconds,
                width=meta.width,
                height=meta.height,
                frame_rate_num=meta.frame_rate_num,
                frame_rate_den=meta.frame_rate_den,
            )
        shooters.append(
            CompareShooterBundle(label=label, project_root=tmp_path / label, stages_by_number=stages)
        )
    return shooters


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_two_shooter_fade_keeps_the_length_and_every_track(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    shooters = _two_shooters(tmp_path, ffmpeg=ffmpeg)

    def render(name: str, transitions: tuple[composition.Transition, ...]) -> mp4_grid.GridRenderResult:
        return mp4_grid.render_grid_mp4(
            shooters,
            audio_label="Anders",
            output_path=tmp_path / f"{name}.mp4",
            canvas=mp4_grid.GridCanvas(640, 360, SYNTHETIC_FPS_NUM, SYNTHETIC_FPS_DEN),
            head_pad_seconds=0.5,
            tail_pad_seconds=1.0,
            ffmpeg_binary=ffmpeg,
            work_dir=tmp_path / name,
            transitions=transitions,
        )

    cut = render("cut", ())
    fade = render(
        "fade",
        (composition.Transition(from_stage_index=0, to_stage_index=1, kind="fade", duration_seconds=1.0),),
    )
    assert fade.degradations == () and all(stage.ok for stage in fade.stages)
    boundary = tmp_path / "fade" / "boundary-000.mov"
    assert probe_seconds(boundary, ffprobe=ffprobe) == pytest.approx(1.0, abs=FRAME)
    assert [kind for kind, _ in _streams(boundary, ffprobe=ffprobe)] == ["video", "audio", "audio", "audio"]
    assert probe_seconds(fade.output_path, ffprobe=ffprobe) == pytest.approx(
        probe_seconds(cut.output_path, ffprobe=ffprobe), abs=2 * FRAME
    )
    assert _streams(fade.output_path, ffprobe=ffprobe) == [
        ("video", "VideoHandler"),
        ("audio", "Mix"),
        ("audio", "Anders"),
        ("audio", "Mathias"),
    ]
    assert [c.start_seconds for c in fade.chapters] == [c.start_seconds for c in cut.chapters]


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_second_render_through_the_segment_cache_encodes_nothing_and_matches(tmp_path: Path) -> None:
    """The grid's segment cache: the same grid in a new temp work dir
    reuses every stage, edge and boundary, so only the stitch runs, and
    the stitched file is the first one's length with the same tracks."""
    from splitsmith.segment_cache import SegmentCache

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    shooters = _two_shooters(tmp_path, ffmpeg=ffmpeg)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)
    fade = (composition.Transition(from_stage_index=0, to_stage_index=1, kind="fade", duration_seconds=1.0),)

    def render(name: str) -> tuple[mp4_grid.GridRenderResult, list[str], list[str]]:
        stage_calls: list[str] = []
        boundary_calls: list[str] = []

        def counting(into: list[str]):  # type: ignore[no-untyped-def]
            def run(cmd, **kwargs):  # type: ignore[no-untyped-def]
                into.append(str(cmd[-1]))
                return subprocess.run(cmd, **kwargs)

            return run

        result = mp4_grid.render_grid_mp4(
            shooters,
            audio_label="Anders",
            output_path=tmp_path / f"{name}.mp4",
            canvas=mp4_grid.GridCanvas(640, 360, SYNTHETIC_FPS_NUM, SYNTHETIC_FPS_DEN),
            head_pad_seconds=0.5,
            tail_pad_seconds=1.0,
            ffmpeg_binary=ffmpeg,
            runner=counting(stage_calls),
            boundary_runner=counting(boundary_calls),
            work_dir=tmp_path / name,
            transitions=fade,
            segment_cache=cache,
        )
        return result, stage_calls, boundary_calls

    first, first_stages, first_boundaries = render("first")
    assert first.degradations == () and all(stage.ok for stage in first.stages)
    assert len(first_stages) == 3 and len(first_boundaries) == 3  # two edges and the boundary
    second, second_stages, second_boundaries = render("second")
    assert second_stages == [str(tmp_path / "second.mp4")] and second_boundaries == []
    assert second.degradations == () and all(stage.ok for stage in second.stages)
    assert probe_seconds(second.output_path, ffprobe=ffprobe) == pytest.approx(
        probe_seconds(first.output_path, ffprobe=ffprobe), abs=FRAME / 2
    )
    assert _streams(second.output_path, ffprobe=ffprobe) == _streams(first.output_path, ffprobe=ffprobe)


# --- stings (#1245): the clip reaches the boundary's pixels -------------------------


class _RedStingRasterizer:
    """Yields opaque red frames for any template, so the sting's presence
    is a colour the boundary frame either has or has not."""

    def __init__(self) -> None:
        self.frames_rendered = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        raise AssertionError("not used")

    def render_template(self, template, *, context, width: int, height: int) -> bytes:
        raise AssertionError("not used")

    def engine_version(self) -> str:
        return "red"

    def render_template_frames(self, template, *, context, width, height, fps, max_seconds):
        import math

        from splitsmith.overlay.raster import TemplateFrames

        count = max(1, math.ceil(max_seconds * fps - 1e-9))
        red = bytes((255, 0, 0, 255)) * (width * height)

        def frames():
            for _ in range(count):
                self.frames_rendered += 1
                yield red

        return TemplateFrames(
            duration=max_seconds, frame_count=count, width=width, height=height, frames=frames()
        )


def _mean_rgb(
    path: Path, *, at: float, ffmpeg: str, box: tuple[float, float, float, float] | None = None
) -> tuple[float, float, float]:
    """The mean colour of the frame ``at`` seconds into ``path``, over
    ``box`` (left, top, right, bottom as fractions of the frame) when given."""
    import io
    import subprocess

    from PIL import Image, ImageStat

    done = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-v",
            "error",
            "-ss",
            f"{at:g}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        capture_output=True,
    )
    assert done.returncode == 0, done.stderr[-2000:].decode("utf-8", "replace")
    with Image.open(io.BytesIO(done.stdout)) as image:
        rgb = image.convert("RGB")
        if box is not None:
            width, height = rgb.size
            rgb = rgb.crop(
                (int(width * box[0]), int(height * box[1]), int(width * box[2]), int(height * box[3]))
            )
        r, g, b = ImageStat.Stat(rgb).mean
    return r, g, b


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_grid_sting_is_visible_on_the_boundary_and_keeps_every_track(tmp_path: Path) -> None:
    """Issue #1245 on the grid: the red sting covers the boundary, the N+1
    tracks survive the extra video input, and the length is the cut's."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    source = tmp_path / "source.mp4"
    build_synthetic_video(source)
    shooters: list[CompareShooterBundle] = []
    for label in ("Anders", "Mathias"):
        stages: dict[int, CompareStageBundle] = {}
        for number in (1, 2):
            trim = tmp_path / f"{label}-{number}.mp4"
            cut_clip(source, trim, 120, ffmpeg=ffmpeg)
            audit = tmp_path / f"{label}-{number}.json"
            write_audit(audit, (1100, 1320))
            meta = probe_video(trim)
            stages[number] = CompareStageBundle(
                stage_number=number,
                stage_name=f"Stage {number}",
                trim_path=trim,
                audit_path=audit,
                beep_offset_in_clip=1.0,
                duration_seconds=meta.duration_seconds,
                width=meta.width,
                height=meta.height,
                frame_rate_num=meta.frame_rate_num,
                frame_rate_den=meta.frame_rate_den,
            )
        shooters.append(
            CompareShooterBundle(label=label, project_root=tmp_path / label, stages_by_number=stages)
        )

    def render(name: str, transitions: tuple[composition.Transition, ...]) -> mp4_grid.GridRenderResult:
        return mp4_grid.render_grid_mp4(
            shooters,
            audio_label="Anders",
            output_path=tmp_path / f"{name}.mp4",
            canvas=mp4_grid.GridCanvas(640, 360, SYNTHETIC_FPS_NUM, SYNTHETIC_FPS_DEN),
            head_pad_seconds=0.5,
            tail_pad_seconds=1.0,
            ffmpeg_binary=ffmpeg,
            work_dir=tmp_path / name,
            rasterizer=_RedStingRasterizer(),
            transitions=transitions,
        )

    cut = render("cut", ())
    sting = composition.Transition(
        from_stage_index=0, to_stage_index=1, kind="sting:wipe", duration_seconds=1.0
    )
    stung = render("sting", (sting,))
    assert stung.degradations == () and all(stage.ok for stage in stung.stages)
    boundary = tmp_path / "sting" / "boundary-000.mov"
    assert probe_seconds(boundary, ffprobe=ffprobe) == pytest.approx(1.0, abs=FRAME)
    assert [kind for kind, _ in _streams(boundary, ffprobe=ffprobe)] == ["video", "audio", "audio", "audio"]
    for at in (0.05, 0.5, 0.9):
        r, g, b = _mean_rgb(boundary, at=at, ffmpeg=ffmpeg)
        assert r > 200 and g < 60 and b < 60, (at, r, g, b)
    r, g, b = _mean_rgb(tmp_path / "sting" / "stage1.mov", at=1.0, ffmpeg=ffmpeg)
    assert not (r > 200 and g < 60 and b < 60), ("the stage is not red", r, g, b)
    assert probe_seconds(stung.output_path, ffprobe=ffprobe) == pytest.approx(
        probe_seconds(cut.output_path, ffprobe=ffprobe), abs=2 * FRAME
    )
    assert _streams(stung.output_path, ffprobe=ffprobe) == [
        ("video", "VideoHandler"),
        ("audio", "Mix"),
        ("audio", "Anders"),
        ("audio", "Mathias"),
    ]


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_cached_grid_sting_draws_no_frame(tmp_path: Path) -> None:
    """#1535: the grid writes its sting clip in the boundary encode's
    ``prepare``, so a second render through the same cache reuses the
    boundary without drawing a frame of the sting."""
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    shooters = _two_shooters(tmp_path, ffmpeg=ffmpeg)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)
    sting = composition.Transition(
        from_stage_index=0, to_stage_index=1, kind="sting:wipe", duration_seconds=1.0
    )

    def render(name: str) -> _RedStingRasterizer:
        raster = _RedStingRasterizer()
        result = mp4_grid.render_grid_mp4(
            shooters,
            audio_label="Anders",
            output_path=tmp_path / f"{name}.mp4",
            canvas=mp4_grid.GridCanvas(640, 360, SYNTHETIC_FPS_NUM, SYNTHETIC_FPS_DEN),
            head_pad_seconds=0.5,
            tail_pad_seconds=1.0,
            ffmpeg_binary=ffmpeg,
            work_dir=tmp_path / name,
            rasterizer=raster,
            transitions=(sting,),
            segment_cache=cache,
        )
        assert result.degradations == () and all(stage.ok for stage in result.stages)
        return raster

    assert render("first").frames_rendered == 30
    assert render("second").frames_rendered == 0, "a cached boundary draws no sting frame"
    assert not (tmp_path / "second" / "boundary-000_sting.mov").exists()


# --- frames stings (#1535): the clip replaces the crossfade -------------------------


def _colour_shooters(tmp_path: Path) -> list[CompareShooterBundle]:
    """Two shooters, each with a red stage 1 and a blue stage 2 (4 s, the
    beep at 1 s), so a boundary pixel says which edge it shows."""
    shooters: list[CompareShooterBundle] = []
    for label in ("Anders", "Mathias"):
        stages: dict[int, CompareStageBundle] = {}
        for number, colour in ((1, "red"), (2, "blue")):
            trim = build_color_video(tmp_path / f"{label}-{number}.mp4", color=colour)
            audit = tmp_path / f"{label}-{number}.json"
            write_audit(audit, (1100, 1320))
            meta = probe_video(trim)
            stages[number] = CompareStageBundle(
                stage_number=number,
                stage_name=f"Stage {number}",
                trim_path=trim,
                audit_path=audit,
                beep_offset_in_clip=1.0,
                duration_seconds=meta.duration_seconds,
                width=meta.width,
                height=meta.height,
                frame_rate_num=meta.frame_rate_num,
                frame_rate_den=meta.frame_rate_den,
            )
        shooters.append(
            CompareShooterBundle(label=label, project_root=tmp_path / label, stages_by_number=stages)
        )
    return shooters


_PUSH = composition.Transition(from_stage_index=0, to_stage_index=1, kind="sting:push", duration_seconds=1.0)


def _render_framesy(
    shooters: list[CompareShooterBundle],
    tmp_path: Path,
    name: str,
    *,
    ffmpeg: str,
    raster: PushRasterizer,
    head_pad_seconds: float = 0.5,
    tail_pad_seconds: float = 1.0,
    segment_cache: SegmentCache | None = None,
    argvs: list[list[str]] | None = None,
) -> mp4_grid.GridRenderResult:
    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if argvs is not None:
            argvs.append([str(token) for token in cmd])
        return subprocess.run(cmd, **kwargs)  # type: ignore[call-overload]

    return mp4_grid.render_grid_mp4(
        shooters,
        audio_label="Anders",
        output_path=tmp_path / f"{name}.mp4",
        canvas=mp4_grid.GridCanvas(640, 360, SYNTHETIC_FPS_NUM, SYNTHETIC_FPS_DEN),
        head_pad_seconds=head_pad_seconds,
        tail_pad_seconds=tail_pad_seconds,
        ffmpeg_binary=ffmpeg,
        work_dir=tmp_path / name,
        rasterizer=raster,
        overlay_theme="framesy",  # type: ignore[arg-type]
        transitions=(_PUSH,),
        segment_cache=segment_cache,
        boundary_runner=run,
    )


#: Both tiles' rows of the 640x360 grid (each 16:9 tile is 320x180, centred).
_TILE_ROWS = (0.3, 0.7)


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
@pytest.mark.parametrize(
    ("head_pad", "tail_pad", "head_padded"),
    [(0.5, 1.0, False), (0.9, 2.5, True)],
    ids=["head-handle", "padded"],
)
def test_a_grid_frames_sting_replaces_the_crossfade_with_its_edges(
    tmp_path: Path, monkeypatch, head_pad: float, tail_pad: float, head_padded: bool
) -> None:
    """#1535 on the grid: across the boundary the left half is the incoming
    (blue) stage, the right half the outgoing edge, the column the sting
    leaves transparent is black, and the N+1 tracks survive.

    The grid's tail pad runs past every clip (``grid_boundary_fit`` needs it
    to hold half the transition), so a grid's tail edge is always padded and
    its tiles hold their last frame there (#1647): the right half is the
    outgoing red, not black. With no
    head handle (``padded``) the head edge is padded too and its first
    frame held, so the left half is blue from the first frame."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    install_frames_look(tmp_path, monkeypatch)
    raster = PushRasterizer()
    argvs: list[list[str]] = []
    result = _render_framesy(
        _colour_shooters(tmp_path), tmp_path, "out", ffmpeg=ffmpeg, raster=raster,
        head_pad_seconds=head_pad, tail_pad_seconds=tail_pad, argvs=argvs,
    )  # fmt: skip
    assert result.degradations == () and all(stage.ok for stage in result.stages)
    assert raster.frames_drawn == 30, "one second at the grid's rate, drawn once"
    boundary = tmp_path / "out" / "boundary-000.mov"
    (argv,) = [a for a in argvs if a[-1] == str(boundary)]
    graph = argv[argv.index("-filter_complex") + 1]
    assert "apad=" in graph and ("adelay=" in graph) is head_padded, graph
    assert probe_seconds(boundary, ffprobe=ffprobe) == pytest.approx(1.0, abs=FRAME)
    assert _streams(boundary, ffprobe=ffprobe) == [
        ("video", "VideoHandler"),
        ("audio", "Mix"),
        ("audio", "Anders"),
        ("audio", "Mathias"),
    ]
    top, bottom = _TILE_ROWS
    for at in (0.05, 0.5, 0.95):
        left = _mean_rgb(boundary, at=at, ffmpeg=ffmpeg, box=(0.05, top, 0.4, bottom))
        right = _mean_rgb(boundary, at=at, ffmpeg=ffmpeg, box=(0.6, top, 0.95, bottom))
        assert left[2] > 180 and left[0] < 60, (at, left)
        # The outgoing edge, past the clips' end: their last frame, held.
        assert right[0] > 180 and right[2] < 60, (at, right)
        # What the sting leaves transparent is black, not the crossfade under it.
        gap = _mean_rgb(
            boundary, at=at, ffmpeg=ffmpeg, box=(PUSH_GAP[0] + 0.02, top, PUSH_GAP[1] - 0.02, bottom)
        )
        assert max(gap) < 30, (at, gap)


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_cached_grid_frames_sting_extracts_and_draws_nothing(tmp_path: Path, monkeypatch) -> None:
    """#1535: a cached grid boundary is reused without extracting an edge or
    drawing a frame; an edit to the template misses and draws it again."""
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    look = install_frames_look(tmp_path, monkeypatch)
    shooters = _colour_shooters(tmp_path)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)

    def render(name: str) -> PushRasterizer:
        raster = PushRasterizer()
        result = _render_framesy(shooters, tmp_path, name, ffmpeg=ffmpeg, raster=raster, segment_cache=cache)
        assert result.degradations == () and all(stage.ok for stage in result.stages)
        return raster

    first = render("a")
    assert first.frames_drawn == 30 and first.loads == 2
    assert not list(
        (tmp_path / "a" / "boundary-000_frames").rglob("*.jpg")
    ), "the edge frames are removed after the render"
    second = render("b")
    assert second.frames_drawn == 0 and second.loads == 1, "the decision-time load only"
    assert not list(
        (tmp_path / "b" / "boundary-000_frames").rglob("*.jpg")
    ), "a hit leaves no frames folder either"
    template = look.root / "push.html"
    template.write_text(template.read_text(encoding="utf-8") + "<!-- edited -->\n", encoding="utf-8")
    assert render("c").frames_drawn == 30, "a template edit misses and redraws"
    assert not list((tmp_path / "c" / "boundary-000_frames").rglob("*.jpg"))


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_grid_rendered_stage_by_stage_is_the_one_process_render(tmp_path: Path) -> None:
    """#1691: each stage on its own (``only_stage``) into one cache, then the
    whole grid from it, decodes to the same frames and samples, every stream,
    as one process rendering it all, across a fade."""
    from tests.test_proxy import _frame_digest

    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    shooters = _two_shooters(tmp_path, ffmpeg=ffmpeg)
    fade = (composition.Transition(from_stage_index=0, to_stage_index=1, kind="fade", duration_seconds=1.0),)

    def render(name: str, cache: SegmentCache, **kwargs: object) -> mp4_grid.GridRenderResult:
        return mp4_grid.render_grid_mp4(
            shooters,
            audio_label="Anders",
            output_path=tmp_path / f"{name}.mp4",
            canvas=mp4_grid.GridCanvas(640, 360, SYNTHETIC_FPS_NUM, SYNTHETIC_FPS_DEN),
            head_pad_seconds=0.5,
            tail_pad_seconds=1.0,
            ffmpeg_binary=ffmpeg,
            work_dir=tmp_path / name,
            transitions=fade,
            segment_cache=cache,
            **kwargs,
        )

    split = SegmentCache(root=tmp_path / "split-cache", max_bytes=1 << 30)
    for number in (1, 2):
        assert render(f"stage{number}", split, only_stage=number).stages[0].ok
    stitched = render("stitched", split)
    whole = render("whole", SegmentCache(root=tmp_path / "whole-cache", max_bytes=1 << 30))
    assert all(stage.ok for stage in stitched.stages)
    assert _frame_digest(stitched.output_path) == _frame_digest(whole.output_path)


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_stitch_that_reads_its_segments_over_http_matches_the_local_one(tmp_path: Path) -> None:
    """#1691: a hosted stitch names stored segments by presigned URL instead
    of pulling a 4K match's gigabytes onto a memory disk. Real ffmpeg, real
    HTTP with range requests: the same frames and samples as a local stitch."""
    import contextlib

    from tests.test_proxy import _frame_digest, _RangeServer

    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    shooters = _two_shooters(tmp_path, ffmpeg=ffmpeg)
    fade = (composition.Transition(from_stage_index=0, to_stage_index=1, kind="fade", duration_seconds=1.0),)
    servers: dict[str, str] = {}

    class _Served(SegmentCache):
        def lookup(self, key, *, suffix=".mp4", fetch=True):  # type: ignore[no-untyped-def]
            return super().lookup(key, suffix=suffix)

        def concat_source(self, path: Path) -> str:
            return servers.get(path.name, super().concat_source(path))

    def render(name: str, cache: SegmentCache) -> mp4_grid.GridRenderResult:
        return mp4_grid.render_grid_mp4(
            shooters,
            audio_label="Anders",
            output_path=tmp_path / f"{name}.mp4",
            canvas=mp4_grid.GridCanvas(640, 360, SYNTHETIC_FPS_NUM, SYNTHETIC_FPS_DEN),
            head_pad_seconds=0.5,
            tail_pad_seconds=1.0,
            ffmpeg_binary=ffmpeg,
            work_dir=tmp_path / name,
            transitions=fade,
            segment_cache=cache,
        )

    local = render("local", SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30))
    served = _Served(root=tmp_path / "cache", max_bytes=1 << 30)
    with contextlib.ExitStack() as stack:
        for segment in sorted((tmp_path / "cache").glob("*.mov")):
            servers[segment.name] = stack.enter_context(_RangeServer(segment)).url
        remote = render("remote", served)
    listed = (tmp_path / "remote" / "concat.txt").read_text(encoding="utf-8")
    assert listed.count("http://") >= 2
    assert all(stage.ok for stage in remote.stages)
    assert _frame_digest(remote.output_path) == _frame_digest(local.output_path)


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_stage_encode_reports_fractions(tmp_path: Path) -> None:
    """Each encoded stage and the stitch report how far ffmpeg has got
    (spec 2026-10-25), so a part never sits still while it works."""
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    steps: list[mp4_grid.GridRenderStep] = []
    mp4_grid.render_grid_mp4(
        _two_shooters(tmp_path, ffmpeg=ffmpeg),
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=mp4_grid.GridCanvas(640, 360, SYNTHETIC_FPS_NUM, SYNTHETIC_FPS_DEN),
        head_pad_seconds=0.5,
        tail_pad_seconds=1.0,
        ffmpeg_binary=ffmpeg,
        work_dir=tmp_path / "work",
        progress=steps.append,
    )
    encoding = [s for s in steps if s.status == "encoding" and s.fraction is not None]
    assert {s.plan.stage_number for s in encoding if s.plan is not None} == {1, 2}
    assert all(0.0 <= (s.fraction or 0.0) <= 1.0 for s in encoding)
    assert any(s.status == "stitching" and s.fraction == 1.0 for s in steps)
