import subprocess
from pathlib import Path

import pytest

from splitsmith.composition import SequenceFormat
from splitsmith.config import VideoMetadata
from splitsmith.reel_plan import FrameRef, ReelCard, ReelClip
from splitsmith.reel_render import (
    SFX_STEMS,
    card_command,
    clip_command,
    frame_command,
    reel_format,
    render_reel,
    sfx_cues,
    sfx_mix_command,
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
    clip = ReelClip("002_cold", Path("/m/a.mp4"), 7.4, 1.2, speed=0.5, freeze_seconds=0.4, sfx=("slowmo",))
    argv = clip_command(clip, sequence=UP, output_path=Path("/w/out.mp4"))
    graph = argv[argv.index("-filter_complex") + 1]
    assert "crop='min(iw,ih*0.5625)':'min(ih,iw/0.5625)'" in graph
    assert "scale=1080:1920" in graph
    assert "setpts=(PTS-STARTPTS)/0.5" in graph
    assert "fps=30/1" in graph
    assert "tpad=stop_mode=clone:stop_duration=0.4" in graph
    assert argv[argv.index("-ss") + 1] == "7.4" and argv[argv.index("-t") + 1] == "1.2"
    assert "anullsrc" in " ".join(argv)  # slowed: silent, the stem plays over it
    assert argv.count("/m/a.mp4") == 1


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


def test_a_card_is_silent_unless_it_is_the_timer_beep():
    def audio(sfx):
        argv = card_command(
            Path("/w/bg.png"),
            Path("/w/card.mov"),
            seconds=0.7,
            sfx=sfx,
            sequence=UP,
            output_path=Path("/w/o.mp4"),
        )
        assert "/w/card.mov" in argv and argv[argv.index("-t") + 1] == "0.7"
        return " ".join(argv)

    assert "sine=frequency=3000" in audio(("beep",))
    assert "anullsrc" in audio(("impact",)) and "sine" not in audio(("impact",))


def test_every_stem_ships_and_names_its_hit():
    for kind, (path, hit) in SFX_STEMS.items():
        assert path.is_file(), kind
        assert hit >= 0


def test_cues_sit_at_each_items_start_on_the_stitched_timeline():
    items = (
        ReelCard("001_beep", "slam", 0.6, "", sfx=("beep",)),
        ReelClip("002_cold", Path("/m/a.mp4"), 0.0, 1.0, speed=0.5, sfx=("slowmo",)),
        ReelCard("003_title", "title", 1.6, "T", sfx=("riser", "braam")),
        ReelClip("004_beat", Path("/m/a.mp4"), 0.0, 1.2),
    )
    # The beep is drawn in its card; every other kind is a stem cue.
    assert sfx_cues(items) == (("slowmo", 0.6), ("riser", 2.6), ("braam", 2.6))


def test_mix_places_each_stems_hit_on_its_cue():
    cues = (("braam", 2.6), ("riser", 2.6), ("impact", 0.5))
    argv = sfx_mix_command(Path("/w/stitched.mp4"), cues, output_path=Path("/w/reel.mp4"))
    graph = argv[argv.index("-filter_complex") + 1]
    # braam hits 1.2 s in: starts 1.4 s into the reel.
    assert "[1:a]adelay=1400|1400" in graph
    # riser ends (2.2 s) on the cue: starts at 0.4 s.
    assert "[2:a]adelay=400|400" in graph
    # impact's hit at 1.2 s is before its 0.5 s cue: its first 0.7 s is cut.
    assert "[3:a]atrim=start=0.7,asetpts=PTS-STARTPTS" in graph
    assert "amix=inputs=4:normalize=0:duration=first" in graph
    assert argv[argv.index("-c:v") + 1] == "copy"


def test_no_cues_is_no_mix():
    assert sfx_mix_command(Path("/w/s.mp4"), (), output_path=Path("/w/r.mp4")) is None


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
        ReelCard("001_title", "title", 1.6, "Bromma", lines=("Anna",), sfx=("braam",)),
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
    assert len(calls) == 6  # title card, clip, frame grab, callout card, stitch, sfx mix
    assert "concat" in calls[-2]
    assert "amix" in " ".join(calls[-1]) and calls[-1][-1] == str(tmp_path / "reel.mp4")
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
    for i, (source, speed, sfx) in enumerate([(src, 1.0, ()), (fast, 0.5, ("slowmo",))]):
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

    # The shipped stems decode and mix over it without changing its length.
    mixed = tmp_path / "mixed.mp4"
    cues = (("slowmo", 1.3), ("braam", 0.2), ("sting", 3.0), ("impact", 0.0))
    proc = subprocess.run(sfx_mix_command(final, cues, output_path=mixed), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert _probe_seconds(mixed) == pytest.approx(_probe_seconds(final), abs=0.05)
