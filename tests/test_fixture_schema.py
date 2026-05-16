"""Tests for ``splitsmith.fixture_schema`` (issue #123)."""

from __future__ import annotations

import json
from pathlib import Path

from splitsmith.fixture_schema import (
    AgcState,
    AudioSource,
    Camera,
    CameraMount,
    CameraPosition,
    Gun,
    GunAction,
    GunMuzzleDevice,
    PowerFactor,
    Venue,
    VenueEnvironment,
    VenueSurface,
    _make_suggested_id,
    backfill_fixture,
    is_anchor_stale,
    shots_revision_sha,
)

# ---------------------------------------------------------------------------
# shots_revision_sha
# ---------------------------------------------------------------------------


def test_revision_sha_is_deterministic() -> None:
    shots = [{"time": 1.0, "shot_number": 1}, {"time": 2.0, "shot_number": 2}]
    assert shots_revision_sha(shots) == shots_revision_sha(shots)


def test_revision_sha_changes_on_shot_edit() -> None:
    shots_a = [{"time": 1.0, "shot_number": 1}]
    shots_b = [{"time": 1.05, "shot_number": 1}]
    assert shots_revision_sha(shots_a) != shots_revision_sha(shots_b)


def test_revision_sha_empty_list() -> None:
    sha = shots_revision_sha([])
    assert len(sha) == 64  # hex SHA-256


def test_revision_sha_is_64_hex_chars() -> None:
    sha = shots_revision_sha([{"time": 1.0}])
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


# ---------------------------------------------------------------------------
# is_anchor_stale
# ---------------------------------------------------------------------------


def _make_fixture(shots: list[dict], anchor_sha: str | None = None) -> dict:
    base: dict = {"shots": shots}
    if anchor_sha is not None:
        base["anchor"] = {
            "fixture_slug": "some-anchor",
            "revision_sha": anchor_sha,
            "promoted_at": "2026-01-01T00:00:00+00:00",
            "offset_seconds": 0.5,
            "snap_window_ms": 60,
        }
    return base


def test_not_stale_when_sha_matches() -> None:
    shots = [{"time": 1.0}]
    sha = shots_revision_sha(shots)
    derived = _make_fixture(shots, anchor_sha=sha)
    anchor = _make_fixture(shots)
    assert not is_anchor_stale(derived, anchor)


def test_stale_when_anchor_shots_changed() -> None:
    original_shots = [{"time": 1.0}]
    sha = shots_revision_sha(original_shots)
    derived = _make_fixture(original_shots, anchor_sha=sha)
    anchor = _make_fixture([{"time": 1.05}])
    assert is_anchor_stale(derived, anchor)


def test_not_stale_when_no_anchor_block() -> None:
    # Hand-audited fixture -- no anchor block means it IS the anchor.
    fixture = {"shots": [{"time": 1.0}]}
    anchor = {"shots": [{"time": 1.0}]}
    assert not is_anchor_stale(fixture, anchor)


# ---------------------------------------------------------------------------
# _make_suggested_id
# ---------------------------------------------------------------------------


def test_suggested_id_iphone() -> None:
    result = _make_suggested_id("Apple", "iPhone 15 Pro")
    assert result == "apple-iphone15pro"


def test_suggested_id_insta360() -> None:
    result = _make_suggested_id("Insta360", "GO 3S")
    assert result == "insta360-go3s"


def test_suggested_id_no_brand_prefix_double() -> None:
    # Model already starts with make -- don't double it.
    result = _make_suggested_id("GoPro", "GoPro HERO 13")
    assert result == "gopro-hero13"


def test_suggested_id_none_when_both_missing() -> None:
    assert _make_suggested_id(None, None) is None


def test_suggested_id_model_only() -> None:
    result = _make_suggested_id(None, "HERO 13")
    assert result == "hero13"


def test_suggested_id_slugified() -> None:
    result = _make_suggested_id("Some Make", "Model X/Pro (2026)")
    assert result is not None
    assert " " not in result
    assert result == result.lower()
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in result)


# ---------------------------------------------------------------------------
# Camera model
# ---------------------------------------------------------------------------


