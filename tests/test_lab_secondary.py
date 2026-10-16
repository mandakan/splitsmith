"""Planning snapped promotions of secondary angles (#1363)."""

from splitsmith.fixture_schema import (
    REVIEW_NEEDED,
    REVIEWED,
    CameraMount,
    CameraProbeResult,
    shots_revision_sha,
)
from splitsmith.lab.secondary import (
    AnchorFixture,
    AngleVideo,
    StageAngles,
    camera_for,
    plan_secondary_promotions,
    snap_beep_now,
    snap_is_stale,
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


# --- Re-snapping a snap whose anchor changed (2026-10-14) -----------------------


GO3S = "VID_20260101_101010_00_001.mp4"


def _snapped_stage():
    stage = _stage(_video("aaaaaa1", "IMG_1.MOV"), _video("bbbbbb2", GO3S))
    anchor = AnchorFixture("stage-shots-m-2026-stage1-s1", "IMG_1.MOV", REVIEWED, 20)
    snap = AnchorFixture(f"{anchor.stem}-go3s", GO3S, REVIEW_NEEDED, 20)
    return stage, anchor, snap


def test_a_snap_whose_anchor_changed_is_snapped_again_in_place():
    stage, anchor, snap = _snapped_stage()
    asked: list[tuple[str, str]] = []

    def stale(s: AnchorFixture, a: AnchorFixture) -> bool:
        asked.append((s.stem, a.stem))
        return True

    plans = plan_secondary_promotions([stage], [anchor, snap], _cams(("bbbbbb2", GO3S)), stale_snap=stale)
    assert len(plans) == 1
    p = plans[0]
    assert (p.slug, p.anchor_stem, p.skip, p.replaces) == (snap.stem, anchor.stem, None, True)
    assert asked == [(snap.stem, anchor.stem)]


def test_a_snap_whose_anchor_is_unchanged_is_left_alone():
    stage, anchor, snap = _snapped_stage()
    plans = plan_secondary_promotions(
        [stage], [anchor, snap], _cams(("bbbbbb2", GO3S)), stale_snap=lambda s, a: False
    )
    assert plans == []


def test_a_reviewed_snap_whose_anchor_changed_is_listed_never_overwritten():
    stage, anchor, snap = _snapped_stage()
    # Fewer shots than the anchor, so it is not itself picked as the anchor.
    reviewed = AnchorFixture(snap.stem, GO3S, REVIEWED, 19)
    plans = plan_secondary_promotions(
        [stage], [anchor, reviewed], _cams(("bbbbbb2", GO3S)), stale_snap=lambda s, a: True
    )
    assert len(plans) == 1
    assert plans[0].replaces is False
    assert "reviewed" in plans[0].skip


def test_without_the_check_a_snapped_angle_is_done():
    stage, anchor, snap = _snapped_stage()
    assert plan_secondary_promotions([stage], [anchor, snap], _cams(("bbbbbb2", GO3S))) == []


# --- When a snap is stale (2026-10-14) -----------------------------------------


def _snap_doc(beep: float = 5.0, window_start: float = 1.5, seated: bool = True) -> dict:
    return {
        "beep_time": beep,
        "fixture_window_in_source": [window_start, 40.0],
        "anchor": {"revision_sha": shots_revision_sha([{"time": 1.0}]), "seated": seated},
        "shots": [],
    }


def test_a_current_snap_is_not_stale():
    assert not snap_is_stale(_snap_doc(), {"shots": [{"time": 1.0}]}, beep_now=6.5)


def test_a_snap_is_stale_when_its_anchor_changed():
    assert snap_is_stale(_snap_doc(), {"shots": [{"time": 1.2}]}, beep_now=6.5)


def test_a_snap_is_stale_when_it_predates_seating():
    assert snap_is_stale(_snap_doc(seated=False), {"shots": [{"time": 1.0}]}, beep_now=6.5)


def test_a_snap_is_stale_when_the_angle_s_beep_moved():
    """The snap aligns on this angle's own beep (beep_time + the window's
    start, in the source); moving it moves every shot."""
    assert snap_is_stale(_snap_doc(), {"shots": [{"time": 1.0}]}, beep_now=6.5709)
    assert not snap_is_stale(_snap_doc(), {"shots": [{"time": 1.0}]}, beep_now=6.5004)
    assert not snap_is_stale(_snap_doc(), {"shots": [{"time": 1.0}]}, beep_now=None)


def test_a_fixture_that_is_not_a_snap_is_never_stale():
    """A hand-audited primary can be the stage's anchor's neighbour in the
    planner's loop; it has no anchor block and must never be snapped over."""
    primary = {"beep_time": 5.0, "fixture_window_in_source": [1.5, 40.0], "shots": []}
    assert not snap_is_stale(primary, {"shots": [{"time": 1.0}]}, beep_now=9.9)


def test_a_snap_reads_the_beep_of_its_own_stage_and_file():
    """One file can hold several stages (a single take) and a file name can
    repeat across matches; the beep is the one for this file on this stage."""
    beeps = {
        ("/m/a/raw/VID_1.mp4", 1): 10.0,
        ("/m/a/raw/VID_1.mp4", 2): 200.0,
        ("/m/b/raw/VID_1.mp4", 1): 55.0,
    }
    snap = {"source_video": "/m/a/raw/VID_1.mp4", "stage_number": 2}
    assert snap_beep_now(snap, beeps) == 200.0
    assert snap_beep_now({"source_video": "/m/c/raw/VID_1.mp4", "stage_number": 2}, beeps) is None
    assert snap_beep_now({"stage_number": 2}, beeps) is None


def test_a_snap_finds_its_beep_through_a_linked_source(tmp_path):
    """A project's raw/ names its footage through links; a snap records
    the file it read. Both are the same video."""
    real = tmp_path / "raw" / "VID_1.mp4"
    real.parent.mkdir()
    real.write_bytes(b"")
    link = tmp_path / "project-raw-VID_1.mp4"
    link.symlink_to(real)
    beeps = {(str(link.resolve()), 3): 12.5}
    assert snap_beep_now({"source_video": str(real), "stage_number": 3}, beeps) == 12.5
    assert snap_beep_now({"source_video": str(link), "stage_number": 3}, beeps) == 12.5


def test_a_take_on_two_stages_snaps_each_stage_onto_its_own_anchor():
    """One headcam take is the primary on stages 7 and 8 (a multi-stage single
    take), so two fixtures share its file name. Each stage's secondary snaps
    onto its own stage's fixture, and a clip registered on both stages gets a
    snap on each. Keyed by file name alone, stage 8 found stage 7's fixture
    (Ess Black Handgun 2026)."""
    take = "VID_20260412_163829_00_053.mp4"
    anchors = [
        AnchorFixture("stage-shots-m-2026-stage7-s1", take, REVIEWED, 13, stage_number=7),
        AnchorFixture("stage-shots-m-2026-stage8-s1", take, REVIEWED, 15, stage_number=8),
    ]
    s7 = _stage(_video("t7", take), _video("a7", "IMG_2190.MOV"), _video("both7", "IMG_9000.MOV"), n=7)
    s8 = _stage(_video("t8", take), _video("b8", "IMG_2191.MOV"), _video("both8", "IMG_9000.MOV"), n=8)
    cams = _cams(
        ("a7", "IMG_2190.MOV"), ("b8", "IMG_2191.MOV"), ("both7", "IMG_9000.MOV"), ("both8", "IMG_9000.MOV")
    )

    plans = plan_secondary_promotions([s7, s8], anchors, cams)

    got = sorted((p.video.video_id, p.anchor_stem) for p in plans if p.skip is None)
    assert got == [
        ("a7", "stage-shots-m-2026-stage7-s1"),
        ("b8", "stage-shots-m-2026-stage8-s1"),
        ("both7", "stage-shots-m-2026-stage7-s1"),
        ("both8", "stage-shots-m-2026-stage8-s1"),
    ]
    assert all(p.slug.startswith(p.anchor_stem) for p in plans)
