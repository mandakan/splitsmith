"""Unit tests for the promote-from-anchor engine (issue #124).

Tests cover the fixture-building and drift-estimation helpers without
running real audio or ensemble models.  The full pipeline (cross-align +
ensemble + snap) is integration-tested by running the CLI against real
fixtures; those tests are marked @pytest.mark.integration and excluded
from the default run.
"""

from __future__ import annotations

from splitsmith.fixture_schema import (
    AgcState,
    AnchorLink,
    AudioSource,
    Camera,
    CameraMount,
    CameraPosition,
    HistoryEntry,
    shots_revision_sha,
)
from splitsmith.lab.promote import (
    _build_fixture,
    _build_report,
    _estimate_drift,
    _slug_from_source,
)
from splitsmith.lab.snap_window import SnapResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CAMERA = Camera(
    id="apple-iphone17pro",
    make="Apple",
    model="iPhone 17 Pro",
    mount=CameraMount.tripod,
    position=CameraPosition.bay_fixed,
    audio_source=AudioSource.internal,
    agc_state=AgcState.unknown,
    sample_rate=48000,
)

_ANCHOR_SHOTS = [
    {"shot_number": 1, "time": 2.0, "subclass": "paper"},
    {"shot_number": 2, "time": 3.0, "subclass": "paper"},
    {"shot_number": 3, "time": 4.0, "subclass": "steel"},
]

_ANCHOR_DATA = {
    "beep_time": 0.5,
    "stage_time_seconds": 10.0,
    "stage_number": 5,
    "stage_name": "K-vallen",
    "stage_rounds": {"expected": 3, "paper": 2, "poppers": 0, "plates": 1},
    "tolerance_ms": 15,
    "shots": _ANCHOR_SHOTS,
    "source": "stage 5 k-vallen (audio extracted)",
}


def _make_snap(
    shot_number: int,
    anchor_time: float,
    snapped_time: float | None,
    displacement_ms: float | None = None,
    time_since_beep_s: float = 1.5,
    sanity_flag: str = "",
) -> SnapResult:
    return SnapResult(
        shot_number=shot_number,
        anchor_time=anchor_time,
        predicted_time=anchor_time + 0.1,
        snapped_time=snapped_time,
        displacement_ms=displacement_ms,
        snap_confidence=0.9 if snapped_time is not None else None,
        time_since_beep_s=time_since_beep_s,
        sanity_flag=sanity_flag if snapped_time is None else sanity_flag,
    )


def _make_anchor_link() -> AnchorLink:
    return AnchorLink(
        fixture_slug="stage5-anchor",
        revision_sha=shots_revision_sha(_ANCHOR_SHOTS),
        promoted_at="2026-05-05T12:00:00+00:00",
        offset_seconds=0.635,
        drift_ms_per_minute=1.2,
        snap_window_ms=60,
    )


def _make_history() -> HistoryEntry:
    return HistoryEntry(
        at="2026-05-05T12:00:00+00:00",
        action="promote-from-anchor",
        tool_version="0.1.0",
        details={"snapped": 3, "missed": 0},
    )


# ---------------------------------------------------------------------------
# _estimate_drift
# ---------------------------------------------------------------------------


def test_estimate_drift_returns_none_with_one_snap() -> None:
    snaps = [_make_snap(1, 2.0, 2.1, displacement_ms=100.0, time_since_beep_s=1.5)]
    assert _estimate_drift(snaps) is None


def test_estimate_drift_flat_returns_near_zero() -> None:
    snaps = [
        _make_snap(i, float(i), float(i) + 0.09, displacement_ms=90.0, time_since_beep_s=float(i))
        for i in range(1, 6)
    ]
    drift = _estimate_drift(snaps)
    assert drift is not None
    assert abs(drift) < 1.0  # near-zero slope -> near-zero drift


def test_estimate_drift_positive_slope() -> None:
    # displacement grows linearly: +1ms per second -> +60ms/min
    snaps = [
        _make_snap(
            i,
            float(i),
            float(i) + i * 0.001,
            displacement_ms=float(i),
            time_since_beep_s=float(i),
        )
        for i in range(1, 6)
    ]
    drift = _estimate_drift(snaps)
    assert drift is not None
    assert 55.0 < drift < 65.0


def test_estimate_drift_skips_missed_shots() -> None:
    snaps = [
        _make_snap(1, 1.0, 1.09, displacement_ms=90.0, time_since_beep_s=1.0),
        _make_snap(2, 2.0, None, sanity_flag="no-candidate"),
        _make_snap(3, 3.0, 3.09, displacement_ms=90.0, time_since_beep_s=3.0),
    ]
    drift = _estimate_drift(snaps)
    assert drift is not None  # two snapped shots are enough


# ---------------------------------------------------------------------------
# _slug_from_source
# ---------------------------------------------------------------------------


def test_slug_from_source_uses_stage_fields() -> None:
    anchor = {"stage_number": 5, "stage_name": "K-vallen"}
    slug = _slug_from_source(anchor)
    assert slug == "stage5-k-vallen"


def test_slug_from_source_falls_back_to_anchor() -> None:
    slug = _slug_from_source({})
    assert slug == "anchor"