def test_camera_round_trips_via_json() -> None:
    cam = Camera(
        id="go3s",
        make="Insta360",
        model="GO 3S",
        mount=CameraMount.head,
        position=CameraPosition.shooter,
        audio_source=AudioSource.internal,
        agc_state=AgcState.unknown,
        sample_rate=48000,
    )
    raw = cam.model_dump_json()
    cam2 = Camera.model_validate_json(raw)
    assert cam2.id == "go3s"
    assert cam2.mount == CameraMount.head
    assert cam2.position == CameraPosition.shooter


def test_camera_position_enum_serialises_as_string() -> None:
    cam = Camera(
        id="go3s",
        mount=CameraMount.head,
        position=CameraPosition.bay_fixed,
        audio_source=AudioSource.internal,
    )
    d = cam.model_dump(mode="json")
    assert d["position"] == "bay-fixed"


# ---------------------------------------------------------------------------
# backfill_fixture
# ---------------------------------------------------------------------------


def _write_fixture(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


GO3S = Camera(
    id="go3s",
    make="Insta360",
    model="GO 3S",
    mount=CameraMount.head,
    position=CameraPosition.shooter,
    audio_source=AudioSource.internal,
)


def test_backfill_adds_camera_venue_gun_and_history(tmp_path: Path) -> None:
    f = tmp_path / "fixture.json"
    _write_fixture(f, {"shots": [{"time": 1.0}]})
    changed = backfill_fixture(f, GO3S, Venue(), Gun())
    assert changed
    data = _read_fixture(f)
    assert data["camera"]["id"] == "go3s"
    assert data["camera"]["mount"] == "head"
    assert data["venue"]["environment"] == "unknown"
    assert data["venue"]["surface"] == "unknown"
    assert data["gun"]["calibre"] == "unknown"
    assert data["gun"]["muzzle_device"] == "unknown"
    assert data["history"] == []


def test_backfill_idempotent(tmp_path: Path) -> None:
    f = tmp_path / "fixture.json"
    _write_fixture(f, {"shots": [], "camera": {"id": "go3s"}, "venue": {}, "gun": {}, "history": []})
    changed = backfill_fixture(f, GO3S)
    assert not changed


def test_backfill_dry_run_does_not_write(tmp_path: Path) -> None:
    f = tmp_path / "fixture.json"
    _write_fixture(f, {"shots": [{"time": 1.0}]})
    changed = backfill_fixture(f, GO3S, Venue(), Gun(), dry_run=True)
    assert changed
    data = _read_fixture(f)
    assert "camera" not in data  # not written


def test_backfill_adds_only_missing_blocks(tmp_path: Path) -> None:
    # Has camera + venue but no gun or history.
    f = tmp_path / "fixture.json"
    _write_fixture(f, {"shots": [], "camera": {"id": "go3s"}, "venue": {}})
    changed = backfill_fixture(f, GO3S, Venue(), Gun())
    assert changed
    data = _read_fixture(f)
    assert data["history"] == []
    assert data["gun"]["calibre"] == "unknown"
    assert data["camera"]["id"] == "go3s"


def test_venue_explicit_values() -> None:
    v = Venue(environment=VenueEnvironment.indoor, surface=VenueSurface.concrete)
    d = v.model_dump(mode="json")
    assert d["environment"] == "indoor"
    assert d["surface"] == "concrete"


def test_venue_defaults_to_unknown() -> None:
    v = Venue()
    assert v.environment == VenueEnvironment.unknown
    assert v.surface == VenueSurface.unknown


def test_gun_explicit_values() -> None:
    g = Gun(
        calibre="9mm",
        muzzle_device=GunMuzzleDevice.compensator,
        action=GunAction.semi_auto,
        power_factor=PowerFactor.minor,
    )
    d = g.model_dump(mode="json")
    assert d["calibre"] == "9mm"
    assert d["muzzle_device"] == "comp"
    assert d["action"] == "semi-auto"
    assert d["power_factor"] == "minor"


def test_gun_defaults_to_unknown() -> None:
    g = Gun()
    assert g.calibre == "unknown"
    assert g.muzzle_device == GunMuzzleDevice.unknown
    assert g.action == GunAction.unknown
    assert g.power_factor == PowerFactor.unknown
