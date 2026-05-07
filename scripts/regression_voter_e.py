"""Regression check for Voter E: production ensemble run on audited fixtures.

Loads every head-mounted Go 3S audited fixture, runs the production
``detect_shots_ensemble`` twice -- once with Voter E off (the legacy
4-voter behaviour) and once with Voter E on as a precision veto -- and
reports per-fixture and aggregate precision/recall against the labeled
shots. This is the acceptance check required by issue #183: combined
audio + Voter E must not regress recall vs the legacy ensemble while
ideally lifting precision.

Run:
    uv run python scripts/regression_voter_e.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

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
    n_tp = sum(1 for k, t in zip(kept, is_shot) if k and t)
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

    print(f"Evaluating {len(fixtures)} head-mounted Go 3S fixtures with reachable video\n")

    rows = []
    agg_off: dict[str, int] = {"tp": 0, "fp": 0, "fn": 0}
    agg_on: dict[str, int] = {"tp": 0, "fp": 0, "fn": 0}
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
        # source-video time of the beep = start of fixture window + beep within window.
        source_beep = float(window[0]) + beep

        result_off = detect_shots_ensemble(
            audio,
            sr,
            beep,
            stage_t,
            runtime,
            ensemble_config=EnsembleConfig(enable_voter_e=False),
            camera_class=cam_class,
        )
        result_on = detect_shots_ensemble(
            audio,
            sr,
            beep,
            stage_t,
            runtime,
            ensemble_config=EnsembleConfig(enable_voter_e=True, e_required=True),
            camera_class=cam_class,
            video_path=source_video,
            source_beep_time=source_beep,
        )

        is_shot = _resolve_truth(truth, result_off.candidates)
        kept_off = [c.kept for c in result_off.candidates]
        kept_on = [c.kept for c in result_on.candidates]
        m_off = _eval("off", kept_off, is_shot)
        m_on = _eval("on", kept_on, is_shot)

        agg_off["tp"] += m_off["tp"]
        agg_off["fp"] += m_off["fp"]
        agg_off["fn"] += m_off["fn"]
        agg_on["tp"] += m_on["tp"]
        agg_on["fp"] += m_on["fp"]
        agg_on["fn"] += m_on["fn"]

        delta_p = m_on["precision"] - m_off["precision"]
        delta_r = m_on["recall"] - m_off["recall"]
        rows.append(
            (fixture_path.stem, m_off, m_on, delta_p, delta_r)
        )
        print(
            f"{fixture_path.stem:48s}  "
            f"off P/R = {m_off['precision']:.3f}/{m_off['recall']:.3f} "
            f"({m_off['tp']}/{m_off['kept']} kept; {m_off['fn']} miss)   "
            f"on P/R = {m_on['precision']:.3f}/{m_on['recall']:.3f} "
            f"({m_on['tp']}/{m_on['kept']} kept; {m_on['fn']} miss)   "
            f"deltaP={delta_p:+.3f}  deltaR={delta_r:+.3f}"
        )

    def agg_pr(d: dict[str, int]) -> tuple[float, float]:
        kept = d["tp"] + d["fp"]
        gold = d["tp"] + d["fn"]
        return (d["tp"] / kept if kept else 0.0, d["tp"] / gold if gold else 0.0)

    p_off, r_off = agg_pr(agg_off)
    p_on, r_on = agg_pr(agg_on)
    print()
    print(
        f"AGGREGATE   off P/R={p_off:.3f}/{r_off:.3f}   "
        f"on P/R={p_on:.3f}/{r_on:.3f}   "
        f"deltaP={p_on-p_off:+.3f}  deltaR={r_on-r_off:+.3f}"
    )
    print(
        f"FP suppressed by Voter E: {agg_off['fp']} -> {agg_on['fp']} "
        f"({agg_off['fp'] - agg_on['fp']} false positives removed)"
    )
    print(
        f"Recall cost: {agg_off['fn']} -> {agg_on['fn']} false negatives "
        f"(delta {agg_on['fn'] - agg_off['fn']:+d})"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
