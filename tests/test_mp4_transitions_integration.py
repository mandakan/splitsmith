"""A real ffmpeg render of a 1 s fade between two synthetic stages (#1244):
the boundary file is d long and the stitched file keeps the cut's length.
The unit tests compare declared durations; this one probes the files."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from splitsmith import composition, mp4_render
from splitsmith.audit_data import audit_shots_to_engine_shots, read_audit_data
from splitsmith.fcpxml_gen import StageComposition, probe_video
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


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_fade_keeps_the_timeline_length_in_the_real_file(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    source = tmp_path / "source.mp4"
    build_synthetic_video(source)
    stages: list[StageComposition] = []
    for number in (1, 2):
        trim = tmp_path / f"stage{number}.mp4"
        cut_clip(source, trim, 120, ffmpeg=ffmpeg)  # 4 s, beep at 1 s
        audit = tmp_path / f"stage{number}.json"
        write_audit(audit, (1100, 1320))
        stages.append(
            StageComposition(
                stage_name=f"Stage {number}",
                video_path=trim,
                video=probe_video(trim),
                shots=audit_shots_to_engine_shots(read_audit_data(audit), beep_time_in_source=0.0),
                beep_offset_seconds=1.0,
                head_pad_seconds=0.5,
                tail_pad_seconds=1.0,
            )
        )

    def render(name: str, transitions: tuple[composition.Transition, ...]) -> mp4_render.Mp4RenderResult:
        comp = composition.from_stage_compositions(stages, project_name="m", transitions=transitions)
        return mp4_render.render_mp4(
            comp, output_path=tmp_path / f"{name}.mp4", work_dir=tmp_path / name, ffmpeg_binary=ffmpeg
        )

    cut = render("cut", ())
    fade = render(
        "fade",
        (composition.Transition(from_stage_index=0, to_stage_index=1, kind="fade", duration_seconds=1.0),),
    )
    assert fade.degradations == ()
    assert probe_seconds(tmp_path / "fade" / "boundary_000.mp4", ffprobe=ffprobe) == pytest.approx(
        1.0, abs=FRAME
    )
    assert probe_seconds(fade.output_path, ffprobe=ffprobe) == pytest.approx(
        probe_seconds(cut.output_path, ffprobe=ffprobe), abs=2 * FRAME
    )
    assert fade.duration_seconds == pytest.approx(cut.duration_seconds)


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
    path: Path, *, at: float, ffmpeg: str, box: tuple[float, float] | None = None
) -> tuple[float, float, float]:
    """The mean colour of the frame ``at`` seconds into ``path``, over the
    horizontal span ``box`` (fractions of the width) when given."""
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
            rgb = rgb.crop((int(width * box[0]), 0, int(width * box[1]), height))
        r, g, b = ImageStat.Stat(rgb).mean
    return r, g, b


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_sting_is_visible_on_the_boundary_and_nowhere_else(tmp_path: Path) -> None:
    """Issue #1245: a sting whose every frame is opaque red paints the
    boundary red for its whole length; the stages keep their own picture
    and the stitched file keeps the cut's length."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    source = tmp_path / "source.mp4"
    build_synthetic_video(source)
    stages: list[StageComposition] = []
    for number in (1, 2):
        trim = tmp_path / f"stage{number}.mp4"
        cut_clip(source, trim, 120, ffmpeg=ffmpeg)
        audit = tmp_path / f"stage{number}.json"
        write_audit(audit, (1100, 1320))
        stages.append(
            StageComposition(
                stage_name=f"Stage {number}",
                video_path=trim,
                video=probe_video(trim),
                shots=audit_shots_to_engine_shots(read_audit_data(audit), beep_time_in_source=0.0),
                beep_offset_seconds=1.0,
                head_pad_seconds=0.5,
                tail_pad_seconds=1.0,
            )
        )

    def render(name: str, transitions: tuple[composition.Transition, ...]) -> mp4_render.Mp4RenderResult:
        comp = composition.from_stage_compositions(stages, project_name="m", transitions=transitions)
        return mp4_render.render_mp4(
            comp,
            output_path=tmp_path / f"{name}.mp4",
            work_dir=tmp_path / name,
            ffmpeg_binary=ffmpeg,
            rasterizer=_RedStingRasterizer(),
        )

    cut = render("cut", ())
    sting = composition.Transition(
        from_stage_index=0, to_stage_index=1, kind="sting:wipe", duration_seconds=1.0
    )
    stung = render("sting", (sting,))
    assert stung.degradations == ()
    boundary = tmp_path / "sting" / "boundary_000.mp4"
    assert probe_seconds(boundary, ffprobe=ffprobe) == pytest.approx(1.0, abs=FRAME)
    for at in (0.05, 0.5, 0.9):
        r, g, b = _mean_rgb(boundary, at=at, ffmpeg=ffmpeg)
        assert r > 200 and g < 60 and b < 60, (at, r, g, b)
    r, g, b = _mean_rgb(tmp_path / "sting" / "stage_000.mp4", at=1.0, ffmpeg=ffmpeg)
    assert not (r > 200 and g < 60 and b < 60), ("the stage is not red", r, g, b)
    assert probe_seconds(stung.output_path, ffprobe=ffprobe) == pytest.approx(
        probe_seconds(cut.output_path, ffprobe=ffprobe), abs=2 * FRAME
    )


# --- frames stings (#1535): the clip replaces the crossfade -------------------------


