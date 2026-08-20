"""One place decides what an unreachable fixture source video means.

Five scripts read ``source_video`` to pull frames from the original
recording, and each used to skip a fixture it could not reach. A skip is
invisible in a build log that scrolls, so the corpus a model was trained
on shrank without anyone deciding it should. These pin the loud default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.fixture_sources import MissingSourceVideoError, resolve_source_video


def test_returns_the_path_when_the_video_is_reachable(tmp_path: Path) -> None:
    video = tmp_path / "stage_1.mov"
    video.write_bytes(b"not really a video, but it exists")

    resolved = resolve_source_video({"source_video": str(video)}, "stage-shots-x-stage1-s0fe3d797")

    assert resolved == video


def test_raises_naming_the_fixture_and_the_path_when_unreachable(tmp_path: Path) -> None:
    missing = tmp_path / "unmounted" / "stage_1.mov"

    with pytest.raises(MissingSourceVideoError) as excinfo:
        resolve_source_video({"source_video": str(missing)}, "stage-shots-x-stage1-s0fe3d797")

    message = str(excinfo.value)
    assert "stage-shots-x-stage1-s0fe3d797" in message
    assert str(missing) in message
    assert "--allow-missing-video" in message


def test_raises_when_the_fixture_has_no_source_video_at_all() -> None:
    with pytest.raises(MissingSourceVideoError):
        resolve_source_video({}, "stage-shots-x-stage1-s0fe3d797")

    with pytest.raises(MissingSourceVideoError):
        resolve_source_video({"source_video": ""}, "stage-shots-x-stage1-s0fe3d797")


def test_allow_missing_downgrades_both_failures_to_none(tmp_path: Path) -> None:
    missing = tmp_path / "unmounted" / "stage_1.mov"

    assert resolve_source_video({"source_video": str(missing)}, "fix", allow_missing=True) is None
    assert resolve_source_video({}, "fix", allow_missing=True) is None
