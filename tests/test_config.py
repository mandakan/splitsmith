"""Tests for config and stage-JSON loading."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from splitsmith.config import (
    CompetitorStages,
    Config,
    Shot,
    StageData,
)

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_default_config_has_spec_values() -> None:
    cfg = Config()
    assert cfg.beep_detect.freq_min_hz == 2000
    assert cfg.beep_detect.freq_max_hz == 5000
    assert cfg.shot_detect.min_gap_ms == 80
    assert cfg.video_match.tolerance_minutes == 15
    assert cfg.output.split_color_thresholds.green_max == 0.25
    assert cfg.output.fcpxml_version == "1.10"


def test_config_yaml_override(tmp_path: Path) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("shot_detect:\n  min_gap_ms: 60\noutput:\n  trim_buffer_seconds: 3.0\n")
    cfg = Config.load(yaml_file)
    assert cfg.shot_detect.min_gap_ms == 60
    assert cfg.output.trim_buffer_seconds == 3.0
    # untouched defaults still hold
    assert cfg.beep_detect.freq_min_hz == 2000


def test_config_load_none_returns_defaults() -> None:
    assert Config.load(None) == Config()


def test_shot_confidence_bounds() -> None:
    Shot(
        shot_number=1,
        time_absolute=1.0,
        time_from_beep=0.0,
        split=0.0,
        peak_amplitude=0.5,
        confidence=0.0,
    )
    with pytest.raises(ValidationError):
        Shot(
            shot_number=1,
            time_absolute=1.0,
            time_from_beep=0.0,
            split=0.0,
            peak_amplitude=0.5,
            confidence=1.5,
        )


def test_competitor_stages_parses_example() -> None:
    raw = json.loads((EXAMPLES_DIR / "tallmilan-2026.json").read_text())
    competitor = CompetitorStages.model_validate(raw["competitors"][0])
    assert competitor.club == "Sample IPSC Club"
    assert competitor.division == "Production Optics"
    assert len(competitor.stages) == 7
    # _stages_sorted validator orders by stage_number
    assert [s.stage_number for s in competitor.stages] == [1, 2, 3, 4, 5, 6, 7]
    stage3 = next(s for s in competitor.stages if s.stage_number == 3)
    assert stage3.stage_name == "Per told me to do it!"
    assert stage3.time_seconds == pytest.approx(14.74)


def test_stage_data_requires_timezone() -> None:
    # Pydantic accepts ISO8601 with offset; bare naive strings produce naive datetimes.
    s = StageData(
        stage_number=1,
        stage_name="Test",
        time_seconds=10.0,
        scorecard_updated_at="2026-04-26T13:57:48.978620+00:00",
    )
    assert s.scorecard_updated_at.tzinfo is not None
