"""Pure-function lab core.

Used identically by ``/api/lab/*`` and ``splitsmith lab``. No FastAPI,
no Typer, no globals. Heavy model loads are passed in; callers cache.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from ..beep_detect import load_audio
from ..config import ShotDetectConfig
from ..ensemble.api import (
    EnsembleConfig,
    EnsembleRuntime,
    detect_shots_ensemble,
)
from ..shot_detect import detect_shots


DEFAULT_FIXTURES_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
DEFAULT_RUNS_ROOT = Path("build/lab/runs")
LATEST_RUN_FILENAME = "latest.json"


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
    audit_mtime: float
    audio_mtime: float | None = None


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
                audit_mtime=json_path.stat().st_mtime,
                audio_mtime=wav.stat().st_mtime if wav.exists() else None,
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
    tolerance_ms: float = Field(default=75.0, gt=0.0)
    use_expected_rounds: bool = Field(
        default=True,
        description="Pass the audit's ``stage_rounds.expected`` into the ensemble (adaptive voter C + apriori boost).",
    )
    voter_a_floor_override: float | None = None
    voter_b_threshold_override: float | None = None
    voter_c_threshold_override: float | None = None
    voter_d_threshold_override: float | None = None

    def to_ensemble_config(self) -> EnsembleConfig:
        return EnsembleConfig(consensus=self.consensus, apriori_boost=self.apriori_boost)


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


class EvalFixture(BaseModel):
    """All eval state for one fixture: universe, metrics, diff lists.

    The ``candidates`` list is the per-candidate cache used by
    ``rescore_universe`` -- so a Lab session loads the universe once,
    then sweeps consensus / apriori sliders without touching CLAP/PANN.
    """

    slug: str
    audit_path: str
    audio_path: str
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
    )


# ---------------------------------------------------------------------------
# Eval (heavy) and rescore (light)
# ---------------------------------------------------------------------------


def run_eval(
    runtime: EnsembleRuntime,
    *,
    fixtures_root: Path | None = None,
    slugs: list[str] | None = None,
    config: EvalConfig | None = None,
) -> EvalRun:
    """Run the ensemble against fixtures and build a fresh ``EvalUniverse``.

    Caller passes a pre-loaded ``runtime`` so model weights are
    amortised across many calls (Lab UI loads once on first request).
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
    for fix in catalog:
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
            )
            for i, c in enumerate(result.candidates)
        ]
        metrics = _metrics(truth_times, candidates)
        fixtures.append(
            EvalFixture(
                slug=fix.slug,
                audit_path=fix.audit_path,
                audio_path=fix.audio_path,
                expected_rounds=fix.expected_rounds,
                candidates=candidates,
                truth_times=truth_times,
                metrics=metrics,
                audit_mtime=fix.audit_mtime,
                audio_mtime=fix.audio_mtime,
            )
        )

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
            slack = max(3, int(expected * 0.10 + 0.5))
            target = expected + slack
            if target >= score_c.size:
                vote_c = np.ones_like(score_c, dtype=np.int64)
            else:
                vote_c = np.zeros_like(score_c, dtype=np.int64)
                vote_c[np.argsort(-score_c)[:target]] = 1
        else:
            vote_c = (score_c >= c_thr).astype(np.int64)

        boost = np.zeros_like(confs)
        if expected and expected > 0:
            top = np.argsort(-confs)[:expected]
            boost[top] = config.apriori_boost
        vote_total = vote_a + vote_b + vote_c + vote_d
        ensemble_score = vote_total.astype(np.float64) + boost
        kept_mask = ensemble_score >= config.consensus

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
        rescored.append(
            fix.model_copy(update={"candidates": new_cands, "metrics": metrics})
        )

    new_universe = universe.model_copy(update={"fixtures": rescored, "tolerance_ms": config.tolerance_ms})
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
    payload["promoted_from"] = str(req.audit_json_path)
    if req.extra_metadata:
        payload.setdefault("provenance", {}).update(req.extra_metadata)

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
