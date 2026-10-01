"""Tests for video_match.match_videos_to_stages."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from splitsmith import video_match
from splitsmith.config import StageData, VideoMatchConfig
from splitsmith.video_match import match_videos_to_stages, recording_start_from_tags, video_timestamp


@pytest.fixture(autouse=True)
def _no_container_tags(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """The stat-based tests below write empty files; keep them off ffprobe so
    they exercise the filesystem fallback without shelling out. The
    integration test opts out to read real tags."""
    if "integration" in request.keywords:
        return
    monkeypatch.setattr(video_match, "read_format_tags", lambda path, **_: {})


def _make_video(path: Path, mtime_utc: datetime) -> Path:
    path.write_bytes(b"")
    ts = mtime_utc.timestamp()
    os.utime(path, (ts, ts))
    return path


def _stage(stage_number: int, scorecard_at: datetime, time_seconds: float = 15.0) -> StageData:
    return StageData(
        stage_number=stage_number,
        stage_name=f"Stage {stage_number}",
        time_seconds=time_seconds,
        scorecard_updated_at=scorecard_at,
    )


def test_clean_one_to_one_match(tmp_path: Path) -> None:
    base = datetime(2026, 4, 26, 13, 0, tzinfo=UTC)
    stage = _stage(1, base + timedelta(minutes=5))
    # Recording finished 3 minutes before the scorecard was typed in -- well
    # within the default 15-minute tolerance window.
    video = _make_video(tmp_path / "stage1.mp4", base + timedelta(minutes=2))

    result = match_videos_to_stages([video], [stage], VideoMatchConfig(prefer_ctime=False))
    assert len(result.matches) == 1
    m = result.matches[0]
    assert m.stage_number == 1
    assert m.video_path == video
    assert result.ambiguous_stages == {}
    assert result.orphan_videos == []
    assert result.unmatched_stages == []


def test_video_after_scorecard_is_orphan(tmp_path: Path) -> None:
    base = datetime(2026, 4, 26, 13, 0, tzinfo=UTC)
    stage = _stage(1, base)
    # Video timestamp is 30s AFTER the scorecard -- impossible (scorecard is
    # typed in after the stage), so no match.
    video = _make_video(tmp_path / "stage1.mp4", base + timedelta(seconds=30))

    result = match_videos_to_stages([video], [stage], VideoMatchConfig(prefer_ctime=False))
    assert result.matches == []
    assert result.unmatched_stages == [1]
    assert result.orphan_videos == [video]


def test_video_outside_tolerance_is_orphan(tmp_path: Path) -> None:
    base = datetime(2026, 4, 26, 13, 0, tzinfo=UTC)
    stage = _stage(1, base)
    # 20 minutes before scorecard, default tolerance is 15.
    video = _make_video(tmp_path / "stage1.mp4", base - timedelta(minutes=20))

    result = match_videos_to_stages(
        [video], [stage], VideoMatchConfig(tolerance_minutes=15, prefer_ctime=False)
    )
    assert result.matches == []
    assert result.unmatched_stages == [1]
    assert result.orphan_videos == [video]


def test_two_videos_in_same_window_are_ambiguous(tmp_path: Path) -> None:
    base = datetime(2026, 4, 26, 13, 0, tzinfo=UTC)
    stage = _stage(1, base + timedelta(minutes=10))
    a = _make_video(tmp_path / "a.mp4", base + timedelta(minutes=2))
    b = _make_video(tmp_path / "b.mp4", base + timedelta(minutes=4))

    result = match_videos_to_stages([a, b], [stage], VideoMatchConfig(prefer_ctime=False))
    assert result.matches == []
    assert sorted(result.ambiguous_stages.keys()) == [1]
    assert sorted(result.ambiguous_stages[1]) == sorted([a, b])
    assert result.orphan_videos == []
    assert result.unmatched_stages == []


def test_video_overlapping_two_stages_blocks_match(tmp_path: Path) -> None:
    """A video that falls inside the windows of two adjacent stages should NOT be
    silently assigned -- the SPEC says ambiguous cases need manual mapping."""
    base = datetime(2026, 4, 26, 13, 0, tzinfo=UTC)
    stage1 = _stage(1, base + timedelta(minutes=10))
    stage2 = _stage(2, base + timedelta(minutes=14))  # 4 min later
    # Video at base+8min lands in BOTH windows (default tol=15min).
    video = _make_video(tmp_path / "ambiguous.mp4", base + timedelta(minutes=8))

    result = match_videos_to_stages([video], [stage1, stage2], VideoMatchConfig(prefer_ctime=False))
    assert result.matches == []
    assert sorted(result.ambiguous_stages.keys()) == [1, 2]
    assert result.ambiguous_stages[1] == [video]
    assert result.ambiguous_stages[2] == [video]


def test_full_match_with_seven_stages(tmp_path: Path) -> None:
    """Realistic case: 7 stages, 7 videos, each video 4 minutes before its scorecard."""
    base = datetime(2026, 4, 26, 11, 0, tzinfo=UTC)
    stages = []
    videos = []
    for i in range(1, 8):
        scorecard = base + timedelta(minutes=20 * i)
        stages.append(_stage(i, scorecard))
        v = _make_video(tmp_path / f"stage{i}.mp4", scorecard - timedelta(minutes=4))
        videos.append(v)

    result = match_videos_to_stages(
        videos, stages, VideoMatchConfig(tolerance_minutes=10, prefer_ctime=False)
    )
    assert len(result.matches) == 7
    assert {m.stage_number for m in result.matches} == set(range(1, 8))
    assert {m.video_path for m in result.matches} == set(videos)
    assert result.ambiguous_stages == {}
    assert result.orphan_videos == []
    assert result.unmatched_stages == []


def test_unrelated_video_is_orphaned(tmp_path: Path) -> None:
    base = datetime(2026, 4, 26, 13, 0, tzinfo=UTC)
    stage = _stage(1, base + timedelta(minutes=5))
    matched = _make_video(tmp_path / "stage1.mp4", base + timedelta(minutes=2))
    extra = _make_video(tmp_path / "extra.mp4", base - timedelta(hours=2))

    result = match_videos_to_stages([matched, extra], [stage], VideoMatchConfig(prefer_ctime=False))
    assert len(result.matches) == 1 and result.matches[0].video_path == matched
    assert result.orphan_videos == [extra]


def test_stage_with_no_video_is_unmatched(tmp_path: Path) -> None:
    base = datetime(2026, 4, 26, 13, 0, tzinfo=UTC)
    s1 = _stage(1, base + timedelta(minutes=5))
    s2 = _stage(2, base + timedelta(hours=3))  # no video for this one
    video = _make_video(tmp_path / "stage1.mp4", base + timedelta(minutes=2))

    result = match_videos_to_stages([video], [s1, s2], VideoMatchConfig(prefer_ctime=False))
    assert {m.stage_number for m in result.matches} == {1}
    assert result.unmatched_stages == [2]


def test_video_timestamp_is_recorded_in_match(tmp_path: Path) -> None:
    base = datetime(2026, 4, 26, 13, 0, tzinfo=UTC)
    stage = _stage(1, base + timedelta(minutes=5))
    video_ts = base + timedelta(minutes=2)
    video = _make_video(tmp_path / "stage1.mp4", video_ts)

    result = match_videos_to_stages([video], [stage], VideoMatchConfig(prefer_ctime=False))
    m = result.matches[0]
    # Filesystem timestamp resolution is at most 1us; allow 1s slack.
    assert abs((m.video_timestamp - video_ts).total_seconds()) < 1.0


@pytest.mark.skipif(
    not hasattr(Path(__file__).stat(), "st_birthtime"),
    reason="st_birthtime not available on this platform",
)
def test_prefer_ctime_uses_birthtime_when_available(tmp_path: Path) -> None:
    """On macOS / APFS, st_birthtime should be preferred when prefer_ctime=True."""
    base = datetime(2026, 4, 26, 13, 0, tzinfo=UTC)
    stage = _stage(1, base + timedelta(minutes=5))
    video = tmp_path / "stage1.mp4"
    video.write_bytes(b"")
    # Set mtime to something that would NOT match the stage.
    bad_mtime = base - timedelta(hours=2)
    os.utime(video, (bad_mtime.timestamp(), bad_mtime.timestamp()))
    # st_birthtime is set by the filesystem when the file was created (just now,
    # which is well after the stage). With prefer_ctime=True, the function reads
    # birthtime, which should also fail to match (file was created "now").
    result = match_videos_to_stages([video], [stage], VideoMatchConfig(prefer_ctime=True))
    assert result.matches == []  # birthtime ~now doesn't fall in the stage window either
    # And with prefer_ctime=False, it falls back to the (bad) mtime -> still no match.
    result2 = match_videos_to_stages([video], [stage], VideoMatchConfig(prefer_ctime=False))
    assert result2.matches == []


# ---------------------------------------------------------------------------
# Window helper + classifier (production UI -- issue #13)
# ---------------------------------------------------------------------------


def test_match_window_is_asymmetric() -> None:
    """The match window ends at scorecard_updated_at and extends ``tolerance``
    backwards. The scorecard is typed *after* the run finishes, so anything
    after it can't be the recording."""
    from splitsmith.video_match import match_window

    sc = datetime(2026, 5, 2, 14, 30, 0, tzinfo=UTC)
    lower, upper = match_window(sc, tolerance_minutes=15)
    assert upper == sc
    assert lower == sc - timedelta(minutes=15)


