"""Regression check for Voter E: production ensemble run on audited fixtures.

Loads every head-mounted Go 3S audited fixture, runs the production
``detect_shots_ensemble`` across a fusion-rule grid:

* off              -- legacy 4-voter ensemble (current main without Voter E)
* unconditional    -- Voter E veto applies to every candidate (issue #183)
* strong3 / strong4 -- conditional veto suppressed when audio ``vote_total``
  >= the threshold (issue #185). ``strong4`` skips Voter E whenever all
  four audio voters agreed; ``strong3`` is the looser variant.

Reports per-fixture and aggregate precision/recall against the labeled
shots so the regression on audio-dominant stages can be quantified
across rules.

Run:
    uv run python scripts/regression_voter_e.py
"""

from __future__ import annotations

import json
from pathlib import Path

from splitsmith.beep_detect import load_audio
from splitsmith.ensemble import (
    EnsembleConfig,
    detect_shots_ensemble,
    load_ensemble_runtime,
)
from splitsmith.ensemble.calibration import camera_class_from_mount

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
TOL_MS = 75.0


VARIANTS: list[tuple[str, EnsembleConfig, bool]] = [
    ("off", EnsembleConfig(enable_voter_e=False), False),
    ("unconditional", EnsembleConfig(enable_voter_e=True, e_required=True), True),
    (
        "strong3",
        EnsembleConfig(enable_voter_e=True, e_required=True, e_audio_strong_min_votes=3),
        True,
    ),
    (
        "strong4",
        EnsembleConfig(enable_voter_e=True, e_required=True, e_audio_strong_min_votes=4),
        True,
    ),
]


def _resolve_truth(truth: dict, candidates) -> list[bool]:
    """Greedy nearest-time match between detected candidates and labeled shots."""
    truth_times = sorted(float(s["time"]) for s in truth.get("shots", []) or [])
    used: set[int] = set()
    matched = [False] * len(candidates)
    for tt in truth_times:
        best_i, best_d = None, None
        for i, c in enumerate(candidates):
            if i in used:
                continue
            d = abs(c.time - tt) * 1000.0
            if d <= TOL_MS and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is not None:
            used.add(best_i)
            matched[best_i] = True
    return matched


def _eval(name: str, kept: list[bool], is_shot: list[bool]) -> dict:
    n_kept = sum(kept)
    n_shot = sum(is_shot)
    n_tp = sum(1 for k, t in zip(kept, is_shot, strict=True) if k and t)
    n_fp = n_kept - n_tp
    n_fn = n_shot - n_tp
    precision = n_tp / n_kept if n_kept else 0.0
    recall = n_tp / n_shot if n_shot else 0.0
    return {
        "label": name,
        "kept": n_kept,
        "shots": n_shot,
        "tp": n_tp,
        "fp": n_fp,
        "fn": n_fn,
        "precision": precision,
        "recall": recall,
    }


def main() -> int:
    runtime = load_ensemble_runtime(with_voter_e=True)
    if runtime.visual is None:
        print("Voter E artifacts not shipped; rebuild calibration first.")
        return 2

    fixtures: list[Path] = []
    for p in sorted(FIXTURES_DIR.glob("stage-shots-*.json")):
        if any(skip in p.name for skip in ("peaks", "promotion-report", "iphone")):
            continue
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        cam = d.get("camera") or {}
        if cam.get("id") != "go3s" or cam.get("mount") != "head":
            continue
        if not d.get("source_video") or not Path(d["source_video"]).exists():
            continue
        fixtures.append(p)

    print(f"Evaluating {len(fixtures)} head-mounted Go 3S fixtures with reachable video")
    print(f"Variants: {[v[0] for v in VARIANTS]}\n")

    aggregates: dict[str, dict[str, int]] = {name: {"tp": 0, "fp": 0, "fn": 0} for name, _, _ in VARIANTS}

    rows: list[dict] = []
    for fixture_path in fixtures:
        truth = json.loads(fixture_path.read_text())
        wav = FIXTURES_DIR / f"{fixture_path.stem}.wav"
        if not wav.exists():
            continue
        audio, sr = load_audio(wav)
        beep = float(truth["beep_time"])
        stage_t = float(truth["stage_time_seconds"])
        cam_class = camera_class_from_mount((truth.get("camera") or {}).get("mount"))
        window = truth.get("fixture_window_in_source") or [0.0, 0.0]
        source_video = Path(truth["source_video"])
        source_beep = float(window[0]) + beep

        # The candidate universe is identical across variants -- only the
        # kept-mask changes. Compute truth labels once.
        result_universe = detect_shots_ensemble(
            audio,
            sr,
            beep,
            stage_t,
            runtime,
            ensemble_config=EnsembleConfig(enable_voter_e=False),
            camera_class=cam_class,
        )
        is_shot = _resolve_truth(truth, result_universe.candidates)

        per_variant: dict[str, dict] = {}
        for name, cfg, needs_video in VARIANTS:
            result = detect_shots_ensemble(
                audio,
                sr,
                beep,
                stage_t,
                runtime,
                ensemble_config=cfg,
                camera_class=cam_class,
                video_path=source_video if needs_video else None,
                source_beep_time=source_beep if needs_video else None,
            )
            kept = [c.kept for c in result.candidates]
            m = _eval(name, kept, is_shot)
            per_variant[name] = m
            aggregates[name]["tp"] += m["tp"]
            aggregates[name]["fp"] += m["fp"]
            aggregates[name]["fn"] += m["fn"]

        rows.append({"fixture": fixture_path.stem, "metrics": per_variant})

        line = f"{fixture_path.stem:48s}"
        for name in (v[0] for v in VARIANTS):
            m = per_variant[name]
            line += (
                f"  | {name:13s} P/R={m['precision']:.3f}/{m['recall']:.3f} "
                f"({m['tp']}/{m['kept']} kept; {m['fn']} miss)"
            )
        print(line)

    def agg_pr(d: dict[str, int]) -> tuple[float, float]:
        kept = d["tp"] + d["fp"]
        gold = d["tp"] + d["fn"]
        return (d["tp"] / kept if kept else 0.0, d["tp"] / gold if gold else 0.0)

    print("\n=== aggregate ===")
    print(f"{'variant':14s}  {'P':>6s}  {'R':>6s}  {'TP':>4s}  {'FP':>4s}  {'FN':>4s}")
    for name in (v[0] for v in VARIANTS):
        p, r = agg_pr(aggregates[name])
        a = aggregates[name]
        print(f"{name:14s}  {p:6.3f}  {r:6.3f}  " f"{a['tp']:4d}  {a['fp']:4d}  {a['fn']:4d}")

    print("\n=== per-fixture recall regressions vs off ===")
    regressions: dict[str, list[tuple[str, float]]] = {
        name: [] for name in (v[0] for v in VARIANTS) if name != "off"
    }
    for row in rows:
        off_r = row["metrics"]["off"]["recall"]
        for name in regressions:
            delta = row["metrics"][name]["recall"] - off_r
            if delta < 0:
                regressions[name].append((row["fixture"], delta))
    for name in (v[0] for v in VARIANTS):
        if name == "off":
            continue
        items = regressions[name]
        if not items:
            print(f"{name:14s}  no per-fixture recall regressions")
            continue
        print(f"{name:14s}  {len(items)} regressions:")
        for fix, delta in items:
            print(f"  {fix}  deltaR={delta:+.3f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
