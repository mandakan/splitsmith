"""Two-pass detection prototype: librosa first, Ricker CWT in suspicious gaps.

Status: PROTOTYPE. Not wired into the production pipeline. Kept for reference
if/when we want to revisit a recall fallback for librosa misses on busy audio
(see issue #6).

Approach:
1. Run the existing detect_shots() pipeline (mel-band spectral flux + min-gap
   + echo refractory + rise-foot leading edge) -- the cheap sparse first pass.
2. Compute |Ricker CWT| max-across-scales over the whole stage segment.
3. Sample CWT response at every pass-1 candidate; take the lower-quartile
   value as the "real shot" CWT strength threshold.
4. Find suspicious gaps in the pass-1 candidate sequence: gap > gap_factor *
   median_split or > gap_min_ms (whichever is larger).
5. Inside each suspicious gap, find local maxima of the CWT envelope above
   the strength threshold; emit them as extra candidates.

Findings on the four audited fixtures:
- Recovers the persistent Stage 7 t=6.6399 librosa miss (recall 71/71).
- Cost: even at the tightest strength quantile (q=0.75) this adds 64 extra
  candidates total across the four fixtures (~22 % over baseline 289).
  Stage 2 is the worst case -- its natural long pauses (mag change, target
  transitions) get flagged as suspicious gaps and CWT fires on whatever
  ambient transients live there.
- Stage 3 (calm, well-spaced): adds 0 extras at any threshold.

Why it's not the production answer:
- The user prefers fewer candidates over higher recall. Two-pass detection
  trades 1 missed shot for 64+ extra candidates -- wrong direction.
- The deeper failure mode (high ambient floor relative to real shots) is
  better attacked by pre-processing the audio so the existing single-pass
  detector fires cleanly. See ``scripts/eval_whitening.py``.

Run:
    uv run python scripts/eval_cwt_twopass.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.shot_detect import _POST_BEEP_SKIP_S, detect_shots

FIX = Path("tests/fixtures")
NAMES = [
    "stage-shots",
    "stage-shots-blacksmith-h5",
    "stage-shots-tallmilan-stage2",
    "stage-shots-tallmilan-stage7",
]
TOL_MS = 75.0


def ricker(width: int) -> np.ndarray:
    """Mexican-hat (Ricker) wavelet of given half-width sigma in samples."""
    n = int(8 * width)
    if n % 2 == 0:
        n += 1
    t = np.arange(n) - n // 2
    a = (t / width) ** 2
    norm = 2.0 / (np.sqrt(3.0 * width) * np.pi**0.25)
    return norm * (1.0 - a) * np.exp(-a / 2.0)


def cwt_envelope(audio: np.ndarray, sr: int) -> np.ndarray:
    """Max-across-scales |Ricker CWT|. Scales sized for gunshot-like rise times."""
    scales = [12, 24, 48, 96, 192]  # ~0.25, 0.5, 1, 2, 4 ms at 48 kHz
    out = np.zeros((len(scales), audio.size), dtype=np.float64)
    sig = audio.astype(np.float64)
    for k, w in enumerate(scales):
        psi = ricker(w)
        out[k] = fftconvolve(sig, psi[::-1], mode="same")
    return np.abs(out).max(axis=0)


def two_pass_detect(
    audio: np.ndarray,
    sr: int,
    beep: float,
    stage_t: float,
    *,
    gap_factor: float = 3.0,
    gap_min_ms: int = 250,
    shot_strength_quantile: float = 0.75,
    min_extra_gap_s: float = 0.080,
) -> tuple[list[float], list[float]]:
    shots = detect_shots(audio, sr, beep, stage_t, ShotDetectConfig())
    pass1_t = sorted(s.time_absolute for s in shots)
    if len(pass1_t) < 2:
        return pass1_t, []
    env = cwt_envelope(audio, sr)
    pad = int(0.005 * sr)
    p1_strengths = [
        float(env[max(0, int(t * sr) - pad) : min(env.size, int(t * sr) + pad)].max())
        for t in pass1_t
    ]
    threshold = float(np.quantile(p1_strengths, shot_strength_quantile))
    splits = [pass1_t[i + 1] - pass1_t[i] for i in range(len(pass1_t) - 1)]
    median_split = float(np.median(splits))
    gap_thresh = max(median_split * gap_factor, gap_min_ms / 1000.0)
    boundaries = [beep + _POST_BEEP_SKIP_S] + pass1_t + [beep + stage_t + 1.0]
    extras: list[float] = []
    for i in range(len(boundaries) - 1):
        a, b = boundaries[i], boundaries[i + 1]
        if b - a <= gap_thresh:
            continue
        i0 = max(0, int((a + 0.020) * sr))
        i1 = min(env.size, int((b - 0.020) * sr))
        if i1 - i0 < int(0.020 * sr):
            continue
        win_env = env[i0:i1]
        above = win_env > threshold
        last_p = -(10**9)
        min_gap_samples = int(min_extra_gap_s * sr)
        in_run = False
        run_start = 0
        for j in range(win_env.size):
            if above[j]:
                if not in_run:
                    in_run, run_start = True, j
            else:
                if in_run:
                    am = run_start + int(np.argmax(win_env[run_start:j]))
                    if am - last_p >= min_gap_samples:
                        extras.append((i0 + am) / sr)
                        last_p = am
                    in_run = False
        if in_run:
            am = run_start + int(np.argmax(win_env[run_start:]))
            if am - last_p >= min_gap_samples:
                extras.append((i0 + am) / sr)
    extras = sorted(p for p in extras if all(abs(p - q) > 0.080 for q in pass1_t))
    return pass1_t, extras


def evaluate(name: str, **kw) -> dict:
    truth = json.loads((FIX / f"{name}.json").read_text())
    audio, sr = load_audio(FIX / f"{name}.wav")
    pre_path = FIX / f"{name}.json.before-rise-foot"
    gt_src = json.loads(pre_path.read_text()) if pre_path.exists() else truth
    gt = sorted(s["time"] for s in gt_src.get("shots", []))
    pass1, extras = two_pass_detect(
        audio, sr, truth["beep_time"], truth["stage_time_seconds"], **kw
    )
    all_t = sorted(pass1 + extras)
    used: set[int] = set()
    drags: list[float] = []
    misses: list[float] = []
    for t in gt:
        best_i = best_d = None
        for i, c in enumerate(all_t):
            if i in used:
                continue
            d = abs(c - t) * 1000.0
            if d <= TOL_MS and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is None:
            misses.append(t)
        else:
            used.add(best_i)
            drags.append(best_d)
    return {
        "pass1": len(pass1),
        "extras": len(extras),
        "total": len(all_t),
        "matched": len(drags),
        "misses": misses,
        "gt": len(gt),
    }


def main() -> None:
    for q in [0.25, 0.50, 0.75]:
        print(f"\n[shot_strength_quantile={q}, gap_factor=3.0, gap_min_ms=250]")
        tot_p1 = tot_ex = tot_match = tot_gt = 0
        for name in NAMES:
            r = evaluate(
                name, gap_factor=3.0, gap_min_ms=250, shot_strength_quantile=q
            )
            miss_s = ", ".join(f"{t:.4f}" for t in r["misses"]) or "-"
            rec = r["matched"] / r["gt"] if r["gt"] else 0
            print(
                f"  {name:38s} pass1={r['pass1']:3d} extras={r['extras']:3d} "
                f"total={r['total']:3d} recall={rec*100:5.1f}% "
                f"mis={len(r['misses'])} {miss_s}"
            )
            tot_p1 += r["pass1"]
            tot_ex += r["extras"]
            tot_match += r["matched"]
            tot_gt += r["gt"]
        print(
            f"  {'TOTAL':38s} pass1={tot_p1:3d} extras={tot_ex:3d} "
            f"total={tot_p1+tot_ex:3d} recall={tot_match/tot_gt*100:5.1f}% "
            f"mis={tot_gt-tot_match}"
        )


if __name__ == "__main__":
    main()
