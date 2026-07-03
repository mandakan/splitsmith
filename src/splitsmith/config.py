"""Pydantic models for configuration and pipeline data structures."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Pipeline data structures
# ---------------------------------------------------------------------------


class StageRounds(BaseModel):
    """Round count + target breakdown for a stage.

    Drives the ensemble's adaptive Voter C and apriori boost (issue #103
    + #143 follow-up): ``expected`` is the canonical "expected number of
    shots on this stage" used to pick top-(K+slack) and to lift the
    top-K candidates over the consensus line. ``paper_targets`` and
    ``steel_targets`` are passive metadata for now -- surfaced in the
    audit JSON for downstream stratified eval.

    Sourced from SSI Scoreboard's ``min_rounds`` / ``paper_targets`` /
    ``steel_targets`` per stage; populated by the project's scoreboard
    import path.
    """

    expected: int | None = Field(
        default=None,
        description="Required round count from the stage card (SSI ``min_rounds``).",
    )
    paper_targets: int | None = Field(default=None, description="Paper-target count.")
    steel_targets: int | None = Field(default=None, description="Steel-target count.")


class StageData(BaseModel):
    stage_number: int
    stage_name: str
    time_seconds: float
    scorecard_updated_at: datetime
    stage_rounds: StageRounds | None = None


IntervalClass = Literal[
    "first_shot",
    "split",
    "transition",
    "movement",
    "reload",
    "activation",
]

IntervalClassSource = Literal["auto", "manual"]


class Shot(BaseModel):
    shot_number: int
    time_absolute: float
    time_from_beep: float
    split: float
    peak_amplitude: float
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""

    # Coaching annotations (issue #159). All optional so old audit JSONs
    # load unchanged; the Coach page populates them on first open via the
    # auto-classifier (#160). interval_class_source is required whenever
    # interval_class is set, so we can preserve manual overrides across
    # re-classification.
    interval_class: IntervalClass | None = None
    interval_class_source: IntervalClassSource | None = None
    improvement_flag: bool = False
    coaching_note: str | None = None

    @model_validator(mode="after")
    def _coach_annotations_consistent(self) -> Shot:
        if self.interval_class is not None and self.interval_class_source is None:
            raise ValueError("interval_class_source must be set when interval_class is set")
        if self.interval_class is None and self.interval_class_source is not None:
            raise ValueError("interval_class_source must be unset when interval_class is unset")
        return self


class StageAnalysis(BaseModel):
    stage: StageData
    video_path: Path
    beep_time: float
    shots: list[Shot]
    anomalies: list[str] = Field(default_factory=list)


class BeepCandidate(BaseModel):
    """One ranked beep candidate from ``beep_detect``.

    Surfaced to the production UI so the user can pick a different candidate
    when the auto-winner is wrong (issue #22). Fields:

    * ``score`` -- composite ranking score: silence-preference (``run_peak /
      pre_window_mean``) tilted by tonal concentration. Higher = stronger.
    * ``silence_score`` -- raw silence-preference component, kept for
      diagnostics + threshold tuning.
    * ``tonal_score`` -- raw tonal-concentration ratio in [0, 1]: fraction
      of the run's bandpassed energy that falls inside the IPSC timer
      fundamental band. ~1.0 for a pure tone, << 1.0 for gunshots / steel.
    * ``confidence`` -- calibrated probability in [0, 1] that this candidate
      is the real beep. Empirically validated against the labeled fixture
      set (issue #220 layer 3); >=0.7 right ~95 % of the time, 0.5-0.7
      lands in the HITL queue (issue #219). Layer 2's raw ``score`` ranks
      candidates; ``confidence`` is the threshold-able trust value.
    """

    time: float
    score: float
    peak_amplitude: float
    duration_ms: float
    silence_score: float = 0.0
    tonal_score: float = 0.0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class BeepDetection(BaseModel):
    """Result of running beep_detect on a clip; carries diagnostics for the audit report.

    ``candidates`` holds the top-N silence-preference-ranked candidates with
    ``candidates[0]`` matching ``time``/``peak_amplitude``/``duration_ms``.
    The list is empty for callers that don't request alternatives (kept
    optional for backwards compatibility with on-disk audit JSON).
    ``confidence`` mirrors the winning candidate's confidence so callers
    that don't surface the candidate list still get the threshold value.
    """

    time: float
    peak_amplitude: float
    duration_ms: float
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    candidates: list[BeepCandidate] = Field(default_factory=list)


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
    # Cutoff is ``max(min_amplitude * global_peak, noise_floor * noise_factor,
    # min_abs_peak)``. Each leg defends a different failure mode:
    #
    # * ``min_amplitude * global_peak`` -- on recordings where a gunshot sets
    #   the global peak, this floor is high and effectively unused (gunshots
    #   sit in the same 2-5 kHz band). Kept low (0.05) for that case.
    # * ``noise_floor * noise_factor`` -- recovers handheld / phone clips
    #   where the beep is faint (envelope ~0.01-0.05) but still well above
    #   the median noise floor. The dominant gating leg in practice.
    # * ``min_abs_peak`` -- belt-and-braces against pathological clips with
    #   sub-noise envelopes (e.g. a thumb over the mic). Set very low so it
    #   doesn't crowd out faint real beeps.
    min_amplitude: float = 0.05
    min_abs_peak: float = 0.005
    # Cutoff = max(..., noise_floor * noise_factor). 5x is empirically the
    # crossover between "always finds the beep" and "starts admitting low-SNR
    # ambient transients" on the labelled fixture set. Real beeps clear this
    # by 10x+ comfortably.
    noise_floor_factor: float = 5.0
    # Hilbert-envelope smoothing window. The IPSC tone is sustained ~300-500
    # ms but its envelope wobbles (carrier intermodulation, mic AGC pump);
    # 40 ms smoothing bridges the natural intra-beep dips so a single run
    # spans the whole tone. 10 ms (the previous default) fragmented faint
    # beeps into 100-150 ms shards that fell below ``min_duration_ms``.
    envelope_smoothing_ms: float = 40.0
    # Silence-preference scoring: an IPSC beep is preceded by ~3 s of "Are you
    # ready / Stand by", then a brief pause. A steel ring or shot during the
    # stage is NOT. We score each candidate by run_peak / (mean envelope in
    # [start - silence_window_s, start - silence_pre_skip_s] + eps) and pick
    # the highest score. ``silence_pre_skip_s`` skips the immediate ramp-up
    # so the metric isn't polluted by the beep's own envelope leakage.
    silence_window_s: float = 1.5
    silence_pre_skip_s: float = 0.2
    # Minimum amount of available pre-window (after the skip) for
    # silence-preference scoring to be meaningful. Below this, silence_
    # score falls back to the neutral 1.0 -- a candidate at t=0.05 s
    # otherwise gets a degenerate ``peak / noise_floor`` score that
    # beats real beeps whose pre-window contains "Are you ready / Stand
    # by" chatter. 0.2 s is well below the headcam minimum (0.3 s
    # available at the canonical 0.5 s pre-trim) so it doesn't disable
    # the metric on the trivially-trimmed case.
    min_pre_window_s: float = 0.2
    # Tonal-quality scoring: the IPSC timer emits a near-pure tone whose
    # exact frequency varies by manufacturer (Pact, Pocket Pro, CED, Tallmi-
    # lan etc.) -- empirical fundamentals on the labelled fixture set span
    # 2.3-3.2 kHz. Gunshots, steel rings, and RO chatter spread energy
    # across the full 2-5 kHz band. We measure energy concentration in
    # [tonal_band_lo_hz, tonal_band_hi_hz] vs the wider
    # [freq_min_hz, freq_max_hz] band and tilt the silence-preference score
    # by the ratio. ``tonal_weight`` (in [0, 1]) sets how strongly this
    # tilts ranking; 1.0 = full weight, 0.0 = legacy silence-only. Empiri-
    # cally 0.5 is the sweet spot: enough tilt to demote broadband shots
    # without throwing out real beeps that sit at the band edges.
    tonal_band_lo_hz: int = 2200
    tonal_band_hi_hz: int = 3500
    tonal_weight: float = 0.7
    # Duration-prior scoring: IPSC timer beeps run 300-500 ms; gunshots and
    # steel rings post-smoothing land at 100-200 ms. The duration factor
    # ramps linearly from 0 at ``dur_match_min_ms`` to 1 at
    # ``dur_match_full_ms`` and is multiplied into the composite score.
    # Demotes short transients without rejecting them outright -- a borderline
    # candidate can still surface in top-N for HITL review.
    dur_match_min_ms: float = 150.0
    dur_match_full_ms: float = 300.0
    dur_match_weight: float = 1.0
    # Hard search-window cap. Real IPSC beeps come within the first ~30 s of a
    # head-cam recording: shooter walks to the line, RO runs through commands,
    # beep. After that window we're inside the stage where mid-stage moments
    # of relative silence followed by a loud transient (a steel ring after a
    # reload, etc.) can score higher than the actual beep on silence-preference
    # alone. Capping the search avoids that failure mode entirely. Override per
    # video in YAML if your recording is longer-leading.
    search_window_s: float = 30.0
    # Number of ranked candidates to surface alongside the auto-winner.
    # The production UI shows these as "other candidates" so the user can
    # pick the right one without typing a timestamp by hand (issue #22).
    # ``0`` returns just the winner (legacy behaviour).
    top_n_candidates: int = 5


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
    # placed its rise foot 100-200 ms after the true onset.
    search_half_window_ms: float = 200.0
    # Reject refinements whose confidence falls below this threshold and
    # keep the original timestamp instead. 0.0 = always accept.
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # Envelope-only: re-anchor to the wide-window peak only when it is at
    # least this much louder than the local-position peak. Small ratios
    # (< 2.0) mean the original was already on a peak; tight rise-foot
    # adjustment is used instead. Mirrors the same heuristic as the
    # candidate-time reverb-chain re-anchor.
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
    # libx264 preset. Audit clips are throw-away cache files: encode speed
    # matters, file size doesn't. ``ultrafast`` is ~3-5x faster than ``fast``
    # on 2K/4K Insta360 footage at the cost of larger output -- a worthwhile
    # trade for the audit screen feeling responsive (issue #26).
    trim_audit_preset: str = "ultrafast"
    # Encoder for audit-mode trims. ``"auto"`` picks ``h264_videotoolbox`` on
    # macOS when ffmpeg advertises it (~10x speedup on large sources) and
    # falls back to ``libx264`` everywhere else. Set to a specific encoder
    # name to override.
    trim_audit_encoder: str = "auto"


class CoachAutoClassifyConfig(BaseModel):
    """Gap-time thresholds for the Coach interval auto-classifier (#160).

    The auto-classifier only ever assigns ``first_shot``, ``split``,
    ``transition``, or ``movement``. ``reload`` and ``activation`` are
    user-only overrides -- the rule cannot reliably distinguish them
    from movement without target metadata or hand cues.
    """

    # gap <= split_max_s -> "split"
    split_max_s: float = Field(default=0.50, gt=0.0)
    # split_max_s < gap <= transition_max_s -> "transition"
    transition_max_s: float = Field(default=1.00, gt=0.0)
    # transition_max_s < gap -> "movement". The UI surfaces a "could be
    # reload?" hint when the gap exceeds reload_hint_min_s.
    reload_hint_min_s: float = Field(default=2.50, gt=0.0)


class BeepWindowConfig(BaseModel):
    """Search-window derivation for multi-stage single-take videos.

    scorecard_updated_at is typed 1-3 min after the run ends and the run
    ends stage_time after the beep, so the expected beep offset inside
    the file is (scorecard - video_start) - stage_time - scorecard_lead_s.
    The window pads that estimate; clamping and a minimum length keep it
    inside the file and useful even when the estimate is rough.
    """

    scorecard_lead_s: float = 120.0
    pre_pad_s: float = 180.0
    post_pad_s: float = 180.0
    reset_margin_s: float = 45.0
    min_window_s: float = 20.0
    conflict_threshold_s: float = 2.0


class Config(BaseModel):
    beep_detect: BeepDetectConfig = Field(default_factory=BeepDetectConfig)
    shot_detect: ShotDetectConfig = Field(default_factory=ShotDetectConfig)
    shot_refine: ShotRefineConfig = Field(default_factory=ShotRefineConfig)
    video_match: VideoMatchConfig = Field(default_factory=VideoMatchConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    coach_auto_classify: CoachAutoClassifyConfig = Field(default_factory=CoachAutoClassifyConfig)
    beep_windows: BeepWindowConfig = Field(default_factory=BeepWindowConfig)

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
