"""Tests for the YouTube sidecar (issue #204 layer 1).

Walks a synthetic Composition end-to-end and pins the chapter math,
description format, and .srt output. The actual JSON sidecar is
small + Pydantic-validated; covered with one round-trip assertion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith import composition, youtube_sidecar
from splitsmith.config import Shot, VideoMetadata
from splitsmith.fcpxml_gen import StageComposition


def _shot(n: int, t: float, s: float) -> Shot:
    return Shot(
        shot_number=n,
        time_absolute=10.0 + t,
        time_from_beep=t,
        split=s,
        peak_amplitude=0.5,
        confidence=0.8,
    )


def _meta_30fps(duration: float = 20.0) -> VideoMetadata:
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=duration,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _make_video(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"")
    return p


def _two_stage_composition(tmp_path: Path) -> composition.Composition:
    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    stages = [
        StageComposition(
            stage_name="Stage 1 -- Skipper",
            video_path=primary_a,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0), _shot(2, 1.3, 0.3)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        ),
        StageComposition(
            stage_name="Stage 2 -- Long Range",
            video_path=primary_b,
            video=_meta_30fps(),
            shots=[_shot(1, 0.8, 0.8)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        ),
    ]
    return composition.from_stage_compositions(stages, project_name="Bromma Spring")


# --- chapters --------------------------------------------------------------


def test_chapters_one_per_stage_with_correct_offsets(tmp_path: Path) -> None:
    """Stage A effective = 11s (head_pad=5 covers head; tail_pad=5
    leaves 5s after last shot at 6.3s -> tail_trim=8.7 -> 11.3 ->
    rounded 11s in seconds-precision math). Stage B starts at 11s."""
    comp = _two_stage_composition(tmp_path)
    chapters = youtube_sidecar._compute_chapters(comp)
    assert [c.title for c in chapters] == [
        "Stage 1 -- Skipper",
        "Stage 2 -- Long Range",
    ]
    assert chapters[0].start_seconds == 0.0
    # Stage A effective: 20 - 0 (head_trim=0) - max(0, 13.7-5) = 20-8.7 = 11.3.
    assert abs(chapters[1].start_seconds - 11.3) < 0.001


def test_intro_adds_chapter_anchor(tmp_path: Path) -> None:
    primary_a = _make_video(tmp_path, "a.mp4")
    intro = composition.Segment(
        asset=composition.Asset(
            path=_make_video(tmp_path, "intro.mp4"),
            metadata=_meta_30fps(duration=5.0),
        ),
        name="Intro",
    )
    stage = StageComposition(
        stage_name="Stage 1",
        video_path=primary_a,
        video=_meta_30fps(),
        shots=[_shot(1, 1.0, 1.0)],
        beep_offset_seconds=5.0,
        head_pad_seconds=5.0,
        tail_pad_seconds=5.0,
    )
    comp = composition.from_stage_compositions([stage], project_name="m", intro=intro)
    chapters = youtube_sidecar._compute_chapters(comp)
    assert chapters[0].title == "Intro"
    assert chapters[0].start_seconds == 0.0
    assert chapters[1].title == "Stage 1"
    # Intro is 5s -> stage 1 starts at 5.0.
    assert chapters[1].start_seconds == 5.0


def test_slate_titles_shift_chapter_offsets(tmp_path: Path) -> None:
    """A slate before stage 0 pushes its chapter time forward."""
    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    stages = [
        StageComposition(
            stage_name=f"S{i}",
            video_path=p,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        )
        for i, p in ((0, primary_a), (1, primary_b))
    ]
    comp = composition.from_stage_compositions(
        stages,
        project_name="m",
        titles={
            0: composition.TitleCard(text="S0", style="slate", duration_seconds=2.0),
            1: composition.TitleCard(text="S1", style="slate", duration_seconds=2.0),
        },
    )
    chapters = youtube_sidecar._compute_chapters(comp)
    # YouTube ignores a chapter list that does not start at 0:00, so the
    # slate in front of stage 0 gets an opener named after the match.
    # Slate S0 = 2s -> stage 0 chapter at 2s. Stage 0 effective ~11s
    # -> stage 1 slate starts at 13s, stage 1 chapter at 15s.
    assert [c.title for c in chapters] == ["m", "S0", "S1"]
    assert chapters[0].start_seconds == 0.0
    assert chapters[1].start_seconds == 2.0
    assert abs(chapters[2].start_seconds - 15.0) < 0.001


def test_title_page_and_summary_holds_shift_chapters_and_captions(tmp_path: Path) -> None:
    """The rendered MP4 (#973 / #972) puts a title page at the head and a
    summary hold after every stage; the chapters and the .srt walk that
    same spine or every timestamp after the first is early."""
    from splitsmith.stage_summary_data import TileStageData

    stages = [
        StageComposition(
            stage_name=f"S{i}",
            video_path=_make_video(tmp_path, f"{i}.mp4"),
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        )
        for i in (0, 1)
    ]
    hold = composition.SummaryHold(
        data=TileStageData(label="Me", stage_number=1), label="Me", duration_seconds=3.0
    )
    comp = composition.from_stage_compositions(
        stages,
        project_name="Bromma",
        titles={0: composition.TitleCard(text="S0", style="slate", duration_seconds=2.0)},
        title_page=composition.MatchTitle(text="Bromma", duration_seconds=4.0),
        summaries={0: hold, 1: hold},
        closing=composition.MatchTitle(text="Bromma", duration_seconds=2.0),
    )
    chapters = youtube_sidecar._compute_chapters(comp)
    # Title page 4s + slate 2s -> stage 0 at 6s; effective ~11s + hold 3s
    # -> stage 1 (no slate) at 20s. Opener at 0:00 covers the title page.
    assert [(c.title, round(c.start_seconds, 3)) for c in chapters] == [
        ("Bromma", 0.0),
        ("S0", 6.0),
        ("S1", 20.0),
    ]

    srt = tmp_path / "c.srt"
    youtube_sidecar.write_srt(comp, srt)
    blocks = srt.read_text().strip().split("\n\n")
    # Shot 1 is 1s after the beep; the beep sits at the visible head (pad
    # covers the whole 5s lead), so the caption lands 6s into stage 0's
    # action: 6 + 6 = 12s, and 20 + 6 = 26s for stage 1.
    assert blocks[0].splitlines()[1].startswith("00:00:12,000")
    assert blocks[1].splitlines()[1].startswith("00:00:26,000")


def test_format_chapter_time_under_hour() -> None:
    assert youtube_sidecar._format_chapter_time(0) == "0:00"
    assert youtube_sidecar._format_chapter_time(65.7) == "1:05"
    assert youtube_sidecar._format_chapter_time(120) == "2:00"


def test_format_chapter_time_over_hour() -> None:
    """YouTube reads ``H:MM:SS`` past 60 minutes; both forms work but
    the explicit hour reads cleanly in long match recaps."""
    assert youtube_sidecar._format_chapter_time(3661) == "1:01:01"
    assert youtube_sidecar._format_chapter_time(7200) == "2:00:00"


# --- sidecar payload ------------------------------------------------------


def test_build_sidecar_round_trips_through_json(tmp_path: Path) -> None:
    comp = _two_stage_composition(tmp_path)
    sidecar = youtube_sidecar.build_sidecar(comp)
    out = tmp_path / "match-youtube.json"
    youtube_sidecar.write_sidecar(sidecar, out)
    parsed = json.loads(out.read_text())
    assert parsed["title"] == "Bromma Spring"
    # Description has chapter lines we can validate.
    assert "0:00 Stage 1 -- Skipper" in parsed["description"]
    assert "Chapters:" in parsed["description"]
    assert parsed["chapters"][0]["title"] == "Stage 1 -- Skipper"
    assert parsed["chapters"][0]["start_seconds"] == 0.0
    # Default tags include the project name + IPSC.
    assert "Bromma Spring" in parsed["tags"]


def test_build_sidecar_uses_lead_above_chapters(tmp_path: Path) -> None:
    comp = _two_stage_composition(tmp_path)
    sidecar = youtube_sidecar.build_sidecar(
        comp,
        description_lead="Match recap from Bromma PK.",
    )
    desc_lines = sidecar.description.split("\n")
    assert desc_lines[0] == "Match recap from Bromma PK."
    # Chapter section comes after a blank line.
    assert "Chapters:" in desc_lines[2]


def test_build_sidecar_overrides_title_and_tags(tmp_path: Path) -> None:
    comp = _two_stage_composition(tmp_path)
    sidecar = youtube_sidecar.build_sidecar(
        comp,
        title="Bromma Spring 2026 -- Match Recap",
        tags=["IPSC", "Production Optics", "Bromma PK"],
    )
    assert sidecar.title == "Bromma Spring 2026 -- Match Recap"
    assert "Production Optics" in sidecar.tags
    # Project default tags are NOT auto-merged; the override replaces.
    assert "Bromma Spring" not in sidecar.tags


# --- .srt output ----------------------------------------------------------


def test_write_srt_emits_one_block_per_visible_shot(tmp_path: Path) -> None:
    comp = _two_stage_composition(tmp_path)
    out = tmp_path / "match.srt"
    youtube_sidecar.write_srt(comp, out)
    blocks = [b for b in out.read_text().split("\n\n") if b.strip()]
    # Stage A has 2 shots at +1.0s and +1.3s; Stage B has 1 shot at
    # +0.8s. All inside the visible windows -> 3 captions.
    assert len(blocks) == 3
    # First block points at stage A's shot 1. With head_trim=0, the
    # visible window starts at source-time 0; the beep is at +5s and
    # shot 1 is at +6s (clip-local). No intro / slate -> spine time
    # equals visible-window time = 6.0s.
    first = blocks[0].splitlines()
    assert first[0] == "1"
    assert first[1].startswith("00:00:06,000")
    assert "Shot 1" in first[2]


def test_write_srt_skips_shots_outside_visible_window(tmp_path: Path) -> None:
    """Tighter pads collapse the tail; out-of-window shots are
    skipped so the caption track doesn't reference vanished frames."""
    primary = _make_video(tmp_path, "a.mp4")
    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary,
            video=_meta_30fps(),
            shots=[
                _shot(1, 1.0, 1.0),  # 6s clip-local, in window
                _shot(2, 14.5, 13.5),  # 19.5s clip-local, beyond tail
            ],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=0.0,
        )
    ]
    comp = composition.from_stage_compositions(stages, project_name="m")
    out = tmp_path / "match.srt"
    youtube_sidecar.write_srt(comp, out)
    blocks = [b for b in out.read_text().split("\n\n") if b.strip()]
    assert len(blocks) == 1
    assert "Shot 1" in blocks[0]


