"""Tests for ``splitsmith.footage_sort`` (spec 2026-10-01).

Fixtures under ``tests/fixtures/footage_sort/`` are real: the probed
metadata of every clip of a 2026 match, every squad shooter's scorecard
times, and the stage the user assigned each clip to by hand. Built with
``scripts/build_footage_sort_fixtures.py``.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from splitsmith.footage_sort import (
    Anchor,
    Override,
    ShooterScorecards,
    SortClip,
    camera_key,
    filename_scheme,
    propose,
)

FIXTURES = Path(__file__).parent / "fixtures" / "footage_sort"
SORTED_MATCHES = [
    "blacksmith-handgun-open-2026",
    "bofors-bombardment-2026",
    "hfo-masters-2026",
    "oden-cup-2026",
    "tallmilan-2026",
    "vads-easter-shoot-2026",
]


def _load(name: str) -> tuple[dict, list[SortClip], list[ShooterScorecards]]:
    fixture = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    shooters = [
        ShooterScorecards(
            key=s["key"],
            name=s["name"],
            scorecards={
                int(k): datetime.fromisoformat(v.replace("Z", "+00:00")) for k, v in s["scorecards"].items()
            },
        )
        for s in fixture["shooters"]
    ]
    clips = [
        SortClip(
            clip_id=c["id"],
            folder=c["folder"],
            filename=c["filename"],
            start=c["start"],
            duration=c["duration"],
            make=c["make"],
            model=c["model"],
        )
        for c in fixture["clips"]
    ]
    return fixture, clips, shooters


def _labels(fixture: dict) -> dict[str, set[tuple[str, int]]]:
    return {
        c["id"]: {(lab["shooter"], lab["stage"]) for lab in c.get("labels", [])} for c in fixture["clips"]
    }


# --- real matches --------------------------------------------------------------


@pytest.mark.parametrize("name", SORTED_MATCHES)
def test_no_confident_proposal_contradicts_the_users_own_sorting(name: str) -> None:
    fixture, clips, shooters = _load(name)
    labels = _labels(fixture)

    proposal = propose(clips, shooters)

    wrong = [
        (p.clip_id, p.shooter, p.stage, sorted(labels[p.clip_id]))
        for p in proposal.clips
        if p.confidence in ("high", "medium")
        and labels[p.clip_id]
        and (p.shooter, p.stage) not in labels[p.clip_id]
    ]
    assert wrong == []


@pytest.mark.parametrize(
    ("name", "high_right"),
    [
        ("blacksmith-handgun-open-2026", 31),
        ("bofors-bombardment-2026", 18),
        ("hfo-masters-2026", 60),
        ("oden-cup-2026", 22),
        ("tallmilan-2026", 26),
        ("vads-easter-shoot-2026", 12),
    ],
)
def test_trusted_clocks_sort_nearly_everything_without_help(name: str, high_right: int) -> None:
    """Phones and head cams with right clocks: every clip the user sorted by
    hand comes out pre-checked and right, except where the scorecards
    themselves cannot tell (Oden Cup stage 11: Martin and Anton scored in
    the same second, 12 minutes after a reshoot)."""
    fixture, clips, shooters = _load(name)
    labels = _labels(fixture)

    proposal = propose(clips, shooters)

    right = sum(
        1 for p in proposal.clips if p.confidence == "high" and (p.shooter, p.stage) in labels[p.clip_id]
    )
    assert right == high_right


def test_tied_scorecards_go_to_the_user() -> None:
    """Oden Cup stage 11: both scorecards at 07:22:33. The reshoot clip
    cannot be placed on either shooter by timing."""
    _, clips, shooters = _load("oden-cup-2026")

    proposal = {p.clip_id: p for p in propose(clips, shooters).clips}

    reshoot = proposal["Anton_pov_stage_11_reshoot.mov"]
    assert reshoot.confidence == "needs_you"
    assert reshoot.reason.issue == "ambiguous"
    assert {(reshoot.shooter, reshoot.stage), (reshoot.reason.rival, reshoot.reason.rival_stage)} == {
        ("martin", 11),
        ("anton", 11),
    }


def test_shared_folders_are_sorted_blind() -> None:
    """Höstfinalen XI as received: Martin's and Anton's folders plus the
    user's head cam. Every phone clip shows someone other than the phone's
    owner (the user's account), all of them pre-checked; the Insta360 runs
    minutes fast and is held back for an anchor."""
    fixture, clips, shooters = _load("hostfinalen-xi-unsorted")
    owner = {c["id"]: c.get("not_shooter") for c in fixture["clips"]}

    proposal = propose(clips, shooters)

    phone = [p for p in proposal.clips if owner[p.clip_id]]
    assert len(phone) == 24
    assert all(p.confidence == "high" and p.shooter not in (None, owner[p.clip_id]) for p in phone)
    head_cam = next(c for c in proposal.cameras if c.scheme == "VID_datetime")
    assert head_cam.clock == "needs_anchor"
    assert {p.confidence for p in proposal.clips if p.camera_key == head_cam.key} == {"needs_you"}


@pytest.mark.parametrize(
    ("name", "expected_right"), [("hostfinalen-xi-unsorted", 8), ("ess-black-handgun-2026", 7)]
)
def test_one_anchor_sorts_the_rest_of_a_bad_clock_camera(name: str, expected_right: int) -> None:
    """Whichever clip the user names, the camera's offset follows and the
    rest sorts with no wrong proposal: the Insta360 3 minutes fast at
    Höstfinalen, 76 days off at ESS (where stages 3 and 4 were scored 10 s
    apart, so one clip stays the user's)."""
    fixture, clips, shooters = _load(name)
    labels = _labels(fixture)
    head_cam = next(c for c in propose(clips, shooters).cameras if c.clock == "needs_anchor")
    labeled = [cid for cid in head_cam.clip_ids if labels[cid]]
    assert len(labeled) == 8

    for anchor_id in labeled:
        shooter, stage = sorted(labels[anchor_id])[0]
        proposal = propose(clips, shooters, anchors=[Anchor(clip_id=anchor_id, shooter=shooter, stage=stage)])

        on_camera = [p for p in proposal.clips if p.camera_key == head_cam.key]
        verdicts = Counter(
            "right" if (p.shooter, p.stage) in labels[p.clip_id] else "wrong"
            for p in on_camera
            if p.confidence in ("high", "medium")
        )
        assert verdicts["wrong"] == 0, anchor_id
        assert verdicts["right"] >= expected_right, anchor_id
        assert next(c for c in proposal.cameras if c.key == head_cam.key).clock == "anchored"


def test_a_76_day_clock_error_is_found_by_the_anchor() -> None:
    _, clips, shooters = _load("ess-black-handgun-2026")
    anchor = Anchor(clip_id="VID_20260412_182407_00_059.mp4", shooter="mathias", stage=1)

    proposal = propose(clips, shooters, anchors=[anchor])

    assert proposal.cameras[0].offset_seconds == pytest.approx(
        timedelta(days=75, hours=16, minutes=12).total_seconds(), abs=300
    )


# --- rules on small cases ------------------------------------------------------

T0 = datetime(2026, 9, 26, 11, 0, tzinfo=UTC)


def _clip(
    clip_id: str, at_s: float, *, duration: float = 40.0, make: str | None = "Apple", folder: str = ""
) -> SortClip:
    return SortClip(
        clip_id=clip_id,
        folder=folder,
        filename=clip_id,
        start=T0 + timedelta(seconds=at_s),
        duration=duration,
        make=make,
        model=None,
    )


def _squad(**cards: dict[int, float]) -> list[ShooterScorecards]:
    return [
        ShooterScorecards(
            key=key, name=key, scorecards={st: T0 + timedelta(seconds=s) for st, s in by_stage.items()}
        )
        for key, by_stage in cards.items()
    ]


def test_filename_schemes() -> None:
    assert filename_scheme("IMG_3007.MOV") == "IMG"
    assert filename_scheme("VID_20260412_132733_00_040.mp4") == "VID_datetime"
    assert filename_scheme("video-1223_singular_display.mov") == "meta"
    assert filename_scheme("Martin_S01_B50.MOV") == "mov"


def test_same_phone_model_in_two_folders_is_two_cameras() -> None:
    a = _clip("IMG_1.MOV", 0, folder="from-martin")
    b = _clip("IMG_1.MOV", 0, folder="from-janne")
    assert camera_key(a) != camera_key(b)


def test_head_cam_and_phone_on_one_run_head_cam_primary() -> None:
    squad = _squad(mathias={1: 120.0})
    clips = [_clip("IMG_1.MOV", 5), _clip("VID_20260926_110000_00_001.mp4", 0, make="Insta360")]

    proposal = propose(clips, squad)

    assert [(p.shooter, p.stage, p.role) for p in proposal.clips] == [
        ("mathias", 1, "secondary"),
        ("mathias", 1, "primary"),
    ]
    assert {p.reason.run_size for p in proposal.clips} == {2}


def test_two_runs_on_one_stage_are_a_conflict() -> None:
    """A clip, then another of the same (shooter, stage) four minutes later
    on another camera: a reshoot or a mis-scored neighbour, never merged."""
    squad = _squad(mathias={1: 400.0}, anton={1: 900.0})
    clips = [_clip("a.MOV", 100, folder="x"), _clip("b.MOV", 340, folder="y")]

    proposal = propose(clips, squad)

    assert {(p.shooter, p.stage, p.confidence, p.reason.issue) for p in proposal.clips} == {
        ("mathias", 1, "needs_you", "conflict")
    }


def test_clip_without_a_timestamp_needs_the_user() -> None:
    clip = SortClip(clip_id="x.mp4", filename="x.mp4", duration=30.0)

    (proposal,) = propose([clip], _squad(mathias={1: 100.0})).clips

    assert (proposal.confidence, proposal.reason.issue) == ("needs_you", "no_timestamp")


def test_clip_of_nobody_on_a_trusted_clock_is_skipped() -> None:
    squad = _squad(mathias={1: 100.0, 2: 1300.0}, anton={1: 200.0, 2: 1400.0})
    clips = [_clip("run1.MOV", 0), _clip("run2.MOV", 1250), _clip("warmup.MOV", 2400)]

    proposal = {p.clip_id: p for p in propose(clips, squad).clips}

    assert (proposal["warmup.MOV"].confidence, proposal["warmup.MOV"].reason.issue) == (
        "skipped",
        "no_candidate",
    )


def test_user_override_and_skip_win() -> None:
    squad = _squad(mathias={1: 100.0}, anton={1: 400.0})
    clips = [_clip("a.MOV", 0), _clip("b.MOV", 300, folder="other")]

    proposal = {
        p.clip_id: p
        for p in propose(
            clips,
            squad,
            overrides=[
                Override(clip_id="a.MOV", shooter="anton", stage=1),
                Override(clip_id="b.MOV", skip=True),
            ],
        ).clips
    }

    assert (proposal["a.MOV"].shooter, proposal["a.MOV"].stage, proposal["a.MOV"].decided_by) == (
        "anton",
        1,
        "user",
    )
    assert (proposal["b.MOV"].confidence, proposal["b.MOV"].shooter) == ("skipped", None)


def test_a_clip_dated_away_from_the_match_is_never_silently_skipped() -> None:
    """Stockholm 2026: renamed head-cam exports from a camera whose clock was
    76 days off sit among clips with good clocks. They match no scorecard,
    and they are the user's to look at, not leftovers."""
    fixture, clips, shooters = _load("stockholm-ipsc-open-2026")
    labels = _labels(fixture)

    proposal = propose(clips, shooters)

    skipped_but_labeled = [
        p.clip_id for p in proposal.clips if p.confidence == "skipped" and labels[p.clip_id]
    ]
    assert skipped_but_labeled == []
    off_clock = [p for p in proposal.clips if p.reason.issue == "outside_match"]
    assert len(off_clock) == 11
    assert all(p.clip_id.startswith("mathias_stage_") for p in off_clock)
