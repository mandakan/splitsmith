"""Fixture schema additions for multi-camera corpus support (issue #123).

Defines the ``camera``, ``anchor``, and ``history`` blocks that are
added to every fixture JSON.  The existing per-shot and candidate fields
are unchanged; these blocks are additive.

Design decisions captured in issue #97 / #123:
- ``camera.id``  identifies the physical *device* (e.g. "go3s",
  "iphone15p-friend"). It does NOT encode the mount position so that
  one device can appear in multiple roles across fixtures.
- ``camera.mount`` + ``camera.position`` together form the grouping key
  for #91 (per-camera-setup normalisation) and stratified eval.
- ``anchor`` is sidecar provenance, not structural -- loaders that don't
  need provenance ignore it. A missing ``anchor`` block means the fixture
  was hand-audited directly (the common case for headcam fixtures).
- ``history`` is append-only.  Every state change writes a row; nothing
  is silently edited.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CameraMount(StrEnum):
    head = "head"
    chest = "chest"
    belt = "belt"
    helmet = "helmet"
    hand = "hand"
    tripod = "tripod"
    monopod = "monopod"
    gimbal = "gimbal"


class CameraPosition(StrEnum):
    shooter = "shooter"
    ro = "ro"
    squadmate = "squadmate"
    bay_fixed = "bay-fixed"


class AudioSource(StrEnum):
    internal = "internal"
    lav_wired = "lav-wired"
    lav_wireless = "lav-wireless"
    shotgun_hotshoe = "shotgun-hotshoe"


class AgcState(StrEnum):
    on = "on"
    off = "off"
    unknown = "unknown"


class VenueEnvironment(StrEnum):
    indoor = "indoor"
    outdoor = "outdoor"
    covered_outdoor = "covered-outdoor"
    unknown = "unknown"


class VenueSurface(StrEnum):
    concrete = "concrete"
    asphalt = "asphalt"
    grass = "grass"
    gravel = "gravel"
    sand = "sand"
    unknown = "unknown"


class GunMuzzleDevice(StrEnum):
    none = "none"
    compensator = "comp"
    suppressor = "can"
    brake = "brake"
    unknown = "unknown"


class GunAction(StrEnum):
    semi_auto = "semi-auto"
    revolver = "revolver"
    unknown = "unknown"


class PowerFactor(StrEnum):
    minor = "minor"
    major = "major"
    unknown = "unknown"


# ---------------------------------------------------------------------------
# Venue block
# ---------------------------------------------------------------------------


class Venue(BaseModel):
    """Acoustic environment of the stage.

    ``environment`` is the primary axis for #88 (AGC behaviour differs
    significantly between indoor hard walls and open outdoor).
    ``surface`` captures floor-reflection character, which affects
    low-angle echo patterns.

    Both default to ``unknown`` so legacy fixtures can be backfilled
    without requiring research into each match location.
    """

    environment: VenueEnvironment = VenueEnvironment.unknown
    surface: VenueSurface = VenueSurface.unknown


# ---------------------------------------------------------------------------
# Gun block
# ---------------------------------------------------------------------------


class Gun(BaseModel):
    """Firearm used during this stage run.

    Affects muzzle blast shape, onset sharpness, and AGC recovery time --
    all relevant to #88 (AGC estimator) and detection precision. Calibre
    and muzzle device are the two strongest acoustic predictors.

    ``calibre`` is free-form (e.g. "9mm", ".40 S&W", "9mm major") because
    competition loads within the same calibre vary significantly.
    ``power_factor`` is the IPSC scoring class (minor / major), which
    correlates with peak SPL and is always known from match data.

    All fields default to ``unknown`` for backward compatibility.
    """

    calibre: str = "unknown"
    muzzle_device: GunMuzzleDevice = GunMuzzleDevice.unknown
    action: GunAction = GunAction.unknown
    power_factor: PowerFactor = PowerFactor.unknown


# ---------------------------------------------------------------------------
# Camera block
# ---------------------------------------------------------------------------


class Shooter(BaseModel):
    """Identifies which shooter performed the run captured by this fixture.

    Two videos from the same match + stage may capture different shooters
    (a friend films their own run on the same stage with the same gun
    rules). The event-grouping key (``event_id`` in :class:`FixtureRecord`)
    must therefore include shooter identity so multi-cam coverage of one
    shooter doesn't get cross-grouped with another shooter on the same
    physical stage.

    ``id`` is the canonical, slugified shooter key written into
    ``event_id``. Stable across promotions so siblings cluster correctly.
    Conventions:

    * ``"ssi-<shooterId>"`` -- preferred when the project's SSI shooter
      pin is known (``MatchProject.selected_shooter_id``).
    * ``"name-<slug>"`` -- when only the competitor's name is available.
    * ``"self"`` -- legacy / single-shooter fallback for projects that
      pre-date this field. The migration uses this as the default.

    ``ssi_shooter_id`` and ``name`` carry the resolvable details for the
    Lab UI; ``id`` is the load-bearing key the grouping logic depends on.
    """

    id: str
    name: str | None = None
    ssi_shooter_id: int | None = None


class Camera(BaseModel):
    """Identifies the physical device and its recording setup for this fixture.

    ``id`` is a short opaque string the user coins once per device (e.g.
    "go3s", "iphone15p-friend"). It does NOT encode mount or position so
    that the same device can appear at different mounts across fixtures.

    ``mount`` + ``position`` together describe where the device was
    placed during *this* recording and form the compound grouping key
    for per-camera-setup normalisation (#91) and stratified eval.

    ``make``, ``model``, ``sample_rate``, ``bit_depth``, ``audio_codec``
    are auto-filled from ffprobe when the source video is available; they
    may be ``None`` for legacy fixtures or pre-extracted WAV-only pairs.
    """

    id: str
    make: str | None = None
    model: str | None = None
    mount: CameraMount
    position: CameraPosition
    audio_source: AudioSource
    agc_state: AgcState = AgcState.unknown
    sample_rate: int | None = None
    bit_depth: int | None = None
    audio_codec: str | None = None


# ---------------------------------------------------------------------------
# Anchor block (only on derived fixtures)
# ---------------------------------------------------------------------------


class AnchorLink(BaseModel):
    """Provenance link from a derived fixture back to its anchor.

    ``revision_sha`` is the SHA-256 of the anchor's ``shots`` array at
    promotion time (canonical JSON, sorted keys).  When the anchor's
    shots are later edited, the sha changes and the derived fixture is
    considered stale until re-promoted.

    The block is intentionally read-only after promotion: updates come
    from a new promotion, which appends a new ``history`` entry.
    """

    fixture_slug: str
    revision_sha: str
    promoted_at: str  # ISO 8601
    offset_seconds: float
    drift_ms_per_minute: float | None = None
    snap_window_ms: int


# ---------------------------------------------------------------------------
# History block
# ---------------------------------------------------------------------------


class HistoryEntry(BaseModel):
    """One entry in the fixture's append-only audit trail."""

    at: str  # ISO 8601
    action: str  # e.g. "promote-from-anchor", "review-confirm", "manual-edit"
    tool_version: str
    details: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# SHA utilities
# ---------------------------------------------------------------------------


def shots_revision_sha(shots: list[dict[str, Any]]) -> str:
    """Stable SHA-256 of the ``shots`` array for anchor-revision tracking.

    Canonical form: ``json.dumps(shots, sort_keys=True, ensure_ascii=True)``.
    The result is a 64-character lowercase hex string.
    """
    canonical = json.dumps(shots, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def is_anchor_stale(derived_fixture: dict[str, Any], anchor_fixture: dict[str, Any]) -> bool:
    """Return True when the anchor's shots have changed since the derived fixture was promoted.

    Both arguments are the raw dicts loaded from their respective JSON files.
    Returns False when the derived fixture has no ``anchor`` block (not derived).
    """
    anchor_block = derived_fixture.get("anchor")
    if not anchor_block:
        return False
    current_sha = shots_revision_sha(anchor_fixture.get("shots", []))
    return current_sha != anchor_block.get("revision_sha", "")


# ---------------------------------------------------------------------------
# ffprobe camera-metadata extractor
# ---------------------------------------------------------------------------


class CameraProbeResult(BaseModel):
    """Camera and audio stream metadata extracted from a video file via ffprobe.

    ``suggested_id`` is a stable, normalized slug derived from make + model
    (e.g. ``"insta360-go3s"``, ``"apple-iphone15pro"``).  It uniquely
    identifies a *model*, not an individual unit -- Apple strips serial
    numbers from QuickTime metadata for privacy.  For disambiguation
    (two different iPhone 15 Pros contributing footage) the user appends a
    suffix: ``"apple-iphone15pro-friend"``.  ``None`` when make + model are
    both unavailable.
    """

    make: str | None = None
    model: str | None = None
    suggested_id: str | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    audio_codec: str | None = None


def probe_camera_metadata(
    video_path: Path,
    *,
    ffprobe_binary: str = "ffprobe",
    timeout: float = 8.0,
) -> CameraProbeResult:
    """Extract camera make/model and audio-stream details from ``video_path``.

    Pulls QuickTime format tags (``com.apple.quicktime.make`` /
    ``com.apple.quicktime.model``) for iPhones and GoPros, and the first
    audio stream's ``sample_rate``, ``bits_per_raw_sample``, and
    ``codec_name``.

    Returns a :class:`CameraProbeResult` with ``None`` for any field
    ffprobe couldn't provide.  Never raises -- on any ffprobe failure the
    result is all-``None`` so callers can fall back to user-supplied values.
    """
    if not shutil.which(ffprobe_binary):
        return CameraProbeResult()

    cmd = [
        ffprobe_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        payload = json.loads(completed.stdout)
    except Exception:
        return CameraProbeResult()

    fmt_tags: dict[str, str] = {}
    fmt = payload.get("format") or {}
    fmt_tags.update(fmt.get("tags") or {})

    make: str | None = (
        fmt_tags.get("com.apple.quicktime.make") or fmt_tags.get("make") or fmt_tags.get("Make")
    )
    model: str | None = (
        fmt_tags.get("com.apple.quicktime.model") or fmt_tags.get("model") or fmt_tags.get("Model")
    )

    sample_rate: int | None = None
    bit_depth: int | None = None
    audio_codec: str | None = None

    for stream in payload.get("streams") or []:
        if stream.get("codec_type") == "audio":
            raw_sr = stream.get("sample_rate")
            if raw_sr:
                try:
                    sample_rate = int(raw_sr)
                except (TypeError, ValueError):
                    pass

            raw_bits = stream.get("bits_per_raw_sample") or stream.get("bits_per_sample")
            if raw_bits:
                try:
                    bit_depth = int(raw_bits)
                except (TypeError, ValueError):
                    pass

            audio_codec = stream.get("codec_name") or None
            break

    suggested_id = _make_suggested_id(make, model)

    return CameraProbeResult(
        make=make or None,
        model=model or None,
        suggested_id=suggested_id,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        audio_codec=audio_codec,
    )


def _make_suggested_id(make: str | None, model: str | None) -> str | None:
    """Derive a stable camera.id slug from make + model.

    Strips punctuation, lowercases, drops redundant brand prefix when
    the model string already starts with the make (e.g. "Apple iPhone 15
    Pro" -> "apple-iphone15pro" not "apple-apple-iphone15pro").
    Returns ``None`` when both inputs are absent.
    """
    import re

    parts: list[str] = []
    if make:
        parts.append(make.lower())
    if model:
        model_lower = model.lower()
        # If the model string already starts with the make, don't double it.
        make_prefix = (make or "").lower().split()[0] if make else ""
        if make_prefix and model_lower.startswith(make_prefix):
            model_lower = model_lower[len(make_prefix) :].lstrip()
        parts.append(model_lower)

    if not parts:
        return None

    combined = "-".join(parts)
    # Collapse whitespace into nothing, then replace non-alphanumeric runs with "-".
    combined = re.sub(r"\s+", "", combined)
    combined = re.sub(r"[^a-z0-9]+", "-", combined)
    combined = combined.strip("-")
    return combined or None


# ---------------------------------------------------------------------------
# Migration helper
# ---------------------------------------------------------------------------

_TOOL_VERSION_UNKNOWN = "pre-schema"


def backfill_fixture(
    fixture_path: Path,
    camera: Camera,
    venue: Venue | None = None,
    gun: Gun | None = None,
    *,
    dry_run: bool = False,
) -> bool:
    """Add ``camera``, ``venue``, ``gun``, and empty ``history`` to an existing fixture JSON.

    Idempotent: returns ``False`` (no change) when all four blocks are
    already present.  Returns ``True`` after writing.  With ``dry_run=True``
    the return value still indicates whether the file *would* have been
    changed but nothing is written.

    ``venue`` and ``gun`` default to their respective all-``unknown`` models
    when not supplied.
    """
    raw = fixture_path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(raw)

    already_has_camera = "camera" in data
    already_has_history = "history" in data
    already_has_venue = "venue" in data
    already_has_gun = "gun" in data

    if already_has_camera and already_has_history and already_has_venue and already_has_gun:
        return False

    if not dry_run:
        if not already_has_camera:
            data["camera"] = camera.model_dump(mode="json")
        if not already_has_venue:
            data["venue"] = (venue or Venue()).model_dump(mode="json")
        if not already_has_gun:
            data["gun"] = (gun or Gun()).model_dump(mode="json")
        if not already_has_history:
            data["history"] = []
        fixture_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

    return True


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()
