"""Compare PANN vs BEATs as Voter D backbones (issue #179).

Reads the per-fixture caches both extractors produce
(``tests/fixtures/.cache/{stem}_pann.npz``, ``{stem}_beats.npz``) and
both pre-built calibration JSONs (``ensemble_calibration_pann_baseline.json``
and the live ``ensemble_calibration.json``, which is whichever backend
the most recent ``build_ensemble_artifacts.py`` run produced -- the
script runs after a BEATs build so the live one is BEATs).

Outputs:
* Per-voter D separation: ROC-AUC and distribution stats over true-shot
  vs non-shot candidates.
* Threshold-only Voter D F1 at the calibrated lowest-positive threshold.
* B/D correlation (Pearson) -- a high number means BEATs is duplicative
  with CLAP and adds little ensemble value.
* Per-camera-class breakdown matching the calibration's stratification.

Reuses ``build_ensemble_artifacts._build_universe`` so the labeling
greedy-match logic is identical to calibration. Doesn't run the full
4-voter ensemble: voter A/B/C scoring is the same on both calibrations
by construction (their thresholds are independent of D's probability),
so the meaningful delta lives in Voter D itself.

Run after extract_beats_embeddings.py + build_ensemble_artifacts.py:
    uv run python scripts/compare_voter_d_backends.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.ensemble import features as feat
from splitsmith.ensemble.calibration import camera_class_from_mount
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
    "stage-shots-tallmilan-2026-stage2-apple-iphone17pro",
    "stage-shots-tallmilan-2026-stage5-apple-iphone17pro",
    "stage-shots-tallmilan-2026-stage6-apple-iphone17pro",
    "stage-shots-tallmilan-2026-stage7-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage1-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage2-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage3-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage5-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage7-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage8-apple-iphone17pro",
]

FIXTURES_DIR = Path("tests/fixtures")
CACHE_DIR = FIXTURES_DIR / ".cache"
DATA_DIR = Path("src/splitsmith/data")

TOLERANCE_MS = 75.0


def _label(cand_t: list[float], truth_shots: list[dict], tol_ms: float) -> list[int]:
    """Greedy nearest-time match (mirrors build_ensemble_artifacts._label)."""
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


def _roc_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """ROC-AUC for binary labels via the rank-sum formulation."""
    n_pos = int(labels.sum())
    n_neg = labels.size - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(scores, kind="stable")
    ranks = np.empty_like(order, dtype=np.float64)
    ranks[order] = np.arange(1, scores.size + 1)
    # Average ranks across ties -- rare in float32 but cheap to handle.
    _, inverse, counts = np.unique(scores, return_inverse=True, return_counts=True)
    rank_sum = np.zeros(counts.size, dtype=np.float64)
    np.add.at(rank_sum, inverse, ranks)
    avg_rank_per_unique = rank_sum / counts
    ranks = avg_rank_per_unique[inverse]
    rank_sum_pos = ranks[labels == 1].sum()
    return float((rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def _f1_at_threshold(probs: np.ndarray, labels: np.ndarray, threshold: float) -> dict:
    keep = probs >= threshold
    tp = int(((keep) & (labels == 1)).sum())
    fp = int(((keep) & (labels == 0)).sum())
    fn = int(((~keep) & (labels == 1)).sum())
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1, "threshold": threshold}


def _threshold_at_recall(probs: np.ndarray, labels: np.ndarray, target_recall: float) -> float:
    """Largest threshold whose recall is >= ``target_recall`` (matches calibration logic)."""
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


def _best_f1(probs: np.ndarray, labels: np.ndarray) -> dict:
    """Sweep thresholds at every positive prob, pick the one with the highest F1."""
    if labels.sum() == 0:
        return {"f1": 0.0, "threshold": 0.0, "tp": 0, "fp": 0, "fn": 0, "precision": 0.0, "recall": 0.0}
    best = _f1_at_threshold(probs, labels, 0.0)
    for thr in sorted({float(p) for p, l in zip(probs.tolist(), labels.tolist()) if l == 1}):
        m = _f1_at_threshold(probs, labels, thr)
        if m["f1"] > best["f1"]:
            best = m
    return best


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 2:
        return float("nan")
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denom) if denom > 0 else float("nan")


def _build_paired_universe() -> list[dict]:
    """Per-fixture: detect at max recall, label, attach PANN + BEATs probs and CLAP diff."""
    rows: list[dict] = []
    for fix in DEFAULT_FIXTURES:
        truth_path = FIXTURES_DIR / f"{fix}.json"
        wav_path = FIXTURES_DIR / f"{fix}.wav"
        clap_path = CACHE_DIR / f"{fix}_clap.npz"
        pann_path = CACHE_DIR / f"{fix}_pann.npz"
        beats_path = CACHE_DIR / f"{fix}_beats.npz"
        if not all(p.exists() for p in (truth_path, wav_path, clap_path, pann_path, beats_path)):
            print(f"  skip {fix}: missing cache(s)")
            continue
        truth = json.loads(truth_path.read_text())
        cam_class = camera_class_from_mount((truth.get("camera") or {}).get("mount"))
        audio, sr = load_audio(wav_path)
        cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
        shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg)
        if not shots:
            continue
        cand_t = [s.time_absolute for s in shots]
        labels = _label(cand_t, truth.get("shots", []), TOLERANCE_MS)
        clap = np.load(clap_path, allow_pickle=True)
        pann = np.load(pann_path)
        beats = np.load(beats_path)
        if not (
            clap["audio_emb"].shape[0]
            == pann["gunshot_prob"].shape[0]
            == beats["gunshot_prob"].shape[0]
            == len(shots)
        ):
            print(f"  skip {fix}: cache row counts mismatch")
            continue
        clap_diff = feat.clap_diff_from_similarities(clap["text_sims"])
        for i in range(len(shots)):
            rows.append(
                {
                    "fixture": fix,
                    "camera_class": cam_class,
                    "label": labels[i],
                    "pann": float(pann["gunshot_prob"][i]),
                    "beats": float(beats["gunshot_prob"][i]),
                    "clap_diff": float(clap_diff[i]),
                }
            )
    return rows


def _print_voter_d_summary(rows: list[dict], pann_thresh: float, beats_thresh: float, label: str) -> None:
    pann = np.array([r["pann"] for r in rows])
    beats = np.array([r["beats"] for r in rows])
    clap_diff = np.array([r["clap_diff"] for r in rows])
    labels = np.array([r["label"] for r in rows], dtype=np.int64)

    print(f"\n=== {label} (n={len(rows)}, pos={int(labels.sum())}, neg={int((labels == 0).sum())}) ===")
    for name, scores, thresh in [("PANN", pann, pann_thresh), ("BEATs", beats, beats_thresh)]:
        pos = scores[labels == 1]
        neg = scores[labels == 0]
        auc = _roc_auc(scores, labels)
        m_calib = _f1_at_threshold(scores, labels, thresh)
        m_r95 = _f1_at_threshold(scores, labels, _threshold_at_recall(scores, labels, 0.95))
        m_best = _best_f1(scores, labels)
        gap_med = float(np.median(pos)) - float(np.median(neg))
        print(
            f"  {name:5s}  AUC={auc:.4f}  pos_med={np.median(pos):.4f} neg_med={np.median(neg):.4f}  "
            f"med_gap={gap_med:+.4f}"
        )
        print(
            f"           calibrated thr={thresh:.4f}  P={m_calib['precision']:.3f} R={m_calib['recall']:.3f} F1={m_calib['f1']:.3f}"
        )
        print(
            f"           R>=0.95   thr={m_r95['threshold']:.4f}  P={m_r95['precision']:.3f} R={m_r95['recall']:.3f} F1={m_r95['f1']:.3f}"
        )
        print(
            f"           best F1   thr={m_best['threshold']:.4f}  P={m_best['precision']:.3f} R={m_best['recall']:.3f} F1={m_best['f1']:.3f}"
        )

    # B vs D correlation (Pearson on the score vectors).
    rho_pann = _pearson(clap_diff, pann)
    rho_beats = _pearson(clap_diff, beats)
    print(f"  CLAP-diff <-> Voter D Pearson r:  PANN={rho_pann:+.3f}   BEATs={rho_beats:+.3f}")


def main() -> None:
    print("Building paired universe (PANN + BEATs caches)...")
    rows = _build_paired_universe()
    print(f"  total: {len(rows)} candidates across {len({r['fixture'] for r in rows})} fixtures")

    pann_cal = json.loads((DATA_DIR / "ensemble_calibration_pann_baseline.json").read_text())
    beats_cal = json.loads((DATA_DIR / "ensemble_calibration.json").read_text())
    if beats_cal.get("voter_d_backend") != "beats":
        raise SystemExit(
            "Live ensemble_calibration.json is not the BEATs build; rebuild with "
            "scripts/build_ensemble_artifacts.py --voter-d-backend beats first."
        )

    # Headcam (default class) thresholds.
    pann_th_head = pann_cal["thresholds_by_camera_class"]["headcam"]["voter_d_threshold"]
    beats_th_head = beats_cal["thresholds_by_camera_class"]["headcam"]["voter_d_threshold"]
    pann_th_hand = pann_cal["thresholds_by_camera_class"]["handheld"]["voter_d_threshold"]
    beats_th_hand = beats_cal["thresholds_by_camera_class"]["handheld"]["voter_d_threshold"]

    _print_voter_d_summary(rows, pann_th_head, beats_th_head, label="ALL CANDIDATES")
    _print_voter_d_summary(
        [r for r in rows if r["camera_class"] == "headcam"],
        pann_th_head,
        beats_th_head,
        label="HEADCAM",
    )
    _print_voter_d_summary(
        [r for r in rows if r["camera_class"] == "handheld"],
        pann_th_hand,
        beats_th_hand,
        label="HANDHELD",
    )

    # Per-fixture mini-table for headcam, sorted by F1 delta.
    print("\n=== Per-fixture Voter D F1 (headcam, at calibrated headcam threshold) ===")
    by_fixture = {}
    for r in rows:
        if r["camera_class"] != "headcam":
            continue
        by_fixture.setdefault(r["fixture"], []).append(r)
    summary = []
    for fix, frows in by_fixture.items():
        labels = np.array([r["label"] for r in frows])
        pann = np.array([r["pann"] for r in frows])
        beats = np.array([r["beats"] for r in frows])
        if labels.sum() == 0:
            continue
        m_p = _f1_at_threshold(pann, labels, pann_th_head)
        m_b = _f1_at_threshold(beats, labels, beats_th_head)
        summary.append((fix, m_p, m_b))
    summary.sort(key=lambda x: x[2]["f1"] - x[1]["f1"])
    for fix, m_p, m_b in summary:
        print(
            f"  {fix:55s}  PANN F1={m_p['f1']:.3f}  BEATs F1={m_b['f1']:.3f}  "
            f"delta={m_b['f1'] - m_p['f1']:+.3f}"
        )


if __name__ == "__main__":
    main()
