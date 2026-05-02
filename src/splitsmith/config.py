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
    # Fraction of the global envelope peak that a candidate run must clear.
    # Lowered from 0.3 because Insta360 GO 3S audio at competitions records the
    # beep MUCH quieter than nearby steel rings or shots; a 0.3 floor would
    # silently skip the real beep when a louder transient exists earlier in the
    # clip. 0.05 admits faint candidates; the silence-preference scoring below
    # then picks the one preceded by the longest pre-event silence.
    min_amplitude: float = 0.05
    # Absolute peak floor on the bandpassed envelope. Belt-and-braces against
    # very-quiet false positives (RO chatter clipping into the 2-5 kHz band,
    # mic handling, etc.) when the leading window happens to contain a
    # near-silent segment. Real IPSC beeps from a Insta360 GO 3S typically peak
    # around 0.05-0.20 in [-1, 1] -- 0.04 is well below that. The effective
    # cutoff per recording is max(min_amplitude * global_peak, min_abs_peak).
    min_abs_peak: float = 0.04
    # Silence-preference scoring: an IPSC beep is preceded by ~3 s of "Are you
    # ready / Stand by", then a brief pause. A steel ring or shot during the
    # stage is NOT. We score each candidate by run_peak / (mean envelope in
    # [start - silence_window_s, start - silence_pre_skip_s] + eps) and pick
    # the highest score. ``silence_pre_skip_s`` skips the immediate ramp-up
    # so the metric isn't polluted by the beep's own envelope leakage.
    silence_window_s: float = 1.5
    silence_pre_skip_s: float = 0.2
    # Hard search-window cap. Real IPSC beeps come within the first ~30 s of a
    # head-cam recording: shooter walks to the line, RO runs through commands,
    # beep. After that window we're inside the stage where mid-stage moments
    # of relative silence followed by a loud transient (a steel ring after a
    # reload, etc.) can score higher than the actual beep on silence-preference
    # alone. Capping the search avoids that failure mode entirely. Override per
    # video in YAML if your recording is longer-leading.
    search_window_s: float = 30.0


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


class ShotRefineConfig(BaseModel):
    """Second-pass timing refinement.

    Run AFTER candidate filtering / user audit -- never feeds back into the
    voter feature distribution. Updates only the timestamp.
    """

    # ``"envelope"`` = wide broadband peak + rise-foot backtrack (default,
    # robust on busy IPSC stages). ``"aic"`` = Akaike picker on bandpassed
    # raw waveform (sub-ms accurate on isolated transients but degrades on
    # busy reverb backgrounds).
    method: Literal["envelope", "aic"] = "envelope"
    # Half-width of the audio window scanned around each approximate time.
    # 200 ms covers reverb-anchored cases where the candidate generator
    # placed its rise foot 100-200 ms after the true onset (see
    # PRECISION_LIMITS.md section 4a).
    search_half_window_ms: float = 200.0
    # Reject refinements whose confidence falls below this threshold and
    # keep the original timestamp instead. 0.0 = always accept.
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # Envelope-only: re-anchor to the wide-window peak only when it is at
    # least this much louder than the local-position peak. Small ratios
    # (< 2.0) mean the original was already on a peak; tight rise-foot
    # adjustment is used instead. Mirrors the same heuristic as the
    # candidate-time reverb-chain re-anchor (PRECISION_LIMITS section 4a).
    reanchor_ratio: float = 2.0
    # AIC-only: bandpass before AIC. Muzzle-blast energy concentrates above
    # ~500 Hz; bandpassing reduces wind/handling noise that can mask the
    # variance shift the AIC picker keys on.
    bandpass_low_hz: float | None = 500.0
    bandpass_high_hz: float | None = 12000.0


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
    # Trim mode (issue #16):
    # - "lossless": ffmpeg -c copy. Instant, archival-quality, but inherits the
    #   source's 1-4s GOP -- bad for in-browser scrubbing in the audit screen.
    # - "audit":    re-encodes video with a short GOP (default 0.5s @ 30fps) so
    #   browser <video> seeks land on a keyframe within ~1 frame of the
    #   pointer. Audio is stream-copied to keep the detector's input bit-exact.
    # CLI default stays "lossless" for backward compatibility; the production
    # UI defaults to "audit" because real-time scrubbing depends on it.
    trim_mode: Literal["lossless", "audit"] = "lossless"
    # Audit-mode encoding parameters. ``trim_gop_frames=15`` at 30fps means a
    # keyframe every 0.5s; lower for tighter scrub, higher for smaller files.
    trim_gop_frames: int = 15
    trim_audit_crf: int = 20
    trim_audit_preset: str = "fast"


class Config(BaseModel):
    beep_detect: BeepDetectConfig = Field(default_factory=BeepDetectConfig)
    shot_detect: ShotDetectConfig = Field(default_factory=ShotDetectConfig)
    shot_refine: ShotRefineConfig = Field(default_factory=ShotRefineConfig)
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
