"""The footage sort's recording order, file names and shooting order
(``footage_sort_names.py`` and ``footage_sort._order_pass``, spec
2026-10-22): clocks and scorecard times failed, the user knows the order
the shooter shot the stages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from splitsmith import footage_sort
from splitsmith.footage_sort import CameraShooter, Override, ShooterScorecards, SortClip, propose
from splitsmith.footage_sort_names import (
    name_number,
    name_time,
    recording_rank,
    shooter_in_name,
    stage_in_name,
)

PEOPLE = [("henrik", "Henrik Berg"), ("mathias", "Mathias Axell")]
T0 = datetime(2023, 9, 9, 9, tzinfo=UTC)


def test_name_time_and_number() -> None:
    assert name_time("PXL_20230909_101522123.mp4") == "20230909101522123"
    assert name_time("VID_20230909_101522.mp4") == "20230909101522000"
    assert name_time("IMG_0412.MOV") is None
    assert name_number("IMG_0412.MOV") == (412, 0)
    assert name_number("video-2438_singular_display.mov") == (2438, 0)
    gopro = ["GX010013.MP4", "GX020012.MP4", "GX010012.MP4"]
    assert sorted(gopro, key=name_number) == ["GX010012.MP4", "GX020012.MP4", "GX010013.MP4"]  # type: ignore[arg-type]


def test_stage_in_name() -> None:
    assert stage_in_name("henrik_stage_1.MOV") == 1
    assert stage_in_name("Albin_pov_stage_11_reshoot.mov") == 11
    assert stage_in_name("Henrik_S01_B50.MOV") == 1
    assert stage_in_name("Stage 03.mp4") == 3
    assert stage_in_name("st7.mp4") == 7
    assert stage_in_name("IMG_3.MOV") is None
    assert stage_in_name("best3.mp4") is None
    assert stage_in_name("PXL_20230909_101522123.mp4") is None


def test_shooter_in_name() -> None:
    assert shooter_in_name("Henrik", PEOPLE) == "henrik"
    assert shooter_in_name("henrik berg clips", PEOPLE) == "henrik"
    assert shooter_in_name("HENRIK_clips", PEOPLE) == "henrik"
    assert shooter_in_name("Höstfinalen XI - mathias", PEOPLE) == "mathias"
    assert shooter_in_name("henrik and mathias", PEOPLE) is None
    assert shooter_in_name("jannes", PEOPLE) is None
    assert shooter_in_name("", PEOPLE) is None


def test_recording_rank_uses_one_kind_for_the_camera() -> None:
    names = ["IMG_0003.MOV", "IMG_0001.MOV", "IMG_0002.MOV"]
    assert recording_rank(names, [None] * 3, [None] * 3, clock_ok=False) == ([1, 2, 0], "name_number")
    mixed = ["IMG_0003.MOV", "clip.mov"]
    starts = [T0 + timedelta(minutes=5), T0]
    assert recording_rank(mixed, starts, [None, None], clock_ok=False) == ([1, 0], "recorded")
    assert recording_rank(mixed, [None, None], [None, None], clock_ok=False) is None


# --- the engine ---------------------------------------------------------


def _phone(n: int, *, prefix: str = "IMG_", folder: str = "phone") -> list[SortClip]:
    """A phone with no usable clock: no recorded start at all."""
    return [
        SortClip(clip_id=f"c{i}", folder=folder, filename=f"{prefix}{i:04d}.MOV") for i in range(1, n + 1)
    ]


def _henrik(order: list[int], covered: list[int] | None = None) -> ShooterScorecards:
    return ShooterScorecards(key="henrik", name="Henrik Berg", order=order, covered=covered or [])


def _by_clip(proposal: footage_sort.SortProposal) -> dict[str, tuple[str | None, int | None, str]]:
    return {p.clip_id: (p.shooter, p.stage, p.confidence) for p in proposal.clips}


def test_equal_counts_line_up_with_the_order() -> None:
    clips = _phone(3)
    key = footage_sort.camera_key(clips[0])

    out = propose(
        clips, [_henrik([3, 1, 2])], camera_shooters=[CameraShooter(camera_key=key, shooter="henrik")]
    )

    assert _by_clip(out) == {
        "c1": ("henrik", 3, "needs_you"),
        "c2": ("henrik", 1, "needs_you"),
        "c3": ("henrik", 2, "needs_you"),
    }
    reason = next(p.reason for p in out.clips if p.clip_id == "c2")
    assert (reason.order_rank, reason.order_position, reason.order_evidence) == (2, 2, "name_number")
    assert out.cameras[0].shooter == "henrik" and out.cameras[0].shooter_source == "user"


def test_an_extra_clip_leaves_its_stretch_open() -> None:
    clips = _phone(4)
    key = footage_sort.camera_key(clips[0])

    out = propose(
        clips, [_henrik([1, 2, 3])], camera_shooters=[CameraShooter(camera_key=key, shooter="henrik")]
    )

    assert all(p.shooter is None for p in out.clips)


def test_a_placed_clip_splits_the_stretches() -> None:
    clips = _phone(5)
    key = footage_sort.camera_key(clips[0])
    # c3 is a walkthrough the user skipped; c4 is placed on stage 30. Before
    # it, two clips for two open stages; after it, one for one.
    overrides = [Override(clip_id="c3", skip=True), Override(clip_id="c4", shooter="henrik", stage=30)]

    out = propose(
        clips,
        [_henrik([10, 20, 30, 40])],
        overrides=overrides,
        camera_shooters=[CameraShooter(camera_key=key, shooter="henrik")],
    )

    got = _by_clip(out)
    assert got["c1"][:2] == ("henrik", 10) and got["c2"][:2] == ("henrik", 20)
    assert got["c5"][:2] == ("henrik", 40)
    assert got["c4"] == ("henrik", 30, "high")


def test_covered_stages_are_not_open() -> None:
    clips = _phone(2)
    key = footage_sort.camera_key(clips[0])

    out = propose(
        clips,
        [_henrik([1, 2, 3], covered=[2])],
        camera_shooters=[CameraShooter(camera_key=key, shooter="henrik")],
    )

    assert [(p.shooter, p.stage) for p in out.clips] == [("henrik", 1), ("henrik", 3)]


def test_a_decided_clip_names_the_camera() -> None:
    clips = _phone(3)

    out = propose(clips, [_henrik([1, 2, 3])], overrides=[Override(clip_id="c1", shooter="henrik", stage=1)])

    assert out.cameras[0].shooter_source == "clip"
    assert [(p.stage, p.confidence) for p in out.clips] == [(1, "high"), (2, "needs_you"), (3, "needs_you")]


def test_the_folder_names_the_camera_and_two_names_name_nobody() -> None:
    mathias = ShooterScorecards(key="mathias", name="Mathias Axell", order=[1, 2])
    out = propose(_phone(2, folder="Henrik"), [_henrik([2, 1]), mathias])
    assert out.cameras[0].shooter_source == "folder"
    assert [p.stage for p in out.clips] == [2, 1]

    both = propose(_phone(2, folder="henrik och mathias"), [_henrik([2, 1]), mathias])
    assert both.cameras[0].shooter is None and all(p.shooter is None for p in both.clips)


def test_a_stage_in_the_file_name_places_the_clip() -> None:
    clips = [
        SortClip(clip_id="a", filename="henrik_stage_2.MOV"),
        SortClip(clip_id="b", filename="henrik_stage_5.MOV"),
    ]

    henrik = _henrik([])
    henrik.stages = [1, 2, 3, 4, 5, 6]
    out = propose(clips, [henrik])

    assert _by_clip(out) == {"a": ("henrik", 2, "medium"), "b": ("henrik", 5, "medium")}
    assert all(p.reason.stage_from_name for p in out.clips)


def test_a_named_stage_must_exist_and_still_be_missing() -> None:
    clips = [
        SortClip(clip_id="a", filename="henrik_stage_99.MOV"),
        SortClip(clip_id="b", filename="henrik_stage_3.MOV"),
        SortClip(clip_id="c", filename="henrik_stage_4.MOV"),
    ]
    henrik = _henrik([], covered=[3])
    henrik.stages = [1, 2, 3, 4]

    out = propose(clips, [henrik])

    assert _by_clip(out)["a"][:2] == (None, None)
    assert _by_clip(out)["b"][:2] == (None, None)
    assert _by_clip(out)["c"] == ("henrik", 4, "medium")


def test_without_an_order_the_clips_ask_for_one() -> None:
    clips = _phone(2)
    key = footage_sort.camera_key(clips[0])

    out = propose(clips, [_henrik([])], camera_shooters=[CameraShooter(camera_key=key, shooter="henrik")])

    assert all(p.reason.issue == "needs_order" and p.shooter is None for p in out.clips)


def test_a_camera_the_clock_places_is_untouched() -> None:
    """Scorecards line the camera up: the order pass changes nothing."""
    cards = {1: T0 + timedelta(minutes=3), 2: T0 + timedelta(minutes=33)}
    clips = [
        SortClip(clip_id="x1", folder="Henrik", filename="IMG_0001.MOV", start=T0, duration=60.0),
        SortClip(
            clip_id="x2",
            folder="Henrik",
            filename="IMG_0002.MOV",
            start=T0 + timedelta(minutes=30),
            duration=60.0,
        ),
    ]
    plain = propose(clips, [ShooterScorecards(key="henrik", name="Henrik Berg", scorecards=cards)])
    ordered = propose(
        clips, [ShooterScorecards(key="henrik", name="Henrik Berg", scorecards=cards, order=[2, 1])]
    )

    assert _by_clip(plain) == _by_clip(ordered)
    assert all(p.reason.order_rank is None for p in ordered.clips)


def test_a_trusted_camera_leaves_an_unplaced_clip_alone() -> None:
    """A clip a trusted clock places on nobody (a warm-up) stays skipped,
    even with the camera's shooter and order known."""
    cards = {1: T0 + timedelta(minutes=3), 2: T0 + timedelta(minutes=33)}
    clips = [
        SortClip(clip_id="x1", folder="Henrik", filename="IMG_0001.MOV", start=T0, duration=60.0),
        SortClip(
            clip_id="x2",
            folder="Henrik",
            filename="IMG_0002.MOV",
            start=T0 + timedelta(minutes=30),
            duration=60.0,
        ),
        SortClip(
            clip_id="warm",
            folder="Henrik",
            filename="IMG_0003.MOV",
            start=T0 + timedelta(hours=2),
            duration=60.0,
        ),
    ]
    henrik = ShooterScorecards(
        key="henrik", name="Henrik Berg", scorecards=cards, order=[1, 2, 3], stages=[1, 2, 3]
    )

    out = propose(clips, [henrik])

    warm = next(p for p in out.clips if p.clip_id == "warm")
    assert out.cameras[0].clock == "trusted"
    assert (warm.shooter, warm.stage, warm.reason.order_rank) == (None, None, None)
