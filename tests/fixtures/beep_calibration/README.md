# Beep calibration suite

Layer-1 deliverable for [#220](../../../../issues/220) (`beep: improve
detection accuracy`). This directory holds the labeled fixture index +
baseline scores that the layer-2 detector improvements measure against.

## Files

* `manifest.yaml` — committed. Per-fixture ground truth + tags. Generated
  by `scripts/build_beep_calibration.py` from `tests/fixtures/*.json`.
  Hand-edit the `tags` list to add fine-grained failure-mode buckets
  (e.g. `cross-bay`, `steel-fp-observed`, `low-spl`); rebuild with
  `--preserve-tags` to keep them on a re-run.
* `baseline.json` — committed. Snapshot of the current detector's
  recall / per-tag stats. Compare against future runs.

## Tracks

Each fixture supports up to two evaluation tracks:

* **clip** — the post-trim WAV in `tests/fixtures/<stem>.wav`. Always
  available. Headcam clips have ~0.5 s pre-beep; iPhone clips have ~5 s.
* **full** — the wide-window WAV in `tests/fixtures/full/<stem>_full.wav`.
  Optional. Produced by `scripts/extract_full_fixture_audio.py` from
  the source MP4 listed in `tests/fixtures/full/_sources.yaml`. Covers
  the late-beep / cross-bay scenarios that don't appear in the clip.

iPhone fixtures have no source MP4 wired up, so they are clip-only.

## Usage

Rebuild manifest after adding new audited fixtures or extracting more
full-source audio:

```bash
uv run python scripts/build_beep_calibration.py
uv run python scripts/build_beep_calibration.py --preserve-tags  # keep hand-edits
```

Run the detector against the suite and print recall per bucket:

```bash
uv run python scripts/eval_beep_detector.py
uv run python scripts/eval_beep_detector.py --tag handheld
uv run python scripts/eval_beep_detector.py --track full
uv run python scripts/eval_beep_detector.py --json out/run.json
```

## Auto-tag rules

* `handheld` / `headcam` — from `audit.camera.mount`.
* `late-beep` — beep > 10 s into source (full track only).
* `very-late-beep` — beep > 30 s into source (current detector hard-fails
  due to its 30 s `search_window_s` cap).
* `steel-prone` — `stage_rounds.plates + stage_rounds.poppers >= 1`.

Add `cross-bay`, `steel-fp-observed`, `low-spl`, `ro-chatter`, etc. by
hand once a fixture is reviewed.
