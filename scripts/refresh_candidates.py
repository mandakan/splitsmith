"""Re-snapshot ``_candidates_pending_audit`` blocks and update each audited
shot's ``candidate_number`` to its new nearest candidate after a deliberate
shot_detect behavior change.

Audited ``time`` fields are NOT modified; only the candidate snapshot and the
candidate_number links. If any audited shot has no candidate within
``tolerance_ms`` of its time, the script aborts (we never want to silently
break ground-truth references).

Run:
    uv run python scripts/refresh_candidates.py
    uv run python scripts/refresh_candidates.py --fixture stage-shots-...
    uv run python scripts/refresh_candidates.py --tolerance-ms 75 --dry-run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.shot_detect import detect_shots

DEFAULT_FIXTURES = [
    "stage-shots",
    "stage-shots-blacksmith-h5",
    "stage-shots-blacksmith-2026-stage1",
    "stage-shots-blacksmith-2026-stage2",
    "stage-shots-blacksmith-2026-stage3",
    "stage-shots-blacksmith-2026-stage5",
    "stage-shots-blacksmith-2026-stage6",
    "stage-shots-blacksmith-2026-stage8",
    "stage-shots-tallmilan-stage2",
    "stage-shots-tallmilan-stage7",
    "stage-shots-tallmilan-2026-stage5",
    "stage-shots-tallmilan-2026-stage6",
]
FIXTURES_DIR = Path("tests/fixtures")


def _candidates_block(shots, beep_time):
    return {
        "_note": (
            "Auto-detected by current shot_detect (rise-foot leading edge with "
            "reverb-chain re-anchor). NOT ground truth."
        ),
        "candidates": [
            {
                "candidate_number": i,
                "time": round(s.time_absolute, 4),
                "ms_after_beep": round((s.time_absolute - beep_time) * 1000, 0),
                "peak_amplitude": round(s.peak_amplitude, 4),
                "confidence": round(s.confidence, 3),
            }
            for i, s in enumerate(shots, start=1)
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append")
    p.add_argument("--tolerance-ms", type=float, default=75.0)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    fixtures = args.fixture or DEFAULT_FIXTURES
    skipped: list[str] = []
    for fix in fixtures:
        fp = FIXTURES_DIR / f"{fix}.json"
        truth = json.loads(fp.read_text())
        audio, sr = load_audio(FIXTURES_DIR / f"{fix}.wav")
        # Match audit-prep / snapshot test: default ShotDetectConfig (no CWT
        # fallback, no min_confidence override). The eval pipelines use a
        # different config but they don't care about the snapshot block.
        cfg = ShotDetectConfig()
        shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg)
        cand_t = [s.time_absolute for s in shots]

        new_links = []
        skip_reason = None
        for s in truth.get("shots", []):
            if s.get("source") == "manual":
                new_links.append((None, 0.0))
                continue
            best_i, best_d = None, None
            for i, c in enumerate(cand_t):
                d = abs(c - s["time"]) * 1000.0
                if best_d is None or d < best_d:
                    best_i, best_d = i, d
            if best_d is None or best_d > args.tolerance_ms:
                skip_reason = (
                    f"detected shot t={s['time']:.4f} has no candidate "
                    f"within {args.tolerance_ms} ms (nearest delta "
                    f"{best_d:.1f} ms) -- existing snapshot kept, possibly "
                    f"originally generated with a different detector config"
                )
                break
            new_links.append((best_i + 1, best_d))
        if skip_reason is not None:
            print(f"{fix}: SKIP - {skip_reason}")
            skipped.append(fix)
            continue

        old_count = len(truth.get("_candidates_pending_audit", {}).get("candidates", []))
        print(
            f"{fix}: {len(shots)} cands (was {old_count}), "
            f"{len(new_links)} audited shots all linked "
            f"(max drift {max((d for _, d in new_links), default=0):.1f} ms)"
        )

        if args.dry_run:
            continue

        for shot, (new_cn, _) in zip(truth.get("shots", []), new_links, strict=True):
            if new_cn is None:
                shot.pop("candidate_number", None)
            else:
                shot["candidate_number"] = new_cn
        truth["_candidates_pending_audit"] = _candidates_block(shots, truth["beep_time"])
        fp.write_text(json.dumps(truth, indent=2) + "\n")

    if skipped:
        print(f"\nSkipped {len(skipped)} fixture(s): {', '.join(skipped)}")


if __name__ == "__main__":
    main()