def test_classify_video_against_stages_in_window() -> None:
    from splitsmith.config import StageData
    from splitsmith.video_match import classify_video_against_stages

    sc = datetime(2026, 5, 2, 14, 30, 0, tzinfo=UTC)
    stages = [StageData(stage_number=1, stage_name="S1", time_seconds=10.0, scorecard_updated_at=sc)]
    cls, hits = classify_video_against_stages(sc - timedelta(minutes=5), stages, tolerance_minutes=15)
    assert cls == "in_window"
    assert hits == [1]


def test_classify_video_against_stages_contested() -> None:
    from splitsmith.config import StageData
    from splitsmith.video_match import classify_video_against_stages

    # Two stages whose windows overlap.
    sc1 = datetime(2026, 5, 2, 14, 30, 0, tzinfo=UTC)
    sc2 = sc1 + timedelta(minutes=5)
    stages = [
        StageData(stage_number=1, stage_name="S1", time_seconds=10.0, scorecard_updated_at=sc1),
        StageData(stage_number=2, stage_name="S2", time_seconds=10.0, scorecard_updated_at=sc2),
    ]
    # Timestamp inside both [sc1-15, sc1] and [sc2-15, sc2].
    cls, hits = classify_video_against_stages(sc1 - timedelta(minutes=2), stages, tolerance_minutes=15)
    assert cls == "contested"
    assert sorted(hits) == [1, 2]


