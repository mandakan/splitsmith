"""Auto-classifier tests for Coach (issue #160).

Covers the gap-time rule table, manual stickiness, stale detection, and
the four edit cases the auto-classifier must handle when Audit moves /
inserts / deletes shots.
"""

from __future__ import annotations

from typing import Any

import pytest

from splitsmith.coach import (
    classify_intervals_in_dicts,
    classify_intervals_in_models,
    is_classification_stale,
    read_coach_fields,
    reload_hinted,
)
from splitsmith.config import CoachAutoClassifyConfig, Shot


@pytest.fixture
def cfg() -> CoachAutoClassifyConfig:
    return CoachAutoClassifyConfig()


def _shot(n: int, ms: int, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"shot_number": n, "ms_after_beep": ms}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------


def test_first_shot_classified_first_shot(cfg: CoachAutoClassifyConfig) -> None:
    shots = [_shot(1, 1500), _shot(2, 1700)]
    classify_intervals_in_dicts(shots, cfg)
    assert shots[0]["interval_class"] == "first_shot"
    assert shots[0]["interval_class_source"] == "auto"


def test_split_under_threshold(cfg: CoachAutoClassifyConfig) -> None:
    # gap = 0.30 s
    shots = [_shot(1, 1500), _shot(2, 1800)]
    classify_intervals_in_dicts(shots, cfg)
    assert shots[1]["interval_class"] == "split"


def test_split_at_boundary_inclusive(cfg: CoachAutoClassifyConfig) -> None:
    # gap exactly == split_max_s (0.50) -> split
    shots = [_shot(1, 1500), _shot(2, 2000)]
    classify_intervals_in_dicts(shots, cfg)
    assert shots[1]["interval_class"] == "split"


def test_transition_in_band(cfg: CoachAutoClassifyConfig) -> None:
    # gap = 0.80 s
    shots = [_shot(1, 1500), _shot(2, 2300)]
    classify_intervals_in_dicts(shots, cfg)
    assert shots[1]["interval_class"] == "transition"


def test_transition_at_boundary_inclusive(cfg: CoachAutoClassifyConfig) -> None:
    # gap exactly == 1.00
    shots = [_shot(1, 1500), _shot(2, 2500)]
    classify_intervals_in_dicts(shots, cfg)
    assert shots[1]["interval_class"] == "transition"


def test_movement_above_transition(cfg: CoachAutoClassifyConfig) -> None:
    # gap = 1.50 s
    shots = [_shot(1, 1500), _shot(2, 3000)]
    classify_intervals_in_dicts(shots, cfg)
    assert shots[1]["interval_class"] == "movement"
    assert reload_hinted(1.5, cfg) is False


def test_movement_with_reload_hint(cfg: CoachAutoClassifyConfig) -> None:
    # gap = 3.00 s -> still movement, but reload_hinted=True
    shots = [_shot(1, 1500), _shot(2, 4500)]
    classify_intervals_in_dicts(shots, cfg)
    assert shots[1]["interval_class"] == "movement"
    assert reload_hinted(3.0, cfg) is True


def test_no_auto_assigns_reload_or_activation(cfg: CoachAutoClassifyConfig) -> None:
    # Spread of gaps across the full range; only first_shot/split/transition/movement.
    shots = [
        _shot(1, 1500),
        _shot(2, 1800),  # 0.30 split
        _shot(3, 2700),  # 0.90 transition
        _shot(4, 4200),  # 1.50 movement
        _shot(5, 8000),  # 3.80 movement (reload-hinted)
    ]
    classify_intervals_in_dicts(shots, cfg)
    classes = {s["interval_class"] for s in shots}
    assert "reload" not in classes
    assert "activation" not in classes


# ---------------------------------------------------------------------------
# Manual stickiness
# ---------------------------------------------------------------------------


