"""Pydantic models for configuration and pipeline data structures."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Pipeline data structures
# ---------------------------------------------------------------------------


class StageData(BaseModel):
    stage_number: int
    stage_name: str
    time_seconds: float
    scorecard_updated_at: datetime


class Shot(BaseModel):
    shot_number: int
    time_absolute: float
    time_from_beep: float
    split: float
    peak_amplitude: float
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""


class StageAnalysis(BaseModel):
    stage: StageData
    video_path: Path
    beep_time: float
    shots: list[Shot]
    anomalies: list[str] = Field(default_factory=list)


class BeepDetection(BaseModel):
    """Result of running beep_detect on a clip; carries diagnostics for the audit report."""

    time: float
    peak_amplitude: float
    duration_ms: float


class TrimResult(BaseModel):
    """Result of trim_video: the output path and the absolute seconds-into-source window."""

    output_path: Path
    start_time: float
    end_time: float

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


class CsvShot(BaseModel):
    """A row of the splits CSV. ``time_from_start`` is seconds from the beep."""

    shot_number: int
    time_from_start: float
    split: float
    peak_amplitude: float
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""


class ReportFiles(BaseModel):
    """Paths to the per-stage output artefacts referenced in the report's footer."""

    video: Path | None = None
    csv: Path | None = None
    fcpxml: Path | None = None


class VideoMetadata(BaseModel):
    """Subset of ffprobe output needed to author an FCPXML at the source frame rate."""

    width: int
    height: int
    duration_seconds: float
    frame_rate_num: int  # e.g. 30000 for 29.97
    frame_rate_den: int  # e.g. 1001 for 29.97


class VideoStageMatch(BaseModel):
    """A confidently-matched (stage, video) pair."""

    stage_number: int
    video_path: Path
    video_timestamp: datetime


class VideoMatchResult(BaseModel):
    """Outcome of match_videos_to_stages: confident matches plus what couldn't be resolved."""

    matches: list[VideoStageMatch] = Field(default_factory=list)
    ambiguous_stages: dict[int, list[Path]] = Field(default_factory=dict)
    orphan_videos: list[Path] = Field(default_factory=list)
    unmatched_stages: list[int] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Configuration models (tunable; YAML-overridable)
# ---------------------------------------------------------------------------


class BeepDetectConfig(BaseModel):
    freq_min_hz: int = 2000
    freq_max_hz: int = 5000
    min_duration_ms: int = 150
    min_amplitude: float = 0.3


class ShotDetectConfig(BaseModel):
    min_gap_ms: int = 80
    onset_delta: float = 0.07
    pre_max_ms: int = 30
    post_max_ms: int = 30
    # Echo refractory: after a kept shot, suppress subsequent onsets within
    # ``echo_refractory_ms`` whose peak amplitude is below
    # ``echo_amplitude_ratio * previous_peak``. Catches classic intra-bay
    # echoes (lower amplitude, < 150 ms after the shot) without dropping real
    # fast splits, which are typically louder relative to the preceding shot.
    echo_refractory_ms: int = 150
    echo_amplitude_ratio: float = 0.4
    # Drop candidates with confidence below this threshold before returning.
    # Default 0.0 = keep all (current behavior). Sweep against audited fixtures
    # (issue #6 follow-up) shows 0.03 is the empirical maximum that preserves
    # recall (289 -> 243 candidates, -16 %, 0 real shots dropped). At 0.04+
    # AGC-ducked real shots start being dropped. 0.03 is the safe opt-in cap.
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Recall fallback: after the librosa-based first pass, optionally run a
    # second pass to fill suspicious gaps. ``"none"`` (default) leaves the
    # behaviour unchanged. ``"cwt"`` runs a Ricker-wavelet two-pass detector
    # (see ``shot_detect._apply_cwt_recall_fallback``); recovers the rare
    # busy-ambient miss where spectral flux fails to fire, at the cost of
    # ~20 % more candidates per stage. Use for known-windy/noisy stages.
    recall_fallback: Literal["none", "cwt"] = "none"


class VideoMatchConfig(BaseModel):
    tolerance_minutes: int = 15
    prefer_ctime: bool = True


class SplitColorThresholds(BaseModel):
    green_max: float = 0.25
    yellow_max: float = 0.35
    transition_min: float = 1.0


class OutputConfig(BaseModel):
    trim_buffer_seconds: float = 5.0
    fcpxml_version: str = "1.10"
    split_color_thresholds: SplitColorThresholds = Field(default_factory=SplitColorThresholds)


class Config(BaseModel):
    beep_detect: BeepDetectConfig = Field(default_factory=BeepDetectConfig)
    shot_detect: ShotDetectConfig = Field(default_factory=ShotDetectConfig)
    video_match: VideoMatchConfig = Field(default_factory=VideoMatchConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @classmethod
    def load(cls, path: Path | None) -> Config:
        if path is None:
            return cls()
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)


# ---------------------------------------------------------------------------
# Stage JSON loader (matches SSI Scoreboard export schema in examples/)
# ---------------------------------------------------------------------------


class CompetitorStages(BaseModel):
    """Subset of the SSI Scoreboard export needed by the pipeline."""

    competitor_id: int
    name: str
    division: str
    club: str
    stages: list[StageData]

    @field_validator("stages")
    @classmethod
    def _stages_sorted(cls, v: list[StageData]) -> list[StageData]:
        return sorted(v, key=lambda s: s.stage_number)
