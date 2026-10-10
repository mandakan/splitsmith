"""Planning snapped promotions of secondary angles (#1363)."""

from splitsmith.fixture_schema import REVIEW_NEEDED, REVIEWED, CameraMount, CameraProbeResult
from splitsmith.lab.secondary import (
    AnchorFixture,
    AngleVideo,
    StageAngles,
    camera_for,
    plan_secondary_promotions,
)

NO_PROBE = CameraProbeResult()
IPHONE = CameraProbeResult(make="Apple", model="iPhone 17 Pro", suggested_id="apple-iphone17pro")


def test_files_are_recognised_by_name():
    go3s = camera_for("VID_20260802_112027_00_089-00.00.30.378-00.00.53.313.mp4", None, NO_PROBE)
    assert (go3s.id, go3s.mount, go3s.position.value) == ("go3s", CameraMount.head, "shooter")
    assert camera_for("video-1234_singular_display.mov", None, NO_PROBE).id == "meta-vanguard"
    assert camera_for("DJI_20260926101500_0012_D.MP4", None, NO_PROBE).mount == CameraMount.head
    samsung = camera_for("20260926_162418.mp4", None, NO_PROBE)
    assert (samsung.id, samsung.mount, samsung.position.value) == ("samsung", CameraMount.hand, "squadmate")
    phone = camera_for("IMG_3120.MOV", None, IPHONE)
    assert (phone.id, phone.model, phone.mount) == ("apple-iphone17pro", "iPhone 17 Pro", CameraMount.hand)


def test_an_unknown_file_or_a_disagreeing_mount_is_not_guessed():
    assert camera_for("Mathias_S01_B50.MOV", None, NO_PROBE) is None
    assert camera_for("IMG_3120.MOV", "head", IPHONE) is None
    assert camera_for("IMG_3120.MOV", "hand", IPHONE) is not None


def _stage(*videos: AngleVideo, n: int = 1) -> StageAngles:
    return StageAngles("m-2026", "s_1", n, videos)


def _video(vid: str, name: str, reviewed: bool = True) -> AngleVideo:
    return AngleVideo(vid, name, 10.0, reviewed)


def _cams(*pairs):
    return {
        vid: camera_for(name, None, IPHONE if name.startswith("IMG") else NO_PROBE) for vid, name in pairs
    }


def test_a_video_without_a_fixture_is_snapped_onto_the_reviewed_one():
    stage = _stage(_video("aaaaaa1", "IMG_1.MOV"), _video("bbbbbb2", "VID_20260101_101010_00_001.mp4"))
    anchor = AnchorFixture("stage-shots-m-2026-stage1-s1", "IMG_1.MOV", REVIEWED, 20)
    plans = plan_secondary_promotions([stage], [anchor], _cams(("bbbbbb2", "VID_20260101_101010_00_001.mp4")))
    assert len(plans) == 1
    p = plans[0]
    assert (p.anchor_stem, p.slug, p.skip) == (anchor.stem, f"{anchor.stem}-go3s", None)


def test_skips_say_why():
    stage = _stage(
        _video("a1", "IMG_1.MOV"),
        _video("b2", "IMG_2.MOV", reviewed=False),
        _video("c3", "clip.mp4"),
    )
    anchor = AnchorFixture("stage-shots-m-2026-stage1-s1", "IMG_1.MOV", REVIEWED, 20)
    plans = {
        p.video.video_id: p
        for p in plan_secondary_promotions([stage], [anchor], _cams(("b2", "IMG_2.MOV"), ("c3", "clip.mp4")))
    }
    assert plans["b2"].skip == "beep not reviewed"
    assert plans["c3"].skip.startswith("camera not recognised")


def test_a_snap_is_never_snapped_again():
    stage = _stage(_video("a1", "IMG_1.MOV"), _video("b2", "VID_20260101_101010_00_001.mp4"))
    derived = AnchorFixture("stage-shots-m-2026-stage1-s1-apple", "IMG_1.MOV", REVIEW_NEEDED, 20)
    (plan,) = plan_secondary_promotions([stage], [derived], _cams(("b2", "VID_20260101_101010_00_001.mp4")))
    assert plan.skip == "no reviewed fixture on this stage to snap onto"


def test_the_anchor_is_the_reviewed_fixture_with_most_shots():
    stage = _stage(
        _video("a1", "IMG_1.MOV"),
        _video("a2", "IMG_2.MOV"),
        _video("b3", "VID_20260101_101010_00_001.mp4"),
    )
    few = AnchorFixture("stage-shots-m-2026-stage1-s1-few", "IMG_1.MOV", REVIEWED, 12)
    many = AnchorFixture("stage-shots-m-2026-stage1-s1", "IMG_2.MOV", REVIEWED, 20)
    (plan,) = plan_secondary_promotions([stage], [few, many], _cams(("b3", "VID_20260101_101010_00_001.mp4")))
    assert plan.anchor_stem == many.stem


def test_two_phones_on_one_stage_get_distinct_slugs():
    stage = _stage(
        _video("a1", "VID_20260101_101010_00_001.mp4"),
        _video("b2aaaa", "IMG_2.MOV"),
        _video("c3bbbb", "IMG_3.MOV"),
    )
    anchor = AnchorFixture("stage-shots-m-2026-stage1-s1", "VID_20260101_101010_00_001.mp4", REVIEWED, 20)
    plans = plan_secondary_promotions(
        [stage], [anchor], _cams(("b2aaaa", "IMG_2.MOV"), ("c3bbbb", "IMG_3.MOV"))
    )
    assert sorted(p.slug for p in plans) == [
        f"{anchor.stem}-apple-iphone17pro-b2aaaa",
        f"{anchor.stem}-apple-iphone17pro-c3bbbb",
    ]


def test_a_taken_slug_is_not_overwritten():
    stage = _stage(_video("a1", "VID_20260101_101010_00_001.mp4"), _video("b2aaaa", "IMG_2.MOV"))
    anchor = AnchorFixture("stage-shots-m-2026-stage1-s1", "VID_20260101_101010_00_001.mp4", REVIEWED, 20)
    other = AnchorFixture(f"{anchor.stem}-apple-iphone17pro", "IMG_9.MOV", REVIEWED, 20)
    (plan,) = plan_secondary_promotions([stage], [anchor, other], _cams(("b2aaaa", "IMG_2.MOV")))
    assert plan.slug == f"{anchor.stem}-apple-iphone17pro-b2aaaa"


def test_the_projects_make_and_model_place_a_renamed_file():
    go3s = camera_for("mathias_stage_2.mp4", "head", NO_PROBE, make="Insta360", model="GO 3S")
    assert (go3s.id, go3s.mount) == ("go3s", CameraMount.head)
    vanguard = camera_for("stage3.mov", None, NO_PROBE, make="Meta", model="Vanguard")
    assert vanguard.id == "meta-vanguard"
    assert (
        camera_for("martin_8_1.MOV", "hand", IPHONE, make="Apple", model="iPhone 17 Pro").id
        == "apple-iphone17pro"
    )
    assert camera_for("mystery.mp4", "head", NO_PROBE, make="Acme", model="Cam") is None


def test_a_samsung_probe_places_a_renamed_phone_file():
    samsung = camera_for("anton_8_2.mp4", "hand", CameraProbeResult(make="Samsung"))
    assert (samsung.id, samsung.mount) == ("samsung", CameraMount.hand)
