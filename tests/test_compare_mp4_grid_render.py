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

import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from splitsmith.compare import mp4_grid
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle

FFMPEG = shutil.which("ffmpeg")

#: Applied per-test rather than module-wide: the driver tests below run
#: everywhere, and marking them ``integration`` would skip them exactly
#: where they are needed most -- a machine with no ffmpeg.
integration = pytest.mark.integration
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="needs a real ffmpeg on PATH")

CANVAS = mp4_grid.GridCanvas(width=640, height=360, frame_rate_num=30, frame_rate_den=1)
FRAME_SECONDS = 1 / 30
STAGE_SECONDS = 4.0


def _source(path: Path, *, seconds: float, color: str) -> Path:
    """A solid-colour clip with a tone, so both streams are measurable."""
    cmd = [
        FFMPEG, "-hide_banner", "-y",
        "-f", "lavfi", "-t", str(seconds), "-i", f"color=c={color}:s=320x240:r=30",
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


def _render(tmp_path: Path, tiles: tuple[mp4_grid.GridTile, ...], name: str) -> Path:
    plan = mp4_grid.GridStagePlan(
        stage_number=1,
        stage_name="Stage 1",
        tiles=tiles,
        duration_seconds=STAGE_SECONDS,
        audio_label=tiles[-1].label,
        rows=1,
        cols=len(tiles),
    )
    out = tmp_path / name
    cmd = mp4_grid.build_stage_command(plan, canvas=CANVAS, output_path=out, ffmpeg_binary=FFMPEG)
    done = subprocess.run(list(cmd), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-3000:]
    return out


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
    assert calls[0][-1] == str(tmp_path / "work" / "stage1.mp4")
    assert calls[1][-1] == str(tmp_path / "work" / "stage2.mp4")
    assert calls[2][calls[2].index("-f") + 1] == "concat"
    assert calls[2][-1] == str(tmp_path / "grid.mp4")
    assert [(s.stage_number, s.ok) for s in result.stages] == [(1, True), (2, True)]
    assert result.failed == ()
    assert result.output_path == tmp_path / "grid.mp4"


def test_the_stitch_keeps_every_audio_track_and_the_chosen_default(tmp_path: Path):
    # Stream copy drops the track names and re-derives the default flag
    # onto the first audio track, so the stitch has to restate both. Get
    # the labels wrong here and the file plays the alphabetically-first
    # shooter -- silently, after the whole match has been encoded.
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
    # Alphabetical slots: Anders is 0, Mathias is 1.
    assert ("-metadata:s:a:0", "title=Anders") in pairs
    assert ("-metadata:s:a:0", "handler_name=Anders") in pairs
    assert ("-metadata:s:a:1", "title=Mathias") in pairs
    assert ("-disposition:a:0", "0") in pairs
    assert ("-disposition:a:1", "default") in pairs


def test_the_concat_list_names_only_the_segments_that_rendered(tmp_path: Path):
    # concat -c copy refuses a list naming a segment that was never
    # written, and a failed stage leaves no file behind.
    def inner(cmd, **kwargs):
        code = 1 if str(tmp_path / "work" / "stage1.mp4") in [str(c) for c in cmd] else 0
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
    assert "stage2.mp4" in listed
    assert "stage1.mp4" not in listed
    assert str(tmp_path / "work" / "concat.txt") in calls[-1]


def test_a_failing_stage_does_not_abort_the_run(tmp_path: Path):
    def inner(cmd, **kwargs):
        code = 1 if str(tmp_path / "work" / "stage2.mp4") in [str(c) for c in cmd] else 0
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


def _real_shooters(tmp_path: Path, *, broken_stage: int | None = None):
    """Two shooters with two stages each, backed by real 2s clips."""
    colours = {"Anders": "red", "Mathias": "blue"}
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
    assert _audio_streams(result.output_path) == [("Anders", False), ("Mathias", True)]
    for slot in range(2):
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
    assert _audio_streams(result.output_path) == [("Anders", False), ("Mathias", True)]
