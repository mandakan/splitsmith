"""Pure-function lab core.

Used identically by ``/api/lab/*`` and ``splitsmith lab``. No FastAPI,
no Typer, no globals. Heavy model loads are passed in; callers cache.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from ..beep_detect import load_audio
from ..ensemble.api import (
    EnsembleConfig,
    EnsembleRuntime,
    detect_shots_ensemble,
)

DEFAULT_FIXTURES_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
DEFAULT_RUNS_ROOT = Path("build/lab/runs")
LATEST_RUN_FILENAME = "latest.json"

# Slug pattern for fixtures audited from a stage: ``stage-shots-<match-slug>-stage<N>(-<extra>)?``.
# Used by :func:`match_stage_from_slug` to derive the (match, stage) part of
# the event_id without needing the audit JSON in hand. Shooter identity
# can't be derived from the slug -- it comes from the fixture's
# ``shooter`` block; ``DEFAULT_SHOOTER_KEY`` is the legacy fallback.
_SLUG_PATTERN = re.compile(r"^stage-shots-(?P<match>.+)-stage(?P<n>\d+)(?:-.+)?$")

# Sentinel shooter key for fixtures with no recorded shooter identity.
# All pre-issue-#149 fixtures default to this on migration -- they were
# all the project owner. Going forward, promote-from-project stamps a
# real ``ssi-<id>`` key when the project's shooter pin is set.
DEFAULT_SHOOTER_KEY: str = "self"


def shooter_token(ssi_shooter_id: int | str) -> str:
    """Compute the public, stable, non-PII token for an SSI shooter.

    The token is ``"s" + sha256("ssi-<id>").hexdigest()[:8]``. It is the
    only shooter identifier ever written into the public fixtures repo
    -- raw SSI IDs and competitor names stay in private project files.
    The same shooter always yields the same token across all matches
    and stages, so a fixture corpus can be filtered by shooter without
    leaking identity.
    """
    raw = f"ssi-{ssi_shooter_id}".encode()
    return "s" + hashlib.sha256(raw).hexdigest()[:8]


_LOCAL_HOME_PREFIX = re.compile(r"^/(Users|home)/[^/]+/")
_MATCH_DIR_TAIL = re.compile(r"^matches/[^/]+/")


def scrub_local_path(value: str | None) -> str | None:
    """Strip the user's home-dir prefix from a path string for PII safety.

    Removes ``/Users/<name>/`` or ``/home/<name>/`` and the
    ``matches/<match>/`` segment that follows it in this project's
    layout, leaving the meaningful tail (e.g. ``raw/IMG_3005.MOV``).
    Strings that don't start with a user-home prefix pass through; this
    is a labelling helper, not a security boundary.
    """
    if not isinstance(value, str) or not value:
        return value
    m = _LOCAL_HOME_PREFIX.match(value)
    if not m:
        return value
    tail = value[m.end() :]
    m2 = _MATCH_DIR_TAIL.match(tail)
    if m2:
        tail = tail[m2.end() :]
    return tail


def match_stage_from_slug(slug: str) -> tuple[str, int] | None:
    """Parse ``stage-shots-<match>-stage<N>(-<extra>)?`` -> ``(match, N)``.

    Returns ``None`` for slugs that don't fit the standard pattern. The
    shooter component of the event_id is *not* in the slug; combine the
    parsed result with a shooter key via :func:`build_event_id`.
    """
    match = _SLUG_PATTERN.match(slug)
    if not match:
        return None
    return match.group("match"), int(match.group("n"))


def build_event_id(match_slug: str, stage_number: int, shooter_key: str) -> str:
    """Compose the canonical event_id ``<match>:<stage>:<shooter>``.

    Multi-camera siblings of the same shooter-stage performance share an
    event_id; different shooters on the same physical stage get distinct
    ids so the Lab table never cross-groups them.
    """
    return f"{match_slug}:{int(stage_number)}:{shooter_key}"


def event_id_from_payload(slug: str, payload: dict[str, Any]) -> str | None:
    """Build the full ``event_id`` for a fixture JSON.

    Precedence: explicit top-level ``event_id`` on the JSON wins.
    Otherwise: parse the slug for ``(match, stage)``, combine with the
    shooter key from ``payload["shooter"]["id"]`` (``DEFAULT_SHOOTER_KEY``
    when absent). Returns ``None`` only when the slug doesn't fit the
    standard pattern AND no explicit event_id is set.
    """
    explicit = payload.get("event_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    parsed = match_stage_from_slug(slug)
    if parsed is None:
        return None
    match_slug, n = parsed
    shooter_key = DEFAULT_SHOOTER_KEY
    shooter_block = payload.get("shooter")
    if isinstance(shooter_block, dict):
        raw = shooter_block.get("id")
        if isinstance(raw, str) and raw:
            shooter_key = raw
    return build_event_id(match_slug, n, shooter_key)


# ---------------------------------------------------------------------------
# Fixture catalog
# ---------------------------------------------------------------------------


class FixtureRecord(BaseModel):
    """One audited fixture, as surfaced to the Lab UI / CLI."""

    slug: str
    audit_path: str
    audio_path: str
    has_audio: bool
    n_shots: int
    expected_rounds: int | None = None
    stage_time_seconds: float | None = None
    beep_time: float | None = None
    source: str | None = None
    source_video: str | None = None
    audit_mtime: float
    audio_mtime: float | None = None
    # Slug of the anchor fixture this one was promoted from (issue #125).
    # Non-null only for derived secondary fixtures; the SPA surfaces a
    # "re-review" link back to the diff-confirm screen for these.
    anchor_slug: str | None = None
    # Event grouping key (issue #149 follow-up). Identifies the same
    # shooter-stage-match across multi-camera coverage so the Lab table
    # can render siblings together. Stored on the fixture JSON when
    # available; falls back to slug derivation for legacy fixtures.
    event_id: str | None = None


def list_fixtures(fixtures_root: Path | None = None) -> list[FixtureRecord]:
    """Walk ``<fixtures_root>/*.json`` and pair each with its sibling WAV.

    Skips backup/peaks artifacts (``*.before-promote``, ``*.peaks-*.json``)
    and files that don't carry a ``shots`` array.
    """
    root = (fixtures_root or DEFAULT_FIXTURES_ROOT).resolve()
    out: list[FixtureRecord] = []
    if not root.is_dir():
        return out
    for json_path in sorted(root.glob("*.json")):
        name = json_path.name
        if name.endswith(".before-promote") or ".peaks-" in name:
            continue
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or "shots" not in payload:
            continue
        wav = json_path.with_suffix(".wav")
        rounds = None
        if isinstance(payload.get("stage_rounds"), dict):
            rounds = payload["stage_rounds"].get("expected")
        anchor_slug: str | None = None
        anchor_block = payload.get("anchor")
        if isinstance(anchor_block, dict):
            raw = anchor_block.get("fixture_slug")
            if isinstance(raw, str):
                anchor_slug = raw
        # event_id precedence: explicit on the fixture JSON beats
        # slug+shooter derivation. Lets a curated event-grouping
        # override the parser for cases where the slug doesn't match
        # the standard pattern.
        event_id = event_id_from_payload(json_path.stem, payload)
        out.append(
            FixtureRecord(
                slug=json_path.stem,
                audit_path=str(json_path),
                audio_path=str(wav),
                has_audio=wav.exists(),
                n_shots=len(payload.get("shots", [])),
                expected_rounds=rounds,
                stage_time_seconds=payload.get("stage_time_seconds"),
                beep_time=payload.get("beep_time"),
                source=payload.get("source"),
                source_video=payload.get("source_video"),
                audit_mtime=json_path.stat().st_mtime,
                audio_mtime=wav.stat().st_mtime if wav.exists() else None,
                anchor_slug=anchor_slug,
                event_id=event_id,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Eval data shapes
# ---------------------------------------------------------------------------


class EvalConfig(BaseModel):
    """Knobs the user can tune from the Lab UI."""

    consensus: int = Field(default=3, ge=1, le=5)
    apriori_boost: float = Field(default=1.0, ge=0.0)
    c_required: bool = Field(
        default=True,
        description=(
            "Issue #103 C-veto: voter C must say yes for a candidate "
            "to be kept. Default on; turn off to compare with the old "
            "3-of-4 consensus."
        ),
    )
    tolerance_ms: float = Field(default=75.0, gt=0.0)
    use_expected_rounds: bool = Field(
        default=True,
        description=(
            "Pass the audit's ``stage_rounds.expected`` into the ensemble "
            "(adaptive voter C + apriori boost)."
        ),
    )
    voter_a_floor_override: float | None = None
    voter_b_threshold_override: float | None = None
    voter_c_threshold_override: float | None = None
    voter_d_threshold_override: float | None = None

    def to_ensemble_config(self) -> EnsembleConfig:
        return EnsembleConfig(
            consensus=self.consensus,
            apriori_boost=self.apriori_boost,
            c_required=self.c_required,
        )


REASON_VALUES: tuple[str, ...] = (
    "cross_bay",
    "echo",
    "barrel_echo",
    "wind",
    "movement",
    "steel_ring",
    "speech",
    "handling",
    "agc_artifact",
    "other",
    "unknown",
)
SUBCLASS_VALUES: tuple[str, ...] = ("paper", "steel", "barrel", "unknown")
UNLABELED_REASON: str = "unlabeled"
UNLABELED_SUBCLASS: str = "unlabeled"


class EvalCandidate(BaseModel):
    """One candidate enriched with ground-truth label for diffing."""

    candidate_number: int
    time: float
    ms_after_beep: int
    confidence: float
    peak_amplitude: float
    score_c: float
    clap_diff: float
    gunshot_prob: float
    vote_a: int
    vote_b: int
    vote_c: int
    vote_d: int
    vote_total: int
    apriori_boost: float
    ensemble_score: float
    kept: bool
    truth: int = Field(description="1 if matched to a ground-truth shot within tolerance, else 0.")
    matched_shot_number: int | None = None
    reason: str | None = Field(
        default=None,
        description=(
            "Optional FP class for rejected candidates (issue #86). "
            "One of REASON_VALUES; ``None`` when unlabeled."
        ),
    )
    subclass: str | None = Field(
        default=None,
        description=(
            "Optional positive subclass for kept candidates (issue #86). "
            "One of SUBCLASS_VALUES; ``None`` when unlabeled."
        ),
    )


class EvalFixtureMetrics(BaseModel):
    """Precision / recall + per-voter contribution for one fixture."""

    n_truth: int
    n_kept: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    voter_recall: dict[str, float] = Field(
        description="Recall when only a single voter's vote is required (informational).",
    )
    fp_by_reason: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Surviving FPs grouped by ``reason`` label (issue #86). "
            "Unlabeled FPs are counted under the ``unlabeled`` key."
        ),
    )
    positives_by_subclass: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Kept TPs grouped by ``subclass`` label (issue #86). "
            "Unlabeled positives are counted under ``unlabeled``."
        ),
    )


class EvalFixture(BaseModel):
    """All eval state for one fixture: universe, metrics, diff lists.

    The ``candidates`` list is the per-candidate cache used by
    ``rescore_universe`` -- so a Lab session loads the universe once,
    then sweeps consensus / apriori sliders without touching CLAP/PANN.
    """

    slug: str
    audit_path: str
    audio_path: str
    source_video: str | None = None
    expected_rounds: int | None = None
    candidates: list[EvalCandidate]
    truth_times: list[float]
    metrics: EvalFixtureMetrics
    audit_mtime: float
    audio_mtime: float | None = None


class EvalUniverse(BaseModel):
    """Cached, model-output-bound layer (heavy to compute)."""

    fixtures: list[EvalFixture]
    voter_a_floor: float
    voter_b_threshold: float
    voter_c_threshold: float
    voter_d_threshold: float
    tolerance_ms: float


class RunSummary(BaseModel):
    """Aggregate metrics for a run (the headline numbers)."""

    n_fixtures: int
    n_truth: int
    n_kept: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    fp_by_reason: dict[str, int] = Field(
        default_factory=dict,
        description="Corpus-wide FP counts by ``reason`` label (issue #86).",
    )
    positives_by_subclass: dict[str, int] = Field(
        default_factory=dict,
        description="Corpus-wide kept TP counts by ``subclass`` label (issue #86).",
    )


class EvalRun(BaseModel):
    """One full eval/rescore result. Persisted under build/lab/runs/."""

    config: EvalConfig
    summary: RunSummary
    universe: EvalUniverse
    config_hash: str
    built_at: str


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _label_truth(
    cand_times: np.ndarray,
    truth_shots: list[dict[str, Any]],
    tolerance_ms: float,
) -> tuple[np.ndarray, list[int | None], list[float]]:
    """Greedy nearest-time labelling. Returns (labels, matched_shot_number, truth_times)."""
    labels = np.zeros(cand_times.size, dtype=np.int64)
    matched: list[int | None] = [None] * cand_times.size
    used: set[int] = set()
    sorted_truth = sorted(truth_shots, key=lambda s: s.get("time", 0.0))
    truth_times: list[float] = [float(s.get("time", 0.0)) for s in sorted_truth]
    for s in sorted_truth:
        t = float(s.get("time", 0.0))
        best_i: int | None = None
        best_d: float | None = None
        for i, c in enumerate(cand_times):
            if i in used:
                continue
            d = abs(float(c) - t) * 1000.0
            if d <= tolerance_ms and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is not None:
            used.add(best_i)
            labels[best_i] = 1
            matched[best_i] = int(s.get("shot_number") or 0) or None
    return labels, matched, truth_times


def _metrics(
    truth_times: list[float],
    candidates: list[EvalCandidate],
) -> EvalFixtureMetrics:
    n_truth = len(truth_times)
    kept = [c for c in candidates if c.kept]
    n_kept = len(kept)
    tp = sum(1 for c in kept if c.truth == 1)
    fp = n_kept - tp
    fn = n_truth - tp
    precision = tp / n_kept if n_kept else 0.0
    recall = tp / n_truth if n_truth else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    voter_recall: dict[str, float] = {}
    if n_truth:
        truth_caps = [c for c in candidates if c.truth == 1]
        for key in ("vote_a", "vote_b", "vote_c", "vote_d"):
            voter_recall[key] = sum(getattr(c, key) for c in truth_caps) / n_truth
    else:
        voter_recall = {"vote_a": 0.0, "vote_b": 0.0, "vote_c": 0.0, "vote_d": 0.0}

    fp_by_reason: dict[str, int] = {}
    for c in kept:
        if c.truth == 1:
            continue
        key = c.reason or UNLABELED_REASON
        fp_by_reason[key] = fp_by_reason.get(key, 0) + 1

    positives_by_subclass: dict[str, int] = {}
    for c in kept:
        if c.truth != 1:
            continue
        key = c.subclass or UNLABELED_SUBCLASS
        positives_by_subclass[key] = positives_by_subclass.get(key, 0) + 1

    return EvalFixtureMetrics(
        n_truth=n_truth,
        n_kept=n_kept,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        voter_recall={k: round(v, 4) for k, v in voter_recall.items()},
        fp_by_reason=fp_by_reason,
        positives_by_subclass=positives_by_subclass,
    )


def _summary(fixtures: list[EvalFixture]) -> RunSummary:
    n_truth = sum(f.metrics.n_truth for f in fixtures)
    n_kept = sum(f.metrics.n_kept for f in fixtures)
    tp = sum(f.metrics.true_positives for f in fixtures)
    fp = sum(f.metrics.false_positives for f in fixtures)
    fn = sum(f.metrics.false_negatives for f in fixtures)
    precision = tp / n_kept if n_kept else 0.0
    recall = tp / n_truth if n_truth else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    fp_by_reason: dict[str, int] = {}
    positives_by_subclass: dict[str, int] = {}
    for f in fixtures:
        for k, v in f.metrics.fp_by_reason.items():
            fp_by_reason[k] = fp_by_reason.get(k, 0) + v
        for k, v in f.metrics.positives_by_subclass.items():
            positives_by_subclass[k] = positives_by_subclass.get(k, 0) + v

    return RunSummary(
        n_fixtures=len(fixtures),
        n_truth=n_truth,
        n_kept=n_kept,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        fp_by_reason=fp_by_reason,
        positives_by_subclass=positives_by_subclass,
    )


def _time_key(t: float) -> float:
    """Stable key for matching live candidates to stored audit entries.

    Round to 3 decimals (1 ms): the audit JSON canonically rounds times to
    4 decimals, so anything tighter than 1 ms is noise from float repr.
    """
    return round(float(t), 3)


def _load_labels_from_audit(
    audit: dict[str, Any],
) -> tuple[dict[float, str], list[tuple[float, str]]]:
    """Extract ``reason`` (rejected) + ``subclass`` (kept) labels.

    Reasons live in ``_candidates_pending_audit.labels_by_time`` -- a flat
    ``{time_str: reason}`` map keyed by candidate time rounded to 1 ms.
    Lookup is exact: the candidate's own time is the key.

    Subclasses live on ``shots[]`` entries -- each kept shot can carry
    one. Returned as ``[(audit_time, subclass), ...]`` because the
    audit shot time can differ from the matched candidate's detected
    time by up to the matching tolerance (~75 ms). Callers do a
    nearest-time lookup per candidate via :func:`_subclass_for_time`.
    """
    reason_by_time: dict[float, str] = {}
    subclass_entries: list[tuple[float, str]] = []

    pending = audit.get("_candidates_pending_audit") or {}
    raw_labels = pending.get("labels_by_time") if isinstance(pending, dict) else None
    if isinstance(raw_labels, dict):
        for k, v in raw_labels.items():
            try:
                t = float(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, str) and v:
                reason_by_time[_time_key(t)] = v

    for s in audit.get("shots", []):
        t = s.get("time")
        sub = s.get("subclass")
        if t is None or not isinstance(sub, str) or not sub:
            continue
        subclass_entries.append((float(t), sub))

    return reason_by_time, subclass_entries


def _subclass_for_time(
    candidate_time: float,
    subclass_entries: list[tuple[float, str]],
    *,
    tolerance_ms: float = 75.0,
) -> str | None:
    """Nearest-time subclass lookup. Mirrors the ±75 ms matching tolerance
    used elsewhere in the lab so a subclass attached to an audit shot
    propagates onto whichever candidate the audit matched it to."""
    best: tuple[float, str] | None = None
    for t, sub in subclass_entries:
        d_ms = abs(t - candidate_time) * 1000.0
        if d_ms <= tolerance_ms and (best is None or d_ms < best[0]):
            best = (d_ms, sub)
    return best[1] if best is not None else None


class CandidateLabel(BaseModel):
    """One label patch to apply via :func:`apply_labels`.

    ``time`` is the source of truth for *where* the label attaches.
    ``candidate_number`` is informational (kept for client logging) but
    not used to look up storage -- that prevented labels from surviving
    detector reshuffles. Reasons key off ``time`` directly; subclasses
    look up the matching ``shots[]`` entry by time as well.
    """

    candidate_number: int
    time: float = Field(description="Candidate time in seconds (1 ms resolution).")
    reason: str | None = Field(
        default=None,
        description="FP class for a rejected candidate; ``None`` clears any prior label.",
    )
    subclass: str | None = Field(
        default=None,
        description="Positive subclass for a kept candidate; ``None`` clears any prior label.",
    )


def apply_labels(audit_path: Path, labels: list[CandidateLabel]) -> dict[str, int]:
    """Patch the audit JSON's labels in place.

    Reasons are written to ``_candidates_pending_audit.labels_by_time``
    (a flat ``{time_str: reason}`` map keyed by 1 ms time). Subclasses
    are attached to the matching ``shots[]`` entry by time-proximity.
    The pending candidates list itself is treated as immutable detector
    output -- never rewritten -- so audit invariants stay intact across
    ensemble re-runs. Atomic write via ``.tmp`` + ``.bak`` rotation.
    """
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    pending = audit.setdefault("_candidates_pending_audit", {})
    if not isinstance(pending, dict):
        pending = {}
        audit["_candidates_pending_audit"] = pending
    labels_by_time: dict[str, str] = pending.setdefault("labels_by_time", {})
    if not isinstance(labels_by_time, dict):
        labels_by_time = {}
        pending["labels_by_time"] = labels_by_time
    shots = audit.setdefault("shots", [])

    counts = {"reason_set": 0, "reason_cleared": 0, "subclass_set": 0, "subclass_cleared": 0}
    for label in labels:
        time_key = f"{round(float(label.time), 3):.3f}"

        if label.reason is not None:
            if label.reason not in REASON_VALUES:
                raise ValueError(f"unknown reason: {label.reason!r}")
            if labels_by_time.get(time_key) != label.reason:
                labels_by_time[time_key] = label.reason
            counts["reason_set"] += 1
        elif time_key in labels_by_time:
            labels_by_time.pop(time_key, None)
            counts["reason_cleared"] += 1

        # Subclass attaches to a kept shot at this time. Find the closest
        # shot within 75 ms (matches the lab's labeling tolerance).
        shot = _find_shot_at_time(shots, float(label.time), tolerance_ms=75.0)
        if shot is not None and label.subclass is not None:
            if label.subclass not in SUBCLASS_VALUES:
                raise ValueError(f"unknown subclass: {label.subclass!r}")
            shot["subclass"] = label.subclass
            counts["subclass_set"] += 1
        elif shot is not None and label.subclass is None and "subclass" in shot:
            shot.pop("subclass", None)
            counts["subclass_cleared"] += 1

    tmp = audit_path.with_suffix(audit_path.suffix + ".tmp")
    backup = audit_path.with_suffix(audit_path.suffix + ".bak")
    tmp.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    if backup.exists():
        backup.unlink()
    audit_path.replace(backup)
    tmp.replace(audit_path)
    return counts


def _find_shot_at_time(
    shots: list[dict[str, Any]],
    t: float,
    *,
    tolerance_ms: float,
) -> dict[str, Any] | None:
    """Return the ``shots[]`` entry whose ``time`` is closest to ``t`` within tolerance."""
    best: tuple[float, dict[str, Any]] | None = None
    for s in shots:
        st = s.get("time")
        if st is None:
            continue
        d_ms = abs(float(st) - t) * 1000.0
        if d_ms <= tolerance_ms and (best is None or d_ms < best[0]):
            best = (d_ms, s)
    return best[1] if best is not None else None


# ---------------------------------------------------------------------------
# Eval (heavy) and rescore (light)
# ---------------------------------------------------------------------------


def run_eval(
    runtime: EnsembleRuntime,
    *,
    fixtures_root: Path | None = None,
    slugs: list[str] | None = None,
    config: EvalConfig | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> EvalRun:
    """Run the ensemble against fixtures and build a fresh ``EvalUniverse``.

    Caller passes a pre-loaded ``runtime`` so model weights are
    amortised across many calls (Lab UI loads once on first request).
    ``progress(i, total, slug)`` (when provided) fires after each
    fixture is processed -- shaped to feed JobHandle.update.
    """
    cfg = config or EvalConfig()
    cal = runtime.calibration
    catalog = list_fixtures(fixtures_root)
    if slugs:
        wanted = set(slugs)
        catalog = [f for f in catalog if f.slug in wanted]
    catalog = [f for f in catalog if f.has_audio]

    fixtures: list[EvalFixture] = []
    ec = cfg.to_ensemble_config()
    total = len(catalog)
    for i, fix in enumerate(catalog):
        audit = json.loads(Path(fix.audit_path).read_text(encoding="utf-8"))
        audio, sr = load_audio(Path(fix.audio_path))
        beep_time = float(audit.get("beep_time", 0.0))
        stage_time = float(audit.get("stage_time_seconds", 0.0))
        expected = fix.expected_rounds if cfg.use_expected_rounds else None

        result = detect_shots_ensemble(
            audio,
            sr,
            beep_time,
            stage_time,
            runtime,
            expected_rounds=expected,
            ensemble_config=ec,
        )
        cand_times = np.array([c.time for c in result.candidates], dtype=np.float64)
        labels, matched, truth_times = _label_truth(
            cand_times, audit.get("shots", []), cfg.tolerance_ms
        )
        reason_by_time, subclass_entries = _load_labels_from_audit(audit)
        candidates = [
            EvalCandidate(
                candidate_number=c.candidate_number,
                time=c.time,
                ms_after_beep=c.ms_after_beep,
                confidence=c.confidence,
                peak_amplitude=c.peak_amplitude,
                score_c=c.score_c,
                clap_diff=c.clap_diff,
                gunshot_prob=c.gunshot_prob,
                vote_a=c.vote_a,
                vote_b=c.vote_b,
                vote_c=c.vote_c,
                vote_d=c.vote_d,
                vote_total=c.vote_total,
                apriori_boost=c.apriori_boost,
                ensemble_score=c.ensemble_score,
                kept=c.kept,
                truth=int(labels[i]),
                matched_shot_number=matched[i],
                reason=reason_by_time.get(_time_key(c.time)),
                subclass=_subclass_for_time(c.time, subclass_entries),
            )
            for i, c in enumerate(result.candidates)
        ]
        metrics = _metrics(truth_times, candidates)
        fixtures.append(
            EvalFixture(
                slug=fix.slug,
                audit_path=fix.audit_path,
                audio_path=fix.audio_path,
                source_video=fix.source_video,
                expected_rounds=fix.expected_rounds,
                candidates=candidates,
                truth_times=truth_times,
                metrics=metrics,
                audit_mtime=fix.audit_mtime,
                audio_mtime=fix.audio_mtime,
            )
        )
        if progress is not None:
            progress(i + 1, total, fix.slug)

    universe = EvalUniverse(
        fixtures=fixtures,
        voter_a_floor=cal.voter_a_floor,
        voter_b_threshold=cal.voter_b_threshold,
        voter_c_threshold=cal.voter_c_threshold,
        voter_d_threshold=cal.voter_d_threshold,
        tolerance_ms=cfg.tolerance_ms,
    )
    summary = _summary(fixtures)
    return EvalRun(
        config=cfg,
        summary=summary,
        universe=universe,
        config_hash=_hash_config(cfg),
        built_at=datetime.now(UTC).isoformat(),
    )


def relabel_run(run: EvalRun) -> EvalRun:
    """Re-attach ``reason`` / ``subclass`` labels from disk to a cached run.

    No model calls and no detector run: just re-reads each fixture's
    audit JSON, re-keys ``reason`` / ``subclass`` entries by time, and
    rebuilds per-fixture + summary breakdowns. Used by ``/api/lab/labels``
    so a label save can return a fresh ``EvalRun`` to the SPA without
    triggering a full eval (which is multi-second on 12 fixtures).
    """
    new_fixtures: list[EvalFixture] = []
    for fix in run.universe.fixtures:
        audit_path = Path(fix.audit_path)
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            new_fixtures.append(fix.model_copy(deep=True))
            continue
        reason_by_time, subclass_entries = _load_labels_from_audit(audit)

        new_candidates = [
            c.model_copy(
                update={
                    "reason": reason_by_time.get(_time_key(c.time)),
                    "subclass": _subclass_for_time(c.time, subclass_entries),
                }
            )
            for c in fix.candidates
        ]
        metrics = _metrics(fix.truth_times, new_candidates)
        new_fixtures.append(
            fix.model_copy(update={"candidates": new_candidates, "metrics": metrics})
        )

    new_universe = run.universe.model_copy(update={"fixtures": new_fixtures})
    summary = _summary(new_fixtures)
    return run.model_copy(
        update={
            "universe": new_universe,
            "summary": summary,
            "built_at": datetime.now(UTC).isoformat(),
        }
    )


def rescore_universe(universe: EvalUniverse, config: EvalConfig) -> EvalRun:
    """Recompute votes / consensus / metrics from a cached universe.

    Voter B/C/D get the calibrated thresholds (or per-voter overrides
    if the config supplies them). Voter A uses the calibrated floor
    (or the override). No model calls; sub-100 ms for the full set.
    """
    a_floor = (
        config.voter_a_floor_override
        if config.voter_a_floor_override is not None
        else universe.voter_a_floor
    )
    b_thr = (
        config.voter_b_threshold_override
        if config.voter_b_threshold_override is not None
        else universe.voter_b_threshold
    )
    c_thr = (
        config.voter_c_threshold_override
        if config.voter_c_threshold_override is not None
        else universe.voter_c_threshold
    )
    d_thr = (
        config.voter_d_threshold_override
        if config.voter_d_threshold_override is not None
        else universe.voter_d_threshold
    )

    rescored: list[EvalFixture] = []
    for fix in universe.fixtures:
        if not fix.candidates:
            rescored.append(fix.model_copy(deep=True))
            continue

        confs = np.array([c.confidence for c in fix.candidates], dtype=np.float64)
        clap_diff = np.array([c.clap_diff for c in fix.candidates], dtype=np.float64)
        score_c = np.array([c.score_c for c in fix.candidates], dtype=np.float64)
        gun = np.array([c.gunshot_prob for c in fix.candidates], dtype=np.float64)

        vote_a = (confs >= a_floor).astype(np.int64)
        vote_b = (clap_diff >= b_thr).astype(np.int64)
        vote_d = (gun >= d_thr).astype(np.int64)

        expected = fix.expected_rounds if config.use_expected_rounds else None
        if expected and expected > 0:
            # Mirrors ``ensemble.voters.vote_c_adaptive`` (issue #103
            # + follow-up): top-(K+slack) by GBDT prob, with a
            # confidence-override at 0.75 so very-confident shots
            # pass even when the K-cap silences them.
            slack = max(3, int(expected * 0.25 + 0.5))
            target = expected + slack
            if target >= score_c.size:
                vote_c = np.ones_like(score_c, dtype=np.int64)
            else:
                vote_c = np.zeros_like(score_c, dtype=np.int64)
                vote_c[np.argsort(-score_c)[:target]] = 1
            vote_c[score_c >= 0.75] = 1
        else:
            vote_c = (score_c >= c_thr).astype(np.int64)

        boost = np.zeros_like(confs)
        if expected and expected > 0:
            top = np.argsort(-confs)[:expected]
            boost[top] = config.apriori_boost
        vote_total = vote_a + vote_b + vote_c + vote_d
        ensemble_score = vote_total.astype(np.float64) + boost
        kept_mask = ensemble_score >= config.consensus
        if config.c_required:
            kept_mask = kept_mask & vote_c.astype(bool)

        new_cands: list[EvalCandidate] = []
        for i, c in enumerate(fix.candidates):
            new_cands.append(
                c.model_copy(
                    update={
                        "vote_a": int(vote_a[i]),
                        "vote_b": int(vote_b[i]),
                        "vote_c": int(vote_c[i]),
                        "vote_d": int(vote_d[i]),
                        "vote_total": int(vote_total[i]),
                        "apriori_boost": float(boost[i]),
                        "ensemble_score": round(float(ensemble_score[i]), 2),
                        "kept": bool(kept_mask[i]),
                    }
                )
            )
        metrics = _metrics(fix.truth_times, new_cands)
        rescored.append(fix.model_copy(update={"candidates": new_cands, "metrics": metrics}))

    new_universe = universe.model_copy(
        update={"fixtures": rescored, "tolerance_ms": config.tolerance_ms},
    )
    return EvalRun(
        config=config,
        summary=_summary(rescored),
        universe=new_universe,
        config_hash=_hash_config(config),
        built_at=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------


@dataclass
class PromoteRequest:
    """Inputs for ``promote_stage_to_fixture``."""

    audit_json_path: Path
    audit_wav_path: Path
    fixture_slug: str
    fixtures_root: Path | None = None
    overwrite: bool = False
    extra_metadata: dict[str, Any] = field(default_factory=dict)
    # Shooter identity to stamp on the fixture (issue #149 follow-up).
    # ``None`` means "preserve whatever's in the source audit JSON, else
    # default to ``DEFAULT_SHOOTER_KEY``" -- used by promote-from-anchor
    # paths that already inherited shooter from the anchor block.
    # Promote-from-project paths pass an explicit dict with ``id``,
    # optional ``name`` + ``ssi_shooter_id`` so the resulting fixture
    # groups under its shooter rather than the legacy ``self`` sentinel.
    shooter: dict[str, Any] | None = None
    # Visual/source provenance the calibrator + Voter E need. Caller
    # derives these from the project (primary video path, trim window,
    # camera mount + position + audio source). When ``require_provenance``
    # is True, ``promote_stage_to_fixture`` refuses any of these missing
    # so the published fixture is calibration-ready out of the gate
    # rather than needing a later manual backfill.
    source_video: Path | str | None = None
    fixture_window_in_source: tuple[float, float] | list[float] | None = None
    camera: dict[str, Any] | None = None
    require_provenance: bool = True


def promote_stage_to_fixture(req: PromoteRequest) -> FixtureRecord:
    """Copy a stage's audit JSON + sibling WAV into the fixtures dir.

    Refuses to overwrite an existing fixture unless ``overwrite=True``.
    Adds ``promoted_at`` + any ``extra_metadata`` to the JSON so the
    fixture carries provenance.
    """
    root = (req.fixtures_root or DEFAULT_FIXTURES_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target_json = root / f"{req.fixture_slug}.json"
    target_wav = root / f"{req.fixture_slug}.wav"
    if target_json.exists() and not req.overwrite:
        raise FileExistsError(f"fixture already exists: {target_json}")
    if not req.audit_json_path.exists():
        raise FileNotFoundError(f"audit JSON missing: {req.audit_json_path}")
    if not req.audit_wav_path.exists():
        raise FileNotFoundError(f"audit WAV missing: {req.audit_wav_path}")

    payload = json.loads(req.audit_json_path.read_text(encoding="utf-8"))
    payload["promoted_at"] = datetime.now(UTC).isoformat()
    # ``promoted_from`` is omitted from the public fixture: it would
    # carry the user's home dir. The audit JSON name is recoverable
    # from the slug + the project the user can identify locally.
    if req.extra_metadata:
        payload.setdefault("provenance", {}).update(req.extra_metadata)

    # Caller-supplied source/window/camera win over the audit JSON.
    # The promote-from-project endpoint derives these from the live
    # project (primary path, beep_time, trim buffers, camera mount);
    # the audit JSON itself only carries shot/beep data because that's
    # what the SPA writes. ``require_provenance`` defends the lab
    # corpus from another silent half-labelled fixture landing.
    if req.source_video is not None:
        payload["source_video"] = str(req.source_video)
    if req.fixture_window_in_source is not None:
        payload["fixture_window_in_source"] = [
            round(float(req.fixture_window_in_source[0]), 4),
            round(float(req.fixture_window_in_source[1]), 4),
        ]
    if req.camera is not None:
        payload["camera"] = dict(req.camera)

    # Strip local home-dir prefixes from human-readable path fields so
    # the published fixture carries no OS-username PII.
    if "source" in payload:
        payload["source"] = scrub_local_path(payload.get("source"))
    if "source_video" in payload:
        payload["source_video"] = scrub_local_path(payload.get("source_video"))

    if req.require_provenance:
        missing = []
        if not isinstance(payload.get("source_video"), str) or not payload["source_video"]:
            missing.append("source_video")
        fwis = payload.get("fixture_window_in_source")
        if not (isinstance(fwis, (list, tuple)) and len(fwis) == 2):
            missing.append("fixture_window_in_source")
        cam = payload.get("camera")
        if not (isinstance(cam, dict) and cam.get("mount")):
            missing.append("camera.mount")
        if missing:
            raise ValueError(
                "promote_stage_to_fixture: audit data is incomplete; the "
                f"following provenance fields are missing: {', '.join(missing)}. "
                "Caller must derive these from the project (primary video "
                "path, trim window, camera mount) and pass them on the "
                "PromoteRequest, or set require_provenance=False for legacy "
                "promotes."
            )

    # Shooter identity + event_id stamping. Caller-supplied shooter wins
    # over whatever's in the source audit JSON (project-promote provides
    # this from ``selected_shooter_id``); falls back to existing data on
    # the audit, then to the DEFAULT_SHOOTER_KEY sentinel.
    if req.shooter is not None:
        payload["shooter"] = dict(req.shooter)
    elif not isinstance(payload.get("shooter"), dict):
        payload["shooter"] = {"id": DEFAULT_SHOOTER_KEY}
    if "event_id" not in payload or not isinstance(payload.get("event_id"), str):
        derived = event_id_from_payload(req.fixture_slug, payload)
        if derived:
            payload["event_id"] = derived

    tmp_json = target_json.with_suffix(".json.tmp")
    tmp_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp_json.replace(target_json)
    shutil.copy2(req.audit_wav_path, target_wav)
    catalog = list_fixtures(root)
    for rec in catalog:
        if rec.slug == req.fixture_slug:
            return rec
    raise RuntimeError("fixture promote completed but record not found in catalog")


# ---------------------------------------------------------------------------
# Run persistence
# ---------------------------------------------------------------------------


def _hash_config(cfg: EvalConfig) -> str:
    blob = json.dumps(cfg.model_dump(), sort_keys=True, ensure_ascii=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def save_run(run: EvalRun, *, runs_root: Path | None = None) -> Path:
    """Persist a run as deterministic JSON + update the ``latest.json`` pointer."""
    root = (runs_root or DEFAULT_RUNS_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = run.built_at.replace(":", "").replace("-", "").replace(".", "")[:15]
    target = root / f"{stamp}-{run.config_hash}.json"
    blob = json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=True)
    target.write_text(blob + "\n", encoding="utf-8")
    latest = root / LATEST_RUN_FILENAME
    latest.write_text(blob + "\n", encoding="utf-8")
    return target


def load_run(path: Path) -> EvalRun:
    """Read a persisted run JSON back into an ``EvalRun`` model."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return EvalRun.model_validate(payload)


def save_config_yaml(
    *,
    run: EvalRun,
    name: str,
    output_dir: Path | None = None,
    note: str | None = None,
    overwrite: bool = False,
) -> Path:
    """Write ``configs/ensemble.<name>.yaml`` for a finished run.

    Used by both the ``splitsmith lab save-config`` CLI and the
    "Save as YAML" button in the Lab UI tuning panel. The YAML carries
    the active ``EvalConfig``, the run's headline summary, and the
    provenance needed to replay the result later.
    """
    import yaml  # local import: yaml is only needed in this code path

    out_dir = (output_dir or Path("configs")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"ensemble.{name}.yaml"
    if target.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite {target} (pass overwrite=True to force)")
    payload = {
        "name": name,
        "config": run.config.model_dump(),
        "summary": run.summary.model_dump(),
        "provenance": {
            "built_at": run.built_at,
            "config_hash": run.config_hash,
            "fixtures": [f.slug for f in run.universe.fixtures],
            "note": note,
        },
    }
    with target.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=True, default_flow_style=False)
    return target
