"""Per-shooter camera selection for trim and compare exports.

``StageVideo.video_id`` hashes ``"<path>#<stage_number>"`` (``project.py``),
so it identifies a file on one stage, not a camera across a match. A choice
that holds for a whole match therefore keys off ``camera_mount`` (the
helmet/chest classification from issue #143) or ``role``.

Resolution is mount-first so a user who tags mounts gets the obvious
behaviour, with ``primary`` / ``secondary`` as the fallback for untagged
projects.
"""

from __future__ import annotations

from .ui.project import StageVideo

#: Role names accepted as selectors when no mount matches.
ROLE_SELECTORS = ("primary", "secondary")


class CameraResolutionError(ValueError):
    """A camera selector matches nothing in a shooter's project."""


def available_selectors(videos: list[StageVideo]) -> list[str]:
    """Every selector that could resolve against ``videos``, sorted.

    Used to build error messages that tell the user what they *can* pick.
    ``ignored`` videos contribute nothing -- they are never selectable.
    """
    selectors: set[str] = set()
    for video in videos:
        if video.role == "ignored":
            continue
        if video.camera_mount:
            selectors.add(video.camera_mount)
        if video.role in ROLE_SELECTORS:
            selectors.add(video.role)
    return sorted(selectors)


def resolve_camera(videos: list[StageVideo], camera: str | None) -> StageVideo | None:
    """Return the video ``camera`` names on this stage, or ``None``.

    ``None`` means "not on this stage" -- normal when a cam was forgotten or
    its battery died -- and the caller substitutes the primary. It does not
    mean the selector is invalid; :func:`validate_camera` decides that once,
    across the whole project.

    ``camera=None`` selects the primary, preserving pre-existing behaviour.
    """
    selectable = [v for v in videos if v.role != "ignored"]
    if camera is None:
        return next((v for v in selectable if v.role == "primary"), None)

    by_mount = [v for v in selectable if v.camera_mount == camera]
    if by_mount:
        return by_mount[0]

    if camera in ROLE_SELECTORS:
        by_role = [v for v in selectable if v.role == camera]
        if camera == "secondary" and len(by_role) > 1:
            raise CameraResolutionError(
                f"stage has two or more secondaries; select by mount instead "
                f"(available: {', '.join(available_selectors(videos))})"
            )
        if by_role:
            return by_role[0]

    return None


def validate_camera(stages_videos: list[list[StageVideo]], camera: str | None) -> None:
    """Raise when ``camera`` resolves on no stage of a shooter's project.

    Resolvable on at least one stage is enough: per-stage gaps are handled
    by substitution, but a value that matches nothing anywhere is a typo or
    a stale config and must fail loudly rather than silently exporting every
    tile from the primary.
    """
    if camera is None:
        return
    every_selector: set[str] = set()
    for videos in stages_videos:
        every_selector.update(available_selectors(videos))
        try:
            if resolve_camera(videos, camera) is not None:
                return
        except CameraResolutionError:
            # Ambiguity on one stage still proves the selector is meaningful.
            return
    raise CameraResolutionError(
        f"camera {camera!r} matches no mount or role in this project "
        f"(available: {', '.join(sorted(every_selector)) or 'none'})"
    )


def parse_camera_overrides(pairs: list[str]) -> dict[str, str]:
    """Parse ``SLUG=VALUE`` camera pairs from the CLI.

    A pair without '=' is a user error worth stopping for -- dropping it
    silently would export the wrong camera and look like it worked. Raises
    ``ValueError``; CLI callers turn that into exit code 2.
    """
    overrides: dict[str, str] = {}
    for pair in pairs:
        slug, sep, value = pair.partition("=")
        if not sep or not slug or not value:
            raise ValueError(f"--camera expects SLUG=VALUE, got {pair!r}")
        overrides[slug] = value
    return overrides