def test_write_srt_handles_empty_composition(tmp_path: Path) -> None:
    """A stage with no markers must not raise; we still write an
    empty file so the caller can reference it consistently."""
    primary = _make_video(tmp_path, "a.mp4")
    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary,
            video=_meta_30fps(),
            shots=[],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        )
    ]
    comp = composition.from_stage_compositions(stages, project_name="m")
    out = tmp_path / "match.srt"
    youtube_sidecar.write_srt(comp, out)
    assert out.exists()
    assert out.read_text() == ""


# --- chapter markers in FCPXML --------------------------------------------


def test_fcpxml_with_chapter_markers_emits_one_per_stage(tmp_path: Path) -> None:
    """The FCPXML emitter carries chapter markers when the IR's
    ``chapter_markers`` flag is on, so an NLE-side MP4 export gets a
    populated chapter atom without any extra plumbing."""
    from xml.etree import ElementTree as ET

    from splitsmith.config import OutputConfig
    from splitsmith.fcpxml_gen import generate_match_fcpxml

    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    out = tmp_path / "match.fcpxml"
    stages = [
        StageComposition(
            stage_name=name,
            video_path=p,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        )
        for name, p in (("A", primary_a), ("B", primary_b))
    ]
    generate_match_fcpxml(
        stages=stages,
        output_path=out,
        project_name="match",
        config=OutputConfig(),
        chapter_markers=True,
    )
    root = ET.fromstring(out.read_bytes())
    chapter_markers = root.findall(".//chapter-marker")
    assert len(chapter_markers) == 2
    assert {m.attrib["value"] for m in chapter_markers} == {"A", "B"}


