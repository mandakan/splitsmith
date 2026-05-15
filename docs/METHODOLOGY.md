# Detection methodology

Splitsmith treats *consistency across stages and matches* as more important than matching any other tool's exact timestamps. This document covers what the detector does, why, and how its output relates to a hardware shot timer like the CED7000.

## Beep detection (`beep_detect.py`)

1. Bandpass-filter the audio to `[freq_min_hz, freq_max_hz]` (default 2-5 kHz). Hilbert envelope, smoothed at 40 ms (broad enough to bridge the natural intra-beep dips IPSC tones produce). A separate 10 ms-smoothed envelope is held for rise-foot timing so the smoothing bias doesn't shift the leading edge.
2. **Adaptive cutoff**: a candidate run must clear `max(min_amplitude * peak, noise_floor * noise_factor, min_abs_peak)`. The noise-floor leg recovers handheld / phone clips where the beep is faint in absolute terms but still well above the recording's median noise floor.
3. **Composite scoring**: each candidate run is ranked by `silence_score * tonal_factor * duration_factor`:
   - silence_score = run_peak / max-of-pre-window (preceded by quiet "Are you ready / Stand by" wins over preceded by recent shots)
   - tonal_factor = energy concentration in the IPSC fundamental band (2.2-3.5 kHz) vs the wider search band; demotes broadband shots and steel rings
   - duration_factor = squared ramp 150 -> 300 ms; demotes short transients without rejecting them outright
4. **Adaptive rise-foot leading edge**: walk back from the run's peak while the envelope stays above `max(peak * 5%, noise_floor * 1.5x)`. The noise-floor floor stops the walk from sliding into pre-beep silence on faint beeps where 5 % of the peak is below the floor.
5. **Calibrated confidence in [0, 1]** per candidate -- a weighted blend of tonal purity, duration plausibility, and saturating silence preference, tilted by the margin to the runner-up. Empirically validated against the labelled fixture set under `tests/fixtures/beep_calibration/`: confidence >= 0.7 is right ~95 % of the time. The production UI / MCP use this to gate the **auto-trust** chain (`automation.beep_low_confidence_threshold`, default 0.6); below the threshold the beep lands in the HITL queue.

Calibration evidence + per-confidence-bin precision live under `tests/fixtures/beep_calibration/baseline.json`; rebuild via `scripts/build_beep_calibration.py` after adding new audited fixtures and re-run `scripts/eval_beep_detector.py` to refresh the table.

### Auto-trust + HITL queue (issue #219)

A detected beep with `confidence >= automation.beep_low_confidence_threshold` (default 0.6) flips `beep_reviewed=True` automatically -- the downstream chain (auto-trim, auto-shot-detect-on-beep-verified) fires without a manual review click. Below the threshold the beep stays unreviewed and shows up in the **HITL queue**:

- HTTP: `GET /api/hitl-queue` returns `{items: [...], threshold: float}`.
- SPA: the **Needs review** card on the Ingest page polls the queue and surfaces each item's `suggested_action` text + a one-click "Open" button that scrolls to the relevant stage row.
- MCP: `get_hitl_queue` exposes the same shape; an agent (or `/splitsmith-match`) drives the picks via `select_beep_candidate` / `set_beep_manual` / `mark_beep_reviewed`.

Tune the threshold via `~/.splitsmith/config.yaml`:

```yaml
automation:
  beep_low_confidence_threshold: 0.6   # auto-trust >= this; below is HITL
  shot_detect_on_beep_verified: true   # the existing chain gate
```

Per-project overrides + the resolved provenance badge (CLI > project > global > default) ride on the same automation block.

## Shot detection (`shot_detect.py`)

1. **Skip the first 500 ms after the beep.** Beep tones are 200-400 ms and human reaction + draw is never under 500 ms on a head-mounted recording.
2. **`librosa.onset.onset_detect`** with spectral flux (default `delta=0.07`, `pre_max=post_max=30 ms`) finds onset frames at ~10.7 ms resolution.
3. **80 ms minimum-gap filter** (greedy): drop onsets within 80 ms of a previously-kept one. Catches close echoes from steel/walls.
4. **150 ms echo refractory**: drop subsequent onsets within 150 ms of a kept onset whose peak amplitude is below 40% of the previous peak. Catches lower-amplitude intra-bay echoes.
5. **Half-rise leading edge**: this is the per-shot time you see in outputs. For each kept onset, find the absolute peak `|audio|` in a 30 ms window around the librosa frame, then report the first sample whose `|audio|` reaches **half** that peak. This is the "leading edge" definition used everywhere downstream.

