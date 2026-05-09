"""Mine hard negatives from the wide-window fixture audio (issue #87).

For each fixture with a ``tests/fixtures/full/{stem}_full.wav`` produced by
``scripts/extract_full_fixture_audio.py``:

1. Run the raw candidate generator (Voter A only, max-recall config) across
   the entire wide audio.
2. Drop candidates that fall inside the audited stage window (mapped from the
   audit JSON's ``fixture_window_in_source`` + ``beep_time`` +
   ``stage_time_seconds`` into ``_full.wav`` coordinates), with small
   exclusion pads to absorb beep-region ringdown and any unaudited tail
   shots.
3. Tag survivors as ``pre_beep`` or ``post_stage``. By construction these
   regions cannot contain real stage shots, so any candidate that survives
   Voter A's confidence floor here is a hard negative -- no human labelling
   needed.
4. Persist them as ``tests/fixtures/.cache/_mined_negatives.npz`` for the
   ensemble build script, plus ``tests/fixtures/full/_mining_report.json``
   so the user can spot-audit the harvest (project rule: optimize for the
   audit trail).

The full ensemble is intentionally NOT used here -- raw Voter A keeps the
mined negatives feature-comparable to the positives the calibration script
already sees, and avoids baking in the current ensemble's blind spots.

Run:
    uv run python scripts/mine_negatives.py
    uv run python scripts/mine_negatives.py --fixture stage-shots-blacksmith-2026-stage1-s97dcec94
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.shot_detect import detect_shots

FIXTURES_DIR = Path("tests/fixtures")
FULL_DIR = FIXTURES_DIR / "full"
CACHE_DIR = FIXTURES_DIR / ".cache"
MINED_PATH = CACHE_DIR / "_mined_negatives.npz"
REPORT_PATH = FULL_DIR / "_mining_report.json"

# Pads applied around the audited stage window before subtraction. Keeps
# beep ringdown out of the pre_beep bucket and absorbs unaudited late shots
# at the tail. The post-pad default is generous (2 s) because some audits
# under-state stage_time_seconds relative to the actual last shot -- we'd
# rather lose a few easy negatives than mislabel a real shot.
DEFAULT_EXCLUSION_PRE_PAD_S = 1.0
DEFAULT_EXCLUSION_POST_PAD_S = 2.0


def _stage_window_in_full(audit: dict, sidecar: dict) -> tuple[float, float]:
    """Map audited beep/stage-end into ``_full.wav`` coordinates.

    Audit JSON's ``beep_time`` is relative to the SHORT fixture (always 0.5s
    by construction). The short fixture starts at ``fixture_window_in_source[0]``
    in source coords. The wide audio starts at ``full_window_in_source[0]``.
    Subtract to get the offset.

    Stage end uses ``max(stage_time_seconds, last_audited_shot_time)`` because
    a few fixtures (e.g. stage3) have audited shots that run past the official
    stage time -- if we exclude only up to ``stage_time_seconds`` those real
    shots get harvested as post_stage negatives, which is a labelling bug.
    """
    fws_start = float(audit["fixture_window_in_source"][0])
    full_start = float(sidecar["full_window_in_source"][0])
    beep_in_full = (fws_start + float(audit["beep_time"])) - full_start
    stage_t = float(audit["stage_time_seconds"])
    shots = audit.get("shots") or []
    last_shot_from_beep = (
        max(float(s["time"]) for s in shots) - float(audit["beep_time"]) if shots else 0.0
    )
    stage_end_in_full = beep_in_full + max(stage_t, last_shot_from_beep)
    return beep_in_full, stage_end_in_full


def mine_one(
    stem: str,
    *,
    exclusion_pre_pad: float,
    exclusion_post_pad: float,
    log: Callable[[str], None],
) -> list[dict]:
    audit_path = FIXTURES_DIR / f"{stem}.json"
    full_wav = FULL_DIR / f"{stem}_full.wav"
    full_json = FULL_DIR / f"{stem}_full.json"
    if not (audit_path.exists() and full_wav.exists() and full_json.exists()):
        log(f"  SKIP {stem}: missing audit / full wav / sidecar")
        return []

    audit = json.loads(audit_path.read_text())
    sidecar = json.loads(full_json.read_text())
    audio, sr = load_audio(full_wav)
    duration = audio.size / sr

    cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
    # Scan the whole wide audio: pass beep_time=0 + stage_time=duration so
    # detect_shots' internal slice covers [0.5s, duration]. The first 500 ms
    # are skipped by _POST_BEEP_SKIP_S; that's fine since source videos
    # typically open with the camera being aimed, no shots.
    shots = detect_shots(audio, sr, beep_time=0.0, stage_time=duration, config=cfg)
    if not shots:
        log(f"  {stem}: no candidates in {duration:.0f}s of audio")
        return []

    beep_in_full, stage_end_in_full = _stage_window_in_full(audit, sidecar)
    excl_lo = beep_in_full - exclusion_pre_pad
    excl_hi = stage_end_in_full + exclusion_post_pad

    rows: list[dict] = []
    for s in shots:
        t = s.time_absolute
        if excl_lo <= t <= excl_hi:
            continue  # inside the audited stage window -- not a free negative
        if t < beep_in_full:
            tag = "pre_beep"
        else:
            tag = "post_stage"
        rows.append(
            {
                "fixture": stem,
                "time_in_full": float(t),
                "confidence": float(s.confidence),
                "peak_amplitude": float(s.peak_amplitude),
                "region_tag": tag,
            }
        )
    log(
        f"  {stem}: {len(rows)} mined "
        f"(pre_beep={sum(1 for r in rows if r['region_tag'] == 'pre_beep')}, "
        f"post_stage={sum(1 for r in rows if r['region_tag'] == 'post_stage')}) "
        f"from {len(shots)} candidates over {duration:.0f}s"
    )
    return rows


def write_outputs(all_rows: list[dict], log: Callable[[str], None]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    FULL_DIR.mkdir(parents=True, exist_ok=True)

    if not all_rows:
        log("No mined negatives; not writing outputs.")
        return

    np.savez(
        MINED_PATH,
        fixture=np.array([r["fixture"] for r in all_rows], dtype=object),
        time_in_full=np.array([r["time_in_full"] for r in all_rows], dtype=np.float64),
        confidence=np.array([r["confidence"] for r in all_rows], dtype=np.float64),
        peak_amplitude=np.array([r["peak_amplitude"] for r in all_rows], dtype=np.float64),
        region_tag=np.array([r["region_tag"] for r in all_rows], dtype=object),
    )
    log(f"Wrote {MINED_PATH}")

    by_fixture: dict[str, list[dict]] = {}
    for r in all_rows:
        by_fixture.setdefault(r["fixture"], []).append(r)
    report = {
        "n_total": len(all_rows),
        "n_pre_beep": sum(1 for r in all_rows if r["region_tag"] == "pre_beep"),
        "n_post_stage": sum(1 for r in all_rows if r["region_tag"] == "post_stage"),
        "per_fixture": {
            stem: {
                "n": len(rows),
                "n_pre_beep": sum(1 for r in rows if r["region_tag"] == "pre_beep"),
                "n_post_stage": sum(1 for r in rows if r["region_tag"] == "post_stage"),
                "sample_times_pre_beep": [
                    round(r["time_in_full"], 3)
                    for r in rows
                    if r["region_tag"] == "pre_beep"
                ][:10],
                "sample_times_post_stage": [
                    round(r["time_in_full"], 3)
                    for r in rows
                    if r["region_tag"] == "post_stage"
                ][:10],
            }
            for stem, rows in sorted(by_fixture.items())
        },
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n")
    log(f"Wrote {REPORT_PATH}")


def mine_all(
    fixtures: list[str] | None = None,
    *,
    exclusion_pre_pad: float = DEFAULT_EXCLUSION_PRE_PAD_S,
    exclusion_post_pad: float = DEFAULT_EXCLUSION_POST_PAD_S,
    log: Callable[[str], None] = print,
) -> list[dict]:
    if fixtures is None:
        fixtures = sorted(p.stem.removesuffix("_full") for p in FULL_DIR.glob("*_full.wav"))
    if not fixtures:
        log(
            f"No *_full.wav files in {FULL_DIR}. Run "
            "scripts/extract_full_fixture_audio.py first."
        )
        return []
    log(f"Mining negatives across {len(fixtures)} fixture(s)...")
    rows: list[dict] = []
    for stem in fixtures:
        rows.extend(
            mine_one(
                stem,
                exclusion_pre_pad=exclusion_pre_pad,
                exclusion_post_pad=exclusion_post_pad,
                log=log,
            )
        )
    write_outputs(rows, log=log)
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append", help="Fixture stem (repeatable).")
    p.add_argument(
        "--exclusion-pre-pad",
        type=float,
        default=DEFAULT_EXCLUSION_PRE_PAD_S,
        help="Seconds before the beep to also exclude (absorb beep ringdown).",
    )
    p.add_argument(
        "--exclusion-post-pad",
        type=float,
        default=DEFAULT_EXCLUSION_POST_PAD_S,
        help="Seconds after the audited last shot to also exclude.",
    )
    args = p.parse_args()
    mine_all(
        fixtures=args.fixture or None,
        exclusion_pre_pad=args.exclusion_pre_pad,
        exclusion_post_pad=args.exclusion_post_pad,
    )


if __name__ == "__main__":
    main()
