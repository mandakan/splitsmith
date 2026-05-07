"""Sweep Voter E aggregation over single-frame vs multi-frame offsets (#184).

For each audited head-mounted Go 3S fixture with a reachable source
video, extract CLIP image embeddings at two candidate-frame configs:

* single  -- offsets ``(0.0,)`` (the v0 / #183 baseline)
* multi   -- offsets ``(0.0, 0.030, 0.080)`` (candidate, +30 ms muzzle
  flash, +80 ms recoil rise; concat-aggregated by widening the linear
  probe input)

Train a logistic regression with leave-one-fixture-out on shots vs
cross_bay candidates and report per-fixture + aggregate ROC AUC and
precision-at-recall (1.0 / 0.95). Frame extraction is cached, so a
re-run only pays the cost of new offsets.

Decision (per #184):

* Adopt multi-frame if it lifts mean per-fixture AUC by >= +0.02 with no
  per-fixture regressions vs single-frame.
* Adopt with conditional override if it helps the weak v0 folds without
  regressing the strong ones.
* Otherwise drop and keep single-frame.

Run:
    uv run python scripts/sweep_multiframe_voter_e.py
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.ensemble import visual as vis
from splitsmith.shot_detect import detect_shots

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
CACHE_DIR = FIXTURES_DIR / ".cache"
TOL_MS = 75.0

CONFIGS: list[tuple[str, tuple[float, ...]]] = [
    ("single", (0.0,)),
    ("multi", (0.0, 0.030, 0.080)),
]


def _label_candidates(cand_t: list[float], shots: list[dict], tol_ms: float) -> list[int]:
    labels = [0] * len(cand_t)
    used: set[int] = set()
    for s in sorted(shots, key=lambda x: x["time"]):
        t = float(s["time"])
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


def _resolve_subclass(audit: dict, time_s: float, tol_ms: float = 25.0) -> str | None:
    block = (audit.get("_candidates_pending_audit") or {}).get("labels_by_time") or {}
    if not block:
        return None
    pairs: list[tuple[float, float, str]] = []
    for k, v in block.items():
        try:
            t = float(k)
        except (TypeError, ValueError):
            continue
        pairs.append((abs(t - time_s) * 1000.0, t, str(v)))
    if not pairs:
        return None
    pairs.sort(key=lambda x: x[0])
    return pairs[0][2] if pairs[0][0] <= tol_ms else None


def _cache_path(fixture: str, offsets: tuple[float, ...]) -> Path:
    key = "_".join(f"{int(round(o * 1000)):+d}" for o in offsets)
    return CACHE_DIR / f"{fixture}_visual_offsets_{key}.npz"


def _embed_fixture(
    fixture: str, offsets: tuple[float, ...], runtime: vis.VisualRuntime
) -> tuple[np.ndarray | None, np.ndarray, np.ndarray, list[str | None], float]:
    """Returns (embeddings, labels, candidate_times, subclasses, elapsed_s).

    ``embeddings`` is ``None`` when the fixture's source video is not
    reachable; caller skips such fixtures.
    """
    truth_path = FIXTURES_DIR / f"{fixture}.json"
    wav_path = FIXTURES_DIR / f"{fixture}.wav"
    if not truth_path.exists() or not wav_path.exists():
        return None, np.zeros(0), np.zeros(0), [], 0.0
    truth = json.loads(truth_path.read_text())
    source_video_str = truth.get("source_video") or ""
    if not source_video_str:
        return None, np.zeros(0), np.zeros(0), [], 0.0
    source_video = Path(source_video_str)
    if not source_video.exists():
        return None, np.zeros(0), np.zeros(0), [], 0.0

    cache = _cache_path(fixture, offsets)
    if cache.exists():
        try:
            d = np.load(cache, allow_pickle=True)
            sub_arr = d["subclasses"]
            return (
                d["embeddings"],
                d["labels"],
                d["candidate_times"],
                [None if s == "" else str(s) for s in sub_arr.tolist()],
                0.0,
            )
        except (KeyError, ValueError, EOFError):
            pass

    audio, sr = load_audio(wav_path)
    cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
    shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg)
    if not shots:
        return None, np.zeros(0), np.zeros(0), [], 0.0
    cand_t = np.array([s.time_absolute for s in shots], dtype=np.float64)
    labels = np.array(
        _label_candidates(cand_t.tolist(), truth.get("shots", []) or [], TOL_MS),
        dtype=np.int64,
    )
    subclasses = [_resolve_subclass(truth, float(t)) for t in cand_t]

    window = truth.get("fixture_window_in_source") or [0.0, 0.0]
    source_times = vis.candidate_times_in_source(
        cand_t,
        audit_beep_in_clip=float(truth["beep_time"]),
        source_beep_time=float(window[0]) + float(truth["beep_time"]),
    )
    t0 = time.time()
    embeds = vis.compute_visual_features(source_video, source_times, runtime, frame_offsets=offsets)
    elapsed = time.time() - t0

    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        cache,
        embeddings=embeds.astype(np.float32),
        labels=labels,
        candidate_times=cand_t,
        subclasses=np.array(["" if s is None else s for s in subclasses]),
    )
    return embeds.astype(np.float32), labels, cand_t, subclasses, elapsed


def _train_and_score_lofo(
    embeddings_by_fixture: dict[str, np.ndarray],
    labels_by_fixture: dict[str, np.ndarray],
    subclasses_by_fixture: dict[str, list[str | None]],
) -> dict[str, np.ndarray]:
    """LOFO LR: hold out one fixture, train on the rest's shots vs cross_bay,
    score the held-out fixture's full candidate set."""
    fixtures = sorted(embeddings_by_fixture)
    held_out_scores: dict[str, np.ndarray] = {}
    for held in fixtures:
        train_emb_chunks: list[np.ndarray] = []
        train_y_chunks: list[np.ndarray] = []
        for f in fixtures:
            if f == held:
                continue
            sub = subclasses_by_fixture[f]
            mask = np.array(
                [
                    (labels_by_fixture[f][i] == 1) or (sub[i] == "cross_bay")
                    for i in range(len(sub))
                ],
                dtype=bool,
            )
            train_emb_chunks.append(embeddings_by_fixture[f][mask])
            train_y_chunks.append(labels_by_fixture[f][mask])
        x_train = np.concatenate(train_emb_chunks, axis=0)
        ytr = np.concatenate(train_y_chunks, axis=0)
        if ytr.sum() == 0 or (ytr == 0).sum() == 0 or len(ytr) < 10:
            held_out_scores[held] = np.zeros(embeddings_by_fixture[held].shape[0])
            continue
        clf = LogisticRegression(C=1.0, max_iter=1000, class_weight="balanced", solver="lbfgs")
        clf.fit(x_train, ytr)
        held_out_scores[held] = clf.predict_proba(embeddings_by_fixture[held])[:, 1]
    return held_out_scores


