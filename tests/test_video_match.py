"""Tests for video_match.match_videos_to_stages."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from splitsmith.config import StageData, VideoMatchConfig
from splitsmith.video_match import match_videos_to_stages


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
    from splitsmith.video_match import classify_video_against_stages
    from splitsmith.config import StageData

    sc = datetime(2026, 5, 2, 14, 30, 0, tzinfo=UTC)
    stages = [StageData(stage_number=1, stage_name="S1", time_seconds=10.0, scorecard_updated_at=sc)]
    cls, hits = classify_video_against_stages(
        sc - timedelta(minutes=5), stages, tolerance_minutes=15
    )
    assert cls == "in_window"
    assert hits == [1]


def test_classify_video_against_stages_contested() -> None:
    from splitsmith.video_match import classify_video_against_stages
    from splitsmith.config import StageData

    # Two stages whose windows overlap.
    sc1 = datetime(2026, 5, 2, 14, 30, 0, tzinfo=UTC)
    sc2 = sc1 + timedelta(minutes=5)
    stages = [
        StageData(stage_number=1, stage_name="S1", time_seconds=10.0, scorecard_updated_at=sc1),
        StageData(stage_number=2, stage_name="S2", time_seconds=10.0, scorecard_updated_at=sc2),
    ]
    # Timestamp inside both [sc1-15, sc1] and [sc2-15, sc2].
    cls, hits = classify_video_against_stages(
        sc1 - timedelta(minutes=2), stages, tolerance_minutes=15
    )
    assert cls == "contested"
    assert sorted(hits) == [1, 2]


def test_classify_video_against_stages_orphan() -> None:
    from splitsmith.video_match import classify_video_against_stages
    from splitsmith.config import StageData

    sc = datetime(2026, 5, 2, 14, 30, 0, tzinfo=UTC)
    stages = [StageData(stage_number=1, stage_name="S1", time_seconds=10.0, scorecard_updated_at=sc)]
    # Way before any window.
    cls, hits = classify_video_against_stages(
        sc - timedelta(hours=3), stages, tolerance_minutes=15
    )
    assert cls == "orphan"
    assert hits == []


def test_classify_video_against_stages_no_timestamp() -> None:
    from splitsmith.video_match import classify_video_against_stages

    cls, hits = classify_video_against_stages(None, [], tolerance_minutes=15)
    assert cls == "no_timestamp"
    assert hits == []
