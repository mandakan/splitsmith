"""Run the ensemble engine over every fixture with the active artifact set; write per-fixture TP/FP/FN.

Usage: [SPLITSMITH_ARTIFACTS_DIR=<dir>] uv run python scripts/eval_ensemble_artifacts.py <out.json>
Two modes per fixture: with the stage's expected rounds (the app's path when the scorecard knows them)
and without. Compare two outputs with scripts/compare_ensemble_evals.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from splitsmith.beep_detect import load_audio
from splitsmith.ensemble.api import detect_shots_ensemble, load_ensemble_runtime
from splitsmith.ensemble.calibration import camera_class_from_mount

TOLERANCE_S = 0.075


def score(kept: list[float], truth: list[float]) -> tuple[int, int, int]:
    used: set[int] = set()
    tp = 0
    for t in sorted(kept):
        best = None
        for j, s in enumerate(truth):
            if (
                j not in used
                and abs(s - t) <= TOLERANCE_S
                and (best is None or abs(s - t) < abs(truth[best] - t))
            ):
                best = j
        if best is not None:
            used.add(best)
            tp += 1
    return tp, len(kept) - tp, len(truth) - tp


def main(out_path: Path) -> None:
    runtime = load_ensemble_runtime(with_voter_e=False)
    out: dict[str, dict] = {}
    for fx in sorted(Path("tests/fixtures").glob("stage-shots-*.json")):
        wav = fx.with_suffix(".wav")
        d = json.loads(fx.read_text())
        if not wav.exists() or not d.get("shots") or d.get("stage_time_seconds") is None:
            continue
        audio, sr = load_audio(wav)
        cam = d.get("camera") or {}
        cls = camera_class_from_mount(cam.get("mount"))
        truth = [float(s["time"]) for s in d["shots"]]
        expected = (d.get("stage_rounds") or {}).get("expected")
        row: dict = {"camera_class": cls}
        for mode, rounds in (("rounds", expected), ("blind", None)):
            res = detect_shots_ensemble(
                audio,
                sr,
                float(d["beep_time"]),
                float(d["stage_time_seconds"]),
                runtime,
                expected_rounds=rounds,
                camera_class=cls,
                camera_make=cam.get("make"),
                camera_model=cam.get("model"),
            )
            row[mode] = score([c.time for c in res.candidates if c.kept], truth)
        out[fx.stem] = row
        print(fx.stem, row, flush=True)
    out_path.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