def _colour_stages(
    tmp_path: Path, *, head_pad_seconds: float = 0.5, tail_pad_seconds: float = 1.0
) -> list[StageComposition]:
    """A red stage then a blue one, so a boundary pixel says which edge it
    shows. The default pads leave both edges a full handle; a longer pad
    on the 4 s clips leaves none, so the boundary pads both edges."""
    stages = []
    for number, colour in ((1, "red"), (2, "blue")):
        trim = build_color_video(tmp_path / f"stage{number}.mp4", color=colour)
        audit = tmp_path / f"stage{number}.json"
        write_audit(audit, (1100, 1320))
        stages.append(
            StageComposition(
                stage_name=f"Stage {number}",
                video_path=trim,
                video=probe_video(trim),
                shots=audit_shots_to_engine_shots(read_audit_data(audit), beep_time_in_source=0.0),
                beep_offset_seconds=1.0,
                head_pad_seconds=head_pad_seconds,
                tail_pad_seconds=tail_pad_seconds,
            )
        )
    return stages


_PUSH = composition.Transition(from_stage_index=0, to_stage_index=1, kind="sting:push", duration_seconds=1.0)


def _render_framesy(
    stages: list[StageComposition],
    tmp_path: Path,
    name: str,
    *,
    ffmpeg: str,
    raster: PushRasterizer,
    segment_cache: SegmentCache | None = None,
    argvs: list[list[str]] | None = None,
) -> mp4_render.Mp4RenderResult:
    comp = composition.from_stage_compositions(stages, project_name="m", transitions=(_PUSH,))

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if argvs is not None:
            argvs.append([str(token) for token in cmd])
        return subprocess.run(cmd, **kwargs)

    return mp4_render.render_mp4(
        comp,
        output_path=tmp_path / f"{name}.mp4",
        work_dir=tmp_path / name,
        ffmpeg_binary=ffmpeg,
        rasterizer=raster,
        overlay_theme="framesy",
        segment_cache=segment_cache,
        runner=run,
    )


#: The edges a frames boundary is drawn from: a full handle at both ends,
#: and the default-config shape with none, where the boundary pads both.
_EDGE_CASES = pytest.mark.parametrize(
    ("head_pad", "tail_pad", "padded"), [(0.5, 1.0, False), (0.9, 2.5, True)], ids=["handles", "padded"]
)


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
@_EDGE_CASES
def test_a_frames_sting_replaces_the_crossfade_with_its_edges(
    tmp_path: Path, monkeypatch, head_pad: float, tail_pad: float, padded: bool
) -> None:
    """#1535: across the boundary the left half is the incoming (blue)
    stage, the right half the outgoing (red) one; a fade would be purple
    everywhere. A padded edge holds its frame, so the halves keep their
    colour from the first frame to the last."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    install_frames_look(tmp_path, monkeypatch)
    raster = PushRasterizer()
    argvs: list[list[str]] = []
    stages = _colour_stages(tmp_path, head_pad_seconds=head_pad, tail_pad_seconds=tail_pad)
    result = _render_framesy(stages, tmp_path, "out", ffmpeg=ffmpeg, raster=raster, argvs=argvs)
    assert result.degradations == ()
    assert raster.frames_drawn == 30, "one second at the sequence rate, drawn once"
    boundary = tmp_path / "out" / "boundary_000.mp4"
    (argv,) = [a for a in argvs if a[-1] == str(boundary)]
    graph = argv[argv.index("-filter_complex") + 1]
    assert ("apad=" in graph and "adelay=" in graph) is padded, graph
    assert probe_seconds(boundary, ffprobe=ffprobe) == pytest.approx(1.0, abs=FRAME)
    for at in (0.05, 0.5, 0.95):
        left = _mean_rgb(boundary, at=at, ffmpeg=ffmpeg, box=(0.05, 0.45))
        right = _mean_rgb(boundary, at=at, ffmpeg=ffmpeg, box=(0.55, 0.95))
        assert left[2] > 180 and left[0] < 60, (at, left)
        assert right[0] > 180 and right[2] < 60, (at, right)
        # What the sting leaves transparent is black, not the crossfade under it.
        gap = _mean_rgb(boundary, at=at, ffmpeg=ffmpeg, box=(PUSH_GAP[0] + 0.02, PUSH_GAP[1] - 0.02))
        assert max(gap) < 30, (at, gap)
    assert [entry.cues for entry in result.timeline if entry.kind == "transition"] == [raster.sounds]


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_cached_frames_sting_extracts_and_draws_nothing(tmp_path: Path, monkeypatch) -> None:
    """#1535: a cached boundary is reused without extracting an edge or
    drawing a frame (only the decision-time load runs, for the cues); an
    edit to the template misses and draws it again."""
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    look = install_frames_look(tmp_path, monkeypatch)
    stages = _colour_stages(tmp_path)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=1 << 30)

    def render(name: str) -> tuple[PushRasterizer, mp4_render.Mp4RenderResult]:
        raster = PushRasterizer()
        result = _render_framesy(stages, tmp_path, name, ffmpeg=ffmpeg, raster=raster, segment_cache=cache)
        assert result.degradations == ()
        return raster, result

    first, _ = render("a")
    assert first.frames_drawn == 30 and first.loads == 2
    assert (tmp_path / "a" / "boundary_000_frames").is_dir()

    second, result = render("b")
    assert second.frames_drawn == 0 and second.loads == 1, "the decision-time load only"
    assert not (tmp_path / "b" / "boundary_000_frames").exists(), "a hit extracts nothing"
    assert [entry.cues for entry in result.timeline if entry.kind == "transition"] == [second.sounds]

    template = look.root / "push.html"
    template.write_text(template.read_text(encoding="utf-8") + "<!-- edited -->\n", encoding="utf-8")
    third, _ = render("c")
    assert third.frames_drawn == 30, "a template edit misses and redraws"
    assert (tmp_path / "c" / "boundary_000_frames").is_dir()
