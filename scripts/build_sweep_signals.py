"""Materialise the per-candidate signal table used by sweep / plot scripts.

Writes ``build/sweeps/signals.parquet`` -- one row per (fixture,
candidate_idx) carrying every per-voter raw signal *and* the ground-
truth label. The table is invariant under threshold / consensus
sweeps, so ``run_sweep.py`` can replay millions of voter combinations
over it in seconds without ever touching CLAP / PANN / the WAV files
again.

What's included per row:

* ``label`` (0/1) -- greedy nearest-time match to ``shots[].time`` in
  the audit JSON, within ``--tolerance-ms``.
* Voter A inputs -- detector ``confidence``, ``peak_amplitude``.
* Voter B input -- ``clap_diff`` (shot - not-shot prompt similarity).
* Voter C inputs -- ``score_c`` (shipped GBDT probability), every
  hand-crafted feature column, every per-prompt CLAP similarity.
* Voter D input -- ``gunshot_prob`` (PANN).
* Voter E input -- ``voter_e_signal`` (CLIP probe P(shot)) when the
  source video can be located; ``NaN`` otherwise.
* Stage prior -- ``expected_rounds`` from ``stage_rounds.expected`` if
  present, and ``audit_count`` from the labelled positives.
* Provenance -- ``signals_build_id`` (ISO timestamp + git short SHA)
  and ``camera_class``.

Re-run after adding new audited fixtures, changing the CLAP prompt
bank, or retraining Voter C's GBDT. The ``CLAP`` and ``PANN`` caches
under ``tests/fixtures/.cache/`` must already exist; build them with
``scripts/extract_clap_features.py`` + ``scripts/extract_audio_embeddings.py``
the same way the calibration pipeline expects.

Run:
    uv run python scripts/build_sweep_signals.py
    uv run python scripts/build_sweep_signals.py --skip-voter-e
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

# Make sibling scripts importable as plain modules (the scripts/
# directory is not a package).
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Import the same canonical fixture list calibration uses so the sweep
# universe stays in lockstep with the shipped artifacts. Underscore-
# prefixed in build_ensemble_artifacts; pulling it directly keeps a
# single source of truth.
from build_ensemble_artifacts import (  # type: ignore[import-not-found]
    DEFAULT_FIXTURES,
    WRONG_CLIP_FIXTURES,
)

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.ensemble import features as feat
from splitsmith.ensemble import visual as vis
from splitsmith.ensemble.calibration import (
    DEFAULT_CAMERA_CLASS,
    camera_class_from_mount,
    load_calibration,
    load_voter_c_model,
    load_voter_e_probe,
)
from splitsmith.ensemble.tta import compute_tta_agreement
from splitsmith.shot_detect import detect_shots

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
FULL_DIR = FIXTURES_DIR / "full"
CACHE_DIR = FIXTURES_DIR / ".cache"
OUT_DIR = REPO_ROOT / "build" / "sweeps"

# Same private tuple build_ensemble_artifacts uses; importing the
# underscore name is the only way to keep column ordering aligned with
# the GBDT's expected feature layout.
HAND_FEATURE_NAMES: tuple[str, ...] = feat._HAND_FEATURE_NAMES  # noqa: SLF001


def _git_short_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, stderr=subprocess.DEVNULL
        )
        return out.decode().strip()
    except Exception:
        return "nogit"


def _label(cand_t: list[float], truth_shots: list[dict], tol_ms: float) -> list[int]:
    labels = [0] * len(cand_t)
    used: set[int] = set()
    for s in sorted(truth_shots, key=lambda x: x["time"]):
        t = s["time"]
        best_i, best_d = None, None
        for i, c in enumerate(cand_t):
            if i in used:
                continue
            d = abs(c - t) * 1000.0
            if d <= tol_ms and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is not None:
            used.add(best_i)
            labels[best_i] = 1
    return labels


def _locate_source_video(truth: dict, fixture: str) -> tuple[Path, float] | None:
    """Return ``(video_path, source_beep_time)`` for Voter E, or None.

    The audit JSON carries the original source video filename + beep
    timestamp under various legacy shapes; look at the common ones and
    fall back to ``None`` so the caller can skip Voter E for that row.
    """
    source = truth.get("source") or truth.get("video") or {}
    if not isinstance(source, dict):
        return None
    name = source.get("path") or source.get("filename") or source.get("file")
    beep = source.get("beep_time") or source.get("source_beep_time")
    if not name or beep is None:
        return None
    candidates = [
        FULL_DIR / name,
        FIXTURES_DIR / name,
        REPO_ROOT / name,
    ]
    for p in candidates:
        if p.exists():
            return p, float(beep)
    return None


def build_signals(
    fixtures: list[str],
    tolerance_ms: float,
    *,
    skip_voter_e: bool,
) -> tuple[pa.Table, dict]:
    """Produce the signals table + sidecar provenance dict."""
    cal = load_calibration()
    voter_c_model = load_voter_c_model()

    visual_runtime: vis.VisualRuntime | None = None
    if not skip_voter_e and cal.voter_e_probe_artifact:
        probe = load_voter_e_probe(cal.voter_e_probe_artifact)
        if probe is not None:
            visual_runtime = vis.load_visual_runtime(
                probe,
                model_id=cal.voter_e_clip_model_id or vis.CLIP_VISUAL_MODEL_ID,
            )

    rows: list[dict] = []
    fixtures_seen: list[str] = []
    fixtures_skipped: list[tuple[str, str]] = []
    fixtures_with_voter_e: list[str] = []

    for fix in fixtures:
        if fix in WRONG_CLIP_FIXTURES:
            fixtures_skipped.append((fix, "wrong_clip_suspected"))
            continue
        truth_path = FIXTURES_DIR / f"{fix}.json"
        wav_path = FIXTURES_DIR / f"{fix}.wav"
        clap_path = CACHE_DIR / f"{fix}_clap.npz"
        pann_path = CACHE_DIR / f"{fix}_pann.npz"
        if not (truth_path.exists() and wav_path.exists()):
            fixtures_skipped.append((fix, "missing_fixture_files"))
            continue
        if not (clap_path.exists() and pann_path.exists()):
            fixtures_skipped.append((fix, "missing_clap_or_pann_cache"))
            continue

        truth = json.loads(truth_path.read_text())
        camera_block = truth.get("camera") or {}
        cam_class = camera_class_from_mount(camera_block.get("mount"))
        audio, sr = load_audio(wav_path)
        cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
        shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg)
        if not shots:
            fixtures_skipped.append((fix, "no_candidates"))
            continue

        times = np.array([s.time_absolute for s in shots], dtype=np.float64)
        confidences = np.array([s.confidence for s in shots], dtype=np.float64)
        peak_amps = np.array([s.peak_amplitude for s in shots], dtype=np.float64)
        ms_after_beep = np.array([round(s.time_from_beep * 1000) for s in shots], dtype=np.int64)
        labels = _label(times.tolist(), truth.get("shots", []), tolerance_ms)
        audit_count = int(sum(labels))
        rounds = truth.get("stage_rounds") or {}
        expected_rounds = (
            int(rounds["expected"])
            if isinstance(rounds, dict) and rounds.get("expected") is not None
            else None
        )

        clap = np.load(clap_path, allow_pickle=True)
        if clap["audio_emb"].shape[0] != len(shots):
            raise SystemExit(f"{fix}: CLAP cache stale; re-run extract_clap_features.py --force")
        prompts_in_cache = [str(p) for p in clap["prompts"].tolist()]
        if tuple(prompts_in_cache) != feat.CLAP_PROMPTS:
            raise SystemExit(
                f"{fix}: CLAP cache prompt order mismatch; re-run extract_clap_features.py --force"
            )
        clap_sims = clap["text_sims"]
        clap_diff = feat.clap_diff_from_similarities(clap_sims)

        pann = np.load(pann_path)
        if pann["gunshot_prob"].shape[0] != len(shots):
            raise SystemExit(f"{fix}: PANN cache stale; re-run extract_audio_embeddings.py --force")
        gunshot_prob = pann["gunshot_prob"]

        tta_agreement = compute_tta_agreement(
            audio, sr, truth["beep_time"], truth["stage_time_seconds"], times
        )
        hand = feat.compute_hand_features(
            audio, sr, times, truth["beep_time"], confidences, peak_amps, tta_agreement
        )
        x = feat.voter_c_feature_matrix(hand, clap_sims, clap_diff, gunshot_prob, camera_classes=cam_class)
        cls_key = cam_class if cam_class in voter_c_model else cal.default_camera_class
        score_c = voter_c_model[cls_key].predict_proba(x)[:, 1].astype(np.float64)

        n = len(shots)
        voter_e_signal = np.full(n, np.nan, dtype=np.float64)
        voter_e_ok = False
        if visual_runtime is not None:
            located = _locate_source_video(truth, fix)
            if located is not None:
                video_path, source_beep_time = located
                source_times = vis.candidate_times_in_source(
                    times,
                    audit_beep_in_clip=float(truth["beep_time"]),
                    source_beep_time=source_beep_time,
                )
                offsets = (
                    tuple(cal.voter_e_frame_offsets)
                    if cal.voter_e_frame_offsets
                    else vis.DEFAULT_FRAME_OFFSETS
                )
                features = vis.compute_visual_features(
                    video_path, source_times, visual_runtime, frame_offsets=offsets
                )
                voter_e_signal = vis.score_visual_candidates(features, visual_runtime).astype(np.float64)
                voter_e_ok = True
                fixtures_with_voter_e.append(fix)

        for i in range(n):
            row: dict = {
                "fixture": fix,
                "camera_class": cam_class,
                "candidate_idx": i,
                "t_absolute": float(times[i]),
                "t_from_beep": float(shots[i].time_from_beep),
                "ms_after_beep": int(ms_after_beep[i]),
                "label": int(labels[i]),
                "confidence": float(confidences[i]),
                "peak_amplitude": float(peak_amps[i]),
                "tta_agreement": float(tta_agreement[i]),
                "clap_diff": float(clap_diff[i]),
                "score_c": float(score_c[i]),
                "gunshot_prob": float(gunshot_prob[i]),
                "voter_e_signal": float(voter_e_signal[i]),
                "voter_e_available": bool(voter_e_ok),
                "expected_rounds": expected_rounds,
                "audit_count": audit_count,
            }
            for j, name in enumerate(HAND_FEATURE_NAMES):
                row[f"hand_{name}"] = float(hand[i, j])
            for j, _prompt in enumerate(feat.CLAP_PROMPTS):
                row[f"clap_sim_{j:02d}"] = float(clap_sims[i, j])
            rows.append(row)
        fixtures_seen.append(fix)

    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    sha = _git_short_sha()
    signals_build_id = f"{now}_{sha}"

    for row in rows:
        row["signals_build_id"] = signals_build_id

    if not rows:
        raise SystemExit("No fixtures produced any candidates; nothing to write.")

    table = pa.Table.from_pylist(rows)
    sidecar = {
        "signals_build_id": signals_build_id,
        "built_at": dt.datetime.now(dt.UTC).isoformat(),
        "git_short_sha": sha,
        "tolerance_ms": tolerance_ms,
        "default_camera_class": DEFAULT_CAMERA_CLASS,
        "clap_prompts": list(feat.CLAP_PROMPTS),
        "hand_feature_names": list(HAND_FEATURE_NAMES),
        "fixtures_included": fixtures_seen,
        "fixtures_skipped": [{"fixture": f, "reason": r} for f, r in fixtures_skipped],
        "fixtures_with_voter_e": fixtures_with_voter_e,
        "skip_voter_e": skip_voter_e,
        "n_rows": len(rows),
        "n_positives": int(sum(r["label"] for r in rows)),
    }
    return table, sidecar


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--fixture",
        action="append",
        help="Restrict to a specific fixture stem (repeatable). Default: full DEFAULT_FIXTURES set.",
    )
    p.add_argument("--tolerance-ms", type=float, default=75.0)
    p.add_argument(
        "--skip-voter-e",
        action="store_true",
        help="Skip CLIP visual probe extraction. Voter E columns become NaN.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=OUT_DIR / "signals.parquet",
        help="Output parquet path. Default: build/sweeps/signals.parquet",
    )
    args = p.parse_args()

    fixtures = args.fixture or list(DEFAULT_FIXTURES)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    table, sidecar = build_signals(fixtures, args.tolerance_ms, skip_voter_e=args.skip_voter_e)
    pq.write_table(table, args.out)
    sidecar_path = args.out.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(sidecar, indent=2))

    print(
        f"wrote {args.out}  ({sidecar['n_rows']} rows, "
        f"{sidecar['n_positives']} positives, "
        f"{len(sidecar['fixtures_included'])} fixtures)"
    )
    print(f"wrote {sidecar_path}")
    if sidecar["fixtures_skipped"]:
        print("skipped:")
        for s in sidecar["fixtures_skipped"]:
            print(f"  {s['fixture']}: {s['reason']}")
    if not args.skip_voter_e:
        print(
            f"Voter E columns populated for {len(sidecar['fixtures_with_voter_e'])} "
            f"fixtures; NaN for the rest (missing source video)."
        )


if __name__ == "__main__":
    main()
