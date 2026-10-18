"""Dressing an export in a Look's sounds after the render."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from splitsmith.audio_dress import (
    DressError,
    SoundCue,
    SoundMap,
    dress_command,
    dress_export,
    dressed_path,
    load_sound_map,
    place_cues,
    place_template_cues,
)
from splitsmith.render_timeline import TimelineEntry, timeline_path, write_timeline

STAGE = TimelineEntry(kind="stage", name="stage_000", stage_index=0, start=0.0, end=20.0, beep=0.0)
SUMMARY = TimelineEntry(kind="summary", name="summary_000", stage_index=0, start=20.0, end=26.0)
TITLE = TimelineEntry(kind="title_page", name="title_page", stage_index=None, start=0.0, end=4.0)


def _cues(*cues: dict) -> SoundMap:
    return SoundMap.model_validate({"cues": list(cues)})


def test_an_intermission_ticks_then_explodes_over_its_bed(tmp_path: Path) -> None:
    sounds = _cues(
        {"on": "summary", "play": "tick.wav", "every": 0.07, "for": 2.6, "gain": 0.5},
        {"on": "summary", "play": "boom.wav", "at": 2.6, "hit": 0.1},
        {"on": "summary", "bed": "music.ogg", "at": 0.2, "duck": 0.3, "fade": 1.0},
        {"on": "stage", "play": "go.wav", "at": "beep"},
    )
    # Timeline order: the stage's beep cue comes before the summary's.
    placed = place_cues(sounds, [STAGE, SUMMARY], tmp_path)
    assert [p.path.name for p in placed] == ["go.wav", "tick.wav", "boom.wav", "music.ogg"]
    go, tick, boom, bed = placed
    assert (go.start, go.length) == (0.0, None)
    assert (tick.start, tick.length, tick.every, tick.gain) == (20.0, 2.6, 0.07, 0.5)
    assert boom.start == pytest.approx(22.5), "the hit lands on the cue"
    assert (bed.start, bed.length, bed.loop, bed.duck, bed.fade) == (20.2, pytest.approx(5.8), True, 0.3, 1.0)


def test_a_beep_cue_skips_an_item_without_a_beep_and_a_late_cue_is_dropped(tmp_path: Path) -> None:
    sounds = _cues(
        {"on": "title_page", "play": "x.wav", "at": "beep"}, {"on": "title_page", "play": "y.wav", "at": 9}
    )
    assert place_cues(sounds, [TITLE], tmp_path) == ()


@pytest.mark.parametrize(
    "cue",
    [
        {"on": "stage"},
        {"on": "stage", "play": "a.wav", "bed": "b.ogg"},
        {"on": "stage", "play": "a.wav", "every": 0.1},
        {"on": "stage", "play": "a.wav", "duck": 0.5},
        {"on": "credits", "play": "a.wav"},
    ],
)
def test_a_malformed_cue_is_refused(cue: dict) -> None:
    with pytest.raises(ValueError):
        SoundCue.model_validate(cue)


def _look(tmp_path: Path, cues: list[dict], files: dict[str, bytes]) -> Path:
    root = tmp_path / "look"
    (root / "sounds").mkdir(parents=True)
    (root / "look.json").write_text(json.dumps({"name": "x", "sounds": {"cues": cues}}))
    for name, data in files.items():
        (root / "sounds" / name).write_bytes(data)
    return root


def test_a_sound_outside_the_looks_sounds_folder_is_refused(tmp_path: Path) -> None:
    (tmp_path / "secret.wav").write_bytes(b"x")
    root = _look(tmp_path, [{"on": "stage", "play": "../../secret.wav"}], {})
    with pytest.raises(DressError, match="not a file"):
        load_sound_map(root)


def test_a_look_without_a_sound_map_has_an_empty_one(tmp_path: Path) -> None:
    """Its templates may declare every sound, so no map is no error."""
    (tmp_path / "look.json").write_text(json.dumps({"name": "x"}))
    assert load_sound_map(tmp_path) == SoundMap()


def test_an_export_nothing_plays_on_is_refused(tmp_path: Path) -> None:
    root = _look(tmp_path, [{"on": "closing", "play": "a.wav"}], {"a.wav": b"x"})
    export = tmp_path / "m.mp4"
    write_timeline(timeline_path(export), (STAGE,))
    with pytest.raises(DressError, match="nothing to play"):
        dress_export(export, root)


def test_the_tick_train_is_one_input_looped(tmp_path: Path) -> None:
    placed = place_cues(
        _cues({"on": "summary", "play": "tick.wav", "every": 0.1, "for": 2.0}), [SUMMARY], tmp_path
    )
    argv = dress_command(tmp_path / "m.mp4", placed, output_path=tmp_path / "o.mp4")
    graph = argv[argv.index("-filter_complex") + 1]
    assert "atrim=end=0.1,apad=whole_len=4800,aloop=loop=19:size=4800,adelay=20000|20000" in graph
    assert argv[argv.index("-c:v") + 1] == "copy"


def test_an_export_without_a_timeline_cannot_be_dressed(tmp_path: Path) -> None:
    root = _look(tmp_path, [{"on": "stage", "play": "a.wav"}], {"a.wav": b"x"})
    with pytest.raises(DressError, match="no timeline"):
        dress_export(tmp_path / "m.mp4", root)


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _rms(path: Path, start: float, end: float) -> float:
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(pcm[int(start * 8000) : int(end * 8000)] ** 2)))


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_dressing_places_sound_on_the_item_and_keeps_the_video(tmp_path: Path) -> None:
    export = tmp_path / "m.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=black:s=320x180:r=30:d=6",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", "6", "-c:v", "libx264", "-c:a", "aac", "-shortest",
        "-metadata", "comment=Art from Somewhere.", str(export),
    )  # fmt: skip
    write_timeline(
        timeline_path(export),
        (
            TimelineEntry(kind="stage", name="stage_000", stage_index=0, start=0.0, end=3.0, beep=0.5),
            TimelineEntry(kind="summary", name="summary_000", stage_index=0, start=3.0, end=6.0),
        ),
    )
    root = _look(tmp_path, [{"on": "summary", "bed": "tone.wav", "fade": 0.5}], {})
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(root / "sounds" / "tone.wav"))

    result = dress_export(export, root)
    out = result.path
    assert result.notes == ()
    assert out == dressed_path(export) == tmp_path / "m-dressed.mp4"
    assert _rms(out, 0.5, 2.8) < 1.0, "the stage keeps its own (silent) sound"
    assert _rms(out, 3.2, 5.0) > 1000.0, "the looped bed plays under the whole summary"
    copied = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_name,nb_frames",
         "-of", "csv=p=0", str(out)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    assert copied == "h264,180"
    comment = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=comment", "-of", "csv=p=0", str(out)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    assert comment == "Art from Somewhere.", "the Look's credits stay in the dressed file"


def test_a_templates_own_cues_play_on_its_item_beside_the_looks(tmp_path: Path) -> None:
    """Cues an item's template declared ride its timeline entry: placed on
    that item alone, after the Look's own, on the same clock."""
    (tmp_path / "tick.wav").write_bytes(b"x")
    (tmp_path / "boom.wav").write_bytes(b"x")
    tally = TimelineEntry(
        kind="match_summary",
        name="match_summary",
        stage_index=None,
        start=30.0,
        end=36.0,
        cues=(
            {"play": "tick.wav", "at": 0.0},
            {"play": "tick.wav", "at": 0.4},
            {"play": "boom.wav", "at": 0.8},
        ),
    )
    other = TimelineEntry(kind="match_summary", name="other", stage_index=None, start=40.0, end=41.0)
    sounds = _cues({"on": "match_summary", "bed": "music.ogg"})
    placed, notes = place_template_cues(sounds, [tally, other], tmp_path)
    assert [(p.path.name, p.start) for p in placed] == [
        ("music.ogg", 30.0),
        ("tick.wav", 30.0),
        ("tick.wav", 30.4),
        ("boom.wav", 30.8),
        ("music.ogg", 40.0),
    ]
    assert notes == ()


def test_a_bad_template_cue_costs_that_cue_alone(tmp_path: Path) -> None:
    (tmp_path / "ok.wav").write_bytes(b"x")
    card = TimelineEntry(
        kind="transition",
        name="boundary_000",
        stage_index=None,
        start=10.0,
        end=11.5,
        cues=(
            {"play": "ok.wav", "at": 0.2},
            {"play": "../../etc/passwd"},
            {"play": "missing.wav"},
            {"bed": "ok.wav", "play": "ok.wav"},
            {"play": "ok.wav", "on": "stage"},
        ),
    )
    placed, notes = place_template_cues(SoundMap(), [card], tmp_path)
    assert [(p.path.name, p.start) for p in placed] == [("ok.wav", 10.2)]
    assert len(notes) == 4 and all(n.startswith("boundary_000: ") for n in notes)
