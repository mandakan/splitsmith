"""Beep-detector calibration suite -- manifest, ground truth, eval aggregation.

This module is the pure-data backbone of the layer-1 work for issue #220
(``beep: improve detection accuracy``). The detector itself lives in
``splitsmith.beep_detect``; this module only describes WHAT to detect and
HOW to score the result.

Two tracks share the suite:

* **Clip track** -- the ~10-50 s WAV files already checked into
  ``tests/fixtures/`` (post-trim, with 0.5 s or 5 s pre-beep padding).
  Always available; covers the trivial-baseline case + handheld iPhone
  clips with 5 s of pre-beep noise.
* **Full track** -- the wide-window WAVs produced by
  ``scripts/extract_full_fixture_audio.py`` under ``tests/fixtures/full/``.
  Covers late-beep / cross-bay scenarios that don't appear in the trimmed
  clips. Optional: only present when the source MP4s have been extracted
  on this machine.

The ``ground_truth_in_clip`` and ``ground_truth_in_full`` fields express
the beep position in seconds within each respective WAV's coordinate
frame. ``detect_beep`` returns clip-relative or full-relative depending
on which audio buffer it's fed -- pick the matching ground truth.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

# Tolerance the audit JSONs themselves use (``tolerance_ms``). A detected
# beep is "correct" if it lands within this many ms of ground truth.
DEFAULT_TOLERANCE_MS = 100.0

# Heuristic thresholds applied during manifest build to seed failure-mode
# tags. These are SUGGESTIONS the user can override by hand-editing the
# ``tags`` list in ``manifest.yaml``.
LATE_BEEP_THRESHOLD_S = 10.0
VERY_LATE_BEEP_THRESHOLD_S = 30.0
STEEL_PRONE_PLATES_THRESHOLD = 1  # any popper / plate count


class BeepFixtureEntry(BaseModel):
    """One fixture's calibration metadata.

    The manifest is a list of these. Field semantics:

    * ``stem`` -- audit JSON / WAV basename (no extension).
    * ``camera_kind`` -- ``head`` for body-mounted cameras (Insta360 GO),
      ``hand`` for handheld phones. The detector should be robust to both
      but the failure-modes differ.
    * ``camera_id`` -- the audit JSON's camera.id, kept for filtering.
    * ``clip_wav`` -- path to the post-trim WAV. Relative to
      ``tests/fixtures/``.
    * ``ground_truth_in_clip`` -- beep time in seconds, relative to start
      of ``clip_wav``. Pulled directly from the audit JSON's ``beep_time``.
    * ``full_wav`` / ``ground_truth_in_full`` / ``full_duration_s`` --
      populated when ``scripts/extract_full_fixture_audio.py`` has been
      run; ``full_wav`` is relative to ``tests/fixtures/``.
    * ``tags`` -- failure-mode buckets. See module-level constants for
      the heuristic auto-tags; humans can add finer tags (``cross-bay``,
      ``steel-fp``, ``ro-chatter``, ...) by editing manifest.yaml.
    """

    stem: str
    camera_kind: str
    camera_id: str | None = None
    clip_wav: str
    ground_truth_in_clip: float
    tolerance_ms: float = DEFAULT_TOLERANCE_MS
    full_wav: str | None = None
    ground_truth_in_full: float | None = None
    full_duration_s: float | None = None
    tags: list[str] = Field(default_factory=list)


class BeepCalibrationManifest(BaseModel):
    """Top-level manifest persisted to ``manifest.yaml``."""

    fixtures: list[BeepFixtureEntry] = Field(default_factory=list)


@dataclass(frozen=True)
class FixtureEvalResult:
    """Outcome of running the detector against one fixture's audio buffer.

    ``track`` distinguishes the clip vs full evaluation since the same
    ``stem`` produces two rows when both wavs are present.
    """

    stem: str
    track: str  # "clip" or "full"
    tags: tuple[str, ...]
    ground_truth_s: float
    tolerance_s: float
    detected_time_s: float | None
    detected_score: float | None
    error_s: float | None  # detected - ground_truth, None if missed
    correct_top1: bool
    correct_in_topn: bool  # any candidate within tolerance
    candidate_count: int
    error_kind: str | None = None  # "not_found", "exception", or None


@dataclass
class EvalSummary:
    """Aggregated eval result. Used to print the report and gate CI."""

    total: int = 0
    top1_hits: int = 0
    topn_hits: int = 0
    not_found: int = 0
    exceptions: int = 0
    by_tag: dict[str, EvalSummary] = field(default_factory=dict)

    @property
    def recall_top1(self) -> float:
        return (self.top1_hits / self.total) if self.total else 0.0

    @property
    def recall_topn(self) -> float:
        return (self.topn_hits / self.total) if self.total else 0.0


def derive_camera_kind(camera_block: dict | None) -> str:
    """Map an audit-JSON ``camera`` dict to ``head`` / ``hand`` / ``unknown``.

    The audit schema uses ``mount`` = ``head`` | ``hand`` directly, but a
    few legacy fixtures don't have the camera block at all -- treat those
    as ``unknown`` so the manifest stays explicit instead of guessing.
    """
    if not isinstance(camera_block, dict):
        return "unknown"
    mount = camera_block.get("mount")
    if mount in ("head", "hand"):
        return mount
    return "unknown"


def auto_tags(
    *,
    camera_kind: str,
    ground_truth_in_full: float | None,
    stage_rounds: dict | None,
) -> list[str]:
    """Seed failure-mode tags from audit-JSON facts.

    Always-applicable:
    * ``handheld`` / ``headcam`` -- from camera mount.

    Conditional (full-audio-only):
    * ``late-beep`` -- beep > 10 s into source. Today's 30 s search cap
      still catches it but silence-preference scoring can drift.
    * ``very-late-beep`` -- beep > 30 s into source. Current detector
      hard-fails on these (search window cap).
    * ``steel-prone`` -- stage has poppers or plates; raises the chance
      of a steel-ring false positive being scored above the beep.
    """
    tags: list[str] = []
    if camera_kind == "head":
        tags.append("headcam")
    elif camera_kind == "hand":
        tags.append("handheld")
    if ground_truth_in_full is not None:
        if ground_truth_in_full >= VERY_LATE_BEEP_THRESHOLD_S:
            tags.append("very-late-beep")
        elif ground_truth_in_full >= LATE_BEEP_THRESHOLD_S:
            tags.append("late-beep")
    if isinstance(stage_rounds, dict):
        plates = int(stage_rounds.get("plates") or 0)
        poppers = int(stage_rounds.get("poppers") or 0)
        if plates + poppers >= STEEL_PRONE_PLATES_THRESHOLD:
            tags.append("steel-prone")
    return tags


def compute_full_beep_time(
    *,
    fixture_window_in_source: tuple[float, float],
    full_window_in_source: tuple[float, float],
    clip_beep_time: float,
) -> float:
    """Translate the audit's clip-relative beep into the full-WAV's frame.

    The audit JSON pins the beep within a TRIMMED clip whose start sits
    at ``fixture_window_in_source[0]`` in source-time. The full WAV
    starts at ``full_window_in_source[0]``. Both are seconds-into-source.
    The beep position in the full WAV is therefore::

        full_beep = (fws[0] - full[0]) + clip_beep_time
    """
    fws_start = fixture_window_in_source[0]
    full_start = full_window_in_source[0]
    return (fws_start - full_start) + clip_beep_time


def evaluate_detection(
    *,
    stem: str,
    track: str,
    tags: Iterable[str],
    ground_truth_s: float,
    tolerance_ms: float,
    detected_time_s: float | None,
    detected_score: float | None,
    candidate_times_s: Iterable[float] = (),
    error_kind: str | None = None,
) -> FixtureEvalResult:
    """Score one detector call against the ground truth.

    ``candidate_times_s`` are the runner-up candidate timestamps the
    detector surfaced (``BeepDetection.candidates[1:].time``). They count
    toward ``correct_in_topn`` even when the top-1 winner was wrong --
    this is the signal that matters for the HITL flow (#219): if the
    real beep is in the top-N list, the human can pick it without
    typing a timestamp.
    """
    tol_s = tolerance_ms / 1000.0
    if detected_time_s is None:
        return FixtureEvalResult(
            stem=stem,
            track=track,
            tags=tuple(tags),
            ground_truth_s=ground_truth_s,
            tolerance_s=tol_s,
            detected_time_s=None,
            detected_score=detected_score,
            error_s=None,
            correct_top1=False,
            correct_in_topn=False,
            candidate_count=0,
            error_kind=error_kind or "not_found",
        )
    error = detected_time_s - ground_truth_s
    correct_top1 = abs(error) <= tol_s
    candidates = list(candidate_times_s)
    correct_in_topn = correct_top1 or any(abs(c - ground_truth_s) <= tol_s for c in candidates)
    return FixtureEvalResult(
        stem=stem,
        track=track,
        tags=tuple(tags),
        ground_truth_s=ground_truth_s,
        tolerance_s=tol_s,
        detected_time_s=detected_time_s,
        detected_score=detected_score,
        error_s=error,
        correct_top1=correct_top1,
        correct_in_topn=correct_in_topn,
        candidate_count=len(candidates) + 1,
        error_kind=error_kind,
    )


def summarize(results: Iterable[FixtureEvalResult]) -> EvalSummary:
    """Aggregate per-fixture results into an overall + per-tag summary."""
    overall = EvalSummary()
    for r in results:
        overall.total += 1
        if r.correct_top1:
            overall.top1_hits += 1
        if r.correct_in_topn:
            overall.topn_hits += 1
        if r.error_kind == "not_found":
            overall.not_found += 1
        elif r.error_kind == "exception":
            overall.exceptions += 1
        for tag in r.tags:
            bucket = overall.by_tag.setdefault(tag, EvalSummary())
            bucket.total += 1
            if r.correct_top1:
                bucket.top1_hits += 1
            if r.correct_in_topn:
                bucket.topn_hits += 1
            if r.error_kind == "not_found":
                bucket.not_found += 1
            elif r.error_kind == "exception":
                bucket.exceptions += 1
    return overall


def load_manifest(path: Path) -> BeepCalibrationManifest:
    """Read a manifest YAML. Missing file returns an empty manifest."""
    import yaml

    if not path.exists():
        return BeepCalibrationManifest()
    raw = yaml.safe_load(path.read_text()) or {}
    return BeepCalibrationManifest.model_validate(raw)


def save_manifest(manifest: BeepCalibrationManifest, path: Path) -> None:
    """Write a manifest YAML in a stable, hand-editable format."""
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump(exclude_none=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, indent=2))


def fixtures_with_full_audio(
    manifest: BeepCalibrationManifest,
    fixtures_dir: Path,
) -> list[BeepFixtureEntry]:
    """Subset of the manifest where the full-track WAV exists on disk."""
    rows = []
    for entry in manifest.fixtures:
        if not entry.full_wav:
            continue
        if (fixtures_dir / entry.full_wav).exists():
            rows.append(entry)
    return rows


def read_audit_json(path: Path) -> dict:
    """Thin wrapper -- only exists so tests can stub the read."""
    return json.loads(path.read_text())
