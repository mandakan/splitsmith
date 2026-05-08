"""Run the current beep detector against every calibration fixture.

This is the layer-1 baseline harness for issue #220. It reads the
manifest produced by ``build_beep_calibration.py``, runs
``splitsmith.beep_detect.detect_beep`` against both the trimmed clip
WAV and (when available) the wide-window full WAV, scores each call
against the recorded ground truth, and prints:

* a per-fixture table (clip + full track),
* per-tag bucket aggregates (handheld vs headcam, late-beep, ...),
* an overall recall summary.

The output is the reference baseline. Layer-2 detector changes should
re-run this script and improve recall / precision against it.

Run::

    uv run python scripts/eval_beep_detector.py
    uv run python scripts/eval_beep_detector.py --json out/beep_eval.json
    uv run python scripts/eval_beep_detector.py --tag late-beep
    uv run python scripts/eval_beep_detector.py --track full
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path

from splitsmith.beep_calibration import (
    BeepFixtureEntry,
    EvalSummary,
    FixtureEvalResult,
    evaluate_detection,
    load_manifest,
    summarize,
)
from splitsmith.beep_detect import BeepNotFoundError, detect_beep, load_audio
from splitsmith.config import BeepDetectConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "beep_calibration" / "manifest.yaml"


def run_one(
    *,
    stem: str,
    track: str,
    wav_path: Path,
    ground_truth_s: float,
    tolerance_ms: float,
    tags: Iterable[str],
    config: BeepDetectConfig,
) -> FixtureEvalResult:
    """Load ``wav_path``, run the detector, and score the result."""
    try:
        audio, sample_rate = load_audio(wav_path)
    except Exception as exc:  # missing file, codec issue
        return evaluate_detection(
            stem=stem,
            track=track,
            tags=tags,
            ground_truth_s=ground_truth_s,
            tolerance_ms=tolerance_ms,
            detected_time_s=None,
            detected_score=None,
            error_kind=f"load_failed: {exc!s}",
        )

    try:
        result = detect_beep(audio, sample_rate, config)
    except BeepNotFoundError:
        return evaluate_detection(
            stem=stem,
            track=track,
            tags=tags,
            ground_truth_s=ground_truth_s,
            tolerance_ms=tolerance_ms,
            detected_time_s=None,
            detected_score=None,
            error_kind="not_found",
        )
    except Exception as exc:  # config / numeric edge cases
        return evaluate_detection(
            stem=stem,
            track=track,
            tags=tags,
            ground_truth_s=ground_truth_s,
            tolerance_ms=tolerance_ms,
            detected_time_s=None,
            detected_score=None,
            error_kind=f"exception: {exc!s}",
        )

    winner_score = result.candidates[0].score if result.candidates else None
    runner_up_times = [c.time for c in result.candidates[1:]]
    return evaluate_detection(
        stem=stem,
        track=track,
        tags=tags,
        ground_truth_s=ground_truth_s,
        tolerance_ms=tolerance_ms,
        detected_time_s=result.time,
        detected_score=winner_score,
        candidate_times_s=runner_up_times,
    )


def evaluate_entry(
    entry: BeepFixtureEntry,
    *,
    fixtures_dir: Path,
    config: BeepDetectConfig,
    tracks: tuple[str, ...],
) -> list[FixtureEvalResult]:
    """Run the detector on each requested track for one fixture."""
    out: list[FixtureEvalResult] = []
    if "clip" in tracks:
        out.append(
            run_one(
                stem=entry.stem,
                track="clip",
                wav_path=fixtures_dir / entry.clip_wav,
                ground_truth_s=entry.ground_truth_in_clip,
                tolerance_ms=entry.tolerance_ms,
                tags=entry.tags,
                config=config,
            )
        )
    if "full" in tracks and entry.full_wav and entry.ground_truth_in_full is not None:
        out.append(
            run_one(
                stem=entry.stem,
                track="full",
                wav_path=fixtures_dir / entry.full_wav,
                ground_truth_s=entry.ground_truth_in_full,
                tolerance_ms=entry.tolerance_ms,
                tags=entry.tags,
                config=config,
            )
        )
    return out


def format_row(r: FixtureEvalResult) -> str:
    if r.detected_time_s is None:
        return (
            f"  [{r.track:4}] {r.stem:55} MISS ({r.error_kind})  " f"truth={r.ground_truth_s:.3f}s"
        )
    err_ms = (r.error_s or 0.0) * 1000.0
    flag = "OK" if r.correct_top1 else ("topN" if r.correct_in_topn else "FAIL")
    score = f"  score={r.detected_score:.1f}" if r.detected_score is not None else ""
    return (
        f"  [{r.track:4}] {r.stem:55} {flag:4}  "
        f"det={r.detected_time_s:.3f}s  truth={r.ground_truth_s:.3f}s  "
        f"err={err_ms:+.1f} ms{score}"
    )


def format_bucket(name: str, summary: EvalSummary) -> str:
    return (
        f"  {name:18}  n={summary.total:3}  "
        f"top1={summary.top1_hits:3} ({summary.recall_top1 * 100:5.1f}%)  "
        f"topN={summary.topn_hits:3} ({summary.recall_topn * 100:5.1f}%)  "
        f"miss={summary.not_found:2}  err={summary.exceptions:2}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--manifest", type=Path, default=MANIFEST_PATH, help="Manifest YAML to evaluate."
    )
    parser.add_argument(
        "--track",
        action="append",
        choices=["clip", "full"],
        help="Run only the given tracks (default: both).",
    )
    parser.add_argument(
        "--tag", action="append", help="Restrict to fixtures with this tag (repeatable)."
    )
    parser.add_argument(
        "--stem", action="append", help="Restrict to fixtures with this stem (repeatable)."
    )
    parser.add_argument(
        "--json", type=Path, help="Write the per-fixture results to this JSON file."
    )
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    if not manifest.fixtures:
        raise SystemExit(
            f"manifest empty or missing at {args.manifest}; "
            "run scripts/build_beep_calibration.py first."
        )

    selected: list[BeepFixtureEntry] = []
    for entry in manifest.fixtures:
        if args.tag and not any(t in entry.tags for t in args.tag):
            continue
        if args.stem and entry.stem not in args.stem:
            continue
        selected.append(entry)
    if not selected:
        raise SystemExit("no fixtures matched the filters")

    tracks = tuple(args.track) if args.track else ("clip", "full")
    config = BeepDetectConfig()

    results: list[FixtureEvalResult] = []
    for entry in selected:
        results.extend(
            evaluate_entry(entry, fixtures_dir=FIXTURES_DIR, config=config, tracks=tracks)
        )

    print(f"Evaluated {len(selected)} fixtures across {len(results)} (fixture, track) rows.\n")
    print("Per-fixture results:")
    for r in results:
        print(format_row(r))

    overall = summarize(results)
    print("\nOverall:")
    print(format_bucket("ALL", overall))
    if overall.by_tag:
        print("\nPer tag:")
        for tag in sorted(overall.by_tag):
            print(format_bucket(tag, overall.by_tag[tag]))

    if args.json:
        payload = {
            "config": config.model_dump(),
            "results": [
                {
                    "stem": r.stem,
                    "track": r.track,
                    "tags": list(r.tags),
                    "ground_truth_s": r.ground_truth_s,
                    "tolerance_s": r.tolerance_s,
                    "detected_time_s": r.detected_time_s,
                    "detected_score": r.detected_score,
                    "error_s": r.error_s,
                    "correct_top1": r.correct_top1,
                    "correct_in_topn": r.correct_in_topn,
                    "candidate_count": r.candidate_count,
                    "error_kind": r.error_kind,
                }
                for r in results
            ],
            "summary": {
                "total": overall.total,
                "top1_hits": overall.top1_hits,
                "topn_hits": overall.topn_hits,
                "not_found": overall.not_found,
                "exceptions": overall.exceptions,
                "recall_top1": overall.recall_top1,
                "recall_topn": overall.recall_topn,
                "by_tag": {
                    tag: {
                        "total": s.total,
                        "top1_hits": s.top1_hits,
                        "topn_hits": s.topn_hits,
                        "not_found": s.not_found,
                        "exceptions": s.exceptions,
                        "recall_top1": s.recall_top1,
                        "recall_topn": s.recall_topn,
                    }
                    for tag, s in overall.by_tag.items()
                },
            },
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
