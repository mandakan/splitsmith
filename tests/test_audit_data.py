"""``audit_data``: the audit JSON, and the engine records it becomes.

Both functions used to live in ``ui/exports.py``. Issue #760 moved them
into a core module because ``compare/overlay_data.py`` needed them and
reaching the web-UI layer for them closed an import cycle -- see
``tests/test_compare_overlay_data.py::test_reading_audit_data_does_not_drag_in_the_web_ui_export_layer``,
which is the assertion that move exists to satisfy.

The conversion tests came with the code. ``ui/exports.py`` still calls
both functions, so its own tests cover them through ``export_stage``;
these cover them directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith import audit_data


def _audit_payload(shots: list[dict] | None = None, beep_in_clip: float = 5.0) -> dict:
    return {
        "stage_number": 1,
        "stage_name": "Stage 1 -- H1",
        "stage_time_seconds": 8.0,
        "beep_time": beep_in_clip,
        "shots": shots if shots is not None else [],
        "_candidates_pending_audit": {
            "candidates": [
                {
                    "candidate_number": 1,
                    "time": 5.5,
                    "ms_after_beep": 500,
                    "peak_amplitude": 0.7,
                    "confidence": 0.9,
                },
                {
                    "candidate_number": 2,
                    "time": 5.9,
                    "ms_after_beep": 900,
                    "peak_amplitude": 0.6,
                    "confidence": 0.85,
                },
            ]
        },
    }


def test_audit_shots_to_engine_shots_computes_splits() -> None:
    """Shot 1's split is the draw (= time_from_beep); shot N>1 is the diff
    against the previous shot's time_from_beep. Mirrors the CLI's csv_gen
    expectations."""
    payload = _audit_payload(
        shots=[
            {"shot_number": 1, "candidate_number": 1, "time": 5.5, "ms_after_beep": 500},
            {"shot_number": 2, "candidate_number": 2, "time": 5.9, "ms_after_beep": 900},
        ]
    )
    shots = audit_data.audit_shots_to_engine_shots(payload, beep_time_in_source=10.0)
    assert [s.shot_number for s in shots] == [1, 2]
    # First shot's split == draw == time_from_beep.
    assert shots[0].split == pytest.approx(0.5)
    assert shots[1].split == pytest.approx(0.4)
    # Engine time_absolute == beep_time_in_source + time_from_beep.
    assert shots[0].time_absolute == pytest.approx(10.5)
    assert shots[1].time_absolute == pytest.approx(10.9)
    # Peak / confidence lifted from the candidate by candidate_number.
    assert shots[0].peak_amplitude == pytest.approx(0.7)
    assert shots[0].confidence == pytest.approx(0.9)


def test_audit_shots_to_engine_shots_orders_by_shot_number() -> None:
    """Shots are sorted by shot_number even if the JSON stores them out of
    order (audit-apply writes append-style; tools may reorder)."""
    payload = _audit_payload(
        shots=[
            {"shot_number": 2, "candidate_number": 2, "time": 5.9, "ms_after_beep": 900},
            {"shot_number": 1, "candidate_number": 1, "time": 5.5, "ms_after_beep": 500},
        ]
    )
    shots = audit_data.audit_shots_to_engine_shots(payload, beep_time_in_source=10.0)
    assert [s.shot_number for s in shots] == [1, 2]


def test_audit_shots_to_engine_shots_handles_manual_shot_without_candidate() -> None:
    """A manually-added shot has candidate_number=None; we still emit the
    shot but with peak/confidence defaults."""
    payload = _audit_payload(
        shots=[
            {"shot_number": 1, "candidate_number": None, "time": 5.5, "ms_after_beep": 500},
        ]
    )
    shots = audit_data.audit_shots_to_engine_shots(payload, beep_time_in_source=10.0)
    assert len(shots) == 1
    assert shots[0].peak_amplitude == 0.0
    assert shots[0].confidence == 0.0


def test_audit_shots_to_engine_shots_carries_interval_classes() -> None:
    """Coach annotations ride along onto the engine Shot (issue #772) so
    downstream consumers - the compare overlay's split statistics - can
    tell a split from a reload. Unannotated shots stay unclassified."""
    payload = _audit_payload(
        shots=[
            {
                "shot_number": 1,
                "candidate_number": 1,
                "ms_after_beep": 500,
                "interval_class": "first_shot",
                "interval_class_source": "auto",
            },
            {
                "shot_number": 2,
                "candidate_number": 2,
                "ms_after_beep": 900,
                "interval_class": "split",
                "interval_class_source": "manual",
            },
            {"shot_number": 3, "candidate_number": None, "ms_after_beep": 1400},
        ]
    )
    shots = audit_data.audit_shots_to_engine_shots(payload, beep_time_in_source=10.0)
    assert [(s.interval_class, s.interval_class_source) for s in shots] == [
        ("first_shot", "auto"),
        ("split", "manual"),
        (None, None),
    ]


def test_audit_shots_to_engine_shots_drops_invalid_interval_classes() -> None:
    """A junk or half-set annotation degrades to unclassified rather than
    failing the read - same posture as the candidate lookups."""
    payload = _audit_payload(
        shots=[
            {
                "shot_number": 1,
                "candidate_number": 1,
                "ms_after_beep": 500,
                "interval_class": "warp",
                "interval_class_source": "auto",
            },
            {
                "shot_number": 2,
                "candidate_number": 2,
                "ms_after_beep": 900,
                "interval_class": "split",
            },
        ]
    )
    shots = audit_data.audit_shots_to_engine_shots(payload, beep_time_in_source=10.0)
    assert [(s.interval_class, s.interval_class_source) for s in shots] == [
        (None, None),
        (None, None),
    ]


def test_audit_shots_to_engine_shots_preserves_notes() -> None:
    payload = _audit_payload(
        shots=[
            {
                "shot_number": 1,
                "candidate_number": 1,
                "time": 5.5,
                "ms_after_beep": 500,
                "notes": "draw",
            },
        ]
    )
    shots = audit_data.audit_shots_to_engine_shots(payload, beep_time_in_source=10.0)
    assert shots[0].notes == "draw"


def test_a_missing_audit_file_is_zero_shots_not_an_error(tmp_path: Path) -> None:
    """The audit-free export path (#619) depends on this branch.

    Detection never having run is a legitimate state -- the lossless trim
    and the FCPXML spine need only a beep and a stage time -- so an
    absent file collapses to zero shots and the shot-dependent artefacts
    skip themselves downstream.
    """
    assert audit_data.read_audit_data(tmp_path / "never-written.json") == {"shots": []}


def test_a_file_that_will_not_parse_is_a_real_fault(tmp_path: Path) -> None:
    """Distinct from "detection never ran", and must not read as zero shots."""
    path = tmp_path / "stage1.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(audit_data.StageExportError, match="failed to read audit JSON"):
        audit_data.read_audit_data(path)


def test_a_written_audit_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "stage1.json"
    payload = _audit_payload(shots=[{"shot_number": 1, "ms_after_beep": 500}])
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert audit_data.read_audit_data(path) == payload
