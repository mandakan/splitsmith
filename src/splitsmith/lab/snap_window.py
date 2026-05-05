"""Cross-camera snap-window measurement (issue #122).

Pure function that, given an audited anchor's beep + shot times and a
secondary camera's beep + Voter-A-positive candidate universe, snaps
each anchor shot to the nearest secondary candidate within a wide
window and reports the displacement plus monotonicity / min-spacing
sanity flags.

The output is the raw material for the empirical answer to "what is the
right snap window for promote-from-anchor?". Wide window (default
+/-200 ms) is intentional: the goal is to see the actual displacement
distribution, not to enforce a tight tolerance.

Why Voter-A-positive only:
A naive nearest-peak snap would happily lock onto brass ejection,
plate clang, or footsteps and pollute the histogram. Voter A is the
calibrated candidate generator -- snapping over its positive set
measures displacement on things that plausibly *are* shots.

Sanity filters surface, but do not resolve, ambiguity:
* ``no-candidate``: nothing inside the window. The detector missed it
  on this camera, or the predicted location is outside the recording.
* ``monotonicity``: snap[i+1] <= snap[i]. Two anchor shots collapsed
  onto the same (or out-of-order) candidate -- a wide-window failure
  mode worth measuring, not silently fixing.
* ``min-spacing``: 0 < snap[i+1] - snap[i] < min_spacing. Adjacent snaps
  too close to be physically distinct shots; usually means a sub-split
  pair that the detector merged.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel


class SnapResult(BaseModel):
    """One anchor shot's snap outcome on the secondary camera."""

    shot_number: int
    anchor_time: float
    predicted_time: float
    snapped_time: float | None
    displacement_ms: float | None
    snap_confidence: float | None
    time_since_beep_s: float
    sanity_flag: str  # "" | "no-candidate" | "monotonicity" | "min-spacing"


def snap_anchor_shots(
    anchor_beep_time: float,
    anchor_shots: list[float],
    secondary_beep_time: float,
    voter_a_candidates: list[tuple[float, float]],
    *,
    window_ms: float = 200.0,
    min_spacing_ms: float = 80.0,
) -> list[SnapResult]:
    """Snap each anchor shot to the nearest secondary Voter-A-positive candidate.

    Args:
        anchor_beep_time: beep timestamp in the anchor's clip-local time, seconds.
        anchor_shots: anchor shot times in the anchor's clip-local time, seconds.
        secondary_beep_time: where the anchor's beep lands on the secondary,
            from cross-correlation alignment.
        voter_a_candidates: list of ``(time_seconds, confidence)`` for every
            Voter-A-positive candidate the secondary's ensemble detector
            produced.
        window_ms: half-width of the snap window. Anchor shots predict a
            secondary time of ``secondary_beep + (anchor_t - anchor_beep)``;
            candidates outside +/- ``window_ms`` are ignored.
        min_spacing_ms: adjacent snapped times closer than this are
            flagged ``min-spacing``. Defaults to 80 ms (just under the
            tightest IPSC sub-split commonly observed).

    Returns:
        One :class:`SnapResult` per anchor shot, in input order.
    """
    window_s = window_ms / 1000.0
    candidates = sorted(voter_a_candidates, key=lambda c: c[0])
    candidate_times = [c[0] for c in candidates]

    results: list[SnapResult] = []
    for i, anchor_t in enumerate(anchor_shots, start=1):
        predicted = secondary_beep_time + (anchor_t - anchor_beep_time)
        time_since_beep = anchor_t - anchor_beep_time

        best_idx: int | None = None
        best_dist = window_s
        for j, ct in enumerate(candidate_times):
            dist = abs(ct - predicted)
            if dist <= best_dist:
                best_dist = dist
                best_idx = j

        if best_idx is None:
            results.append(
                SnapResult(
                    shot_number=i,
                    anchor_time=anchor_t,
                    predicted_time=predicted,
                    snapped_time=None,
                    displacement_ms=None,
                    snap_confidence=None,
                    time_since_beep_s=time_since_beep,
                    sanity_flag="no-candidate",
                )
            )
            continue

        snap_t = candidate_times[best_idx]
        results.append(
            SnapResult(
                shot_number=i,
                anchor_time=anchor_t,
                predicted_time=predicted,
                snapped_time=snap_t,
                displacement_ms=(snap_t - predicted) * 1000.0,
                snap_confidence=candidates[best_idx][1],
                time_since_beep_s=time_since_beep,
                sanity_flag="",
            )
        )

    min_spacing_s = min_spacing_ms / 1000.0
    for k in range(len(results) - 1):
        a, b = results[k], results[k + 1]
        if a.snapped_time is None or b.snapped_time is None:
            continue
        gap = b.snapped_time - a.snapped_time
        if gap <= 0:
            for r in (a, b):
                if r.sanity_flag == "":
                    r.sanity_flag = "monotonicity"
        elif gap < min_spacing_s:
            for r in (a, b):
                if r.sanity_flag == "":
                    r.sanity_flag = "min-spacing"

    return results


_GUIDED_ENVELOPE_SR = 1000  # 1 kHz envelope -> 1 ms snap resolution.