def test_fcpxml_chapter_markers_off_by_default(tmp_path: Path) -> None:
    """Existing exports stay unchanged when the flag isn't set."""
    from xml.etree import ElementTree as ET

    from splitsmith.config import OutputConfig
    from splitsmith.fcpxml_gen import generate_match_fcpxml

    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    out = tmp_path / "match.fcpxml"
    stages = [
        StageComposition(
            stage_name=name,
            video_path=p,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        )
        for name, p in (("A", primary_a), ("B", primary_b))
    ]
    generate_match_fcpxml(
        stages=stages,
        output_path=out,
        project_name="match",
        config=OutputConfig(),
    )
    root = ET.fromstring(out.read_bytes())
    assert root.find(".//chapter-marker") is None


# --- the paste-ready text and the thumbnail --------------------------------


def test_paste_text_carries_real_line_breaks_and_the_thumbnail_line(tmp_path: Path) -> None:
    """A description copied out of the JSON carries ``\\n`` escapes and
    YouTube shows them as text (then sees no chapters). The .txt is what
    the user pastes: real lines, the title on its own, the tags as
    Studio's comma list, the thumbnail named when there is one."""
    comp = _two_stage_composition(tmp_path)
    sidecar = youtube_sidecar.build_sidecar(
        comp, description_lead="Production Optics, head cam.", thumbnail_path=Path("m-thumbnail.jpg")
    )
    text = youtube_sidecar.paste_text(sidecar)
    assert "\\n" not in text
    lines = text.splitlines()
    assert lines[:2] == ["TITLE", "Bromma Spring"]
    assert lines[3] == "DESCRIPTION"
    assert lines[4] == "Production Optics, head cam."
    assert "0:00 Stage 1 -- Skipper" in lines
    assert "0:11 Stage 2 -- Long Range" in lines
    i = lines.index("TAGS")
    assert lines[i + 1] == ", ".join(sidecar.tags)
    assert lines[lines.index("THUMBNAIL") + 1] == "m-thumbnail.jpg"
    assert sidecar.thumbnail_path == "m-thumbnail.jpg"

    # Without a thumbnail the section is absent, not empty.
    assert "THUMBNAIL" not in youtube_sidecar.paste_text(youtube_sidecar.build_sidecar(comp))
    out = tmp_path / "m-youtube.txt"
    youtube_sidecar.write_paste_text(sidecar, out)
    assert out.read_text(encoding="utf-8") == text


