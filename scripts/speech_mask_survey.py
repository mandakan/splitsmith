"""Dry run of the speech masker over the fixture corpus (#1778).

Proposes masks for every fixture whose WAV is here and writes them, with a
summary, to ``build/speech_mask_survey.json``. Changes no fixture and no
audio: it answers how much speech the corpus holds and how many stages
need a person because speech reaches a shot or the beep.

    uv run python scripts/fixture_audio.py fetch
    uv run python scripts/speech_mask_survey.py [--only STEM ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from splitsmith.beep.detect import load_audio
from splitsmith.config import SpeechMaskConfig
from splitsmith.lab import speech_mask

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
OUT = REPO / "build" / "speech_mask_survey.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*", help="fixture stems to survey (default: every fixture with a WAV)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    config = SpeechMaskConfig()
    session = speech_mask.load_vad()
    stems = args.only or sorted(
        p.stem for p in FIXTURES.glob("stage-shots-*.json") if p.with_suffix(".wav").exists()
    )
    rows = []
    for i, stem in enumerate(stems, 1):
        fixture = json.loads((FIXTURES / f"{stem}.json").read_text(encoding="utf-8"))
        audio, sr = load_audio(FIXTURES / f"{stem}.wav")
        proposal = speech_mask.propose_for_fixture(fixture, audio, sr, session, config)
        rows.append(
            {
                "fixture": stem,
                "duration_s": round(len(audio) / sr, 2),
                "masked_s": proposal.masked_seconds,
                "needs_decision": proposal.needs_decision,
                **proposal.model_dump(mode="json"),
            }
        )
        if i % 25 == 0:
            print(f"  {i}/{len(stems)}", file=sys.stderr)

    total = sum(r["duration_s"] for r in rows)
    masked = sum(r["masked_s"] for r in rows)
    summary = {
        "fixtures": len(rows),
        "audio_s": round(total, 1),
        "masked_s": round(masked, 1),
        "with_speech": sum(1 for r in rows if r["regions"] or r["protected_overlaps"]),
        "needs_decision": sum(1 for r in rows if r["needs_decision"]),
        "config": config.model_dump(),
        "method": speech_mask.METHOD,
        "masker_version": speech_mask.MASKER_VERSION,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": summary, "fixtures": rows}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
