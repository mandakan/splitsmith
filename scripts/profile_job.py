"""Profile a real stage job: audio extract, ensemble detect, encode (issue #796).

Drives the *production* detect path from a real audit doc's parameters -- not
guessed values -- so the wall-clock breakdown reflects a genuine job. Encoding
is timed separately (libx264 vs the auto-selected encoder) since that is the
other candidate hot path.

Usage:
    uv run python scripts/profile_job.py <project_dir> <stage_number> [--camera headcam|handheld|auto]

Reads:
    <project>/audit/stage<N>.json         beep_time, stage_time_seconds, stage_rounds
    <project>/trimmed/stage<N>_cam_*_trimmed.mp4   the trimmed stage clip(s)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from splitsmith.ensemble.api import EnsembleConfig, detect_shots_ensemble, load_ensemble_runtime

SR = 48000
TOL_S = 0.050  # match a detected candidate to an audited shot within 50 ms


def classify_camera(clip: Path) -> str:
    """headcam vs handheld by geometry (per LOCAL_MEDIA.md): the 4K60 rig is
    the third-person handheld; the lower-res clip is the first-person headcam."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(clip),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    width = int(out.split(",")[0])
    return "handheld" if width >= 3840 else "headcam"


def extract_audio(clip: Path) -> tuple[np.ndarray, int]:
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(clip),
                "-ac",
                "1",
                "-ar",
                str(SR),
                "-vn",
                str(wav),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        audio, sr = sf.read(str(wav), dtype="float32")
    return audio, sr


def pick_clip(project: Path, stage: int, camera: str) -> tuple[Path, str]:
    clips = sorted(project.glob(f"trimmed/stage{stage}_cam_*_trimmed.mp4"))
    if not clips:
        raise SystemExit(f"no trimmed clip for stage {stage} in {project}")
    classified = [(c, classify_camera(c)) for c in clips]
    if camera == "auto":
        return classified[0]
    for c, cls in classified:
        if cls == camera:
            return c, cls
    raise SystemExit(f"no {camera} clip for stage {stage}; have {[cls for _, cls in classified]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("stage", type=int)
    ap.add_argument("--camera", default="headcam", choices=["headcam", "handheld", "auto"])
    ap.add_argument("--repeat", type=int, default=2)
    args = ap.parse_args()

    audit = json.loads((args.project / "audit" / f"stage{args.stage}.json").read_text())
    beep = float(audit["beep_time"])
    stage_time = float(audit["stage_time_seconds"])
    expected = (audit.get("stage_rounds") or {}).get("expected")
    audited = sorted(float(s["time"]) for s in audit.get("shots", []))

    clip, cam_class = pick_clip(args.project, args.stage, args.camera)
    print(f"clip        : {clip.name}  [{cam_class}]")
    print(
        f"params      : beep={beep}  stage_time={stage_time}  "
        f"expected={expected}  audited_shots={len(audited)}"
    )

    t0 = time.perf_counter()
    audio, sr = extract_audio(clip)
    t_audio = time.perf_counter() - t0

    # Voter E (visual) is torch-only; the shipped slim ONNX path is voters
    # A/B/C (CLAP + PANN + GBDT), which is what build_onnx_session accelerates.
    t0 = time.perf_counter()
    runtime = load_ensemble_runtime(with_voter_e=False)
    t_load = time.perf_counter() - t0

    cfg = EnsembleConfig()
    best = None
    kept_times: list[float] = []
    for i in range(args.repeat):
        t0 = time.perf_counter()
        result = detect_shots_ensemble(
            audio,
            sr,
            beep,
            stage_time,
            runtime,
            expected_rounds=expected,
            ensemble_config=cfg,
            camera_class=cam_class,
        )
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
        kept = [c for c in result.candidates if c.kept]
        kept_times = sorted(c.time for c in kept)
        print(f"  detect run {i}: {dt:.2f}s  kept={len(kept)}/{len(result.candidates)} candidates")

    # Detected-vs-audited agreement (recall against ground truth).
    matched = sum(any(abs(k - a) <= TOL_S for k in kept_times) for a in audited)
    print(f"\naudio extract : {t_audio:.2f}s")
    print(f"runtime load  : {t_load:.2f}s")
    print(f"detect (best) : {best:.2f}s")
    print(f"recall        : {matched}/{len(audited)} audited shots matched within {int(TOL_S*1000)}ms")
    # Emit machine-readable line for cross-run (CPU vs CUDA) comparison.
    print("KEPT_TIMES=" + ",".join(f"{t:.3f}" for t in kept_times))


if __name__ == "__main__":
    main()