def test_manual_class_preserved(cfg: CoachAutoClassifyConfig) -> None:
    shots = [
        _shot(1, 1500),
        _shot(
            2,
            5000,
            interval_class="reload",
            interval_class_source="manual",
            coaching_note="reloaded after array",
        ),
    ]
    classify_intervals_in_dicts(shots, cfg)
    assert shots[1]["interval_class"] == "reload"
    assert shots[1]["interval_class_source"] == "manual"
    assert shots[1]["coaching_note"] == "reloaded after array"


def test_manual_stays_stale_when_rule_disagrees(cfg: CoachAutoClassifyConfig) -> None:
    # Auto would call this "movement"; user marked it "reload". Both stay
    # set after re-classification, and is_classification_stale flags it.
    shots = [
        _shot(1, 1500),
        _shot(2, 5000, interval_class="reload", interval_class_source="manual"),
    ]
    classify_intervals_in_dicts(shots, cfg)
    assert shots[1]["interval_class"] == "reload"
    gap_s = (5000 - 1500) / 1000.0
    assert is_classification_stale(shots[1], gap_s=gap_s, config=cfg) is True


# ---------------------------------------------------------------------------
# Stale detection on auto entries
# ---------------------------------------------------------------------------


def test_auto_stale_when_gap_changes(cfg: CoachAutoClassifyConfig) -> None:
    shots = [_shot(1, 1500), _shot(2, 1800)]
    classify_intervals_in_dicts(shots, cfg)
    assert shots[1]["interval_class"] == "split"
    # Audit edit: shift shot 2 later so the gap is now 0.70 s -> transition.
    new_gap = 0.70
    assert is_classification_stale(shots[1], gap_s=new_gap, config=cfg) is True
    # Reclassify with the new gap; stale clears.
    shots[1]["ms_after_beep"] = 2200
    classify_intervals_in_dicts(shots, cfg)
    assert shots[1]["interval_class"] == "transition"
    assert is_classification_stale(shots[1], gap_s=new_gap, config=cfg) is False


def test_unannotated_never_stale(cfg: CoachAutoClassifyConfig) -> None:
    shot = _shot(1, 1500)
    assert is_classification_stale(shot, gap_s=0.3, config=cfg) is False


# ---------------------------------------------------------------------------
# Edit handling
# ---------------------------------------------------------------------------


def test_move_timestamp_recomputes_neighbours(cfg: CoachAutoClassifyConfig) -> None:
    # Shot 2 sits at 0.30 split from shot 1, and shot 3 at 0.30 split from shot 2.
    shots = [_shot(1, 1500), _shot(2, 1800), _shot(3, 2100)]
    classify_intervals_in_dicts(shots, cfg)
    assert [s["interval_class"] for s in shots] == ["first_shot", "split", "split"]

    # Audit moves shot 2 to ms 2400 -> gap_2_from_1 = 0.90 (transition),
    # gap_3_from_2 = -0.30 ... not realistic; pick a forward move.
    shots[1]["ms_after_beep"] = 2400
    shots[2]["ms_after_beep"] = 3500  # gap_3_from_2 = 1.10 -> movement
    classify_intervals_in_dicts(shots, cfg)
    assert [s["interval_class"] for s in shots] == [
        "first_shot",
        "transition",
        "movement",
    ]


def test_move_timestamp_preserves_manual(cfg: CoachAutoClassifyConfig) -> None:
    shots = [
        _shot(1, 1500),
        _shot(2, 1800, interval_class="reload", interval_class_source="manual"),
        _shot(3, 2100),
    ]
    classify_intervals_in_dicts(shots, cfg)
    # Now move shot 1 later; shot 2 is manual so it stays "reload"; shot 3
    # gets recomputed against shot 2's new timestamp (unchanged here).
    shots[0]["ms_after_beep"] = 1700
    classify_intervals_in_dicts(shots, cfg)
    assert shots[1]["interval_class"] == "reload"
    assert shots[1]["interval_class_source"] == "manual"
    assert shots[2]["interval_class"] == "split"


