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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

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


@dataclass(frozen=True)
class PlannedPromotion:
    stage: StageAngles
    video: AngleVideo
    anchor_stem: str
    slug: str
    camera: Camera | None
    skip: str | None = None


_GO3S = re.compile(r"^VID_\d{8}_\d{6}_\d{2}_\d{3}.*\.mp4$", re.IGNORECASE)
_VANGUARD = re.compile(r"^video-\d+_singular_display.*\.mov$", re.IGNORECASE)
_DJI = re.compile(r"^DJI_\d{14}_\d{4}_D.*\.mp4$", re.IGNORECASE)
_SAMSUNG = re.compile(r"^\d{8}_\d{6}.*\.mp4$", re.IGNORECASE)
_IPHONE = re.compile(r"^IMG_\d{4}.*\.mov$", re.IGNORECASE)


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


def plan_secondary_promotions(
    stages: Iterable[StageAngles],
    fixtures: Iterable[AnchorFixture],
    cameras: Mapping[str, Camera | None],
) -> list[PlannedPromotion]:
    """Every video without a fixture on a stage that has a reviewed fixture.

    ``cameras`` maps a video id to its camera block (``camera_for``'s answer).
    The anchor is the stage's reviewed fixture with the most shots; a stage
    whose only fixtures need review has no anchor (a snap of a snap). Slugs
    are ``<anchor>-<camera id>``, with the video id appended when two videos
    on the stage share a camera id or the slug is taken.
    """
    by_source: dict[str, AnchorFixture] = {}
    taken: set[str] = set()
    for f in fixtures:
        by_source.setdefault(f.source_name, f)
        taken.add(f.stem)

    plans: list[PlannedPromotion] = []
    for stage in stages:
        on_stage = [by_source[v.file_name] for v in stage.videos if v.file_name in by_source]
        anchor = _pick_anchor(on_stage)
        todo = [v for v in stage.videos if v.file_name not in by_source]
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
