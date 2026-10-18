import subprocess
from pathlib import Path

import pytest

from splitsmith.composition import SequenceFormat
from splitsmith.config import VideoMetadata
from splitsmith.reel_plan import FrameRef, ReelCard, ReelClip
from splitsmith.reel_render import (
    card_command,
    clip_command,
    frame_command,
    reel_format,
    render_reel,
    sfx_source,
)
from tests.synthetic_media import build_synthetic_video, ffmpeg_available

UP = SequenceFormat(1080, 1920, 30, 1)


def _meta(w=1920, h=1080, num=30, den=1) -> VideoMetadata:
    return VideoMetadata(width=w, height=h, duration_seconds=60.0, frame_rate_num=num, frame_rate_den=den)


def test_reel_format_upright_and_landscape_and_720p_fallback():
    assert reel_format([_meta()], upright=True) == SequenceFormat(1080, 1920, 30, 1)
    assert reel_format([_meta()], upright=False) == SequenceFormat(1920, 1080, 30, 1)
    assert reel_format([_meta(1280, 720)], upright=True) == SequenceFormat(720, 1280, 30, 1)


def test_reel_format_takes_the_most_common_rate():
    metas = [_meta(num=60), _meta(num=30), _meta(num=60)]
    assert reel_format(metas, upright=True).frame_rate_num == 60


def test_clip_command_crops_scales_conforms_and_slows():
    clip = ReelClip("002_cold", Path("/m/a.mp4"), 7.4, 1.2, speed=0.5, freeze_seconds=0.4, sfx="riser")
    argv = clip_command(clip, sequence=UP, output_path=Path("/w/out.mp4"))
    graph = argv[argv.index("-filter_complex") + 1]
    assert "crop='min(iw,ih*0.5625)':'min(ih,iw/0.5625)'" in graph
    assert "scale=1080:1920" in graph
    assert "setpts=(PTS-STARTPTS)/0.5" in graph
    assert "fps=30/1" in graph
    assert "tpad=stop_mode=clone:stop_duration=0.4" in graph
    assert argv[argv.index("-ss") + 1] == "7.4" and argv[argv.index("-t") + 1] == "1.2"
    assert "aevalsrc" in " ".join(argv)  # the riser, not the source audio


def test_clip_at_real_speed_uses_the_unseeked_source_audio():
    clip = ReelClip("005_beat", Path("/m/a.mp4"), 7.4, 1.2)
    argv = clip_command(clip, sequence=UP, output_path=Path("/w/out.mp4"))
    graph = argv[argv.index("-filter_complex") + 1]
    assert "[1:a]atrim=start=7.4:duration=1.2" in graph
    assert argv.count("/m/a.mp4") == 2


def test_clip_without_source_audio_is_silent():
    clip = ReelClip("005_beat", Path("/m/a.mp4"), 7.4, 1.2)
    argv = clip_command(clip, sequence=UP, output_path=Path("/w/out.mp4"), source_audio=False)
    assert "anullsrc" in " ".join(argv)


@pytest.mark.parametrize("kind", ["beep", "impact", "riser", "whoosh", None])
def test_sfx_source_is_a_lavfi_graph(kind):
    src = sfx_source(kind, 0.7)
    assert "48000" in src
    assert ("anullsrc" in src) == (kind is None)


def test_card_command_with_motion_clip_and_sfx():
    argv = card_command(
        Path("/w/bg.png"),
        Path("/w/card.mov"),
        seconds=0.7,
        sfx="impact",
        sequence=UP,
        output_path=Path("/w/o.mp4"),
    )
    assert "/w/card.mov" in argv and "aevalsrc" in " ".join(argv)
    assert argv[argv.index("-t") + 1] == "0.7"


def test_frame_command_grabs_one_cropped_frame():
    argv = frame_command(FrameRef(Path("/m/a.mp4"), 8.0), sequence=UP, output_path=Path("/w/f.png"))
    assert argv[argv.index("-ss") + 1] == "8"
    assert "-frames:v" in argv and "scale=1080:1920" in " ".join(argv)


class _FakeFrames:
    duration = 0.0

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.frames = [bytes(width * height * 4)]

    def close(self) -> None:
        pass


class _FakeRaster:
    """Stands in for Chromium: every card is a one-frame still."""

    def engine_version(self) -> str:
        return "fake"

    def render_template_frames(self, template, *, context, width, height, fps, max_seconds):
        return _FakeFrames(width, height)


def test_render_reel_encodes_every_item_then_stitches(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def runner(cmd, **kw):
        calls.append(list(cmd))
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix == ".png":
            from PIL import Image

            Image.new("RGB", (UP.width, UP.height)).save(out)
        else:
            out.write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("splitsmith.reel_render._has_audio_stream", lambda p: True)
    items = (
        ReelCard("001_title", "title", 1.6, "Bromma", lines=("Anna",), sfx="impact"),
        ReelClip("002_beat", Path("/m/a.mp4"), 7.4, 1.2),
        ReelCard(
            "003_callout",
            "callout",
            1.2,
            "FASTEST SPLIT",
            figure="0.19",
            unit="SEC",
            backdrop=FrameRef(Path("/m/a.mp4"), 8.0),
        ),
    )
    result = render_reel(
        items,
        sequence=UP,
        output_path=tmp_path / "reel.mp4",
        work_dir=tmp_path / "w",
        runner=runner,
        rasterizer=_FakeRaster(),
    )
    assert len(calls) == 5  # title card, clip, frame grab, callout card, stitch
    assert "concat" in calls[-1]
    assert result.duration_seconds == pytest.approx(1.6 + 1.2 + 1.2)
    assert result.degradations == ()


def test_a_failed_frame_grab_holds_the_callout_on_black(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def runner(cmd, **kw):
        calls.append(list(cmd))
        out = Path(cmd[-1])
        if out.suffix != ".png":  # the grab past the clip's end writes nothing
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("splitsmith.reel_render._has_audio_stream", lambda p: True)
    card = ReelCard("001_callout", "callout", 1.2, "FASTEST SPLIT", backdrop=FrameRef(Path("/m/a.mp4"), 99.0))
    result = render_reel(
        (card,),
        sequence=UP,
        output_path=tmp_path / "reel.mp4",
        work_dir=tmp_path / "w",
        runner=runner,
        rasterizer=_FakeRaster(),
    )
    assert result.duration_seconds == pytest.approx(1.2)
    assert any("001_callout" in d for d in result.degradations)


def _probe_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


@pytest.mark.integration
def test_clips_at_different_rates_stitch(tmp_path):
    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    src = build_synthetic_video(tmp_path / "src.mp4")
    fast = tmp_path / "src60.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-r", "60", str(fast)], check=True)
    outs = []
    for i, (source, speed, sfx) in enumerate([(src, 1.0, None), (fast, 0.5, "riser")]):
        out = tmp_path / f"seg{i}.mp4"
        clip = ReelClip(f"c{i}", source, 0.5, 1.0, speed=speed, freeze_seconds=0.3, sfx=sfx)
        proc = subprocess.run(
            clip_command(clip, sequence=UP, output_path=out), capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert _probe_seconds(out) == pytest.approx(clip.out_seconds, abs=0.07)
        outs.append(out)
    listing = tmp_path / "list.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in outs))
    final = tmp_path / "reel.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            str(final),
        ],
        check=True,
    )
    assert _probe_seconds(final) == pytest.approx(1.3 + 2.3, abs=0.15)
