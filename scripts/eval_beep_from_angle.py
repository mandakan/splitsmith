"""Measure finding a stage's beep from the beep confirmed on another angle (#1442).

For every stage the corpus holds on two or more reviewed angles, each ordered
pair (reference, other) hides the other's labelled beep and finds it from the
reference's (``beep_cross.place_beep``). Reports the error against the other's
label for ``cross_align``'s envelope projection (the baseline), the stage-shot
projection, and the placed beep (the tone's start near it), over every pair
and over the confident ones, then lists every confident miss.

    uv run python scripts/eval_beep_from_angle.py [--json out.json] [--miss-ms 10]

Fetch the fixture audio first (``scripts/fixture_audio.py fetch``).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from splitsmith.beep.cross import place_beep
from splitsmith.beep.detect import load_audio
from splitsmith.cross_align import CrossAlignError, align_secondary_to_primary
from splitsmith.fixture_schema import REVIEWED, review_status

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def corpus() -> dict[tuple, list[dict]]:
    """Reviewed, current fixtures with audio, grouped by stage."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for path in sorted(FIXTURES.glob("stage-shots-*.json")):
        if path.name.endswith("-promotion-report.json"):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "beep_time" not in data or data.get("superseded_by"):
            continue
        if review_status(data) != REVIEWED:
            continue
        wav = path.with_suffix(".wav")
        if not wav.exists():
            continue
        key = (
            data.get("event_id"),
            data.get("stage_number"),
            json.dumps(data.get("shooter"), sort_keys=True),
        )
        groups[key].append({"stem": path.stem, "wav": wav, "beep": float(data["beep_time"])})
    return {k: v for k, v in groups.items() if len(v) >= 2}


def summarize(errors_ms: list[float]) -> dict:
    a = np.abs(np.array(errors_ms))
    if a.size == 0:
        return {"n": 0}
    return {
        "n": int(a.size),
        "median_ms": round(float(np.median(a)), 1),
        "p90_ms": round(float(np.percentile(a, 90)), 1),
        "within_2ms": int((a <= 2).sum()),
        "within_5ms": int((a <= 5).sum()),
        "within_10ms": int((a <= 10).sum()),
        "over_50ms": int((a > 50).sum()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    ap.add_argument("--miss-ms", type=float, default=10.0)
    args = ap.parse_args()

    groups = corpus()
    audio: dict[str, tuple[np.ndarray, int]] = {}
    rows = []
    for angles in groups.values():
        for ref in angles:
            for other in angles:
                if ref is other:
                    continue
                for a in (ref, other):
                    if a["stem"] not in audio:
                        audio[a["stem"]] = load_audio(a["wav"])
                ra, rsr = audio[ref["stem"]]
                oa, osr = audio[other["stem"]]
                truth = other["beep"]
                row: dict = {"ref": ref["stem"], "other": other["stem"], "truth": truth}
                try:
                    env = align_secondary_to_primary(ra, rsr, ref["beep"], oa, osr)
                    row["envelope_ms"] = (env.secondary_beep_time - truth) * 1000
                except CrossAlignError:
                    row["envelope_ms"] = None
                p = place_beep(ra, rsr, ref["beep"], oa, osr)
                if p is None:
                    row["placed_ms"] = None
                else:
                    row.update(
                        projected_ms=(p.projected - truth) * 1000,
                        contrast=p.contrast,
                        confident=p.confident,
                        tone_found=p.tone is not None,
                        placed_ms=(p.time - truth) * 1000,
                    )
                rows.append(row)
    placed = [r for r in rows if r["placed_ms"] is not None]
    confident = [r for r in placed if r["confident"]]
    report = {
        "stages": len(groups),
        "pairs": len(rows),
        "envelope_projection": summarize([r["envelope_ms"] for r in rows if r["envelope_ms"] is not None]),
        "stage_projection": summarize([r["projected_ms"] for r in placed]),
        "placed": summarize([r["placed_ms"] for r in placed]),
        "confident": {
            "pairs": len(confident),
            "placed": summarize([r["placed_ms"] for r in confident]),
            "tone_found": sum(r["tone_found"] for r in confident),
        },
    }
    print(json.dumps(report, indent=2))
    misses = sorted(
        (r for r in confident if abs(r["placed_ms"]) > args.miss_ms), key=lambda r: -abs(r["placed_ms"])
    )
    print(f"\n{len(misses)} confident pairs placed more than {args.miss_ms:.0f} ms off:")
    for r in misses:
        print(
            f"  {r['placed_ms']:+8.1f} ms  (shots {r['projected_ms']:+7.1f}, contrast {r['contrast']:.2f}, "
            f"tone {'yes' if r['tone_found'] else 'no'})  {r['ref']} -> {r['other']}"
        )
    if args.json:
        args.json.write_text(json.dumps({"report": report, "rows": rows}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
