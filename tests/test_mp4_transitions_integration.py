"""A real ffmpeg render of a 1 s fade between two synthetic stages (#1244):
the boundary file is d long and the stitched file keeps the cut's length.
The unit tests compare declared durations; this one probes the files."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from splitsmith import composition, mp4_render
from splitsmith.audit_data import audit_shots_to_engine_shots, read_audit_data
from splitsmith.fcpxml_gen import StageComposition, probe_video
from tests.compare_fixture import cut_clip, probe_seconds, write_audit
from tests.synthetic_media import (
    SYNTHETIC_FPS_DEN,
    SYNTHETIC_FPS_NUM,
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
