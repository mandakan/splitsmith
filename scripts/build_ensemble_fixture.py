"""Build review-ready fixture variants for visual comparison in the UI.

For each input fixture, produces TWO sibling fixtures under
``build/ensemble-review/``:

* ``{stem}-baseline.json``  -- shots[] = baseline (cwt + min_confidence=0.03)
* ``{stem}-ensemble.json``  -- shots[] = N-of-3 ensemble consensus

Both share the same ``_candidates_pending_audit`` (the full max-recall
candidate set), so opening either in the review SPA shows every detector
candidate and highlights only the ones that the chosen filter "kept".

The WAV file required by the review SPA is symlinked alongside each JSON,
so opening either fixture in:

    uv run splitsmith review --fixture <path>

Just Works -- no copy of the audio, no modification of the originals.

Run:
    uv run python scripts/build_ensemble_fixture.py
    uv run python scripts/build_ensemble_fixture.py --consensus 2
    uv run python scripts/build_ensemble_fixture.py --fixture stage-shots-tallmilan-stage7
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.shot_detect import detect_shots

DEFAULT_FIXTURES = [
    "stage-shots",
    "stage-shots-blacksmith-h5",
    "stage-shots-tallmilan-stage2",
    "stage-shots-tallmilan-stage7",
]
FIXTURES_DIR = Path("tests/fixtures")
CACHE_DIR = FIXTURES_DIR / ".cache"
OUTPUT_DIR = Path("build/ensemble-review")

_CLAP_PROMPTS_SHOT = {
    "a single gunshot at close range",
    "a loud handgun shot recorded with a body-worn microphone",
    "a sharp pistol shot in an outdoor competition",
    "a rapid sequence of pistol shots",
}


def _label(cand_t: list[float], gt_t: list[float], tol_ms: float) -> list[int]:
    used: set[int] = set()
    for t in sorted(gt_t):
        best_i, best_d = None, None
        for i, c in enumerate(cand_t):
            if i in used:
                continue
            d = abs(c - t) * 1000.0
            if d <= tol_ms and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is not None:
            used.add(best_i)
    return [1 if i in used else 0 for i in range(len(cand_t))]


def _hand_features(
    audio, sr, t, all_times, beep_time, confidence, peak_amp
) -> list[float]:
    idx = int(round(t * sr))
    n = audio.size
    win = int(0.050 * sr)
    pre_lo, pre_hi = max(0, idx - win), idx
    post_lo, post_hi = idx, min(n, idx + win)
    rms_pre = (
        float(np.sqrt(np.mean(audio[pre_lo:pre_hi].astype(np.float64) ** 2)))
        if pre_hi > pre_lo
        else 0.0
    )
    rms_post = (
        float(np.sqrt(np.mean(audio[post_lo:post_hi].astype(np.float64) ** 2)))
        if post_hi > post_lo
        else 0.0
    )
    pre10 = int(0.010 * sr)
    a_lo = max(0, idx - pre10)
    pre_amp = float(np.max(np.abs(audio[a_lo:idx]))) if idx > a_lo else 0.0
    attack = (peak_amp - pre_amp) / 0.010
    sorted_t = sorted(all_times)
    j = sorted_t.index(t)
    gap_prev = (sorted_t[j] - sorted_t[j - 1]) if j > 0 else 5.0
    return [
        peak_amp,
        confidence,
        rms_pre,
        rms_post,
        rms_post / (rms_pre + 1e-6),
        attack,
        gap_prev,
        (t - beep_time) * 1000.0,
    ]


def _compute_universe(fixtures: list[str], tolerance_ms: float):
    """Return list[dict] one per candidate across all fixtures, with voter signals."""
    universe = []
    per_fixture = {}
    for fix in fixtures:
        truth = json.loads((FIXTURES_DIR / f"{fix}.json").read_text())
        audio, sr = load_audio(FIXTURES_DIR / f"{fix}.wav")
        cfg_recall = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
        cfg_safe = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.03)
        all_shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg_recall)
        safe_shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg_safe)
        safe_times = {round(s.time_absolute, 6) for s in safe_shots}

        gt_t = [s["time"] for s in truth.get("shots", [])]
        cand_t = [s.time_absolute for s in all_shots]
        labels = _label(cand_t, gt_t, tolerance_ms)

        clap = np.load(CACHE_DIR / f"{fix}_clap.npz", allow_pickle=True)
        if clap["audio_emb"].shape[0] != len(all_shots):
            raise SystemExit(
                f"{fix}: CLAP cache stale; re-run extract_clap_features.py --force"
            )
        prompts = [str(p) for p in clap["prompts"].tolist()]
        sims = clap["text_sims"]
        shot_idx = [i for i, p in enumerate(prompts) if p in _CLAP_PROMPTS_SHOT]
        not_idx = [i for i, p in enumerate(prompts) if p not in _CLAP_PROMPTS_SHOT]
        shot_mean = sims[:, shot_idx].mean(axis=1)
        not_mean = sims[:, not_idx].mean(axis=1)
        diff = shot_mean - not_mean

        per_fixture[fix] = {
            "truth": truth,
            "shots": all_shots,
            "indices_in_universe": [],
        }

        for i, shot in enumerate(all_shots):
            feats = _hand_features(
                audio, sr, shot.time_absolute, cand_t, truth["beep_time"],
                shot.confidence, shot.peak_amplitude,
            )
            per_fixture[fix]["indices_in_universe"].append(len(universe))
            universe.append({
                "fixture": fix,
                "t": shot.time_absolute,
                "label": labels[i],
                "vote_a": int(round(shot.time_absolute, 6) in safe_times),
                "clap_diff": float(diff[i]),
                "hand_feats": feats,
                "clap_sims": list(sims[i]),
                "peak_amplitude": float(shot.peak_amplitude),
                "confidence": float(shot.confidence),
                "time_from_beep": float(shot.time_from_beep),
            })
    return universe, per_fixture


def _vote_b_threshold(universe) -> float:
    pos = [c["clap_diff"] for c in universe if c["label"] == 1]
    return min(pos) if pos else 0.0


def _vote_c_probs_and_threshold(universe, target_recall: float):
    X = np.array(
        [c["hand_feats"] + c["clap_sims"] + [c["clap_diff"]] for c in universe], dtype=np.float64
    )
    y = np.array([c["label"] for c in universe], dtype=np.int64)
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    probs = np.zeros_like(y, dtype=np.float64)
    for tr, te in skf.split(X, y):
        clf = GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
        )
        clf.fit(X[tr], y[tr])
        probs[te] = clf.predict_proba(X[te])[:, 1]
    n_pos = int(y.sum())
    pairs = sorted(zip(probs, y, strict=True), key=lambda x: -x[0])
    cum = 0
    threshold = 0.0
    for prob, lbl in pairs:
        if lbl == 1:
            cum += 1
        if cum / n_pos >= target_recall:
            threshold = prob
            break
    return probs, threshold


def _build_fixture_json(
    source_truth: dict,
    shots_kept: list[dict],
    all_candidates: list[dict],
    label: str,
) -> dict:
    out = dict(source_truth)
    out["shots"] = shots_kept
    out["_candidates_pending_audit"] = {
        "_note": (
            f"Auto-generated for ensemble UI comparison ({label}). "
            "shots[] reflects the chosen filter; candidates[] is the full "
            "max-recall detector output."
        ),
        "candidates": all_candidates,
    }
    # Strip any keys that don't make sense in the synthetic fixture.
    out.pop("tolerance_ms", None)
    return out


def _materialize(json_path: Path, wav_src: Path, fixture_dict: dict) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(fixture_dict, indent=2) + "\n")
    wav_link = json_path.with_suffix(".wav")
    if wav_link.exists() or wav_link.is_symlink():
        wav_link.unlink()
    # Use absolute path so symlink resolves regardless of cwd at review time.
    wav_link.symlink_to(wav_src.resolve())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append", help="Fixture stem (repeatable). Default: all four.")
    p.add_argument("--consensus", type=int, default=3, choices=[1, 2, 3],
                   help="N-of-3 consensus level for the *-ensemble.json variant (default 3).")
    p.add_argument("--tolerance-ms", type=float, default=75.0)
    args = p.parse_args()

    fixtures = args.fixture or DEFAULT_FIXTURES
    print(f"Computing voter signals across {len(fixtures)} fixture(s)...")
    universe, per_fixture = _compute_universe(fixtures, args.tolerance_ms)
    print(f"Universe: {len(universe)} candidates, {sum(c['label'] for c in universe)} positives")

    clap_thr = _vote_b_threshold(universe)
    probs, c_thr = _vote_c_probs_and_threshold(universe, target_recall=1.0)
    print(f"Voter B threshold (CLAP shot-notshot): {clap_thr:.4f}")
    print(f"Voter C threshold (GBDT, target recall 100 %): {c_thr:.4f}")

    for i, c in enumerate(universe):
        c["vote_b"] = int(c["clap_diff"] >= clap_thr)
        c["vote_c"] = int(probs[i] >= c_thr)
        c["vote_total"] = c["vote_a"] + c["vote_b"] + c["vote_c"]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nWriting fixture variants to {OUTPUT_DIR}/")

    for fix in fixtures:
        info = per_fixture[fix]
        truth = info["truth"]
        all_shots = info["shots"]
        idxs = info["indices_in_universe"]

        # Build the full candidates[] payload for both variants. Candidate numbers
        # are 1-based and stable within a fixture.
        all_candidates = []
        for n, shot in enumerate(all_shots, start=1):
            all_candidates.append({
                "candidate_number": n,
                "time": round(shot.time_absolute, 4),
                "ms_after_beep": round(shot.time_from_beep * 1000, 0),
                "peak_amplitude": round(shot.peak_amplitude, 4),
                "confidence": round(shot.confidence, 3),
            })

        # Baseline shots[]: candidates voted by A (cwt + min_confidence=0.03).
        baseline_shots = []
        for n, ui in enumerate(idxs, start=1):
            if universe[ui]["vote_a"] == 1:
                baseline_shots.append({
                    "shot_number": len(baseline_shots) + 1,
                    "candidate_number": n,
                    "time": round(universe[ui]["t"], 4),
                    "ms_after_beep": round(universe[ui]["time_from_beep"] * 1000, 0),
                    "source": "detected",
                })

        # Ensemble shots[]: candidates passing the chosen consensus level. Each
        # entry includes vote_total in its notes so the JSON itself documents
        # how the kept set was computed.
        ensemble_shots = []
        for n, ui in enumerate(idxs, start=1):
            if universe[ui]["vote_total"] >= args.consensus:
                ensemble_shots.append({
                    "shot_number": len(ensemble_shots) + 1,
                    "candidate_number": n,
                    "time": round(universe[ui]["t"], 4),
                    "ms_after_beep": round(universe[ui]["time_from_beep"] * 1000, 0),
                    "source": "detected",
                    "ensemble_votes": universe[ui]["vote_total"],
                })

        # Materialize both.
        wav_src = FIXTURES_DIR / f"{fix}.wav"
        baseline_json = OUTPUT_DIR / f"{fix}-baseline.json"
        ensemble_json = OUTPUT_DIR / f"{fix}-ensemble-{args.consensus}of3.json"

        _materialize(baseline_json, wav_src, _build_fixture_json(
            truth, baseline_shots, all_candidates, label="baseline (cwt + min_confidence=0.03)"
        ))
        _materialize(ensemble_json, wav_src, _build_fixture_json(
            truth, ensemble_shots, all_candidates,
            label=f"ensemble {args.consensus}-of-3 consensus (A=baseline+0.03, B=CLAP, C=GBDT)",
        ))

        n_total = len(all_candidates)
        n_base = len(baseline_shots)
        n_ens = len(ensemble_shots)
        n_audited = sum(1 for s in truth.get("shots", []) if s.get("source") != "manual")
        n_manual = sum(1 for s in truth.get("shots", []) if s.get("source") == "manual")
        print(
            f"  {fix}: cands {n_total}, baseline {n_base}, "
            f"ensemble({args.consensus}/3) {n_ens}, "
            f"audited {n_audited} (+{n_manual} manual)"
        )

    print(f"\nOpen in the review UI -- one fixture per browser tab/window:")
    for fix in fixtures:
        b = OUTPUT_DIR / f"{fix}-baseline.json"
        e = OUTPUT_DIR / f"{fix}-ensemble-{args.consensus}of3.json"
        print(f"\n  uv run splitsmith review --fixture {b}")
        print(f"  uv run splitsmith review --fixture {e} --port 5174")


if __name__ == "__main__":
    main()