def test_classify_video_against_stages_orphan() -> None:
    from splitsmith.config import StageData
    from splitsmith.video_match import classify_video_against_stages

    sc = datetime(2026, 5, 2, 14, 30, 0, tzinfo=UTC)
    stages = [StageData(stage_number=1, stage_name="S1", time_seconds=10.0, scorecard_updated_at=sc)]
    # Way before any window.
    cls, hits = classify_video_against_stages(sc - timedelta(hours=3), stages, tolerance_minutes=15)
    assert cls == "orphan"
    assert hits == []


def test_classify_video_against_stages_no_timestamp() -> None:
    from splitsmith.video_match import classify_video_against_stages

    cls, hits = classify_video_against_stages(None, [], tolerance_minutes=15)
    assert cls == "no_timestamp"
    assert hits == []


# ---------------------------------------------------------------------------
# stages_in_span (coverage suggestion helper - Task 7)
# ---------------------------------------------------------------------------


def _stage_data(stage_number: int, scorecard_at: datetime, time_seconds: float = 15.0) -> StageData:
    from splitsmith.config import StageData

    return StageData(
        stage_number=stage_number,
        stage_name=f"Stage {stage_number}",
        time_seconds=time_seconds,
        scorecard_updated_at=scorecard_at,
    )


def test_stages_in_span_covers_middle_two() -> None:
    """Span covering stages 2+3 of 4 returns [2, 3] in scorecard order even
    when stage numbers are shuffled in the input list."""
    from splitsmith.video_match import stages_in_span

    base = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    # Four stages spaced 25 minutes apart.
    s1 = _stage_data(1, base + timedelta(minutes=25))
    s2 = _stage_data(2, base + timedelta(minutes=50))
    s3 = _stage_data(3, base + timedelta(minutes=75))
    s4 = _stage_data(4, base + timedelta(minutes=100))

    # Span starts 5 min before s2's window lower bound (base+35) and ends
    # after s3's scorecard (base+75) but before s4's window (base+85).
    span_start = base + timedelta(minutes=36)
    span_end = base + timedelta(minutes=76)

    # Pass stages in shuffled order to verify the result is still scorecard-sorted.
    result = stages_in_span(span_start, span_end, [s4, s2, s1, s3], tolerance_minutes=15)
    assert result == [2, 3]


def test_stages_in_span_zero_length_inside_one_window() -> None:
    """A zero-length span (start == end) inside a single window returns that stage."""
    from splitsmith.video_match import stages_in_span

    base = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    sc = base + timedelta(minutes=20)
    stage = _stage_data(5, sc)

    # Point inside [sc-15, sc].
    point = sc - timedelta(minutes=7)
    result = stages_in_span(point, point, [stage], tolerance_minutes=15)
    assert result == [5]


