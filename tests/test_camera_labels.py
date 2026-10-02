from pathlib import Path

from splitsmith.match_project import StageVideo, camera_labels


def _video(make: str | None = None, model: str | None = None, mount: str | None = None) -> StageVideo:
    return StageVideo(path=Path("v.mp4"), camera_make=make, camera_model=model, camera_mount=mount)


def test_model_first_with_the_make_when_the_model_alone_says_little() -> None:
    assert camera_labels(
        [
            _video("Insta360", "GO 3S", "head"),
            _video("Apple", "iPhone 17 Pro", "hand"),
            _video(None, "Vanguard"),
        ]
    ) == ["Insta360 GO 3S", "iPhone 17 Pro", "Vanguard"]


def test_mount_then_position_when_nothing_was_probed() -> None:
    assert camera_labels([_video(mount="head"), _video(mount="hand"), _video(mount="chest"), _video()]) == [
        "Head cam",
        "Handheld",
        "Chest cam",
        "Camera 4",
    ]


def test_repeats_are_numbered() -> None:
    assert camera_labels(
        [_video("Apple", "iPhone 17 Pro"), _video("Insta360", "GO 3S"), _video("Apple", "iPhone 17 Pro")]
    ) == ["iPhone 17 Pro 1", "Insta360 GO 3S", "iPhone 17 Pro 2"]
