"""Build the shipped ensemble calibration + GBDT artifacts.

Reads the audited fixtures listed in ``DEFAULT_FIXTURES`` and writes:

* ``src/splitsmith/data/ensemble_calibration.json`` -- per-voter
  thresholds, the CLAP prompt bank, calibration provenance.
* ``src/splitsmith/data/voter_c_gbdt.joblib`` -- the trained
  ``GradientBoostingClassifier`` (fit on ALL calibration data, threshold
  picked from 5-fold CV predictions on the same set).

The production server loads both via ``splitsmith.ensemble.calibration``
and reuses them across detections.

Re-run this script after adding new audited fixtures or changing the
hand-feature / CLAP-prompt set. The fixture-builder script
``scripts/build_ensemble_fixture.py`` continues to produce the
review-time variants under ``build/ensemble-review/``; this script is
the production-time equivalent that ships its outputs in the wheel.

CLAP and PANN feature caches under ``tests/fixtures/.cache/`` are
expected to exist; build them first via
``scripts/extract_clap_features.py`` and
``scripts/extract_audio_embeddings.py``.

Run:
    uv run python scripts/build_ensemble_artifacts.py
    uv run python scripts/build_ensemble_artifacts.py --target-recall 0.95
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.ensemble import features as feat
from splitsmith.ensemble import visual as vis
from splitsmith.ensemble.calibration import (
    DEFAULT_CAMERA_CLASS,
    DEFAULT_VOTER_E_PROBE_FILENAME,
    camera_class_from_mount,
)
from splitsmith.ensemble.tta import compute_tta_agreement
from splitsmith.shot_detect import detect_shots

DEFAULT_FIXTURES = [
    "stage-shots-tallmilan-2026-stage3",
    "stage-shots-blacksmith-2026-stage7",
    "stage-shots-blacksmith-2026-stage1",
    "stage-shots-blacksmith-2026-stage2",
    "stage-shots-blacksmith-2026-stage3",
    "stage-shots-blacksmith-2026-stage5",
    "stage-shots-blacksmith-2026-stage6",
    "stage-shots-blacksmith-2026-stage8",
    "stage-shots-tallmilan-2026-stage2",
    "stage-shots-tallmilan-2026-stage7",
    "stage-shots-tallmilan-2026-stage4",
    "stage-shots-tallmilan-2026-stage5",
    "stage-shots-tallmilan-2026-stage6",
    # Phone-cam (issue #137 / promote-secondary, PR #134). Calibration
    # script auto-skips fixtures missing CLAP / PANN caches; run
    # ``scripts/extract_clap_features.py`` and
    # ``scripts/extract_audio_embeddings.py`` for these stems first to
    # pick them up.
    "stage-shots-tallmilan-2026-stage2-apple-iphone17pro",
    "stage-shots-tallmilan-2026-stage4-apple-iphone17pro",
    "stage-shots-tallmilan-2026-stage5-apple-iphone17pro",
    "stage-shots-tallmilan-2026-stage6-apple-iphone17pro",
    "stage-shots-tallmilan-2026-stage7-apple-iphone17pro",
    # Phone-cam (PR #152, anchored against the blacksmith headcam fixtures
    # to land cross-match acoustic variation -- issue #149's near-term ask).
    "stage-shots-blacksmith-2026-stage1-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage2-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage3-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage5-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage6-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage7-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage8-apple-iphone17pro",
]

# Fixtures whose promote-report flagged ``wrong_clip_suspected: true`` --
# the phone clip linked to that stage is silent at the snapped shot
# positions (probably the wrong clip / occluded mic). Keeping them in
# repo for inspection, skipping them in calibration so they don't pull
# the GBDT toward labelling silence as positive. See issue #154 for the
# re-anchor plan.
WRONG_CLIP_FIXTURES: frozenset[str] = frozenset(
    {
        "stage-shots-blacksmith-2026-stage6-apple-iphone17pro",
        "stage-shots-tallmilan-2026-stage4-apple-iphone17pro",
    }
)
FIXTURES_DIR = Path("tests/fixtures")
FULL_DIR = FIXTURES_DIR / "full"
CACHE_DIR = FIXTURES_DIR / ".cache"
MINED_NEGATIVES_PATH = CACHE_DIR / "_mined_negatives.npz"
DATA_DIR = Path("src/splitsmith/data")

# Cap mined negatives per fixture relative to that fixture's positive count,
# sampled by descending Voter A confidence so the hardest survivors win.
# Keeps the GBDT's class balance bounded and gives more signal per training row.
DEFAULT_NEG_CAP_RATIO: float = 5.0
_TIME_MATCH_TOL_S: float = 1e-3  # full-cache rows are produced from the same WAV


def _label(cand_t: list[float], truth_shots: list[dict], tol_ms: float) -> list[int]:
    """Greedy nearest-time label: 1 if a truth shot is within ``tol_ms`` of the candidate."""
    labels = [0] * len(cand_t)
    used: set[int] = set()
    for s in sorted(truth_shots, key=lambda x: x["time"]):
        t = s["time"]
        best_i, best_d = None, None
        for i, c in enumerate(cand_t):
            if i in used:
                continue
            d = abs(c - t) * 1000.0
            if d <= tol_ms and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is not None:
            used.add(best_i)
            labels[best_i] = 1
    return labels


def _build_universe(
    fixtures: list[str],
    tolerance_ms: float,
    *,
    log: Callable[[str], None] = print,
):
    """Per-fixture: detect at max recall, label, slot CLAP+PANN signals.

    Each universe row carries a ``camera_class`` tag derived from the
    fixture's ``camera.mount`` so the calibrator can stratify per-voter
    thresholds without re-reading the fixture JSONs.
    """
    universe = []
    for fix in fixtures:
        if fix in WRONG_CLIP_FIXTURES:
            log(f"  skip {fix}: wrong_clip_suspected (see issue tracking re-anchor)")
            continue
        truth_path = FIXTURES_DIR / f"{fix}.json"
        wav_path = FIXTURES_DIR / f"{fix}.wav"
        clap_path = CACHE_DIR / f"{fix}_clap.npz"
        pann_path = CACHE_DIR / f"{fix}_pann.npz"
        if not truth_path.exists() or not wav_path.exists():
            log(f"  skip {fix}: missing fixture files")
            continue
        if not clap_path.exists() or not pann_path.exists():
            log(
                f"  skip {fix}: CLAP/PANN cache missing -- run "
                "extract_clap_features.py + extract_audio_embeddings.py "
                "for this stem to include it."
            )
            continue

        truth = json.loads(truth_path.read_text())
        camera_block = truth.get("camera") or {}
        cam_class = camera_class_from_mount(camera_block.get("mount"))
        audio, sr = load_audio(wav_path)
        cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
        shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg)
        if not shots:
            continue
        cand_t = [s.time_absolute for s in shots]
        labels = _label(cand_t, truth.get("shots", []), tolerance_ms)

        clap = np.load(clap_path, allow_pickle=True)
        if clap["audio_emb"].shape[0] != len(shots):
            raise SystemExit(f"{fix}: CLAP cache stale; re-run extract_clap_features.py --force")
        prompts_in_cache = [str(p) for p in clap["prompts"].tolist()]
        if tuple(prompts_in_cache) != feat.CLAP_PROMPTS:
            raise SystemExit(
                f"{fix}: CLAP cache prompt order mismatch with package CLAP_PROMPTS. "
                "Update extract_clap_features.py to import the prompt bank from "
                "splitsmith.ensemble.features and re-run with --force."
            )
        sims = clap["text_sims"]
        clap_diff = feat.clap_diff_from_similarities(sims)

        pann = np.load(pann_path)
        if pann["gunshot_prob"].shape[0] != len(shots):
            raise SystemExit(f"{fix}: PANN cache stale; re-run extract_audio_embeddings.py --force")
        gunshot_prob = pann["gunshot_prob"]

        times = np.array(cand_t, dtype=np.float64)
        confidences = np.array([s.confidence for s in shots], dtype=np.float64)
        peak_amps = np.array([s.peak_amplitude for s in shots], dtype=np.float64)
        tta_agreement = compute_tta_agreement(
            audio, sr, truth["beep_time"], truth["stage_time_seconds"], times
        )
        hand = feat.compute_hand_features(
            audio, sr, times, truth["beep_time"], confidences, peak_amps, tta_agreement
        )

        for i, shot in enumerate(shots):
            universe.append(
                {
                    "fixture": fix,
                    "camera_class": cam_class,
                    "label": labels[i],
                    "confidence": float(shot.confidence),
                    "clap_diff": float(clap_diff[i]),
                    "gunshot_prob": float(gunshot_prob[i]),
                    "hand_feats": hand[i].tolist(),
                    "clap_sims": [float(x) for x in sims[i]],
                }
            )
    return universe


def _voter_a_floor(universe: list[dict]) -> float:
    """Lowest positive confidence; preserves voter A recall by construction."""
    pos = [c["confidence"] for c in universe if c["label"] == 1]
    return max(0.0, min(pos) - 1e-6) if pos else 0.03


def _voter_b_threshold(universe: list[dict]) -> float:
    pos = [c["clap_diff"] for c in universe if c["label"] == 1]
    return float(min(pos)) if pos else 0.0


def _voter_d_threshold(universe: list[dict]) -> float:
    pos = [c["gunshot_prob"] for c in universe if c["label"] == 1]
    return float(min(pos)) if pos else 0.0


def _split_by_camera_class(universe: list[dict]) -> dict[str, list[dict]]:
    """Group universe rows by ``camera_class``."""
    grouped: dict[str, list[dict]] = {}
    for row in universe:
        grouped.setdefault(row.get("camera_class", DEFAULT_CAMERA_CLASS), []).append(row)
    return grouped


def _x_from(universe: list[dict]) -> np.ndarray:
    """Stack per-row Voter C features: ``[hand | clap_sims | clap_diff | camera_class_onehot]``.

    Camera-class one-hot is appended last so the column order matches
    ``voter_c_feature_matrix`` at runtime.
    """
    if not universe:
        return np.zeros((0, feat.VOTER_C_FEATURE_DIM), dtype=np.float64)
    base = np.array(
        [c["hand_feats"] + c["clap_sims"] + [c["clap_diff"]] for c in universe],
        dtype=np.float64,
    )
    classes = [c.get("camera_class", DEFAULT_CAMERA_CLASS) for c in universe]
    cam_block = feat.camera_class_one_hot(classes, base.shape[0])
    return np.concatenate([base, cam_block], axis=1)


def _sample_weights(universe: list[dict], phone_upweight: float) -> np.ndarray:
    """Per-row sample weights: phone-class rows get ``phone_upweight`` to
    counterbalance the headcam-dominated corpus.

    Returns a uniform vector when only one class is present (single-class
    builds get the same training behaviour they did pre-#139).
    """
    classes = [c.get("camera_class", DEFAULT_CAMERA_CLASS) for c in universe]
    if len(set(classes)) <= 1:
        return np.ones(len(classes), dtype=np.float64)
    return np.array(
        [phone_upweight if c != DEFAULT_CAMERA_CLASS else 1.0 for c in classes],
        dtype=np.float64,
    )


def _load_mined_negatives(
    n_pos_by_fixture: dict[str, int],
    *,
    cap_ratio: float,
    log: Callable[[str], None],
) -> tuple[list[dict], dict]:
    """Materialise mined-negative training rows aligned to full-mode caches.

    Returns ``(rows, provenance)``. ``rows`` use the same shape as
    ``_build_universe`` items so they can be appended to the Voter C training
    set. Each fixture's contribution is capped at ``cap_ratio * n_positives``
    by descending Voter A confidence (the hardest survivors). Voter A/B/D
    thresholds are NOT recomputed -- those stay calibrated on positives only.

    Quietly returns no rows when the mined-negatives file or the full-mode
    feature caches are missing, so the script remains a drop-in replacement
    for installs that haven't run the issue #87 mining pipeline yet.
    """
    if not MINED_NEGATIVES_PATH.exists():
        log("No mined-negatives cache; Voter C trains on stage-window negatives only.")
        return [], {"n_mined_negatives_used": 0, "mining_source_fixtures": []}

    mined = np.load(MINED_NEGATIVES_PATH, allow_pickle=True)
    fixtures = mined["fixture"]
    times = mined["time_in_full"].astype(np.float64)
    confidences = mined["confidence"].astype(np.float64)
    peaks = mined["peak_amplitude"].astype(np.float64)
    region_tags = mined["region_tag"]

    by_fixture: dict[str, list[int]] = {}
    for i, fix in enumerate(fixtures):
        by_fixture.setdefault(str(fix), []).append(i)

    rows: list[dict] = []
    used_fixtures: list[str] = []
    skipped: list[str] = []
    for fix, indices in by_fixture.items():
        sidecar_path = FULL_DIR / f"{fix}_full.json"
        wav_path = FULL_DIR / f"{fix}_full.wav"
        clap_path = CACHE_DIR / f"{fix}_clap_full.npz"
        pann_path = CACHE_DIR / f"{fix}_pann_full.npz"
        if not all(p.exists() for p in (sidecar_path, wav_path, clap_path, pann_path)):
            skipped.append(fix)
            continue

        n_pos = n_pos_by_fixture.get(fix, 0)
        if n_pos == 0:
            skipped.append(fix)
            continue
        cap = max(1, int(round(cap_ratio * n_pos)))
        # Hardest survivors first: descending Voter A confidence.
        ordered = sorted(indices, key=lambda i: -confidences[i])[:cap]

        sidecar = json.loads(sidecar_path.read_text())
        audit = json.loads((FIXTURES_DIR / f"{fix}.json").read_text())
        beep_in_full = (
            float(audit["fixture_window_in_source"][0])
            + float(audit["beep_time"])
            - float(sidecar["full_window_in_source"][0])
        )

        clap = np.load(clap_path, allow_pickle=True)
        pann = np.load(pann_path)
        clap_times = clap["times"].astype(np.float64)
        if clap_times.shape != pann["gunshot_prob"].shape:
            raise SystemExit(
                f"{fix}: full CLAP and PANN caches disagree on candidate count "
                f"({clap_times.shape[0]} vs {pann['gunshot_prob'].shape[0]}); "
                "rebuild both with --full --force."
            )
        prompts_in_cache = [str(p) for p in clap["prompts"].tolist()]
        if tuple(prompts_in_cache) != feat.CLAP_PROMPTS:
            raise SystemExit(
                f"{fix}: full CLAP cache prompt order mismatch with package "
                "CLAP_PROMPTS; rebuild with extract_clap_features.py --full --force."
            )
        sims = clap["text_sims"]
        clap_diffs = feat.clap_diff_from_similarities(sims)
        gunshot_probs = pann["gunshot_prob"]

        # Map mined times -> full-cache row indices. Same WAV + same config in
        # mine_negatives and the --full extractors, so an exact (or sub-ms)
        # match always exists; absence is a cache-staleness bug worth raising.
        kept_cache_idx: list[int] = []
        kept_mined_idx: list[int] = []
        for mi in ordered:
            t = times[mi]
            j = int(np.argmin(np.abs(clap_times - t)))
            if abs(clap_times[j] - t) > _TIME_MATCH_TOL_S:
                raise SystemExit(
                    f"{fix}: mined-negative time {t:.4f}s has no match in "
                    f"full CLAP cache (closest {clap_times[j]:.4f}s, "
                    f"delta {abs(clap_times[j] - t)*1e3:.2f}ms). Rebuild full "
                    "caches with --full --force after re-running mine_negatives.py."
                )
            kept_cache_idx.append(j)
            kept_mined_idx.append(mi)

        if not kept_cache_idx:
            continue

        audio, sr = load_audio(wav_path)
        cand_t = np.array(times[kept_mined_idx], dtype=np.float64)
        cand_conf = np.array(confidences[kept_mined_idx], dtype=np.float64)
        cand_peak = np.array(peaks[kept_mined_idx], dtype=np.float64)
        # Mined negatives sit outside the stage window, where detect_shots
        # doesn't run -- so we can't recover a real TTA agreement count for
        # them. Pad with 1.0 (the "original-only" agreement floor). Mining
        # is OFF by default in production calibration; if it ever turns
        # back on this needs a full-fixture detector pass.
        tta_agreement = np.ones(len(cand_t), dtype=np.float64)
        hand = feat.compute_hand_features(
            audio, sr, cand_t, beep_in_full, cand_conf, cand_peak, tta_agreement
        )

        for k, ci in enumerate(kept_cache_idx):
            rows.append(
                {
                    "fixture": fix,
                    "label": 0,
                    "confidence": float(cand_conf[k]),
                    "clap_diff": float(clap_diffs[ci]),
                    "gunshot_prob": float(gunshot_probs[ci]),
                    "hand_feats": hand[k].tolist(),
                    "clap_sims": [float(x) for x in sims[ci]],
                    "region_tag": str(region_tags[kept_mined_idx[k]]),
                    "mined": True,
                }
            )
        used_fixtures.append(fix)
        log(
            f"  mined {fix}: kept {len(kept_cache_idx)} of {len(indices)} "
            f"(cap {cap} = {cap_ratio:g}x {n_pos} positives)"
        )

    if skipped:
        log(
            "  skipped mined fixtures (missing full cache or no positives): "
            + ", ".join(sorted(skipped))
        )

    provenance = {
        "n_mined_negatives_used": len(rows),
        "mining_source_fixtures": sorted(used_fixtures),
        "mining_cap_ratio": cap_ratio,
    }
    return rows, provenance


def _threshold_for_recall(probs: np.ndarray, labels: np.ndarray, target_recall: float) -> float:
    """Pick the largest probability threshold that hits ``target_recall`` on this subset.

    Walks the (prob, label) pairs in descending probability and stops at
    the first cut that captures ``ceil(n_pos * target_recall)`` positives.
    Returns ``0.0`` when there are no positives -- the caller should
    treat this as "no class-specific calibration possible, use default".
    """
    n_pos = int(labels.sum())
    if n_pos == 0:
        return 0.0
    pairs = sorted(zip(probs.tolist(), labels.tolist(), strict=True), key=lambda x: -x[0])
    cum = 0
    threshold = 0.0
    for prob, lbl in pairs:
        if lbl == 1:
            cum += 1
        if cum / n_pos >= target_recall:
            threshold = float(prob)
            break
    return threshold


def _train_voter_c(universe: list[dict], target_recall: float, *, phone_upweight: float):
    """Fit GBDT; return ``(model, global_threshold, cv_probs)``.

    ``cv_probs`` is the held-out probability for each row from 5-fold
    CV -- callers use it to pick per-camera-class thresholds without
    refitting. ``global_threshold`` is the single-class fallback used
    when an artifact has no per-class calibration on file.

    ``phone_upweight`` (issue #139) multiplies the sample weight on
    handheld rows to counterbalance the headcam-dominated corpus.
    1.0 disables weighting (legacy behaviour); 3-5 is reasonable when
    the phone class is roughly 1/4 the size of headcam.
    """
    X = _x_from(universe)
    y = np.array([c["label"] for c in universe], dtype=np.int64)
    sample_weight = _sample_weights(universe, phone_upweight)
    if y.sum() < 5:
        raise SystemExit(
            f"need at least 5 positives for 5-fold CV; got {int(y.sum())}. "
            "Add more audited fixtures."
        )

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_probs = np.zeros_like(y, dtype=np.float64)
    for tr, te in skf.split(X, y):
        f = GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
        )
        f.fit(X[tr], y[tr], sample_weight=sample_weight[tr])
        cv_probs[te] = f.predict_proba(X[te])[:, 1]

    threshold = _threshold_for_recall(cv_probs, y, target_recall)

    clf = GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
    )
    clf.fit(X, y, sample_weight=sample_weight)
    return clf, threshold, cv_probs


DEFAULT_PHONE_UPWEIGHT: float = 4.0
DEFAULT_VOTER_E_TARGET_RECALL: float = 0.95
VISUAL_CACHE_SUFFIX = "_visual.npz"


def _resolve_subclass(audit_json: dict, time_s: float, tol_ms: float = 25.0) -> str | None:
    """Map a candidate's time to its audit-JSON subclass label, if any.

    ``labels_by_time`` keys are stringified times of the original audit
    candidates; we round to milliseconds and pick the closest within
    ``tol_ms``. ``None`` for unlabeled candidates (typical for the
    positive shots and any negatives the user did not classify).
    """
    block = (audit_json.get("_candidates_pending_audit") or {}).get("labels_by_time") or {}
    if not block:
        return None
    items = []
    for k, v in block.items():
        try:
            items.append((float(k), str(v)))
        except (TypeError, ValueError):
            continue
    if not items:
        return None
    diffs = [(abs(t - time_s) * 1000.0, lbl) for t, lbl in items]
    diffs.sort(key=lambda x: x[0])
    return diffs[0][1] if diffs[0][0] <= tol_ms else None


def _visual_cache_path(fixture: str) -> Path:
    return CACHE_DIR / f"{fixture}{VISUAL_CACHE_SUFFIX}"


def _build_visual_universe(
    fixtures: list[str],
    tolerance_ms: float,
    *,
    rebuild: bool = False,
    log: Callable[[str], None] = print,
) -> tuple[list[dict], list[str]]:
    """Compute CLIP image embeddings + labels for Voter E calibration.

    Each row mirrors the structure of ``_build_universe`` rows but adds
    ``embedding`` (the CLIP image feature, possibly multi-frame
    concatenated) and ``subclass`` (from the audit JSON's
    ``labels_by_time``). Returns ``(rows, missing_video_fixtures)`` --
    the second value lists fixtures that were skipped because their
    ``source_video`` could not be resolved (e.g. USB unmounted), so the
    caller can warn without aborting.

    Heavy work (frame extraction + CLIP forward pass) is cached per
    fixture under ``tests/fixtures/.cache/{fix}_visual.npz``.
    """
    visual_runtime: vis.VisualRuntime | None = None

    def _ensure_runtime() -> vis.VisualRuntime:
        nonlocal visual_runtime
        if visual_runtime is None:
            log("  loading CLIP backbone for Voter E embeddings...")
            visual_runtime = vis.load_visual_runtime(probe=None)
        return visual_runtime

    rows: list[dict] = []
    skipped_no_video: list[str] = []
    for fix in fixtures:
        if fix in WRONG_CLIP_FIXTURES:
            continue
        truth_path = FIXTURES_DIR / f"{fix}.json"
        wav_path = FIXTURES_DIR / f"{fix}.wav"
        if not truth_path.exists() or not wav_path.exists():
            continue
        truth = json.loads(truth_path.read_text())
        camera_block = truth.get("camera") or {}
        cam_class = camera_class_from_mount(camera_block.get("mount"))
        # v0 limit: only build Voter E features for the default class
        # (head-mounted Go 3S). Multi-mount support is deferred to #186.
        if cam_class != DEFAULT_CAMERA_CLASS:
            continue

        source_video_str = truth.get("source_video") or ""
        window = truth.get("fixture_window_in_source") or [0.0, 0.0]
        if not source_video_str:
            skipped_no_video.append(fix)
            continue
        source_video = Path(source_video_str)
        if not source_video.exists():
            skipped_no_video.append(fix)
            continue

        cache_path = _visual_cache_path(fix)
        embeddings: np.ndarray | None = None
        cand_times: np.ndarray | None = None
        labels: np.ndarray | None = None
        subclasses: list[str | None] | None = None
        if not rebuild and cache_path.exists():
            try:
                cached = np.load(cache_path, allow_pickle=True)
                embeddings = cached["embeddings"]
                cand_times = cached["candidate_times"]
                labels = cached["labels"]
                sub_arr = cached["subclasses"]
                subclasses = [None if s == "" else str(s) for s in sub_arr.tolist()]
            except (KeyError, ValueError, EOFError):
                embeddings = None

        if embeddings is None:
            audio, sr = load_audio(wav_path)
            cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
            shots = detect_shots(
                audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg
            )
            if not shots:
                continue
            cand_t = np.array([s.time_absolute for s in shots], dtype=np.float64)
            label_list = _label(cand_t.tolist(), truth.get("shots", []), tolerance_ms)
            sub_list = [_resolve_subclass(truth, float(t)) for t in cand_t]
            source_times = vis.candidate_times_in_source(
                cand_t,
                audit_beep_in_clip=float(truth["beep_time"]),
                source_beep_time=float(window[0]) + float(truth["beep_time"]),
            )
            try:
                embeds = vis.compute_visual_features(
                    source_video, source_times, _ensure_runtime()
                )
            except Exception as exc:
                log(f"  skip {fix}: visual feature extraction failed -- {exc}")
                continue
            embeddings = embeds.astype(np.float32)
            cand_times = cand_t
            labels = np.array(label_list, dtype=np.int64)
            subclasses = sub_list
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(
                cache_path,
                embeddings=embeddings,
                candidate_times=cand_times,
                labels=labels,
                subclasses=np.array(["" if s is None else s for s in subclasses]),
            )
            log(f"  cached {fix}: {embeddings.shape[0]} candidates, dim={embeddings.shape[1]}")
        else:
            log(f"  reused cache {fix}: {embeddings.shape[0]} candidates")

        for i in range(embeddings.shape[0]):
            rows.append(
                {
                    "fixture": fix,
                    "camera_class": cam_class,
                    "time": float(cand_times[i]),
                    "label": int(labels[i]),
                    "subclass": subclasses[i] if subclasses else None,
                    "embedding": embeddings[i],
                }
            )

    return rows, skipped_no_video


def _train_voter_e(
    visual_universe: list[dict],
    target_recall: float,
    *,
    log: Callable[[str], None] = print,
) -> tuple[Any | None, float | None]:
    """Train logistic regression on shots vs cross_bay; pick CV threshold.

    Returns ``(probe, threshold)`` or ``(None, None)`` when the corpus is
    too sparse to train a useful head. Threshold is picked from leave-
    one-fixture-out CV scores on the binary subset, then the final probe
    is fit on the full binary subset.
    """
    if not visual_universe:
        log("  Voter E: empty visual universe, skipping")
        return None, None

    binary = [
        row
        for row in visual_universe
        if row["label"] == 1 or row.get("subclass") == "cross_bay"
    ]
    if len(binary) < 20 or sum(1 for r in binary if r["label"] == 1) < 5:
        log(
            f"  Voter E: insufficient binary corpus "
            f"(n={len(binary)}, pos={sum(1 for r in binary if r['label'] == 1)}); "
            "skipping"
        )
        return None, None

    fixtures = sorted({row["fixture"] for row in binary})
    fixture_idx = {f: i for i, f in enumerate(fixtures)}
    X = np.stack([row["embedding"] for row in binary], axis=0).astype(np.float32)
    y = np.array([row["label"] for row in binary], dtype=np.int64)
    groups = np.array([fixture_idx[row["fixture"]] for row in binary], dtype=np.int64)

    cv_probs = np.full(len(binary), np.nan, dtype=np.float64)
    for held in range(len(fixtures)):
        train_mask = groups != held
        test_mask = groups == held
        if train_mask.sum() < 10 or y[train_mask].sum() == 0 or (
            y[train_mask] == 0
        ).sum() == 0:
            continue
        clf = LogisticRegression(
            C=1.0, max_iter=1000, class_weight="balanced", solver="lbfgs"
        )
        clf.fit(X[train_mask], y[train_mask])
        cv_probs[test_mask] = clf.predict_proba(X[test_mask])[:, 1]
    if np.isnan(cv_probs).any():
        # Drop folds that didn't produce held-out scores (degenerate train splits).
        valid = ~np.isnan(cv_probs)
        cv_probs = cv_probs[valid]
        y = y[valid]
    threshold = _threshold_for_recall(cv_probs.astype(np.float64), y, target_recall)

    final = LogisticRegression(
        C=1.0, max_iter=1000, class_weight="balanced", solver="lbfgs"
    )
    final.fit(X, y)
    log(
        f"  Voter E: trained on {len(binary)} samples "
        f"({int(y.sum())} shots / {len(binary) - int(y.sum())} cross_bay), "
        f"threshold={threshold:.4f} at target_recall={target_recall:.2f}"
    )
    return final, float(threshold)


def build_artifacts(
    fixtures: list[str] | None = None,
    *,
    target_recall: float = 0.95,
    tolerance_ms: float = 75.0,
    mining_cap_ratio: float = DEFAULT_NEG_CAP_RATIO,
    use_mined_negatives: bool = False,
    phone_upweight: float = DEFAULT_PHONE_UPWEIGHT,
    voter_e: bool = True,
    voter_e_target_recall: float = DEFAULT_VOTER_E_TARGET_RECALL,
    rebuild_visual: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    """Run the calibration build and write artifacts under ``DATA_DIR``.

    Importable so the production UI's "Rebuild calibration" button can
    drive the same code path as the CLI. Logs progress through ``log``;
    returns the calibration dict that was written.
    """
    fixtures = list(fixtures) if fixtures else list(DEFAULT_FIXTURES)
    log(f"Calibrating ensemble over {len(fixtures)} fixture(s)...")
    universe = _build_universe(fixtures, tolerance_ms, log=log)
    n_total = len(universe)
    n_pos = sum(c["label"] for c in universe)
    log(
        f"Universe: {n_total} candidates, {n_pos} positives "
        f"(across {len({c['fixture'] for c in universe})} fixtures)"
    )
    by_class = _split_by_camera_class(universe)
    log(
        "By camera class: "
        + ", ".join(
            f"{cls}={len(rows)} cands / {sum(r['label'] for r in rows)} pos"
            for cls, rows in sorted(by_class.items())
        )
    )

    n_pos_by_fixture = Counter(c["fixture"] for c in universe if c["label"] == 1)
    if use_mined_negatives:
        mined_rows, mining_provenance = _load_mined_negatives(
            n_pos_by_fixture, cap_ratio=mining_cap_ratio, log=log
        )
    else:
        mined_rows, mining_provenance = [], {
            "n_mined_negatives_used": 0,
            "mining_source_fixtures": [],
        }
    voter_c_universe = universe + mined_rows
    if mined_rows:
        log(
            f"Voter C training set: {len(universe)} stage-window + "
            f"{len(mined_rows)} mined negatives = {len(voter_c_universe)} rows."
        )

    clf, voter_c_global, cv_probs = _train_voter_c(
        voter_c_universe, target_recall=target_recall, phone_upweight=phone_upweight
    )
    if phone_upweight != 1.0 and any(
        row.get("camera_class", DEFAULT_CAMERA_CLASS) != DEFAULT_CAMERA_CLASS
        for row in voter_c_universe
    ):
        log(f"Voter C trained with phone-class sample weight x{phone_upweight:g}")

    # Per-class thresholds. The shared GBDT's CV predictions are sliced
    # by class so the operating point hits target_recall *on that class*
    # rather than on the dominant one. Voter A/B/D still use the lowest-
    # positive rule, computed per class for the same reason.
    cv_universe_classes = [
        row.get("camera_class", DEFAULT_CAMERA_CLASS) for row in voter_c_universe
    ]
    cv_universe_labels = np.array([row["label"] for row in voter_c_universe], dtype=np.int64)

    thresholds_by_class: dict[str, dict] = {}
    metrics_by_class: dict[str, dict] = {}
    for cls in sorted(by_class):
        rows = by_class[cls]
        rows_pos = [r for r in rows if r["label"] == 1]
        if not rows_pos:
            log(f"  {cls}: 0 positives -- skipping (no per-class thresholds emitted)")
            continue

        cls_a = _voter_a_floor(rows)
        cls_b = _voter_b_threshold(rows)
        cls_d = _voter_d_threshold(rows)

        # Voter C: pick the threshold from the CV slice that belongs to
        # this class (positives + the negatives that came from the same
        # camera class). Mined negatives without an explicit class fall
        # back to DEFAULT_CAMERA_CLASS, matching how detection-time
        # callers will look them up.
        cls_mask = np.array([c == cls for c in cv_universe_classes], dtype=bool)
        if cls_mask.sum() == 0 or cv_universe_labels[cls_mask].sum() == 0:
            cls_c = voter_c_global
        else:
            cls_c = _threshold_for_recall(
                cv_probs[cls_mask], cv_universe_labels[cls_mask], target_recall
            )

        thresholds_by_class[cls] = {
            "voter_a_floor": cls_a,
            "voter_b_threshold": cls_b,
            "voter_c_threshold": cls_c,
            "voter_d_threshold": cls_d,
            "n_calibration_candidates": len(rows),
            "n_calibration_positives": len(rows_pos),
            "calibration_fixtures": sorted({r["fixture"] for r in rows}),
        }

        # Per-class precision/recall at the picked C threshold, using CV
        # predictions so it's a held-out metric. Helps spot regressions
        # in the dominant class as new classes get added.
        cls_cv_probs = cv_probs[cls_mask]
        cls_cv_labels = cv_universe_labels[cls_mask]
        kept = cls_cv_probs >= cls_c
        tp = int(((kept) & (cls_cv_labels == 1)).sum())
        fp = int(((kept) & (cls_cv_labels == 0)).sum())
        fn = int(((~kept) & (cls_cv_labels == 1)).sum())
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        metrics_by_class[cls] = {
            "voter_c_precision_cv": round(precision, 4),
            "voter_c_recall_cv": round(recall, 4),
            "voter_c_f1_cv": round(f1, 4),
            "voter_c_tp": tp,
            "voter_c_fp": fp,
            "voter_c_fn": fn,
        }

        log(
            f"  {cls}: A={cls_a:.4f}  B={cls_b:.4f}  C={cls_c:.4f}  D={cls_d:.4f}  "
            f"(n={len(rows)} cands / {len(rows_pos)} pos)  "
            f"voter-C CV P/R/F1={precision:.3f}/{recall:.3f}/{f1:.3f}"
        )

    if not thresholds_by_class:
        raise SystemExit("no camera class produced calibrated thresholds; need >= 1 positive")

    # Voter E (issue #183): train CLIP visual probe head on the head-mounted
    # corpus and merge per-class thresholds back in. Fully optional -- if
    # source videos can't be reached or training is disabled, the build
    # succeeds without Voter E and the runtime falls back to 4 voters.
    voter_e_probe = None
    voter_e_threshold: float | None = None
    voter_e_provenance: dict[str, Any] = {}
    if voter_e:
        log("Voter E: building visual universe...")
        visual_universe, missing_video = _build_visual_universe(
            fixtures, tolerance_ms, rebuild=rebuild_visual, log=log
        )
        if missing_video:
            log(
                "  skipped (source_video unreachable): "
                + ", ".join(sorted(missing_video))
            )
        voter_e_probe, voter_e_threshold = _train_voter_e(
            visual_universe, target_recall=voter_e_target_recall, log=log
        )
        voter_e_provenance = {
            "n_visual_candidates": len(visual_universe),
            "n_visual_skipped_missing_video": len(missing_video),
            "missing_video_fixtures": sorted(missing_video),
        }
        if voter_e_probe is not None and DEFAULT_CAMERA_CLASS in thresholds_by_class:
            thresholds_by_class[DEFAULT_CAMERA_CLASS]["voter_e_threshold"] = voter_e_threshold

    # Default-class top-level fields. Existing code paths that haven't
    # migrated to ``thresholds_for(camera_class)`` keep reading the
    # default class -- byte-identical to today for headcam projects.
    default_cls = (
        DEFAULT_CAMERA_CLASS
        if DEFAULT_CAMERA_CLASS in thresholds_by_class
        else next(iter(sorted(thresholds_by_class)))
    )
    default_thresholds = thresholds_by_class[default_cls]
    log(
        f"Default class for legacy callers: {default_cls!r} "
        f"(A={default_thresholds['voter_a_floor']:.4f} "
        f"B={default_thresholds['voter_b_threshold']:.4f} "
        f"C={default_thresholds['voter_c_threshold']:.4f} "
        f"D={default_thresholds['voter_d_threshold']:.4f})"
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cal_path = DATA_DIR / "ensemble_calibration.json"
    model_path = DATA_DIR / "voter_c_gbdt.joblib"
    voter_e_path = DATA_DIR / DEFAULT_VOTER_E_PROBE_FILENAME

    cal = {
        "voter_a_floor": default_thresholds["voter_a_floor"],
        "voter_b_threshold": default_thresholds["voter_b_threshold"],
        "voter_c_threshold": default_thresholds["voter_c_threshold"],
        "voter_d_threshold": default_thresholds["voter_d_threshold"],
        "voter_e_threshold": default_thresholds.get("voter_e_threshold"),
        "voter_c_target_recall": target_recall,
        "voter_e_target_recall": voter_e_target_recall if voter_e_probe is not None else None,
        "tolerance_ms": tolerance_ms,
        "clap_prompts_shot": list(feat.CLAP_PROMPTS_SHOT),
        "clap_prompts": list(feat.CLAP_PROMPTS),
        "calibration_fixtures": [f for f in fixtures if any(c["fixture"] == f for c in universe)],
        "n_calibration_candidates": n_total,
        "n_calibration_positives": int(n_pos),
        "voter_c_feature_dim": feat.VOTER_C_FEATURE_DIM,
        "voter_e_clip_model_id": vis.CLIP_VISUAL_MODEL_ID if voter_e_probe is not None else None,
        "voter_e_frame_offsets": list(vis.DEFAULT_FRAME_OFFSETS) if voter_e_probe is not None else None,
        "voter_e_probe_artifact": DEFAULT_VOTER_E_PROBE_FILENAME if voter_e_probe is not None else None,
        "built_at": dt.datetime.now(dt.UTC).isoformat(),
        "default_camera_class": default_cls,
        "thresholds_by_camera_class": thresholds_by_class,
        "voter_c_metrics_by_camera_class": metrics_by_class,
        "voter_e_provenance": voter_e_provenance,
        **mining_provenance,
    }
    cal_path.write_text(json.dumps(cal, indent=2) + "\n")
    joblib.dump(clf, model_path)
    if voter_e_probe is not None:
        joblib.dump(voter_e_probe, voter_e_path)
        log(f"Wrote {voter_e_path}")
    log(f"Wrote {cal_path}")
    log(f"Wrote {model_path}")
    return cal


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append", help="Calibration fixture stem (repeatable).")
    p.add_argument("--target-recall", type=float, default=0.95)
    p.add_argument("--tolerance-ms", type=float, default=75.0)
    p.add_argument(
        "--mining-cap-ratio",
        type=float,
        default=DEFAULT_NEG_CAP_RATIO,
        help="Per-fixture cap on mined negatives, as a multiple of that fixture's "
        "positive count. Hardest survivors (highest Voter A confidence) win.",
    )
    p.add_argument(
        "--phone-upweight",
        type=float,
        default=DEFAULT_PHONE_UPWEIGHT,
        help=(
            "Sample-weight multiplier on handheld-class rows when fitting "
            "voter C. Counterbalances the headcam-dominated corpus. 1.0 "
            "disables weighting; 3-5 is reasonable when the phone class is "
            "roughly 1/4 the size of headcam."
        ),
    )
    p.add_argument(
        "--with-mining",
        action="store_true",
        help=(
            "Append tests/fixtures/.cache/_mined_negatives.npz rows to voter "
            "C's training set. OFF by default since the spectral cross-bay "
            "features (#108) made mining a regression: mined region "
            "negatives are easier to separate than the in-stage "
            "cross_bay/echo FPs we actually care about, so the calibrated "
            "threshold drops and FP count rises in threshold-only eval."
        ),
    )
    p.add_argument(
        "--no-voter-e",
        dest="voter_e",
        action="store_false",
        default=True,
        help=(
            "Skip Voter E (CLIP visual probe) calibration. Useful when the "
            "source videos are not mounted or when iterating on the audio "
            "voters only."
        ),
    )
    p.add_argument(
        "--voter-e-target-recall",
        type=float,
        default=DEFAULT_VOTER_E_TARGET_RECALL,
        help=(
            "Target recall for Voter E threshold selection (default 0.95). "
            "Lower values produce a stricter precision veto."
        ),
    )
    p.add_argument(
        "--rebuild-visual",
        action="store_true",
        help=(
            "Force re-extraction of CLIP image embeddings for Voter E, "
            "ignoring tests/fixtures/.cache/{fix}_visual.npz."
        ),
    )
    args = p.parse_args()
    build_artifacts(
        fixtures=args.fixture or None,
        target_recall=args.target_recall,
        tolerance_ms=args.tolerance_ms,
        mining_cap_ratio=args.mining_cap_ratio,
        use_mined_negatives=args.with_mining,
        phone_upweight=args.phone_upweight,
        voter_e=args.voter_e,
        voter_e_target_recall=args.voter_e_target_recall,
        rebuild_visual=args.rebuild_visual,
    )


if __name__ == "__main__":
    main()
