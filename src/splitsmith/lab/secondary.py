"""Plan snapped promotions of every secondary angle (#1363).

Pure: the caller hands in each multi-angle stage's videos and the fixture
corpus, and gets back which videos to snap onto which anchor fixture, as
which camera, under which slug, and why the rest are skipped. The script
``scripts/promote_secondary_angles.py`` does the I/O.

How a file is recognised is ``docs/cameras.md``'s table; a file it cannot
place is skipped and listed, never guessed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..fixture_schema import (
    REVIEWED,
    AudioSource,
    Camera,
    CameraMount,
    CameraPosition,
    CameraProbeResult,
)


@dataclass(frozen=True)
class AngleVideo:
    """One video on a stage, as the project records it."""

    video_id: str
    file_name: str
    beep_time: float | None
    beep_reviewed: bool
    mount: str | None = None


@dataclass(frozen=True)
class StageAngles:
    match: str
    shooter_slug: str
    stage_number: int
    videos: tuple[AngleVideo, ...]


@dataclass(frozen=True)
class AnchorFixture:
    """The parts of an existing fixture the planner needs."""

    stem: str
    source_name: str
    review_status: str
    n_shots: int
    #: The stage the fixture labels (from its ``event_id``). A multi-stage
    #: single take is the source of one fixture per stage, so a file name
    #: alone does not say which; ``None`` on fixtures without an event id.
    stage_number: int | None = None


@dataclass(frozen=True)
class PlannedPromotion:
    stage: StageAngles
    video: AngleVideo
    anchor_stem: str
    slug: str
    camera: Camera | None
    skip: str | None = None
    #: Snaps again over this angle's existing snap (``slug`` is its stem):
    #: the anchor's shots changed after it was snapped.
    replaces: bool = False


_GO3S = re.compile(r"^VID_\d{8}_\d{6}_\d{2}_\d{3}.*\.mp4$", re.IGNORECASE)
_VANGUARD = re.compile(r"^video-\d+_singular_display.*\.mov$", re.IGNORECASE)
_DJI = re.compile(r"^DJI_\d{14}_\d{4}_D.*\.mp4$", re.IGNORECASE)
_SAMSUNG = re.compile(r"^\d{8}_\d{6}.*\.mp4$", re.IGNORECASE)
_IPHONE = re.compile(r"^IMG_\d{4}.*\.mov$", re.IGNORECASE)
_PIXEL = re.compile(r"^PXL_\d{8}_\d{9}.*\.mp4$", re.IGNORECASE)


def camera_for(
    file_name: str,
    mount_hint: str | None,
    probe: CameraProbeResult,
    *,
    make: str | None = None,
    model: str | None = None,
) -> Camera | None:
    """The camera block for a video, or ``None`` when it cannot be placed.

    The make and model the project recorded for the video win (a renamed file
    keeps them); otherwise the file name decides the device
    (``docs/cameras.md``). ffprobe fills in what it can. A handheld camera is
    filmed by someone else (``squadmate``), a head camera by the shooter.
    """
    name = file_name
    known = f"{make or probe.make or ''} {model or probe.model or ''}".strip().lower()
    if known == "insta360 go 3s" or _GO3S.match(name):
        cid, make, model, mount = "go3s", "Insta360", "GO 3S", CameraMount.head
    elif known.endswith("vanguard") or _VANGUARD.match(name):
        cid, make, model, mount = "meta-vanguard", "Meta", "Vanguard", CameraMount.head
    elif known.startswith("dji") or _DJI.match(name):
        cid, make, model, mount = "dji-osmoaction4", "DJI", "Osmo Action 4", CameraMount.head
    elif known.startswith("samsung") or _SAMSUNG.match(name):
        cid, make, model, mount = "samsung", "Samsung", None, CameraMount.hand
    elif known.startswith("google") or _PIXEL.match(name):
        cid, make, model, mount = "google-pixel", "Google", None, CameraMount.hand
    elif _IPHONE.match(name) or "apple" in ((probe.make or "").lower(), known.split(" ")[0]):
        cid = probe.suggested_id or "apple-iphone"
        make, model, mount = probe.make or "Apple", probe.model, CameraMount.hand
    else:
        return None
    if mount_hint and mount_hint != mount.value:
        return None  # the project and the file disagree: a person decides
    return Camera(
        id=cid,
        make=probe.make or make,
        model=probe.model or model,
        mount=mount,
        position=CameraPosition.shooter if mount == CameraMount.head else CameraPosition.squadmate,
        audio_source=AudioSource.internal,
        sample_rate=probe.sample_rate,
        bit_depth=probe.bit_depth,
        audio_codec=probe.audio_codec,
    )


def _pick_anchor(candidates: list[AnchorFixture]) -> AnchorFixture | None:
    reviewed = [a for a in candidates if a.review_status == REVIEWED and a.n_shots > 0]
    if not reviewed:
        return None
    return sorted(reviewed, key=lambda a: (-a.n_shots, a.stem))[0]


#: A beep this far from the one a snap aligned on means the snap is stale.
BEEP_MOVED_S = 0.001


def snap_is_stale(snap: dict, anchor: dict, *, beep_now: float | None) -> bool:
    """Whether a snapped fixture must be snapped again: its anchor's shots
    changed (``is_anchor_stale``), it predates seating on the rise foot (no
    ``anchor.seated``), or this angle's beep moved since (``beep_now``, the
    video's beep in source seconds, against the fixture's beep plus its
    window's start: the snap aligns on it, so every shot moves with it)."""
    from ..fixture_schema import is_anchor_stale

    # Only a snap can be stale: a fixture with no anchor block is a hand
    # audit, and snapping over it would throw that audit away.
    if not snap.get("anchor"):
        return False
    if is_anchor_stale(snap, anchor) or not snap["anchor"].get("seated"):
        return True
    window = snap.get("fixture_window_in_source") or [0.0]
    beep = snap.get("beep_time")
    if beep_now is None or beep is None:
        return False
    return abs(float(beep) + float(window[0]) - float(beep_now)) > BEEP_MOVED_S


