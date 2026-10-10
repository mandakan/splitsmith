"""Score every fixture for review and write the queue's order (#1363).

For each fixture: its review status, whether it was snapped from another
angle, the share of its snapped shots that landed on the snap window's
edge (from its promotion report), and how much its leading edges disagree
shot to shot (``lab.inventory.onset_spread_ms``). Writes
``build/fixture_review_inventory.json``, which the lab's review queue
reads to put the most doubtful fixtures first. Fetch the external fixture
audio first (``scripts/fixture_audio.py fetch``); a fixture without its WAV
is listed without a spread.

Usage:
    uv run python scripts/fixture_review_inventory.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from splitsmith.beep_detect import load_audio
from splitsmith.ensemble import fixtures as fx_module
from splitsmith.fixture_schema import review_status
from splitsmith.lab.inventory import onset_spread_ms, review_priority, suggested_moves

OUT = Path("build/fixture_review_inventory.json")


def _edge_fraction(fixtures: Path, stem: str, data: dict) -> float | None:
    report_path = fixtures / f"{stem}-promotion-report.json"
    if not report_path.exists():
        return None
    report = json.loads(report_path.read_text())
    counts = report.get("counts") or {}
    total = counts.get("anchor_shots") or 0
    if not total:
        return None
    edge = counts.get("at_window_edge")
    if edge is None:  # reports written before the edge count existed
        window = float(report.get("snap_window_ms") or 60.0)
        edge = sum(
            1
            for s in data.get("shots") or []
            if s.get("snap_displacement_ms") is not None and abs(s["snap_displacement_ms"]) >= window - 5.0
        )
    return round(edge / total, 3)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixtures", type=Path, default=fx_module.FIXTURES_DIR)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    entries = []
    missing_audio = 0
    for f in fx_module.all_fixtures(args.fixtures):
        data = json.loads((args.fixtures / f"{f.stem}.json").read_text())
        wav = args.fixtures / f"{f.stem}.wav"
        spread = None
        moves: list[dict] = []
        if wav.exists():
            audio, sr = load_audio(wav)
            times = [float(s["time"]) for s in data.get("shots") or [] if s.get("time") is not None]
            spread = onset_spread_ms(audio, sr, times)
            moves = suggested_moves(audio, sr, times)
        else:
            missing_audio += 1
        entry = {
            "slug": f.stem,
            "match": f.match,
            "stage": f.stage_number,
            "camera": f.camera_id,
            "mount": f.mount,
            "n_shots": f.n_audited_shots,
            "review_status": review_status(data),
            "derived": bool(data.get("anchor")),
            "edge_fraction": _edge_fraction(args.fixtures, f.stem, data),
            "onset_spread_ms": None if spread is None else round(spread, 2),
            # Where the app's leading-edge rule would put shots that look off;
            # a pointer for the reviewer, never written into the fixture.
            "suggested_moves": len(moves),
            "moves": moves,
        }
        score, reasons = review_priority(entry)
        entry["priority"] = round(score, 1)
        entry["reasons"] = reasons
        entries.append(entry)
    entries.sort(key=lambda e: (-e["priority"], e["slug"]))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"version": 1, "fixtures": entries}, indent=2) + "\n")
    needs = sum(1 for e in entries if e["review_status"] == "needs_review")
    print(f"{len(entries)} fixtures, {needs} need review, {missing_audio} without audio; wrote {args.out}")
    for e in entries[:10]:
        print(f"  {e['priority']:6.1f}  {e['slug'][12:]:60s} {'; '.join(e['reasons'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
