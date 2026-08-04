"""Tests for the render driver, plus the ffmpeg runs behind it.

Two kinds live here. The ``render_grid_mp4`` driver tests inject a fake
runner and never shell out, per CLAUDE.md. The rest are marked
``integration`` and skipped without an ffmpeg on PATH, because asserting
on the argument tuple cannot see whether
the graph does what it says. Two defects found while building the
command layer -- the head pad being swallowed when ``setpts`` ran before
``tpad``, and every segment's video ending a tail pad short of its audio
-- both passed a green string-matching suite and were only visible in
the rendered file.
"""

import array
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from splitsmith.compare import mp4_grid
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

#: Applied per-test rather than module-wide: the driver tests below run
#: everywhere, and marking them ``integration`` would skip them exactly
#: where they are needed most -- a machine with no ffmpeg.
integration = pytest.mark.integration
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="needs a real ffmpeg on PATH")
needs_ffprobe = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None, reason="needs a real ffmpeg and ffprobe on PATH"
)

CANVAS = mp4_grid.GridCanvas(width=640, height=360, frame_rate_num=30, frame_rate_den=1)
FRAME_SECONDS = 1 / 30
STAGE_SECONDS = 4.0


def _source(path: Path, *, seconds: float, color: str, fps: str = "30") -> Path:
    """A solid-colour clip with a tone, so both streams are measurable."""
    cmd = [
        FFMPEG, "-hide_banner", "-y",
        "-f", "lavfi", "-t", str(seconds), "-i", f"color=c={color}:s=320x240:r={fps}",
        "-f", "lavfi", "-t", str(seconds), "-i", "sine=frequency=440:sample_rate=48000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ]  # fmt: skip
    done = subprocess.run(cmd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    return path


def _stream_seconds(path: Path, spec: str) -> float:
    """Decode one stream to nowhere and read the timestamp it ends on."""
    done = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path), "-map", spec, "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    stamps = re.findall(r"time=(\d+):(\d+):(\d+\.\d+)", done.stderr)
    assert stamps, done.stderr[-2000:]
    hours, minutes, seconds = stamps[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _patch_colour(path: Path, *, at: float, x: int, y: int) -> tuple[int, int, int]:
    """Mean RGB of an 8x8 patch, for telling black pad from real footage."""
    done = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-v", "error", "-ss", str(at), "-i", str(path),
            "-vf", f"crop=8:8:{x}:{y}", "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],  # fmt: skip
        capture_output=True,
    )
    raw = done.stdout
    assert len(raw) == 8 * 8 * 3, done.stderr[-2000:]
    return tuple(round(sum(raw[i::3]) / len(raw[i::3])) for i in range(3))  # type: ignore[return-value]


def _tile(label: str, trim: Path | None, *, col: int, lead_pad: float = 0.0) -> mp4_grid.GridTile:
    return mp4_grid.GridTile(
        label=label,
        trim_path=trim,
        beep_offset_in_clip=0.0,
        seek_seconds=0.0,
        lead_pad_seconds=lead_pad,
        row=0,
        col=col,
    )


def _render(
    tmp_path: Path,
    tiles: tuple[mp4_grid.GridTile, ...],
    name: str,
    *,
    rows: int | None = None,
    cols: int | None = None,
    canvas: mp4_grid.GridCanvas = CANVAS,
) -> Path:
    plan = mp4_grid.GridStagePlan(
        stage_number=1,
        stage_name="Stage 1",
        tiles=tiles,
        duration_seconds=STAGE_SECONDS,
        audio_label=tiles[-1].label,
        rows=rows if rows is not None else 1,
        cols=cols if cols is not None else len(tiles),
    )
    out = tmp_path / name
    cmd = mp4_grid.build_stage_command(plan, canvas=canvas, output_path=out, ffmpeg_binary=FFMPEG)
    done = subprocess.run(list(cmd), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-3000:]
    return out


def _video_size(path: Path) -> tuple[int, int]:
    """``(width, height)`` of the file's video stream, per ffmpeg itself."""
    done = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    found = re.search(r"Video:.*?, (\d+)x(\d+)", done.stderr)
    assert found, done.stderr[-2000:]
    return int(found.group(1)), int(found.group(2))


def _grid_tile(label: str, trim: Path | None, *, row: int, col: int) -> mp4_grid.GridTile:
    return mp4_grid.GridTile(
        label=label,
        trim_path=trim,
        beep_offset_in_clip=0.0,
        seek_seconds=0.0,
        lead_pad_seconds=0.0,
        row=row,
        col=col,
    )


@integration
@needs_ffmpeg
def test_a_stage_whose_footage_runs_out_still_fills_the_whole_duration(tmp_path: Path):
    # Every tile's content is head_pad + its post-beep span, while the
    # stage runs head_pad + the longest post-beep span + tail_pad -- so
    # the longest tile is always exactly a tail pad short. Without a tail
    # pad on the video the segment's video ends before its audio, and
    # concat -c copy carries that gap into every later stage.
    tiles = (
        _tile("Short", _source(tmp_path / "a.mp4", seconds=2.0, color="red"), col=0),
        _tile("Alsoshort", _source(tmp_path / "b.mp4", seconds=2.5, color="blue"), col=1),
    )
    out = _render(tmp_path, tiles, "short_footage.mp4")

    video = _stream_seconds(out, "0:v:0")
    audio = [_stream_seconds(out, f"0:a:{slot}") for slot in range(len(tiles))]

    assert video == pytest.approx(STAGE_SECONDS, abs=2 * FRAME_SECONDS)
    for track in audio:
        assert track == pytest.approx(STAGE_SECONDS, abs=2 * FRAME_SECONDS)
    for track in audio:
        assert abs(video - track) <= 2 * FRAME_SECONDS


@integration
@needs_ffmpeg
def test_the_tail_pad_is_black_and_does_not_disturb_the_head_pad(tmp_path: Path):
    # A start tpad and a stop tpad in one chain is exactly the kind of
    # interaction that looks fine and is not: the head pad is what keeps
    # a clamped tile's beep on the grid timeline.
    tiles = (
        _tile("Padded", _source(tmp_path / "a.mp4", seconds=2.0, color="red"), col=0, lead_pad=0.5),
        _tile("Plain", _source(tmp_path / "b.mp4", seconds=2.0, color="blue"), col=1),
    )
    out = _render(tmp_path, tiles, "both_pads.mp4")

    cell_w, cell_h = CANVAS.width // 2, CANVAS.height // 2
    centre_x, centre_y = cell_w // 2, cell_h // 2

    # Head pad: black for the first 0.5s of the padded tile only.
    assert _patch_colour(out, at=0.1, x=centre_x, y=centre_y) == (0, 0, 0)
    assert _patch_colour(out, at=0.4, x=centre_x, y=centre_y) == (0, 0, 0)
    assert _patch_colour(out, at=0.1, x=cell_w + centre_x, y=centre_y)[2] > 200  # blue, unpadded
    # Its footage is showing once the pad is spent.
    assert _patch_colour(out, at=1.0, x=centre_x, y=centre_y)[0] > 200  # red

    # Tail pad: both clips are spent well before the stage ends.
    assert _patch_colour(out, at=3.5, x=centre_x, y=centre_y) == (0, 0, 0)
    assert _patch_colour(out, at=3.5, x=cell_w + centre_x, y=centre_y) == (0, 0, 0)

    assert _stream_seconds(out, "0:v:0") == pytest.approx(STAGE_SECONDS, abs=2 * FRAME_SECONDS)


@integration
@needs_ffmpeg
def test_a_cell_no_shooter_reaches_renders_black_not_raw_frame_buffer(tmp_path: Path):
    # Three shooters fill a 2x2 three quarters of the way. xstack's
    # default fill=none leaves the fourth quadrant as whatever the frame
    # buffer held -- YUV(0,0,0), which is RGB(0,135,0): bright green, at
    # every timestamp. Only a roster that leaves a cell empty can see
    # this, which is why 2- and 4-shooter fixtures never did.
    tiles = (
        _grid_tile("A", _source(tmp_path / "a.mp4", seconds=2.0, color="red"), row=0, col=0),
        _grid_tile("B", _source(tmp_path / "b.mp4", seconds=2.0, color="blue"), row=0, col=1),
        _grid_tile("C", _source(tmp_path / "c.mp4", seconds=2.0, color="yellow"), row=1, col=0),
    )
    out = _render(tmp_path, tiles, "three_up.mp4", rows=2, cols=2)

    cell_w, cell_h = CANVAS.width // 2, CANVAS.height // 2
    for at in (0.1, 1.0, 3.5):
        assert _patch_colour(out, at=at, x=cell_w + cell_w // 2, y=cell_h + cell_h // 2) == (0, 0, 0)
    # The three real tiles are still where they belong.
    assert _patch_colour(out, at=1.0, x=cell_w // 2, y=cell_h // 2)[0] > 200  # red
    assert _patch_colour(out, at=1.0, x=cell_w + cell_w // 2, y=cell_h // 2)[2] > 200  # blue
    # The empty cell is not a shooter: three shooter tracks plus the mix,
    # not four. An empty cell that grew a track would also drag a silent
    # input into the mix and cost the three real tiles a quarter of their
    # level.
    assert len(_audio_streams(out)) == 4
    assert _video_size(out) == (CANVAS.width, CANVAS.height)


@integration
@needs_ffmpeg
def test_a_partly_filled_grid_keeps_the_whole_canvas(tmp_path: Path):
    # Six shooters in a 3x3. With the bottom row unfilled xstack's
    # extents are two rows tall and the render silently comes out below
    # the canvas the caller asked for.
    canvas = mp4_grid.GridCanvas(width=960, height=540, frame_rate_num=30, frame_rate_den=1)
    clips = {
        colour: _source(tmp_path / f"{colour}.mp4", seconds=2.0, color=colour)
        for colour in ("red", "blue", "yellow", "green", "white", "gray")
    }
    tiles = tuple(
        _grid_tile(f"S{index}", clip, row=index // 3, col=index % 3)
        for index, clip in enumerate(clips.values())
    )
    out = _render(tmp_path, tiles, "six_up.mp4", rows=3, cols=3, canvas=canvas)

    assert _video_size(out) == (960, 540)
    cell_w, cell_h = 320, 180
    for col in range(3):
        assert _patch_colour(out, at=1.0, x=col * cell_w + 4, y=2 * cell_h + cell_h // 2) == (0, 0, 0)
    assert len(_audio_streams(out)) == 7  # six shooters plus the mix


# --- the merged track -----------------------------------------------------
#
# Track 1 is a mix of every shooter, because YouTube, browser ``<video>``
# and every social embed play audio stream 0 and nothing else -- so a grid
# that ships only per-shooter tracks plays exactly one shooter for anyone
# it is sent to.
#
# Counting tracks cannot see whether the mix is *of* anything. A mix that
# silently dropped a shooter, or that carried the same shooter four times,
# has the right stream count, the right duration and the right
# disposition. So the shooters are given distinct tones and the finished
# mix is asked, per frequency, whether it contains each of them.

#: One tone per tile, far enough apart that a 40 Hz bandpass separates
#: them cleanly. Well inside the band a 48 kHz stream carries.
TONES = (220, 440, 880, 1760)

#: A frequency no tile emits. Without it the presence assertions would
#: pass against a mix of broadband noise, or against a bandpass that had
#: stopped filtering.
ABSENT_TONE = 3520

#: A tone is "in the mix" above this and "not in the mix" below
#: :data:`TONE_ABSENT_CEILING`. Measured on ffmpeg 6.1.1: a present tone
#: lands at -42.2 dB and an absent one at -91.0 dB, so the gap this has to
#: resolve is nearly 50 dB wide.
TONE_PRESENT_FLOOR = -55.0
TONE_ABSENT_CEILING = -60.0


def _tone_source(path: Path, *, frequency: int, seconds: float = 4.0) -> Path:
    """A clip whose audio is one steady tone, so the mix can be told apart."""
    cmd = [
        FFMPEG, "-hide_banner", "-y",
        "-f", "lavfi", "-t", str(seconds), "-i", "color=c=red:s=160x120:r=30",
        "-f", "lavfi", "-t", str(seconds), "-i",
        f"sine=frequency={frequency}:sample_rate=48000,volume=0.5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-shortest", str(path),
    ]  # fmt: skip
    done = subprocess.run(cmd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    return path


def _mean_volume_db(path: Path, slot: int, *, bandpass: int | None = None) -> float:
    """RMS of one audio track in dBFS, optionally through a narrow bandpass.

    The bandpass is applied three times over. One biquad rejects a tone an
    octave away by only ~24 dB, which is not enough to tell "this shooter
    is in the mix" from "the shooter next to him is loud" -- measured on
    ffmpeg 6.1.1, a 220 Hz track read through a single 440 Hz bandpass
    came back at -54.5 dB against a -42.2 dB genuine hit. Three in series
    put the same reading at -90.3.
    """
    chain = [] if bandpass is None else [f"bandpass=f={bandpass}:width_type=h:w=40"] * 3
    done = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-i", str(path), "-map", f"0:a:{slot}",
            "-af", ",".join(chain + ["volumedetect"]), "-f", "null", "-",
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    found = re.search(r"mean_volume: (-?[\d.]+) dB", done.stderr)
    assert found, done.stderr[-3000:]
    return float(found.group(1))


def _max_volume_db(path: Path, slot: int) -> float:
    done = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-i", str(path), "-map", f"0:a:{slot}",
            "-af", "volumedetect", "-f", "null", "-",
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    found = re.search(r"max_volume: (-?[\d.]+) dB", done.stderr)
    assert found, done.stderr[-3000:]
    return float(found.group(1))


def _toned_grid(tmp_path: Path, *, real: int, name: str) -> Path:
    """A 2x2 stage whose first ``real`` tiles carry a tone, rest are filler."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    tiles = tuple(
        _grid_tile(
            f"S{index}",
            _tone_source(tmp_path / f"tone{index}.mov", frequency=TONES[index]) if index < real else None,
            row=index // 2,
            col=index % 2,
        )
        for index in range(len(TONES))
    )
    return _render(tmp_path, tiles, name, rows=2, cols=2)


@integration
@needs_ffmpeg
def test_the_mix_carries_every_shooter(tmp_path: Path):
    # The assertion no stream count can make. Each shooter emits its own
    # tone; the mix is then asked, one bandpass at a time, whether it
    # contains each of them -- and whether it contains a frequency nobody
    # emitted, which is what catches a bandpass that stopped filtering.
    out = _toned_grid(tmp_path, real=4, name="tones_full.mov")

    for index, frequency in enumerate(TONES):
        level = _mean_volume_db(out, 0, bandpass=frequency)
        assert level > TONE_PRESENT_FLOOR, (
            f"S{index}'s {frequency} Hz tone is at {level:.1f} dB in the mix -- that shooter is "
            "missing from the track everyone who is not in an NLE will hear"
        )
    absent = _mean_volume_db(out, 0, bandpass=ABSENT_TONE)
    assert absent < TONE_ABSENT_CEILING, absent

    # And each shooter's own track still carries only their own tone, so
    # the mix was added beside the per-shooter tracks rather than over
    # them. Slot 0 is the mix, so shooter N is slot N+1.
    for index, frequency in enumerate(TONES):
        own = _mean_volume_db(out, index + 1, bandpass=frequency)
        assert own > TONE_PRESENT_FLOOR, (index, own)
        other = _mean_volume_db(out, index + 1, bandpass=TONES[(index + 1) % len(TONES)])
        assert other < TONE_ABSENT_CEILING, (index, other)


@integration
@needs_ffmpeg
def test_the_mix_is_averaged_not_summed_so_it_cannot_clip(tmp_path: Path):
    # ``normalize=1`` scales the sum by 1/inputs. Four uncorrelated
    # sources sum as sqrt(4) against a divisor of 4, so the mix lands
    # 6 dB under one shooter's own track -- quieter, never clipped.
    # ``normalize=0`` would instead put it ~6 dB *over*, into the ceiling,
    # and a gunshot recording is nothing but transients that would clip.
    out = _toned_grid(tmp_path, real=4, name="tones_full.mov")

    mix = _mean_volume_db(out, 0)
    single = _mean_volume_db(out, 1)
    assert mix - single == pytest.approx(-6.0, abs=1.0), (
        f"mix sits {mix - single:+.1f} dB against a single shooter; "
        "normalize=1 puts four uncorrelated sources at -6"
    )
    assert _max_volume_db(out, 0) < -1.0


@integration
@needs_ffmpeg
def test_a_shooter_with_no_trim_is_mixed_in_as_silence(tmp_path: Path):
    # The deliberate trade, pinned so it cannot be "fixed" by accident.
    # ``amix``'s normalize divides by the number of *inputs*, not the
    # number carrying signal, so a stage where half the roster has no
    # trim comes out 3 dB quieter than a fully-covered one. Mixing only
    # the tiles that have footage would even that out -- and make the
    # level step up and down between stages as coverage changes, which is
    # far more noticeable across a match-length video than a level that
    # is consistently conservative.
    full = _toned_grid(tmp_path / "full", real=4, name="tones_full.mov")
    half = _toned_grid(tmp_path / "half", real=2, name="tones_half.mov")

    full_delta = _mean_volume_db(full, 0) - _mean_volume_db(full, 1)
    half_delta = _mean_volume_db(half, 0) - _mean_volume_db(half, 1)
    assert full_delta == pytest.approx(-6.0, abs=1.0), full_delta
    assert half_delta == pytest.approx(-9.0, abs=1.0), half_delta

    # Each present shooter is weighted identically either way -- the
    # missing pair costs level, not balance.
    for path in (full, half):
        for frequency in TONES[:2]:
            assert _mean_volume_db(path, 0, bandpass=frequency) > TONE_PRESENT_FLOOR
    # And the absent pair really is absent from the half-covered mix.
    for frequency in TONES[2:]:
        assert _mean_volume_db(half, 0, bandpass=frequency) < TONE_ABSENT_CEILING


# --- driver ---------------------------------------------------------------
#
# These never shell out: ``runner`` is injected and the paths are fake.


def _bundle(n: int, trim: Path = Path("/trims/s.mp4"), *, beep: float = 2.0) -> CompareStageBundle:
    return CompareStageBundle(
        stage_number=n,
        stage_name=f"Stage {n}",
        trim_path=trim,
        audit_path=Path("/nonexistent.json"),
        beep_offset_in_clip=beep,
        duration_seconds=12.0,
        width=1920,
        height=1080,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _shooters(stages: dict[str, dict[int, CompareStageBundle]] | None = None):
    stages = stages or {label: {1: _bundle(1), 2: _bundle(2)} for label in ("Mathias", "Anders")}
    return [
        CompareShooterBundle(label=label, project_root=Path(f"/p/{label}"), stages_by_number=by_number)
        for label, by_number in stages.items()
    ]


def _ok(cmd, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, 0, b"", b"")


def _pairs(cmd: tuple[str, ...]) -> list[tuple[str, str]]:
    return list(zip(cmd, cmd[1:], strict=False))


def _recorder(inner=_ok):
    calls: list[tuple[str, ...]] = []

    def runner(cmd, **kwargs):
        calls.append(tuple(str(c) for c in cmd))
        return inner(cmd, **kwargs)

    return calls, runner


def test_renders_each_stage_then_concats(tmp_path: Path):
    calls, runner = _recorder()

    result = mp4_grid.render_grid_mp4(
        _shooters(),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        ffmpeg_binary="/bin/ffmpeg",
        runner=runner,
        work_dir=tmp_path / "work",
    )

    assert len(calls) == 3  # two stages + one concat
    # ``.mov``, not ``.mp4``: the segments carry PCM so the stitch has no
    # per-segment AAC priming to accumulate, and MP4 does not carry PCM.
    assert calls[0][-1] == str(tmp_path / "work" / "stage1.mov")
    assert calls[1][-1] == str(tmp_path / "work" / "stage2.mov")
    assert calls[2][calls[2].index("-f") + 1] == "concat"
    assert calls[2][-1] == str(tmp_path / "grid.mp4")
    assert [(s.stage_number, s.ok) for s in result.stages] == [(1, True), (2, True)]
    assert result.failed == ()
    assert result.output_path == tmp_path / "grid.mp4"


def test_the_stitch_keeps_every_audio_track_and_the_chosen_default(tmp_path: Path):
    # Stream copy drops the track names and re-derives the default flag,
    # so the stitch has to restate both. Get the offset wrong here and
    # every shooter is relabelled by one -- silently, after the whole
    # match has been encoded.
    calls, runner = _recorder()

    mp4_grid.render_grid_mp4(
        _shooters(),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        ffmpeg_binary="/bin/ffmpeg",
        runner=runner,
        work_dir=tmp_path / "work",
    )

    concat = calls[-1]
    pairs = _pairs(concat)
    assert ("-map", "0") in pairs
    # The mix is stream 0; alphabetical shooters follow, Anders then Mathias.
    assert ("-metadata:s:a:0", "handler_name=Mix") in pairs
    assert ("-metadata:s:a:1", "title=Anders") in pairs
    assert ("-metadata:s:a:1", "handler_name=Anders") in pairs
    assert ("-metadata:s:a:2", "title=Mathias") in pairs
    assert ("-disposition:a:0", "default") in pairs
    assert ("-disposition:a:1", "0") in pairs
    assert ("-disposition:a:2", "0") in pairs


def test_the_concat_list_names_only_the_segments_that_rendered(tmp_path: Path):
    # The concat demuxer refuses a list naming a segment that was never
    # written, and a failed stage leaves no file behind.
    def inner(cmd, **kwargs):
        code = 1 if str(tmp_path / "work" / "stage1.mov") in [str(c) for c in cmd] else 0
        return subprocess.CompletedProcess(cmd, code, b"", b"boom")

    calls, runner = _recorder(inner)

    mp4_grid.render_grid_mp4(
        _shooters(),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        ffmpeg_binary="/bin/ffmpeg",
        runner=runner,
        work_dir=tmp_path / "work",
    )

    listed = (tmp_path / "work" / "concat.txt").read_text(encoding="utf-8")
    assert "stage2.mov" in listed
    assert "stage1.mov" not in listed
    assert str(tmp_path / "work" / "concat.txt") in calls[-1]


def test_a_failing_stage_does_not_abort_the_run(tmp_path: Path):
    def inner(cmd, **kwargs):
        code = 1 if str(tmp_path / "work" / "stage2.mov") in [str(c) for c in cmd] else 0
        return subprocess.CompletedProcess(cmd, code, b"", b"boom: no such file")

    calls, runner = _recorder(inner)

    result = mp4_grid.render_grid_mp4(
        _shooters(),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        ffmpeg_binary="/bin/ffmpeg",
        runner=runner,
        work_dir=tmp_path / "work",
    )

    failed = result.failed
    assert [s.stage_number for s in failed] == [2]
    assert failed[0].stage_name == "Stage 2"
    assert "boom: no such file" in failed[0].error
    # Stage 1 still made it into the stitch, which still ran.
    assert [s.stage_number for s in result.stages if s.ok] == [1]
    assert calls[-1][calls[-1].index("-f") + 1] == "concat"
    assert result.stages[0].error is None


def test_all_stages_failing_raises_rather_than_concatenating_nothing(tmp_path: Path):
    calls, runner = _recorder(lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b"nope"))

    with pytest.raises(mp4_grid.GridRenderError, match="every stage failed"):
        mp4_grid.render_grid_mp4(
            _shooters(),
            audio_label="Mathias",
            output_path=tmp_path / "grid.mp4",
            canvas=CANVAS,
            ffmpeg_binary="/bin/ffmpeg",
            runner=runner,
            work_dir=tmp_path / "work",
        )

    # The stitch was never attempted -- an empty list yields a zero-byte file.
    assert len(calls) == 2
    assert not (tmp_path / "grid.mp4").exists()


def test_a_failing_stitch_is_fatal(tmp_path: Path):
    def inner(cmd, **kwargs):
        code = 1 if "concat" in [str(c) for c in cmd] else 0
        return subprocess.CompletedProcess(cmd, code, b"", b"cannot stitch")

    _calls, runner = _recorder(inner)

    with pytest.raises(mp4_grid.GridRenderError, match="cannot stitch"):
        mp4_grid.render_grid_mp4(
            _shooters(),
            audio_label="Mathias",
            output_path=tmp_path / "grid.mp4",
            canvas=CANVAS,
            ffmpeg_binary="/bin/ffmpeg",
            runner=runner,
            work_dir=tmp_path / "work",
        )


def test_the_ffmpeg_binary_defaults_to_the_resolved_runtime(tmp_path: Path, monkeypatch):
    # There is no ffmpeg on PATH on the machine this ships to; the binary
    # comes from splitsmith.runtime, which honours SPLITSMITH_FFMPEG and
    # the bundled sidecar locations.
    monkeypatch.setattr(mp4_grid, "runtime", lambda: SimpleNamespace(ffmpeg_binary="/opt/bundled/ffmpeg"))
    calls, runner = _recorder()

    mp4_grid.render_grid_mp4(
        _shooters(),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=runner,
        work_dir=tmp_path / "work",
    )

    assert {cmd[0] for cmd in calls} == {"/opt/bundled/ffmpeg"}


def test_a_missing_ffmpeg_binary_is_reported_once_not_per_stage(tmp_path: Path):
    calls: list[tuple[str, ...]] = []

    def runner(cmd, **kwargs):
        calls.append(tuple(str(c) for c in cmd))
        raise FileNotFoundError(2, "No such file or directory", str(cmd[0]))

    with pytest.raises(mp4_grid.GridRenderError, match="ffmpeg binary not found"):
        mp4_grid.render_grid_mp4(
            _shooters(),
            audio_label="Mathias",
            output_path=tmp_path / "grid.mp4",
            canvas=CANVAS,
            ffmpeg_binary="/nope/ffmpeg",
            runner=runner,
            work_dir=tmp_path / "work",
        )

    assert len(calls) == 1


def test_segments_whose_stream_layouts_disagree_are_never_stitched(tmp_path: Path, monkeypatch):
    # concat -c copy rejects segments with different stream counts. Every
    # plan out of build_stage_plans carries one tile per label, so this is
    # unreachable today -- the guard is here so a future planner change
    # fails loudly instead of producing a stitch ffmpeg refuses halfway
    # through a match-long encode.
    def _plan(number: int, labels: tuple[str, ...]) -> mp4_grid.GridStagePlan:
        return mp4_grid.GridStagePlan(
            stage_number=number,
            stage_name=f"Stage {number}",
            tiles=tuple(
                mp4_grid.GridTile(
                    label=label,
                    trim_path=Path("/trims/s.mp4"),
                    beep_offset_in_clip=0.0,
                    seek_seconds=0.0,
                    lead_pad_seconds=0.0,
                    row=0,
                    col=col,
                )
                for col, label in enumerate(labels)
            ),
            duration_seconds=3.0,
            audio_label="Mathias",
            rows=1,
            cols=len(labels),
        )

    monkeypatch.setattr(
        mp4_grid,
        "build_stage_plans",
        lambda *a, **kw: (_plan(1, ("Anders", "Mathias")), _plan(2, ("Mathias",))),
    )
    calls, runner = _recorder()

    with pytest.raises(mp4_grid.GridRenderError, match="stream layout"):
        mp4_grid.render_grid_mp4(
            _shooters(),
            audio_label="Mathias",
            output_path=tmp_path / "grid.mp4",
            canvas=CANVAS,
            ffmpeg_binary="/bin/ffmpeg",
            runner=runner,
            work_dir=tmp_path / "work",
        )

    assert calls == []  # caught before a single minute of encoding


def test_a_roster_with_no_trims_at_all_raises_before_encoding(tmp_path: Path):
    # The planner's own validation, surfaced rather than swallowed: a
    # silent all-black render is not a useful thing to spend an hour on.
    calls, runner = _recorder()

    with pytest.raises(ValueError, match="no stages with trims"):
        mp4_grid.render_grid_mp4(
            [CompareShooterBundle(label="Mathias", project_root=Path("/p/Mathias"))],
            audio_label="Mathias",
            output_path=tmp_path / "grid.mp4",
            canvas=CANVAS,
            ffmpeg_binary="/bin/ffmpeg",
            runner=runner,
            work_dir=tmp_path / "work",
        )

    assert calls == []


def test_an_empty_plan_set_raises_instead_of_indexing_off_the_end(tmp_path: Path, monkeypatch):
    # Unreachable through build_stage_plans today, but the stream-layout
    # check reads plans[0]; an IndexError here would be a poor way to
    # learn a future planner can return nothing.
    monkeypatch.setattr(mp4_grid, "build_stage_plans", lambda *a, **kw: ())
    calls, runner = _recorder()

    with pytest.raises(mp4_grid.GridRenderError, match="no stages to render"):
        mp4_grid.render_grid_mp4(
            _shooters(),
            audio_label="Mathias",
            output_path=tmp_path / "grid.mp4",
            canvas=CANVAS,
            ffmpeg_binary="/bin/ffmpeg",
            runner=runner,
            work_dir=tmp_path / "work",
        )

    assert calls == []

    assert calls == []


def _audio_streams(path: Path) -> list[tuple[str, bool]]:
    """``(handler_name, is_default)`` per audio track, in container order."""
    done = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    streams: list[tuple[str, bool]] = []
    for chunk in done.stderr.split("Stream #0:")[1:]:
        head = chunk.splitlines()[0]
        if ": Audio:" not in head:
            continue
        handler = re.search(r"handler_name\s*:\s*(.+)", chunk)
        streams.append((handler.group(1).strip() if handler else "", "(default)" in head))
    return streams


def _real_shooters(
    tmp_path: Path,
    *,
    broken_stage: int | None = None,
    colours: dict[str, str] | None = None,
):
    """Shooters with two stages each, backed by real 2s clips."""
    colours = colours or {"Anders": "red", "Mathias": "blue"}
    shooters = []
    for label, colour in colours.items():
        stages = {}
        for number in (1, 2):
            if broken_stage == number:
                trim = tmp_path / f"{label}_missing_{number}.mp4"  # never created
            else:
                trim = _source(tmp_path / f"{label}_{number}.mp4", seconds=2.0, color=colour)
            stages[number] = CompareStageBundle(
                stage_number=number,
                stage_name=f"Stage {number}",
                trim_path=trim,
                audit_path=tmp_path / "audit.json",
                beep_offset_in_clip=0.5,
                duration_seconds=2.0,
                width=320,
                height=240,
                frame_rate_num=30,
                frame_rate_den=1,
            )
        shooters.append(
            CompareShooterBundle(label=label, project_root=tmp_path / label, stages_by_number=stages)
        )
    return shooters


@integration
@needs_ffmpeg
def test_the_driver_renders_and_stitches_a_playable_grid(tmp_path: Path):
    # The whole driver against a real ffmpeg. Arg-tuple assertions cannot
    # see a concat list ffmpeg rejects, nor a stitch that quietly drops
    # three of four audio tracks.
    result = mp4_grid.render_grid_mp4(
        _real_shooters(tmp_path),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        ffmpeg_binary=FFMPEG,
        work_dir=tmp_path / "work",
    )

    assert result.failed == ()
    assert result.output_path.exists()
    # head 1.0 + post-beep 1.5 + tail 0.5 = 3.0 per stage, stitched.
    assert _stream_seconds(result.output_path, "0:v:0") == pytest.approx(6.0, abs=0.2)
    assert _audio_streams(result.output_path) == [
        ("Mix", True),
        ("Anders", False),
        ("Mathias", False),
    ]
    for slot in range(3):
        assert _stream_seconds(result.output_path, f"0:a:{slot}") == pytest.approx(6.0, abs=0.2)


@integration
@needs_ffmpeg
def test_a_stage_whose_source_is_gone_is_skipped_and_the_rest_still_stitches(tmp_path: Path):
    # The behaviour the whole driver exists for: one unreadable stage
    # costs that stage, not the hour spent on the other nineteen.
    result = mp4_grid.render_grid_mp4(
        _real_shooters(tmp_path, broken_stage=2),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        ffmpeg_binary=FFMPEG,
        work_dir=tmp_path / "work",
    )

    assert [s.stage_number for s in result.failed] == [2]
    assert "No such file" in result.failed[0].error
    assert result.output_path.exists()
    assert _stream_seconds(result.output_path, "0:v:0") == pytest.approx(3.0, abs=0.2)
    assert _audio_streams(result.output_path) == [
        ("Mix", True),
        ("Anders", False),
        ("Mathias", False),
    ]


@integration
@needs_ffmpeg
def test_a_three_shooter_match_stitches_with_its_empty_quadrant_black(tmp_path: Path):
    # The whole driver at a roster size that does not fill its grid. The
    # per-stage command tests prove the filler is emitted; only the
    # stitched file proves concat -c copy still accepts segments that
    # carry one, and that the audio count is still the roster's three.
    result = mp4_grid.render_grid_mp4(
        _real_shooters(tmp_path, colours={"Anders": "red", "Bea": "blue", "Mathias": "yellow"}),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        ffmpeg_binary=FFMPEG,
        work_dir=tmp_path / "work",
    )

    assert result.failed == ()
    assert _video_size(result.output_path) == (CANVAS.width, CANVAS.height)
    assert _audio_streams(result.output_path) == [
        ("Mix", True),
        ("Anders", False),
        ("Bea", False),
        ("Mathias", False),
    ]
    cell_w, cell_h = CANVAS.width // 2, CANVAS.height // 2
    for at in (1.0, 4.0):
        assert _patch_colour(result.output_path, at=at, x=cell_w + cell_w // 2, y=cell_h + cell_h // 2) == (
            0,
            0,
            0,
        )


# --- stitch A/V sync ------------------------------------------------------
#
# The stitch used to write AAC into every per-stage segment and join them
# with ``concat -c copy``. Each segment's encoder priming and tail padding
# then survived into the output as real decodable samples that the
# container timeline does not account for, so audio ran progressively late
# against video: +29ms after one stage, +352ms after twelve (measured on
# ffmpeg 6.1.1), which is every beep and every shot landing audibly behind
# the recoil by the back half of a match.
#
# The container metadata hides this completely, and does so twice over.
# The concat demuxer hands the muxer overlapping timestamps, and the mov
# muxer resolves the overlap by shrinking the two AAC frames either side
# of every segment boundary to durations of 1 and 191 samples instead of
# 1024. So the *declared* timeline comes out only 21ms long on a file that
# is 352ms long in samples, and every duration-based check passes: a
# 12-stage render reports audio 342.177s against video 342.156s. No
# decoder can play a 1024-sample frame in 1 sample of time, so a player
# runs the samples out back to back and hears the drift in full.
#
# So the check has to measure decoded *content*, and it has to measure it
# against the picture rather than against a number in a header. The test
# below renders a synchronised marker -- a black->white cut and a
# full-scale audio transient on the very same instant -- and asserts that
# they still coincide in the finished file.
#
# What this must NOT do is count coded samples. An earlier version of this
# test measured ``nb_read_packets * 1024 / sample_rate`` and reported a
# constant ~32ms of audio beyond the video on a file that is in fact
# sample-exact. That figure is real but it is not an offset: it is the one
# AAC encode's 1024 priming samples plus a partial flushed final frame. MP4
# signals priming with an edit list (``elst`` media_time), which the stitch
# writes correctly and every conforming player honours, and the tail sits
# after the last picture where nothing can hear it. Measured on ffmpeg
# 6.1.1 with the marker below: audio landed on exactly the intended sample
# (48000, 168000, 288000, ...) against the exact video frame, at N=2, 6 and
# 12, while the packet-count metric wandered between +34.7 and +40.0ms.
#
# ``initial_padding`` is not the signal to check either. ffprobe reports it
# as 0 for every AAC-in-MP4 stream, including a hand-built control file
# whose marker is known to be sample-exact -- the mov demuxer simply does
# not populate that field, because MP4 carries priming in the edit list.

#: What a single continuous AAC encode legitimately leaves in the decoded
#: stream once the edit list has trimmed the front: a partial final frame,
#: under one AAC frame long, sitting after the last video frame. It is a
#: constant -- it does not grow with the number of segments stitched --
#: and it is inaudible, but it is why the length assertion is not exact.
AAC_TAIL_SLACK_SECONDS = 1024 / 48000  # 21.3ms

#: How far the audio marker may sit from the picture cut it was authored
#: on. Video resolution is one frame, so one frame is the floor of what
#: this can resolve; the fix measures 0 samples of error, not 33ms of it.
MARKER_TOLERANCE_SECONDS = FRAME_SECONDS

#: Not 2. The drift is roughly one AAC frame per segment, so a two-segment
#: stitch lands ~29ms off -- inside any tolerance a sane person picks for a
#: file whose video is 30fps, which is exactly why this defect shipped
#: through a green suite. Eight segments put it at ~230ms, seven video
#: frames, impossible to read as rounding.
DRIFT_STAGE_COUNT = 8


def _presented_audio_seconds(path: Path, slot: int = 0) -> float:
    """How much audio a player actually renders, in decoded samples.

    Not ``nb_read_packets * 1024``: that counts the coded samples, which
    include the encoder priming the edit list exists to hide, so a
    correct file scores ~32ms over. Not ``duration`` / ``duration_ts``
    either: those are the container's claim, and the concat bug lived
    entirely in the gap between the claim and the samples. Decoding is
    the only reading that is both honest about the content and honest
    about the edit list -- ffmpeg's mov demuxer applies ``elst`` unless
    told otherwise, which is what a conforming player does.
    """
    done = subprocess.run(
        [
            FFMPEG, "-v", "error", "-i", str(path), "-map", f"0:a:{slot}",
            "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "48000", "-",
        ],  # fmt: skip
        capture_output=True,
    )
    assert done.returncode == 0, done.stderr[-2000:].decode(errors="replace")
    return len(done.stdout) // 2 / 48000


def _declared_seconds(path: Path, stream: str) -> float:
    """The container's own claim about a stream's length."""
    done = subprocess.run(
        [
            FFPROBE, "-v", "error", "-select_streams", stream,
            "-show_entries", "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return float(done.stdout.strip())


def _audio_mark_times(path: Path, slot: int = 0) -> list[float]:
    """Where each audio transient starts, to the sample, honouring the edit list.

    Deliberately not ``silencedetect``. That filter reports the timestamps
    the container claims, and on a file broken by this defect the claim is
    a physical impossibility: the mov muxer, handed overlapping timestamps
    by the concat demuxer, writes the two AAC frames straddling every
    segment boundary with durations of 1 and 191 samples instead of 1024.
    No decoder can play a 1024-sample frame in 1 sample of time, so a
    player just runs the samples out back to back and every later beep
    lands late -- while ``silencedetect``, reading the impossible table,
    reports every burst exactly on time. Measured on ffmpeg 6.1.1 against
    a file that was 290ms out: ``silencedetect`` saw 21ms.

    Counting samples is what the audio device does, so that is what this
    counts -- one sample at a time, not in blocks, because the whole point
    of the marker is that it can be compared against a picture cut.
    """
    done = subprocess.run(
        [
            FFMPEG, "-v", "error", "-i", str(path), "-map", f"0:a:{slot}",
            "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "48000", "-",
        ],  # fmt: skip
        capture_output=True,
    )
    assert done.returncode == 0, done.stderr[-2000:].decode(errors="replace")
    samples = array.array("h")
    samples.frombytes(done.stdout)
    assert samples, "no audio decoded"

    # Threshold relative to the file's own peak, so the test does not
    # depend on what gain the chain happens to apply. 20ms of quiet ends a
    # burst -- the marker is a square wave, so it crosses zero constantly.
    floor = max(max(samples), -min(samples)) // 4
    gap = 48000 // 50
    marks: list[float] = []
    loud = True
    quiet_for = 0
    for index, value in enumerate(samples):
        if abs(value) > floor:
            if not loud:
                marks.append(index / 48000)
            loud = True
            quiet_for = 0
        else:
            quiet_for += 1
            if quiet_for > gap:
                loud = False
    return marks


def _video_mark_times(path: Path, fps: float = 1 / FRAME_SECONDS) -> list[float]:
    """Where the picture cuts from dark to bright, by decoded frame index.

    Frame index over frame rate, not a container timestamp: the index is
    the order a player puts frames on screen, and the count is what an
    honest reading of a stream-copied video track looks like. Decoding
    applies the video track's own edit list (ffmpeg's mov demuxer honours
    ``elst`` unless told not to), which is what compensates the h264
    reorder delay -- so this and :func:`_audio_mark_times` are read on the
    same timeline a player uses.
    """
    done = subprocess.run(
        [
            FFMPEG, "-v", "error", "-i", str(path), "-map", "0:v:0",
            "-vf", "scale=8:8", "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],  # fmt: skip
        capture_output=True,
    )
    assert done.returncode == 0, done.stderr[-2000:].decode(errors="replace")
    pixels = done.stdout
    step = 64
    means = [sum(pixels[at : at + step]) / step for at in range(0, len(pixels) - step + 1, step)]
    assert means, "no video decoded"
    marks: list[float] = []
    bright = True  # a file that opens bright must not count frame 0 as a cut
    for index, mean in enumerate(means):
        if mean > 120 and not bright:
            marks.append(index / fps)
        bright = mean > 120
    return marks


def _marked_source(
    path: Path, *, seconds: float, mark_at: float, width: int = 320, height: int = 240
) -> Path:
    """A clip whose picture cuts and whose audio fires on the same instant.

    Black until ``mark_at``, white after; silent until ``mark_at``, then a
    full-scale square wave. Both planes are written raw and muxed, rather
    than built with lavfi sources and ``adelay``, because the marker has to
    be exact to the frame *and* to the sample -- it is the reference the
    whole A/V measurement is read against, so it cannot itself carry a
    millisecond of filter rounding.

    A continuous tone would measure length but not *placement*: drift moves
    audio later without changing how much of it there is. And an audio
    marker alone can only be compared against arithmetic. Cutting the
    picture on the same instant is what turns this into a sync measurement:
    the finished file is asked where its sound sits relative to its own
    picture, which is the question the user is actually asking.
    """
    frames = int(round(seconds * 30))
    mark_frame = int(round(mark_at * 30))
    luma = {False: bytes([16]) * (width * height), True: bytes([235]) * (width * height)}
    chroma = bytes([128]) * (width * height // 4)
    raw_video = path.with_suffix(".yuv")
    with raw_video.open("wb") as handle:
        for index in range(frames):
            handle.write(luma[index >= mark_frame])
            handle.write(chroma)
            handle.write(chroma)

    total = int(round(seconds * 48000))
    mark_sample = int(round(mark_at * 48000))
    pcm = array.array("h", bytes(2 * total))
    # A square wave, so the onset is a single-sample step and the detector
    # has no envelope attack to guess at.
    for index in range(mark_sample, total):
        pcm[index] = 20000 if ((index - mark_sample) // 48) % 2 == 0 else -20000
    raw_audio = path.with_suffix(".pcm")
    raw_audio.write_bytes(pcm.tobytes())

    cmd = [
        FFMPEG, "-hide_banner", "-y",
        "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{width}x{height}", "-r", "30",
        "-i", str(raw_video),
        "-f", "s16le", "-ar", "48000", "-ac", "1", "-i", str(raw_audio),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        "-c:a", "aac", "-ac", "2", str(path),
    ]  # fmt: skip
    done = subprocess.run(cmd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    raw_video.unlink()
    raw_audio.unlink()
    return path


@integration
@needs_ffprobe
def test_a_long_stitch_does_not_drift_audio_late_against_video(tmp_path: Path):
    # One clip per shooter, reused as every stage's trim: the stitch does
    # not care that the stages look alike, and eight encodes beat sixteen.
    # Beep at 1.0s in a 2.0s clip, head pad 1.0 -> no seek, no lead pad, so
    # each stage is 1.0 + 1.0 + 0.5 = 2.5s with its tone burst at exactly
    # 1.0s. Stage k's burst therefore belongs at 2.5 * k + 1.0.
    stage_seconds = 2.5
    shooters = []
    for label in ("Anders", "Mathias"):
        clip = _marked_source(tmp_path / f"{label}.mp4", seconds=2.0, mark_at=1.0)
        shooters.append(
            CompareShooterBundle(
                label=label,
                project_root=tmp_path / label,
                stages_by_number={
                    number: CompareStageBundle(
                        stage_number=number,
                        stage_name=f"Stage {number}",
                        trim_path=clip,
                        audit_path=tmp_path / "audit.json",
                        beep_offset_in_clip=1.0,
                        duration_seconds=2.0,
                        width=320,
                        height=240,
                        frame_rate_num=30,
                        frame_rate_den=1,
                    )
                    for number in range(1, DRIFT_STAGE_COUNT + 1)
                },
            )
        )

    result = mp4_grid.render_grid_mp4(
        shooters,
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        ffmpeg_binary=FFMPEG,
        work_dir=tmp_path / "work",
    )
    assert result.failed == ()

    video = _declared_seconds(result.output_path, "v:0")
    assert video == pytest.approx(stage_seconds * DRIFT_STAGE_COUNT, abs=FRAME_SECONDS)

    # Where the picture cuts, on the timeline a player uses. Read once:
    # the video is stream-copied, so every track is measured against the
    # same picture.
    #
    # Deliberately loose against the *plan*: picture placement is
    # quantised to a frame and a whole-frame shift is not a sync defect.
    # This is the sanity check that the grid put the stage where it said;
    # the assertion that bites is sound against picture, below.
    picture = _video_mark_times(result.output_path)
    assert len(picture) == DRIFT_STAGE_COUNT, picture
    for index, cut in enumerate(picture):
        expected = stage_seconds * index + 1.0
        assert cut == pytest.approx(
            expected, abs=2 * FRAME_SECONDS
        ), f"stage {index + 1}: picture cuts at {cut:.4f}s, the grid put it at {expected:.4f}s"

    # Slot 0 is the mix, then one per shooter: the mix is measured for
    # drift on exactly the same terms, because it is the track anything
    # that is not an NLE will actually play.
    for slot in range(len(shooters) + 1):
        presented = _presented_audio_seconds(result.output_path, slot)
        assert presented - video == pytest.approx(0.0, abs=AAC_TAIL_SLACK_SECONDS), (
            f"track {slot} presents {presented - video:+.3f}s of audio against its video; "
            "the segments are leaking per-segment encoder padding into the stitch"
        )

        # Length alone is not sync, and arithmetic alone is not either.
        # Every transient has to still coincide with the picture cut it
        # was authored on -- and the last one is where drift accumulates.
        marks = _audio_mark_times(result.output_path, slot)
        assert len(marks) == DRIFT_STAGE_COUNT, marks
        for index, (mark, cut) in enumerate(zip(marks, picture, strict=True)):
            assert mark - cut == pytest.approx(0.0, abs=MARKER_TOLERANCE_SECONDS), (
                f"track {slot} stage {index + 1}: sound at {mark:.4f}s against a picture cut at "
                f"{cut:.4f}s -- {1000 * (mark - cut):+.1f}ms out"
            )


# --- canvas frame rate ----------------------------------------------------
#
# The canvas geometry is a product decision (a 2x2 of 1080p tiles is
# exactly 4K), but its frame rate is not: forcing 30000/1001 onto 30fps
# GoPro footage resamples every frame for nothing and risks judder. The
# rate follows the audio-source shooter's first stage, which is what
# compare/emitter.py already does for the FCPXML sequence -- otherwise
# the two exporters disagree about the same match.


def _rated_shooters(rates: dict[str, dict[int, tuple[int, int]]]):
    """Shooters whose stages carry the given ``{label: {stage: (num, den)}}`` rates."""
    shooters = []
    for label, by_number in rates.items():
        stages = {
            number: CompareStageBundle(
                stage_number=number,
                stage_name=f"Stage {number}",
                trim_path=Path(f"/trims/{label}{number}.mp4"),
                audit_path=Path("/nonexistent.json"),
                beep_offset_in_clip=2.0,
                duration_seconds=12.0,
                width=1920,
                height=1080,
                frame_rate_num=num,
                frame_rate_den=den,
            )
            for number, (num, den) in by_number.items()
        }
        shooters.append(
            CompareShooterBundle(label=label, project_root=Path(f"/p/{label}"), stages_by_number=stages)
        )
    return shooters


def _rates_used(calls: list[tuple[str, ...]]) -> set[str]:
    """Every ``-r`` value across the per-stage commands (the concat has none)."""
    return {cmd[i + 1] for cmd in calls for i, arg in enumerate(cmd) if arg == "-r"}


def test_the_canvas_frame_rate_follows_the_audio_source_shooter(tmp_path: Path):
    calls, runner = _recorder()

    mp4_grid.render_grid_mp4(
        _rated_shooters({"Mathias": {1: (30, 1), 2: (30, 1)}, "Anders": {1: (60, 1), 2: (60, 1)}}),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        runner=runner,
        work_dir=tmp_path / "work",
    )

    assert _rates_used(calls) == {"30/1"}
    assert "fps=30/1" in " ".join(calls[0])


def test_an_explicitly_pinned_frame_rate_is_never_overridden(tmp_path: Path):
    calls, runner = _recorder()

    mp4_grid.render_grid_mp4(
        _rated_shooters({"Mathias": {1: (30, 1)}, "Anders": {1: (30, 1)}}),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=mp4_grid.GridCanvas(frame_rate_num=24, frame_rate_den=1),
        runner=runner,
        work_dir=tmp_path / "work",
    )

    assert _rates_used(calls) == {"24/1"}


def test_a_custom_size_without_a_frame_rate_still_derives_one(tmp_path: Path):
    # Derivation keys off the frame-rate fields, not off "no canvas given":
    # a caller pinning the geometry must not silently lose the rate.
    calls, runner = _recorder()

    mp4_grid.render_grid_mp4(
        _rated_shooters({"Mathias": {1: (50, 1)}, "Anders": {1: (30, 1)}}),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=mp4_grid.GridCanvas(width=1280, height=720),
        runner=runner,
        work_dir=tmp_path / "work",
    )

    assert _rates_used(calls) == {"50/1"}
    assert "scale=640:720" in " ".join(calls[0])  # geometry still the caller's


def test_a_mixed_rate_match_pins_one_rate_across_every_stage(tmp_path: Path):
    # concat -c copy refuses segments whose frame rate differs, so a match
    # of mixed footage still gets exactly one rate -- the audio source's
    # first stage -- and every other tile is conformed to it.
    calls, runner = _recorder()

    mp4_grid.render_grid_mp4(
        _rated_shooters(
            {
                "Mathias": {1: (30, 1), 2: (60, 1)},
                "Anders": {1: (25, 1), 2: (30000, 1001)},
            }
        ),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        runner=runner,
        work_dir=tmp_path / "work",
    )

    assert _rates_used(calls) == {"30/1"}
    for cmd in calls[:2]:
        assert cmd.count("-r") == 1


def test_the_frame_rate_falls_back_when_no_bundle_can_supply_one():
    assert mp4_grid.derive_frame_rate([], audio_label="Mathias") == (30000, 1001)
    assert mp4_grid.derive_frame_rate(
        [CompareShooterBundle(label="Mathias", project_root=Path("/p"))], audio_label="Mathias"
    ) == (30000, 1001)
    # A shooter that isn't the audio source cannot supply it either.
    assert mp4_grid.derive_frame_rate(_rated_shooters({"Anders": {1: (60, 1)}}), audio_label="Mathias") == (
        30000,
        1001,
    )


def test_the_rate_comes_from_the_lowest_numbered_stage_like_the_emitter():
    shooters = _rated_shooters({"Mathias": {3: (24, 1), 1: (30, 1), 2: (60, 1)}})
    assert mp4_grid.derive_frame_rate(shooters, audio_label="Mathias") == (30, 1)


def test_a_default_canvas_still_reports_the_fallback_rate():
    # build_stage_command is called directly by callers with no shooters to
    # derive from; an unpinned canvas must not emit "None/None".
    assert mp4_grid.GridCanvas().frame_rate == (30000, 1001)
    assert mp4_grid.GridCanvas().rate_string == "30000/1001"
    assert mp4_grid.GridCanvas().fps == pytest.approx(29.97, abs=0.01)
    assert not mp4_grid.GridCanvas().is_frame_rate_pinned
    assert mp4_grid.GridCanvas(frame_rate_num=30, frame_rate_den=1).is_frame_rate_pinned


def test_half_a_frame_rate_is_rejected_rather_than_silently_derived():
    with pytest.raises(ValueError, match="both or neither"):
        mp4_grid.GridCanvas(frame_rate_num=30)
    with pytest.raises(ValueError, match="both or neither"):
        mp4_grid.GridCanvas(frame_rate_den=1)


def _video_fps(path: Path) -> float:
    """The rate ffmpeg reports for the file's video stream."""
    done = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    found = re.search(r"Video:.*?, (\d+(?:\.\d+)?) fps", done.stderr)
    assert found, done.stderr[-2000:]
    return float(found.group(1))


@integration
@needs_ffmpeg
def test_a_mixed_rate_match_still_stitches_at_the_audio_sources_rate(tmp_path: Path):
    # The case that would break the stitch for real: shooters at
    # different rates, and the audio source itself changing rate between
    # stages. concat -c copy refuses segments whose frame rate differs,
    # so one rate has to win for the whole render. Asserting on argv
    # cannot see that ffmpeg accepts the result -- this can.
    sources = {
        ("Mathias", 1): ("blue", "30"),
        ("Mathias", 2): ("blue", "60"),
        ("Anders", 1): ("red", "25"),
        ("Anders", 2): ("red", "30000/1001"),
    }
    shooters = []
    for label in ("Mathias", "Anders"):
        stages = {}
        for number in (1, 2):
            colour, fps = sources[(label, number)]
            clip = _source(tmp_path / f"{label}{number}.mp4", seconds=2.0, color=colour, fps=fps)
            num, _, den = fps.partition("/")
            stages[number] = CompareStageBundle(
                stage_number=number,
                stage_name=f"Stage {number}",
                trim_path=clip,
                audit_path=tmp_path / "audit.json",
                beep_offset_in_clip=0.5,
                duration_seconds=2.0,
                width=320,
                height=240,
                frame_rate_num=int(num),
                frame_rate_den=int(den or 1),
            )
        shooters.append(
            CompareShooterBundle(label=label, project_root=tmp_path / label, stages_by_number=stages)
        )

    result = mp4_grid.render_grid_mp4(
        shooters,
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=mp4_grid.GridCanvas(width=640, height=360),  # size pinned, rate derived
        ffmpeg_binary=FFMPEG,
        work_dir=tmp_path / "work",
    )

    assert result.failed == ()
    # Mathias' first stage is 30fps, so the whole render is -- not 29.97,
    # and not stage 2's 60.
    assert _video_fps(result.output_path) == pytest.approx(30.0, abs=0.01)
    for segment in ("stage1.mov", "stage2.mov"):
        assert _video_fps(tmp_path / "work" / segment) == pytest.approx(30.0, abs=0.01)
    assert _stream_seconds(result.output_path, "0:v:0") == pytest.approx(6.0, abs=0.2)
    assert _audio_streams(result.output_path) == [
        ("Mix", True),
        ("Anders", False),
        ("Mathias", False),
    ]


@integration
@needs_ffmpeg
def test_a_30fps_source_is_not_silently_resampled_to_29_97(tmp_path: Path):
    # The defect this section exists for, measured on the finished file.
    result = mp4_grid.render_grid_mp4(
        _real_shooters(tmp_path),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        canvas=mp4_grid.GridCanvas(width=640, height=360),
        ffmpeg_binary=FFMPEG,
        work_dir=tmp_path / "work",
    )

    assert _video_fps(result.output_path) == pytest.approx(30.0, abs=0.001)