def snap_beep_now(snap: dict, beeps: dict[tuple[str, int], float | None]) -> float | None:
    """This snap's angle's beep as the project has it now, from ``beeps``
    keyed by (the source video's resolved path, stage number): one file can
    hold several stages and a file name repeats across matches. None when
    the snap names no source or the project has no such angle."""
    source = snap.get("source_video")
    stage = snap.get("stage_number")
    if not source or stage is None:
        return None
    return beeps.get((str(Path(source).resolve()), int(stage)))


def plan_secondary_promotions(
    stages: Iterable[StageAngles],
    fixtures: Iterable[AnchorFixture],
    cameras: Mapping[str, Camera | None],
    *,
    stale_snap: Callable[[AnchorFixture, AnchorFixture], bool] | None = None,
) -> list[PlannedPromotion]:
    """Every video without a fixture on a stage that has a reviewed fixture.

    ``cameras`` maps a video id to its camera block (``camera_for``'s answer).
    The anchor is the stage's reviewed fixture with the most shots; a stage
    whose only fixtures need review has no anchor (a snap of a snap). Slugs
    are ``<anchor>-<camera id>``, with the video id appended when two videos
    on the stage share a camera id or the slug is taken.

    With ``stale_snap`` (``fixture_schema.is_anchor_stale`` over the two
    fixtures' JSON), an angle whose fixture is a snap the anchor changed
    since is planned again in place (``replaces``): re-promoting a primary
    must reach its secondaries. A snap someone reviewed by hand is listed
    with the reason, never overwritten.
    """
    # A fixture is found by its stage and file: a multi-stage single take is
    # the primary (or a secondary) on several stages, one fixture each, and
    # by file name alone every stage found the first (Ess Black Handgun 2026).
    by_stage_file: dict[tuple[int, str], AnchorFixture] = {}
    by_file: dict[str, AnchorFixture] = {}
    taken: set[str] = set()
    for f in fixtures:
        if f.stage_number is not None:
            by_stage_file.setdefault((f.stage_number, f.source_name), f)
        else:
            by_file.setdefault(f.source_name, f)
        taken.add(f.stem)

    def fixture_of(stage: StageAngles, file_name: str) -> AnchorFixture | None:
        return by_stage_file.get((stage.stage_number, file_name)) or by_file.get(file_name)

    plans: list[PlannedPromotion] = []
    for stage in stages:
        on_stage = [f for v in stage.videos if (f := fixture_of(stage, v.file_name)) is not None]
        anchor = _pick_anchor(on_stage)
        todo = [v for v in stage.videos if fixture_of(stage, v.file_name) is None]
        if stale_snap is not None and anchor is not None:
            for v in stage.videos:
                f = fixture_of(stage, v.file_name)
                if f is None or f.stem == anchor.stem or not stale_snap(f, anchor):
                    continue
                camera = cameras.get(v.video_id)
                if f.review_status == REVIEWED:
                    skip = "a snap reviewed by hand; its anchor or beep changed since, snap it again by hand"
                    plans.append(PlannedPromotion(stage, v, anchor.stem, f.stem, camera, skip))
                elif v.beep_time is None or not v.beep_reviewed:
                    plans.append(PlannedPromotion(stage, v, anchor.stem, f.stem, camera, "beep not reviewed"))
                elif camera is None:
                    skip = "camera not recognised; set the video's mount or extend docs/cameras.md"
                    plans.append(PlannedPromotion(stage, v, anchor.stem, f.stem, camera, skip))
                else:
                    plans.append(PlannedPromotion(stage, v, anchor.stem, f.stem, camera, replaces=True))
        if not todo or not on_stage:
            continue
        ids = [c.id for v in todo if (c := cameras.get(v.video_id)) is not None]
        for video in todo:
            camera = cameras.get(video.video_id)
            skip = None
            if anchor is None:
                skip = "no reviewed fixture on this stage to snap onto"
            elif video.beep_time is None or not video.beep_reviewed:
                skip = "beep not reviewed"
            elif camera is None:
                skip = "camera not recognised; set the video's mount or extend docs/cameras.md"
            anchor_stem = anchor.stem if anchor else on_stage[0].stem
            slug = f"{anchor_stem}-{camera.id if camera else 'unknown'}"
            if camera is not None and (ids.count(camera.id) > 1 or slug in taken):
                slug = f"{slug}-{video.video_id[:6]}"
            if skip is None:
                taken.add(slug)
            plans.append(PlannedPromotion(stage, video, anchor_stem, slug, camera, skip))
    return plans