def _peak_envelope(audio: np.ndarray, source_sr: int, target_sr: int) -> np.ndarray:
    """Block-MAX of ``|audio|`` downsampled to ``target_sr``.

    For ground-truth fixture creation we want the snap to land on the
    actual amplitude peak of the transient (the gunshot impulse), not
    on the centroid of a smoothed envelope. Hilbert + block-mean spread
    the energy and shifted argmax away from the peak -- block-max on
    raw ``|audio|`` preserves the loudest sample's position to within
    one downsampled bin (1 ms at target_sr=1000).
    """
    if audio.ndim != 1:
        raise ValueError("audio must be mono")
    abs_a = np.abs(audio).astype(np.float32, copy=False)
    block = max(1, source_sr // target_sr)
    trimmed = (abs_a.size // block) * block
    if trimmed == 0:
        return np.zeros(0, dtype=np.float32)
    return abs_a[:trimmed].reshape(-1, block).max(axis=1)


def guided_snap_anchor_shots(
    anchor_beep_time: float,
    anchor_shots: list[float],
    secondary_beep_time: float,
    secondary_audio: np.ndarray,
    secondary_sr: int,
    *,
    window_ms: float = 60.0,
    min_spacing_ms: float = 80.0,
    noise_floor_ratio: float = 3.0,
) -> list[SnapResult]:
    """Snap anchor shots to local envelope peaks within ``window_ms``.

    The anchor + known beep math gives us a tight prior on each shot's
    secondary clip-local time. Within that window, the question is no
    longer "is there a shot?" but "where exactly is the peak?". So we
    drop the calibrated detector threshold (Voter A's confidence floor,
    tuned for headcams) and just localize the maximum of the secondary
    audio's Hilbert envelope. Phone-cam shots that Voter A filters out
    on amplitude grounds still snap because we trust the anchor.

    Args:
        anchor_beep_time: anchor clip-local beep time, seconds.
        anchor_shots: anchor clip-local shot times, seconds.
        secondary_beep_time: secondary clip-local beep time, seconds.
        secondary_audio: mono float32 audio, full secondary clip.
        secondary_sr: source sample rate of ``secondary_audio``.
        window_ms: half-width of the localization window. Tighter than
            the threshold-based snap because we trust the prior more
            (the anchor is already audited and the offset is exact).
        min_spacing_ms: adjacent snaps closer than this get a flag.
        noise_floor_ratio: peak amplitudes below this multiple of the
            envelope's median get a ``low-amplitude`` sanity flag.

    Returns one :class:`SnapResult` per anchor shot. ``snap_confidence``
    is the peak amplitude (0..1-ish), ``sanity_flag`` records
    monotonicity / spacing / amplitude issues.
    """
    envelope = _peak_envelope(secondary_audio, secondary_sr, _GUIDED_ENVELOPE_SR)
    if envelope.size == 0:
        return [
            SnapResult(
                shot_number=i,
                anchor_time=anchor_t,
                predicted_time=secondary_beep_time + (anchor_t - anchor_beep_time),
                snapped_time=None,
                displacement_ms=None,
                snap_confidence=None,
                time_since_beep_s=anchor_t - anchor_beep_time,
                sanity_flag="no-candidate",
            )
            for i, anchor_t in enumerate(anchor_shots, start=1)
        ]

    duration_s = envelope.size / _GUIDED_ENVELOPE_SR
    window_s = window_ms / 1000.0
    median_amp = float(np.median(envelope))
    noise_floor = median_amp * noise_floor_ratio

    results: list[SnapResult] = []
    for i, anchor_t in enumerate(anchor_shots, start=1):
        predicted = secondary_beep_time + (anchor_t - anchor_beep_time)
        time_since_beep = anchor_t - anchor_beep_time

        # Window outside the recording -> no candidate.
        if predicted + window_s < 0 or predicted - window_s > duration_s:
            results.append(
                SnapResult(
                    shot_number=i,
                    anchor_time=anchor_t,
                    predicted_time=predicted,
                    snapped_time=None,
                    displacement_ms=None,
                    snap_confidence=None,
                    time_since_beep_s=time_since_beep,
                    sanity_flag="no-candidate",
                )
            )
            continue

        start_idx = max(0, int(round((predicted - window_s) * _GUIDED_ENVELOPE_SR)))
        end_idx = min(envelope.size, int(round((predicted + window_s) * _GUIDED_ENVELOPE_SR)))
        if end_idx <= start_idx:
            results.append(
                SnapResult(
                    shot_number=i,
                    anchor_time=anchor_t,
                    predicted_time=predicted,
                    snapped_time=None,
                    displacement_ms=None,
                    snap_confidence=None,
                    time_since_beep_s=time_since_beep,
                    sanity_flag="no-candidate",
                )
            )
            continue

        local = envelope[start_idx:end_idx]
        peak_offset = int(np.argmax(local))
        peak_amp = float(local[peak_offset])
        snap_t = (start_idx + peak_offset) / _GUIDED_ENVELOPE_SR

        sanity = "" if peak_amp >= noise_floor else "low-amplitude"
        results.append(
            SnapResult(
                shot_number=i,
                anchor_time=anchor_t,
                predicted_time=predicted,
                snapped_time=snap_t,
                displacement_ms=(snap_t - predicted) * 1000.0,
                snap_confidence=peak_amp,
                time_since_beep_s=time_since_beep,
                sanity_flag=sanity,
            )
        )

    min_spacing_s = min_spacing_ms / 1000.0
    for k in range(len(results) - 1):
        a, b = results[k], results[k + 1]
        if a.snapped_time is None or b.snapped_time is None:
            continue
        gap = b.snapped_time - a.snapped_time
        if gap <= 0:
            for r in (a, b):
                if r.sanity_flag == "":
                    r.sanity_flag = "monotonicity"
        elif gap < min_spacing_s:
            for r in (a, b):
                if r.sanity_flag == "":
                    r.sanity_flag = "min-spacing"

    return results
