"""Measure finding a stage's beep from the beep confirmed on another angle (#1442).

For every stage the corpus holds on two or more reviewed angles, each ordered
pair (reference, other) hides the other's labelled beep and finds it from the
reference's (``beep_cross.place_beep``): the envelope projection alone, the
tone template, and the template moved onto the tone's start. Reports the
error distribution of each against the other's label and lists every miss.

    uv run python scripts/eval_beep_from_angle.py [--json out.json] [--miss-ms 10]

Fetch the fixture audio first (``scripts/fixture_audio.py fetch``).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from splitsmith import beep_tone
from splitsmith.beep_cross import CrossAlignError, line_onset_near, place_beep
from splitsmith.beep_detect import load_audio
from splitsmith.fixture_schema import REVIEWED, review_status

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def corpus() -> dict[tuple, list[dict]]:
    """Reviewed, current fixtures with audio, grouped by stage."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for path in sorted(FIXTURES.glob("stage-shots-*.json")):
        if path.name.endswith("-promotion-report.json"):
            continue
        data = json.loads(path.read_text())
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
        groups[key].append(
            {"stem": path.stem, "wav": wav, "beep": float(data["beep_time"]), "camera": data.get("camera")}
        )
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
                try:
                    p = place_beep(ra, rsr, ref["beep"], oa, osr)
                except CrossAlignError as exc:
                    rows.append({"ref": ref["stem"], "other": other["stem"], "error": str(exc)})
                    continue
                truth = other["beep"]
                line = line_onset_near(ra, rsr, ref["beep"], oa, osr, p.coarse)
                toned = beep_tone.tone_onset(oa, osr, p.coarse) if 0 <= p.coarse <= oa.size / osr else None
                rows.append(
                    {
                        "ref": ref["stem"],
                        "other": other["stem"],
                        "truth": truth,
                        "coarse_ms": (p.coarse - truth) * 1000,
                        "coarse_conf": p.coarse_confidence,
                        "matched_ms": None if p.matched is None else (p.matched - truth) * 1000,
                        "score": p.match_score,
                        "placed_ms": (p.time - truth) * 1000,
                        "line_ms": None if line is None else (line - truth) * 1000,
                        "coarse_tone_ms": ((toned if toned is not None else p.coarse) - truth) * 1000,
                    }
                )
    ok = [r for r in rows if "error" not in r]
    report = {
        "stages": len(groups),
        "pairs": len(rows),
        "failed": len(rows) - len(ok),
        "coarse": summarize([r["coarse_ms"] for r in ok]),
        "matched": summarize([r["matched_ms"] for r in ok if r["matched_ms"] is not None]),
        "placed": summarize([r["placed_ms"] for r in ok]),
        "line": summarize([r["line_ms"] for r in ok if r["line_ms"] is not None]),
        "line_or_coarse": summarize(
            [r["line_ms"] if r["line_ms"] is not None else r["coarse_ms"] for r in ok]
        ),
        "coarse_tone": summarize([r["coarse_tone_ms"] for r in ok]),
    }
    print(json.dumps(report, indent=2))
    misses = sorted((r for r in ok if abs(r["placed_ms"]) > args.miss_ms), key=lambda r: -abs(r["placed_ms"]))
    print(f"\n{len(misses)} placed more than {args.miss_ms:.0f} ms off:")
    for r in misses:
        print(
            f"  {r['placed_ms']:+8.1f} ms  (coarse {r['coarse_ms']:+7.1f}, matched "
            f"{'-' if r['matched_ms'] is None else format(r['matched_ms'], '+7.1f')}, score "
            f"{'-' if r['score'] is None else format(r['score'], '.2f')})  {r['ref']} -> {r['other']}"
        )
    for r in rows:
        if "error" in r:
            print(f"  failed: {r['ref']} -> {r['other']}: {r['error']}")
    if args.json:
        args.json.write_text(json.dumps({"report": report, "rows": rows}, indent=2))


if __name__ == "__main__":
    main()