def test_stages_in_span_before_all_windows_returns_empty() -> None:
    """Span entirely before all windows returns an empty list."""
    from splitsmith.video_match import stages_in_span

    base = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    stages = [_stage_data(i, base + timedelta(minutes=30 * i)) for i in range(1, 5)]

    # Span ends 5 minutes before the earliest window lower bound (base+15).
    span_end = base + timedelta(minutes=10)
    result = stages_in_span(base, span_end, stages, tolerance_minutes=15)
    assert result == []


# --- embedded recording time -------------------------------------------------


def test_quicktime_creationdate_beats_creation_time() -> None:
    """Meta glasses: ``creation_time`` is the phone-app import time, nearly an
    hour after the capture here (real tags from a Blacksmith 2026 clip)."""
    tags = {
        "creation_time": "2026-04-12T12:35:48.000000Z",
        "com.apple.quicktime.creationdate": "2026-04-12T11:38:43Z",
    }
    assert recording_start_from_tags(tags) == datetime(2026, 4, 12, 11, 38, 43, tzinfo=UTC)


def test_creationdate_offset_is_normalized_to_utc() -> None:
    """iPhone writes local time with a compact offset."""
    tags = {"com.apple.quicktime.creationdate": "2026-04-12T13:41:03+0200"}
    assert recording_start_from_tags(tags) == datetime(2026, 4, 12, 11, 41, 3, tzinfo=UTC)


def test_creation_time_alone_is_used() -> None:
    """Insta360 and Android carry only ``creation_time``."""
    tags = {"creation_time": "2026-09-26T13:29:25.000000Z"}
    assert recording_start_from_tags(tags) == datetime(2026, 9, 26, 13, 29, 25, tzinfo=UTC)


@pytest.mark.parametrize(
    "tags",
    [
        {},
        {"creation_time": "1904-01-01T00:00:00.000000Z"},
        {"creation_time": "1970-01-01T00:00:00.000000Z"},
        {"creation_time": "not a date"},
        {"encoder": "Lavf61"},
    ],
)
def test_missing_or_placeholder_tags_yield_none(tags: dict[str, str]) -> None:
    assert recording_start_from_tags(tags) is None


def test_placeholder_creationdate_falls_through_to_creation_time() -> None:
    tags = {
        "com.apple.quicktime.creationdate": "1904-01-01T00:00:00Z",
        "creation_time": "2026-06-28T09:16:40.000000Z",
    }
    assert recording_start_from_tags(tags) == datetime(2026, 6, 28, 9, 16, 40, tzinfo=UTC)


def test_embedded_time_beats_filesystem_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A clip shared by a club mate carries the copy date on disk. The match
    must follow the capture time in the container, not the copy."""
    recorded = datetime(2026, 4, 12, 11, 41, 3, tzinfo=UTC)
    stage = _stage(1, recorded + timedelta(minutes=2))
    # Copied to this disk four months after the match.
    video = _make_video(tmp_path / "IMG_3007.MOV", datetime(2026, 8, 19, 21, 37, tzinfo=UTC))
    monkeypatch.setattr(
        video_match,
        "read_format_tags",
        lambda path, **_: {"com.apple.quicktime.creationdate": "2026-04-12T13:41:03+0200"},
    )

    result = match_videos_to_stages([video], [stage], VideoMatchConfig(prefer_ctime=False))
    assert [m.stage_number for m in result.matches] == [1]
    assert result.matches[0].video_timestamp == recorded


def test_head_cam_recording_past_the_scorecard_still_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A head cam started before the run and kept recording until after the
    score was typed: its start is in the window even though its end is not
    (Höstfinalen 2026 stage 1: started 14 s before the scorecard, 164 s long)."""
    scorecard = datetime(2026, 9, 26, 13, 29, 39, tzinfo=UTC)
    stage = _stage(1, scorecard)
    video = _make_video(tmp_path / "VID_20260926_152925_00_243.mp4", scorecard + timedelta(seconds=150))
    monkeypatch.setattr(
        video_match, "read_format_tags", lambda path, **_: {"creation_time": "2026-09-26T13:29:25.000000Z"}
    )

    result = match_videos_to_stages([video], [stage], VideoMatchConfig(prefer_ctime=False))
    assert [m.stage_number for m in result.matches] == [1]


@pytest.mark.integration
def test_reads_creation_time_from_a_real_container(tmp_path: Path) -> None:
    """End to end through ffprobe: a muxed ``creation_time`` wins over the
    file's own fresh birthtime/mtime."""
    from tests.synthetic_media import build_synthetic_video, ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe not on PATH")
    source = build_synthetic_video(tmp_path / "source.mp4")
    tagged = tmp_path / "tagged.mp4"
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-c",
            "copy",
            "-metadata",
            "creation_time=2026-04-12T11:27:33.000000Z",
            str(tagged),
        ],
        check=True,
    )

    assert video_timestamp(tagged, prefer_ctime=True) == datetime(2026, 4, 12, 11, 27, 33, tzinfo=UTC)