def test_delete_shot_recomputes_trailing(cfg: CoachAutoClassifyConfig) -> None:
    shots = [_shot(1, 1500), _shot(2, 1800), _shot(3, 2100)]
    classify_intervals_in_dicts(shots, cfg)
    # Delete shot 2; shot 3's interval is now (2100 - 1500) = 0.60 -> transition.
    del shots[1]
    classify_intervals_in_dicts(shots, cfg)
    assert [s["interval_class"] for s in shots] == ["first_shot", "transition"]


def test_insert_shot_classifies_new_and_neighbours(cfg: CoachAutoClassifyConfig) -> None:
    shots = [_shot(1, 1500), _shot(2, 3000)]  # gap 1.5 -> movement
    classify_intervals_in_dicts(shots, cfg)
    assert shots[1]["interval_class"] == "movement"
    # User inserts a missed shot at ms 2200; reorder by shot_number.
    inserted = _shot(99, 2200)  # caller assigns proper shot_number later
    shots.insert(1, inserted)
    classify_intervals_in_dicts(shots, cfg)
    # Sort key uses ms_after_beep so the order is 1500/2200/3000.
    classified = sorted(shots, key=lambda s: s["ms_after_beep"])
    assert classified[0]["interval_class"] == "first_shot"
    assert classified[1]["interval_class"] == "transition"  # 0.70 s gap
    assert classified[2]["interval_class"] == "transition"  # 0.80 s gap


def test_bulk_redetect_drops_annotations(cfg: CoachAutoClassifyConfig) -> None:
    # Caller controls bulk re-detect; the function only sees the new list.
    # Old manual annotations on dropped shots are gone because they were
    # attached to those dicts, which the caller replaced.
    new_shots = [_shot(1, 1500), _shot(2, 1800)]
    classify_intervals_in_dicts(new_shots, cfg)
    assert new_shots[0]["interval_class"] == "first_shot"
    assert new_shots[1]["interval_class"] == "split"
    # No ghost manual data carried over -- nothing to assert beyond that
    # the new shots got auto classifications cleanly.
    for s in new_shots:
        assert s["interval_class_source"] == "auto"


# ---------------------------------------------------------------------------
# Pydantic Shot path mirrors the dict path
# ---------------------------------------------------------------------------


def _model_shot(n: int, t: float, **extra: Any) -> Shot:
    base: dict[str, Any] = {
        "shot_number": n,
        "time_absolute": t,
        "time_from_beep": t,
        "split": 0.0,
        "peak_amplitude": 0.5,
        "confidence": 0.9,
    }
    base.update(extra)
    return Shot(**base)


def test_classify_models_matches_dicts(cfg: CoachAutoClassifyConfig) -> None:
    models = [
        _model_shot(1, 1.5),
        _model_shot(2, 1.8),  # 0.30 split
        _model_shot(3, 2.7),  # 0.90 transition
    ]
    out = classify_intervals_in_models(models, cfg)
    assert [s.interval_class for s in out] == ["first_shot", "split", "transition"]
    assert all(s.interval_class_source == "auto" for s in out)
    # Inputs not mutated.
    assert all(s.interval_class is None for s in models)


def test_classify_models_preserves_manual(cfg: CoachAutoClassifyConfig) -> None:
    models = [
        _model_shot(1, 1.5),
        _model_shot(2, 5.0, interval_class="reload", interval_class_source="manual"),
    ]
    out = classify_intervals_in_models(models, cfg)
    assert out[1].interval_class == "reload"
    assert out[1].interval_class_source == "manual"


# ---------------------------------------------------------------------------
# read_coach_fields surfaces auto-written class+source pair
# ---------------------------------------------------------------------------


def test_auto_written_fields_round_trip(cfg: CoachAutoClassifyConfig) -> None:
    shots = [_shot(1, 1500), _shot(2, 1800)]
    classify_intervals_in_dicts(shots, cfg)
    fields = read_coach_fields(shots[1])
    assert fields == {"interval_class": "split", "interval_class_source": "auto"}