def _precision_at_recall(
    scores: np.ndarray, labels: np.ndarray, target: float
) -> tuple[float, int]:
    """Return ``(precision, k)`` -- smallest top-k that captures the target recall."""
    n_pos = int(labels.sum())
    if n_pos == 0:
        return float("nan"), 0
    order = np.argsort(-scores)
    needed = math.ceil(target * n_pos)
    tp = 0
    for k, idx in enumerate(order, 1):
        if labels[idx] == 1:
            tp += 1
        if tp >= needed:
            return tp / k, k
    return float("nan"), len(scores)


def _summary(scores: np.ndarray, labels: np.ndarray) -> dict[str, float | int]:
    if labels.sum() == 0 or (labels == 0).sum() == 0 or scores.size == 0:
        return {"auc": float("nan"), "p_at_r_1.0": float("nan"), "p_at_r_0.95": float("nan")}
    auc = float(roc_auc_score(labels, scores))
    p10, _ = _precision_at_recall(scores, labels, 1.0)
    p95, _ = _precision_at_recall(scores, labels, 0.95)
    return {"auc": auc, "p_at_r_1.0": p10, "p_at_r_0.95": p95}


def main() -> int:
    print("Loading CLIP backbone for embedding extraction...")
    runtime = vis.load_visual_runtime(probe=None)

    audited_fixtures = sorted(
        p.stem
        for p in FIXTURES_DIR.glob("stage-shots-*.json")
        if not any(skip in p.name for skip in ("peaks", "promotion-report", "iphone"))
    )

    per_config: dict[str, dict] = {}

    for cfg_name, offsets in CONFIGS:
        print(f"\n=== config '{cfg_name}' offsets={offsets} ===")
        embeddings_by_fixture: dict[str, np.ndarray] = {}
        labels_by_fixture: dict[str, np.ndarray] = {}
        subclasses_by_fixture: dict[str, list[str | None]] = {}
        cand_times_by_fixture: dict[str, np.ndarray] = {}
        elapsed_total = 0.0

        for fixture in audited_fixtures:
            truth_path = FIXTURES_DIR / f"{fixture}.json"
            try:
                truth = json.loads(truth_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            cam = truth.get("camera") or {}
            if cam.get("id") != "go3s" or cam.get("mount") != "head":
                continue

            embeds, labels, cand_t, subclasses, elapsed = _embed_fixture(fixture, offsets, runtime)
            if embeds is None:
                continue
            embeddings_by_fixture[fixture] = embeds
            labels_by_fixture[fixture] = labels
            subclasses_by_fixture[fixture] = subclasses
            cand_times_by_fixture[fixture] = cand_t
            elapsed_total += elapsed
            print(
                f"  {fixture:48s}  n={embeds.shape[0]:4d}  dim={embeds.shape[1]:5d}  "
                f"elapsed={elapsed:5.1f}s  positives={int(labels.sum()):3d}  "
                f"cross_bay={sum(1 for s in subclasses if s == 'cross_bay'):3d}"
            )

        held_out_scores = _train_and_score_lofo(
            embeddings_by_fixture, labels_by_fixture, subclasses_by_fixture
        )

        per_fixture: dict[str, dict] = {}
        for fixture, scores in held_out_scores.items():
            per_fixture[fixture] = _summary(scores, labels_by_fixture[fixture])
        all_scores = np.concatenate(list(held_out_scores.values()))
        all_labels = np.concatenate([labels_by_fixture[f] for f in held_out_scores])
        aggregate = _summary(all_scores, all_labels)

        per_config[cfg_name] = {
            "offsets": offsets,
            "fixtures": list(held_out_scores.keys()),
            "per_fixture": per_fixture,
            "aggregate": aggregate,
            "elapsed_extract_s": elapsed_total,
        }

    print("\n=== aggregate (held-out across all fixtures pooled) ===")
    print(f"{'config':10s}  {'AUC':>6s}  {'P@R=1.0':>8s}  {'P@R=0.95':>8s}  extract(s)")
    for cfg_name in (c[0] for c in CONFIGS):
        a = per_config[cfg_name]["aggregate"]
        e = per_config[cfg_name]["elapsed_extract_s"]
        print(
            f"{cfg_name:10s}  {a['auc']:6.3f}  {a['p_at_r_1.0']:8.3f}  "
            f"{a['p_at_r_0.95']:8.3f}  {e:6.1f}"
        )

    print("\n=== per-fixture AUC (single -> multi) ===")
    fixtures = sorted(
        set(per_config["single"]["per_fixture"]) & set(per_config["multi"]["per_fixture"])
    )
    deltas: list[float] = []
    print(f"{'fixture':48s}  {'single':>8s}  {'multi':>8s}  {'delta':>8s}")
    for f in fixtures:
        s = per_config["single"]["per_fixture"][f]["auc"]
        m = per_config["multi"]["per_fixture"][f]["auc"]
        d = m - s
        deltas.append(d)
        print(f"{f:48s}  {s:8.3f}  {m:8.3f}  {d:+8.3f}")

    if deltas:
        mean_delta = sum(deltas) / len(deltas)
        regressions = sum(1 for d in deltas if d < 0)
        print(f"\nmean delta AUC: {mean_delta:+.3f}   " f"regressions: {regressions}/{len(deltas)}")
        if mean_delta >= 0.02 and regressions == 0:
            print("DECISION: adopt multi-frame (#184 strict criterion met).")
        elif mean_delta > 0 and regressions == 0:
            print("DECISION: adopt multi-frame conservatively (no regression, modest lift).")
        elif regressions == 0:
            print("DECISION: parity. Keep single-frame (no measurable lift).")
        else:
            print(f"DECISION: keep single-frame ({regressions} per-fixture AUC regressions).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
