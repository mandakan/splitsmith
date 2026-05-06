"""Schema-level tests for Coach annotations on Shot + audit-JSON dicts.

Covers issue #159 (sub-issue of #158): the new Pydantic fields and the
audit-JSON dict helpers in ``splitsmith.coach``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from splitsmith.coach import (
    COACH_FIELDS,
    read_coach_fields,
    write_coach_fields,
)
from splitsmith.config import Shot

FIXTURES = Path(__file__).parent / "fixtures"


def _bare_shot(**overrides: Any) -> Shot:
    base: dict[str, Any] = {
        "shot_number": 1,
        "time_absolute": 2.0,
        "time_from_beep": 1.5,
        "split": 1.5,
        "peak_amplitude": 0.5,
        "confidence": 0.9,
    }
    base.update(overrides)
    return Shot(**base)


# ---------------------------------------------------------------------------
# Pydantic Shot model
# ---------------------------------------------------------------------------


def test_shot_defaults_unannotated() -> None:
    shot = _bare_shot()
    assert shot.interval_class is None
    assert shot.interval_class_source is None
    assert shot.improvement_flag is False
    assert shot.coaching_note is None


def test_shot_round_trip_with_annotations() -> None:
    shot = _bare_shot(
        interval_class="split",
        interval_class_source="manual",
        improvement_flag=True,
        coaching_note="too slow on the second A",
    )
    dumped = shot.model_dump()
    reloaded = Shot.model_validate(dumped)
    assert reloaded == shot
    assert reloaded.interval_class == "split"
    assert reloaded.interval_class_source == "manual"
    assert reloaded.improvement_flag is True
    assert reloaded.coaching_note == "too slow on the second A"


def test_shot_class_without_source_rejected() -> None:
    with pytest.raises(ValidationError):
        _bare_shot(interval_class="split")


def test_shot_source_without_class_rejected() -> None:
    with pytest.raises(ValidationError):
        _bare_shot(interval_class_source="auto")


def test_shot_unknown_class_rejected() -> None:
    with pytest.raises(ValidationError):
        _bare_shot(interval_class="bogus", interval_class_source="manual")


def test_shot_flag_only_is_valid() -> None:
    # No class, no note -- just a "needs work" flag.
    shot = _bare_shot(improvement_flag=True)
    assert shot.improvement_flag is True
    assert shot.interval_class is None


# ---------------------------------------------------------------------------
# Audit-JSON dict helpers
# ---------------------------------------------------------------------------


def test_read_coach_fields_empty_dict() -> None:
    assert read_coach_fields({}) == {}


def test_read_coach_fields_only_set_returned() -> None:
    shot = {
        "shot_number": 1,
        "time": 2.6,
        "ms_after_beep": 2137,
        "improvement_flag": True,
        "coaching_note": "draw was slow",
    }
    assert read_coach_fields(shot) == {
        "improvement_flag": True,
        "coaching_note": "draw was slow",
    }


def test_read_coach_fields_class_pair_required() -> None:
    bad_class = {"interval_class": "split"}
    with pytest.raises(ValueError):
        read_coach_fields(bad_class)
    bad_source = {"interval_class_source": "auto"}
    with pytest.raises(ValueError):
        read_coach_fields(bad_source)


def test_read_coach_fields_unknown_class_rejected() -> None:
    with pytest.raises(ValueError):
        read_coach_fields({"interval_class": "wibble", "interval_class_source": "auto"})


def test_write_coach_fields_round_trip() -> None:
    shot: dict[str, Any] = {"shot_number": 1, "ms_after_beep": 2137}
    write_coach_fields(
        shot,
        interval_class="transition",
        interval_class_source="manual",
        improvement_flag=True,
        coaching_note="late on target B",
    )
    assert read_coach_fields(shot) == {
        "interval_class": "transition",
        "interval_class_source": "manual",
        "improvement_flag": True,
        "coaching_note": "late on target B",
    }


def test_write_coach_fields_preserves_other_keys() -> None:
    shot: dict[str, Any] = {
        "shot_number": 1,
        "time": 2.6,
        "ms_after_beep": 2137,
        "candidate_number": 6,
        "source": "detected",
    }
    write_coach_fields(shot, improvement_flag=True)
    for k in ("shot_number", "time", "ms_after_beep", "candidate_number", "source"):
        assert k in shot


def test_write_coach_fields_clear_class_drops_pair() -> None:
    shot: dict[str, Any] = {
        "interval_class": "split",
        "interval_class_source": "manual",
        "coaching_note": "keep me",
    }
    write_coach_fields(shot, clear_class=True)
    assert "interval_class" not in shot
    assert "interval_class_source" not in shot
    assert shot["coaching_note"] == "keep me"


def test_write_coach_fields_clear_note() -> None:
    shot: dict[str, Any] = {"coaching_note": "obsolete"}
    write_coach_fields(shot, clear_note=True)
    assert "coaching_note" not in shot


def test_write_coach_fields_class_pair_required_on_write() -> None:
    shot: dict[str, Any] = {}
    with pytest.raises(ValueError):
        write_coach_fields(shot, interval_class="split")
    with pytest.raises(ValueError):
        write_coach_fields(shot, interval_class_source="manual")


def test_write_coach_fields_flag_false_clears() -> None:
    shot: dict[str, Any] = {"improvement_flag": True}
    write_coach_fields(shot, improvement_flag=False)
    assert "improvement_flag" not in shot


def test_write_coach_fields_empty_note_clears() -> None:
    shot: dict[str, Any] = {"coaching_note": "old"}
    write_coach_fields(shot, coaching_note="")
    assert "coaching_note" not in shot


# ---------------------------------------------------------------------------
# Realistic fixture round-trip: a real audit-style JSON survives load,
# annotation, save-as-JSON, reload.
# ---------------------------------------------------------------------------


def test_real_audit_json_round_trip(tmp_path: Path) -> None:
    src = FIXTURES / "stage-shots-blacksmith-2026-stage1.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    assert isinstance(data.get("shots"), list)
    assert data["shots"], "fixture must have shots to test against"

    # No coach fields present in the fixture today.
    for s in data["shots"]:
        assert read_coach_fields(s) == {}

    # Annotate a real shot the way the Coach UI would.
    target = data["shots"][0]
    write_coach_fields(
        target,
        interval_class="first_shot",
        interval_class_source="manual",
        improvement_flag=True,
        coaching_note="drew slow on round 1",
    )
    annotated = read_coach_fields(target)
    assert annotated == {
        "interval_class": "first_shot",
        "interval_class_source": "manual",
        "improvement_flag": True,
        "coaching_note": "drew slow on round 1",
    }

    # Round-trip through JSON.
    out = tmp_path / "stage1.json"
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert read_coach_fields(reloaded["shots"][0]) == annotated

    # Other shots remain untouched, no coach fields injected.
    for s in reloaded["shots"][1:]:
        assert read_coach_fields(s) == {}
        for f in COACH_FIELDS:
            assert f not in s