def test_thumbnail_time_is_the_title_page_middle_else_the_first_shot(tmp_path: Path) -> None:
    comp = _two_stage_composition(tmp_path)
    # No title page: stage 1's first shot, 1.0 s after a beep that sits
    # at the visible head (the 5 s pad covers the whole lead).
    assert youtube_sidecar.thumbnail_time_seconds(comp) == 6.0
    with_title = composition.from_stage_compositions(
        [
            StageComposition(
                stage_name="S0",
                video_path=_make_video(tmp_path, "t.mp4"),
                video=_meta_30fps(),
                shots=[_shot(1, 1.0, 1.0)],
                beep_offset_seconds=5.0,
                head_pad_seconds=5.0,
                tail_pad_seconds=5.0,
            )
        ],
        project_name="Bromma",
        title_page=composition.MatchTitle(text="Bromma", duration_seconds=4.0),
    )
    assert youtube_sidecar.thumbnail_time_seconds(with_title) == 2.0
    # A slate before stage 1 pushes the first-shot frame back by its hold.
    slated = composition.from_stage_compositions(
        [
            StageComposition(
                stage_name="S0",
                video_path=_make_video(tmp_path, "s.mp4"),
                video=_meta_30fps(),
                shots=[],
                beep_offset_seconds=5.0,
                head_pad_seconds=2.0,
                tail_pad_seconds=5.0,
            )
        ],
        project_name="Bromma",
        titles={0: composition.TitleCard(text="S0", style="slate", duration_seconds=2.0)},
    )
    # No shot: the beep, 2 s of pad into the action, after the 2 s slate.
    assert youtube_sidecar.thumbnail_time_seconds(slated) == 4.0


def test_write_thumbnail_seeks_on_the_output_side_and_scales_to_1280(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))

    out = tmp_path / "x" / "m-thumbnail.jpg"
    youtube_sidecar.write_thumbnail(tmp_path / "m.mp4", 2.5, out, ffmpeg_binary="/bin/ffmpeg", runner=runner)
    assert out.parent.is_dir()
    cmd = calls[0]
    assert cmd[0] == "/bin/ffmpeg"
    assert cmd.index("-i") < cmd.index("-ss")
    assert cmd[cmd.index("-ss") + 1] == "2.500"
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert cmd[cmd.index("-vf") + 1] == "scale=1280:-2"
    assert cmd[-1] == str(out)


@pytest.mark.integration
def test_write_thumbnail_produces_a_1280_wide_jpeg_of_the_asked_frame(tmp_path: Path) -> None:
    import shutil
    import subprocess

    from PIL import Image

    from tests.synthetic_media import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    # Red for the first second, green after: the frame at 1.5 s is green.
    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            ffmpeg, "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=640x360:r=25:d=1",
            "-f", "lavfi", "-i", "color=c=green:s=640x360:r=25:d=2",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src),
        ],  # fmt: skip
        check=True,
        capture_output=True,
    )
    out = tmp_path / "src-thumbnail.jpg"
    youtube_sidecar.write_thumbnail(src, 1.5, out, ffmpeg_binary=ffmpeg)
    with Image.open(out) as img:
        assert img.format == "JPEG"
        assert img.size == (1280, 720)
        r, g, b = img.convert("RGB").getpixel((640, 360))
    assert g > 100 and r < 80 and b < 80, (r, g, b)
    assert out.stat().st_size < 2_000_000
