"""Camera selection: mount first, role as fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.camera_select import (
    CameraResolutionError,
    available_selectors,
    parse_camera_overrides,
    resolve_camera,
    validate_camera,
)
from splitsmith.ui.project import StageVideo


def _video(name: str, *, role: str, mount: str | None = None) -> StageVideo:
    return StageVideo(path=Path(f"/tmp/{name}.MP4"), role=role, camera_mount=mount)


def test_resolves_by_mount() -> None:
    videos = [
        _video("a", role="primary", mount="helmet"),
        _video("b", role="secondary", mount="chest"),
    ]
    assert resolve_camera(videos, "chest").path.name == "b.MP4"


def test_mount_wins_over_role_name_collision() -> None:
    """A mount literally tagged 'primary' is matched as a mount first."""
    videos = [
        _video("a", role="primary", mount="helmet"),
        _video("b", role="secondary", mount="primary"),
    ]
    assert resolve_camera(videos, "primary").path.name == "b.MP4"


def test_falls_back_to_role() -> None:
    videos = [_video("a", role="primary"), _video("b", role="secondary")]
    assert resolve_camera(videos, "primary").path.name == "a.MP4"
    assert resolve_camera(videos, "secondary").path.name == "b.MP4"


def test_none_selects_primary() -> None:
    videos = [_video("a", role="primary"), _video("b", role="secondary")]
    assert resolve_camera(videos, None).path.name == "a.MP4"


def test_secondary_role_with_two_secondaries_raises() -> None:
    """Ingest order must not decide which camera you get."""
    videos = [
        _video("a", role="primary"),
        _video("b", role="secondary"),
        _video("c", role="secondary"),
    ]
    with pytest.raises(CameraResolutionError, match="two or more secondaries"):
        resolve_camera(videos, "secondary")


def test_duplicate_mount_is_ambiguous() -> None:
    """Two cams wearing one mount tag is the same failure as two secondaries:
    ingest order would decide which angle ships, silently (#618). The message
    names both files, because the fix is re-tagging the project."""
    videos = [
        _video("a", role="primary", mount="helmet"),
        _video("b", role="secondary", mount="chest"),
        _video("c", role="secondary", mount="chest"),
    ]
    with pytest.raises(CameraResolutionError, match="2 cameras tagged 'chest'") as exc:
        resolve_camera(videos, "chest")
    assert "b.MP4" in str(exc.value)
    assert "c.MP4" in str(exc.value)


def test_duplicate_mount_ignores_ignored_videos() -> None:
    """An ignored video is not a camera, so it cannot make one ambiguous."""
    videos = [
        _video("a", role="primary", mount="helmet"),
        _video("b", role="secondary", mount="chest"),
        _video("c", role="ignored", mount="chest"),
    ]
    assert resolve_camera(videos, "chest").path.name == "b.MP4"


def test_unresolvable_on_this_stage_returns_none() -> None:
    """Absent on one stage is normal -- caller substitutes the primary."""
    videos = [_video("a", role="primary", mount="helmet")]
    assert resolve_camera(videos, "chest") is None


def test_ignored_videos_are_never_selected() -> None:
    videos = [_video("a", role="primary"), _video("b", role="ignored", mount="chest")]
    assert resolve_camera(videos, "chest") is None


def test_available_selectors_lists_mounts_and_roles() -> None:
    videos = [
        _video("a", role="primary", mount="helmet"),
        _video("b", role="secondary", mount="chest"),
    ]
    assert available_selectors(videos) == ["chest", "helmet", "primary", "secondary"]


def test_validate_camera_raises_when_never_resolvable() -> None:
    """A value matching nothing anywhere in the project is a config error."""
    stages = [[_video("a", role="primary", mount="helmet")]]
    with pytest.raises(CameraResolutionError) as exc:
        validate_camera(stages, "chest")
    assert "helmet" in str(exc.value)
    assert "primary" in str(exc.value)


def test_validate_camera_accepts_partial_availability() -> None:
    """Resolvable on at least one stage is valid; per-stage gaps are normal."""
    stages = [
        [_video("a", role="primary", mount="helmet")],
        [_video("b", role="primary", mount="helmet"), _video("c", role="secondary", mount="chest")],
    ]
    validate_camera(stages, "chest")  # must not raise


def test_parse_camera_overrides_parses_well_formed_pair() -> None:
    assert parse_camera_overrides(["mathias=chest"]) == {"mathias": "chest"}


def test_parse_camera_overrides_accumulates_multiple_pairs() -> None:
    assert parse_camera_overrides(["mathias=chest", "erik=helmet"]) == {
        "mathias": "chest",
        "erik": "helmet",
    }


def test_parse_camera_overrides_raises_on_missing_equals() -> None:
    with pytest.raises(ValueError, match="SLUG=VALUE"):
        parse_camera_overrides(["mathias"])


def test_parse_camera_overrides_raises_on_empty_slug() -> None:
    with pytest.raises(ValueError, match="SLUG=VALUE"):
        parse_camera_overrides(["=chest"])


def test_parse_camera_overrides_raises_on_empty_value() -> None:
    with pytest.raises(ValueError, match="SLUG=VALUE"):
        parse_camera_overrides(["mathias="])