# ---------------------------------------------------------------------------
# _build_fixture
# ---------------------------------------------------------------------------


def _make_minimal_ensemble_result():
    from splitsmith.ensemble.api import EnsembleCandidate, EnsembleResult

    cands = [
        EnsembleCandidate(
            candidate_number=1,
            time=2.09,
            ms_after_beep=1590,
            peak_amplitude=0.5,
            confidence=0.9,
            vote_a=1,
            vote_b=1,
            vote_c=1,
            vote_d=1,
            vote_total=4,
            apriori_boost=0.0,
            ensemble_score=4.0,
            score_c=0.9,
            clap_diff=0.5,
            gunshot_prob=0.8,
            kept=True,
        )
    ]
    return EnsembleResult(candidates=cands, consensus=3)


def test_build_fixture_includes_camera_anchor_history() -> None:
    snaps = [
        _make_snap(1, 2.0, 2.09, displacement_ms=90.0, time_since_beep_s=1.5),
        _make_snap(2, 3.0, 3.09, displacement_ms=90.0, time_since_beep_s=2.5),
        _make_snap(3, 4.0, 4.09, displacement_ms=90.0, time_since_beep_s=3.5),
    ]
    fixture = _build_fixture(
        anchor=_ANCHOR_DATA,
        snaps=snaps,
        anchor_shots=_ANCHOR_SHOTS,
        secondary_beep_time=1.135,
        secondary_source_desc="/path/to/secondary.mov",
        slug="tallmilan-2026-stage5-phone",
        camera=_CAMERA,
        anchor_link=_make_anchor_link(),
        history_entry=_make_history(),
        ensemble_result=_make_minimal_ensemble_result(),
    )
    assert fixture["camera"]["id"] == "apple-iphone17pro"
    assert fixture["camera"]["mount"] == "tripod"
    assert fixture["anchor"]["fixture_slug"] == "stage5-anchor"
    assert len(fixture["history"]) == 1
    assert fixture["history"][0]["action"] == "promote-from-anchor"


def test_build_fixture_snapped_shots_have_promoted_source() -> None:
    snaps = [_make_snap(1, 2.0, 2.09, displacement_ms=90.0, time_since_beep_s=1.5)]
    fixture = _build_fixture(
        anchor=_ANCHOR_DATA,
        snaps=snaps,
        anchor_shots=_ANCHOR_SHOTS[:1],
        secondary_beep_time=1.135,
        secondary_source_desc="x",
        slug="slug",
        camera=_CAMERA,
        anchor_link=_make_anchor_link(),
        history_entry=_make_history(),
        ensemble_result=_make_minimal_ensemble_result(),
    )
    assert fixture["shots"][0]["source"] == "promoted"
    assert fixture["shots"][0]["time"] == 2.09


def test_build_fixture_missed_shots_have_promoted_missed_source() -> None:
    snaps = [_make_snap(1, 2.0, None, sanity_flag="no-candidate")]
    fixture = _build_fixture(
        anchor=_ANCHOR_DATA,
        snaps=snaps,
        anchor_shots=_ANCHOR_SHOTS[:1],
        secondary_beep_time=1.135,
        secondary_source_desc="x",
        slug="slug",
        camera=_CAMERA,
        anchor_link=_make_anchor_link(),
        history_entry=_make_history(),
        ensemble_result=_make_minimal_ensemble_result(),
    )
    assert fixture["shots"][0]["source"] == "promoted-missed"
    assert fixture["shots"][0]["time"] is None


def test_build_fixture_carries_subclass_from_anchor() -> None:
    snaps = [
        _make_snap(1, 2.0, 2.09, displacement_ms=90.0, time_since_beep_s=1.5),
        _make_snap(2, 4.0, 4.09, displacement_ms=90.0, time_since_beep_s=3.5),
    ]
    fixture = _build_fixture(
        anchor=_ANCHOR_DATA,
        snaps=snaps,
        anchor_shots=[
            {"shot_number": 1, "time": 2.0, "subclass": "paper"},
            {"shot_number": 2, "time": 4.0, "subclass": "steel"},
        ],
        secondary_beep_time=1.135,
        secondary_source_desc="x",
        slug="slug",
        camera=_CAMERA,
        anchor_link=_make_anchor_link(),
        history_entry=_make_history(),
        ensemble_result=_make_minimal_ensemble_result(),
    )
    assert fixture["shots"][0]["subclass"] == "paper"
    assert fixture["shots"][1]["subclass"] == "steel"


def test_build_fixture_beep_time_is_secondary_beep() -> None:
    snaps = [_make_snap(1, 2.0, 2.09, displacement_ms=90.0, time_since_beep_s=1.5)]
    fixture = _build_fixture(
        anchor=_ANCHOR_DATA,
        snaps=snaps,
        anchor_shots=_ANCHOR_SHOTS[:1],
        secondary_beep_time=1.135,
        secondary_source_desc="x",
        slug="slug",
        camera=_CAMERA,
        anchor_link=_make_anchor_link(),
        history_entry=_make_history(),
        ensemble_result=_make_minimal_ensemble_result(),
    )
    assert fixture["beep_time"] == 1.135