### Why half-rise?

| target | property | why we don't use it |
|---|---|---|
| absolute amplitude threshold (CED7000-style) | simple, fast | sensitive to AGC, distance, gain. A quiet AGC-ducked shot crosses the threshold later in its rise than a loud unducked shot, biasing splits. |
| noise-floor-relative threshold | adapts to recording conditions | depends on a tunable "K times noise" knob; biases earlier on slow-rise transients. |
| **half of the local peak (half-rise)** | uses the burst's own peak as reference, so AGC ducking doesn't bias timing; matches what the eye picks when scrubbing a waveform | (the choice) |

Half-rise is the standard "onset" definition in audio-engineering literature for sharp transients. It is **insensitive** to:
- ambient noise levels (uses peak ratio, not absolute energy)
- camera AGC ducking (a quieter shot still has a peak; half-rise lands at the same fractional point)
- recording gain or distance (peak scales linearly; half scales the same way)

It is **sensitive** to:
- the burst's own profile (sharp transients have a sharp leading edge; gradual transients land later)
- the 30 ms peak-search window (transients longer than 30 ms would have their peak underestimated; not a concern for gunshots)

### Comparing splitsmith times to a CED7000 / Pact / similar

**Don't expect absolute timestamps to match.** A CED7000 typically uses an absolute amplitude threshold; splitsmith uses half-rise. On the same recording the two definitions can differ by 5-15 ms per shot.

**Splits *do* match across recordings.** Because the half-rise definition is internally consistent, the *difference* between two consecutive shot times is comparable across stages, matches, and recording conditions. Any constant per-shot offset cancels in the subtraction. This is the metric that matters for training.

If you ever need to compare absolute times to another timer, expect a small constant offset (typically splitsmith reports 5-15 ms earlier than amplitude-threshold timers because half-rise lands earlier in the rise than a fixed threshold).

## Confidence ranking

Each shot has a `confidence` score = geometric mean of normalized onset strength and normalized peak amplitude (each normalized to the max within the kept set). Sorting CSV rows by confidence ascending puts the most likely false positives (echoes, neighbouring bays) at the top -- fast triage when culling.

Real shots that come right after a long pause are AGC-ducked and rank lower in confidence, so don't blindly delete the bottom-N rows. Eyeball timestamps too.

## Ensemble performance dashboard

The 3-voter shot-detection ensemble is parameterised on a handful of knobs (consensus level, per-voter thresholds, apriori boost, Voter C slack, ...). Sweep them over the audited fixture set and render plots + a detailed report:

```bash
# 1. Build the per-candidate signal table (slow; redo after corpus or
#    feature changes). Without --skip-voter-e it also pulls the CLIP
#    visual probe scores for fixtures whose source video is reachable.
uv run python scripts/build_sweep_signals.py --skip-voter-e

# 2. Replay the voters over a parameter grid (fast; pure numpy).
uv run python scripts/run_sweep.py \
    --grid scripts/sweep_grids/consensus_x_apriori.yaml

# 3. Render plots + a markdown report under build/sweeps/<run_id>/.
uv run python scripts/plot_sweep.py
```

The latest sweep's overview PNG lands at `build/sweeps/latest_overview.png` and its detailed report at `build/sweeps/latest_report.md` (with per-fixture P/R/F1 tables and the full parameter dump). Pre-built grids live in `scripts/sweep_grids/`; the full key vocabulary is documented in `scripts/run_sweep.py`. Two parquet files back the dashboard:

- `build/sweeps/signals.parquet` -- one row per (fixture, candidate) with every raw voter signal + ground-truth label. Invariant under threshold sweeps.
- `build/sweeps/runs.parquet` -- one row per (run_id, parameter combo, fixture) with precision / recall / F1, per-voter solo-correct counts, and effective thresholds.

Latest snapshot:

![latest sweep overview](ensemble_dashboard/latest_overview.png)

(See [`ensemble_dashboard/latest_report.md`](ensemble_dashboard/latest_report.md) for the full per-fixture breakdown + parameter dump that produced it.)

The natural next step for a live dashboard is extending the existing **Algorithm Lab** page (`splitsmith ui --lab`) to read `runs.parquet` directly -- it already owns the fixture-eval + live-tuning surface. Standing up a separate Streamlit / marimo app would duplicate that infrastructure.