# --- several cameras on one run ----------------------------------------------


@pytest.fixture
def camera_clocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test file an embedded ``creation_time`` equal to the mtime
    the test set: the camera's own clock, as on real footage."""

    def tags(path: Path, **_: object) -> dict[str, str]:
        return {"creation_time": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()}

    monkeypatch.setattr(video_match, "read_format_tags", tags)


def test_cameras_on_one_run_match_together(tmp_path: Path, camera_clocks: None) -> None:
    """Head cam and phone on the same run (Blacksmith 2026 stage 1 started
    1 s apart): the stage matches with both, earliest first."""
    base = datetime(2026, 4, 12, 11, 27, 33, tzinfo=UTC)
    stage = _stage(1, base + timedelta(minutes=3))
    head = _make_video(tmp_path / "VID_040.mp4", base)
    phone = _make_video(tmp_path / "IMG_3005.MOV", base + timedelta(seconds=1))

    result = match_videos_to_stages([phone, head], [stage], VideoMatchConfig(prefer_ctime=False))

    assert len(result.matches) == 1
    assert result.matches[0].video_path == head
    assert result.matches[0].additional_video_paths == [phone]
    assert result.ambiguous_stages == {}
    assert result.orphan_videos == []


def test_trimmed_head_cam_export_still_joins_the_run(tmp_path: Path, camera_clocks: None) -> None:
    """The widest same-run gap measured: a trimmed head-cam export 78 s
    after the phone (HFO Masters 2026 stage 5)."""
    base = datetime(2026, 8, 2, 8, 26, tzinfo=UTC)
    stage = _stage(5, base + timedelta(minutes=4))
    phone = _make_video(tmp_path / "Mathias_S05.MOV", base)
    head = _make_video(tmp_path / "VID_085-trim.mp4", base + timedelta(seconds=78))

    result = match_videos_to_stages([phone, head], [stage], VideoMatchConfig(prefer_ctime=False))

    assert [(m.video_path, m.additional_video_paths) for m in result.matches] == [(phone, [head])]


def test_two_squad_mates_in_one_window_stay_ambiguous(tmp_path: Path, camera_clocks: None) -> None:
    """Two runs minutes apart (the closest measured: 228 s) are not one run,
    whatever camera filmed them."""
    base = datetime(2026, 9, 26, 12, 52, 4, tzinfo=UTC)
    stage = _stage(8, base + timedelta(minutes=6))
    anton = _make_video(tmp_path / "IMG_3659.mov", base)
    mathias = _make_video(tmp_path / "IMG_3660.mov", base + timedelta(seconds=228))

    result = match_videos_to_stages([anton, mathias], [stage], VideoMatchConfig(prefer_ctime=False))

    assert result.matches == []
    assert result.ambiguous_stages == {8: sorted([anton, mathias])}


def test_one_contested_camera_blocks_the_whole_run(tmp_path: Path, camera_clocks: None) -> None:
    """If any clip of the run also falls in another stage's window, the
    stage is ambiguous: no partial matches."""
    base = datetime(2026, 4, 26, 13, 0, tzinfo=UTC)
    s1 = _stage(1, base + timedelta(minutes=5))
    s2 = _stage(2, base + timedelta(minutes=15))
    a = _make_video(tmp_path / "a.mp4", base + timedelta(minutes=1))
    b = _make_video(tmp_path / "b.mp4", base + timedelta(minutes=1, seconds=30))

    result = match_videos_to_stages([a, b], [s1, s2], VideoMatchConfig(prefer_ctime=False))

    assert result.matches == []
    assert set(result.ambiguous_stages) == {1, 2}


def test_a_batch_copy_without_camera_clocks_is_not_one_run(tmp_path: Path) -> None:
    """Clips with only filesystem times, copied at the range seconds apart,
    land in one window together. Their copy times say nothing about what
    they show, so the stage stays ambiguous rather than grouping them."""
    base = datetime(2026, 4, 12, 11, 30, tzinfo=UTC)
    stage = _stage(1, base + timedelta(minutes=5))
    a = _make_video(tmp_path / "copied_1.mp4", base)
    b = _make_video(tmp_path / "copied_2.mp4", base + timedelta(seconds=4))

    result = match_videos_to_stages([a, b], [stage], VideoMatchConfig(prefer_ctime=False))

    assert result.matches == []
    assert result.ambiguous_stages == {1: [a, b]}
