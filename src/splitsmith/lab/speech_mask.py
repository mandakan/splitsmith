"""Speech masks for fixture audio before anything is published (#1778).

The fixture corpus holds other shooters' recordings, and often the voice of
whoever held the camera. Before a clip reaches a public corpus, speech is
found, proposed as masks, and a person reviews the proposal.

Pipeline, each step separate so the pure parts test without the model:

1. :func:`speech_probabilities` runs Silero VAD (MIT, an ONNX graph run
   through ``onnxruntime``, which the ensemble already needs) over 16 kHz
   audio and returns one speech probability per 32 ms frame.
2. :func:`speech_regions` turns those into regions with hysteresis, a
   minimum length, merging and padding (:class:`SpeechMaskConfig`).
3. :func:`propose_masks` removes the protected windows around every
   labelled shot and the beep. A mask never covers a shot; speech that
   reached into a protected window is listed for the reviewer instead.

Nothing here edits audio or a fixture: the proposal is data, and the
review step decides.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel

from ..config import SpeechMaskConfig
from ..models.manifest import ArtifactSpec, ModelArtifactsSpec

#: Bumped whenever a change would move a proposal for the same audio, so a
#: review stamp names the masker it reviewed.
MASKER_VERSION = 1
METHOD = "silero-vad-5.1.2"

VAD_SAMPLE_RATE = 16000
#: Silero v5 takes exactly 512 samples per call at 16 kHz.
VAD_FRAME = 512
VAD_HOP_S = VAD_FRAME / VAD_SAMPLE_RATE
#: Silero v5 carries 64 samples of the previous frame as context.
_VAD_CONTEXT = 64

VAD_SLUG = "silero_vad"
VAD_ARTIFACT = ArtifactSpec(
    filename="silero_vad.onnx",
    sha256="2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f",
    size_bytes=2327524,
    url="https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx",
)


class MaskRegion(BaseModel):
    """A span of the fixture's audio, seconds from the start of its WAV."""

    start: float
    end: float


class MaskProposal(BaseModel):
    """What the masker proposes for one fixture; a reviewer accepts or edits it."""

    method: str = METHOD
    masker_version: int = MASKER_VERSION
    regions: list[MaskRegion]
    #: Speech that reached into a window around a shot or the beep. It is
    #: not masked there; the stage needs a person to decide.
    protected_overlaps: list[MaskRegion]

    @property
    def needs_decision(self) -> bool:
        return bool(self.protected_overlaps)

    @property
    def masked_seconds(self) -> float:
        return round(sum(r.end - r.start for r in self.regions), 3)


def vad_registry(root: Path | None = None) -> Any:
    """A model registry holding only the VAD graph (cached under the user's
    model directory, verified by sha256 like every model artifact)."""
    from ..models.registry import ModelRegistry

    spec = ModelArtifactsSpec.model_validate({VAD_SLUG: VAD_ARTIFACT.model_dump()})
    return ModelRegistry(spec, root=root)


def load_vad(root: Path | None = None) -> Any:
    """An ``onnxruntime`` session over the VAD graph, downloading it once."""
    import onnxruntime as ort

    path = vad_registry(root).resolve(VAD_SLUG)
    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


def to_vad_rate(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Mono float32 at 16 kHz."""
    from math import gcd

    from scipy.signal import resample_poly

    if sample_rate == VAD_SAMPLE_RATE:
        return audio.astype(np.float32, copy=False)
    g = gcd(VAD_SAMPLE_RATE, sample_rate)
    return resample_poly(audio, VAD_SAMPLE_RATE // g, sample_rate // g).astype(np.float32)


def speech_probabilities(audio_16k: np.ndarray, session: Any) -> np.ndarray:
    """Speech probability per 32 ms frame, frame ``i`` covering
    ``[i * VAD_HOP_S, (i + 1) * VAD_HOP_S)``. The model is stateful, so the
    frames run in order through one state."""
    n = len(audio_16k) // VAD_FRAME
    probs = np.zeros(n, dtype=np.float32)
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, _VAD_CONTEXT), dtype=np.float32)
    sr = np.array(VAD_SAMPLE_RATE, dtype=np.int64)
    for i in range(n):
        frame = audio_16k[i * VAD_FRAME : (i + 1) * VAD_FRAME].reshape(1, -1)
        x = np.concatenate([context, frame], axis=1)
        out, state = session.run(None, {"input": x, "state": state, "sr": sr})
        probs[i] = float(out[0, 0])
        context = frame[:, -_VAD_CONTEXT:]
    return probs


def speech_regions(
    probs: np.ndarray, config: SpeechMaskConfig, *, hop_s: float = VAD_HOP_S, duration: float | None = None
) -> list[MaskRegion]:
    """Regions of speech from per-frame probabilities (pure)."""
    raw: list[tuple[float, float]] = []
    start: int | None = None
    for i, p in enumerate(probs):
        if start is None and p >= config.threshold:
            start = i
        elif start is not None and p < config.end_threshold:
            raw.append((start * hop_s, i * hop_s))
            start = None
    if start is not None:
        raw.append((start * hop_s, len(probs) * hop_s))

    merged: list[tuple[float, float]] = []
    for s, e in raw:
        if merged and s - merged[-1][1] < config.merge_gap_s:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    end_limit = duration if duration is not None else len(probs) * hop_s
    out = []
    for s, e in merged:
        if e - s < config.min_speech_s:
            continue
        out.append(
            MaskRegion(
                start=round(max(0.0, s - config.pad_s), 3), end=round(min(end_limit, e + config.pad_s), 3)
            )
        )
    return out


def protected_windows(times: Iterable[float], config: SpeechMaskConfig) -> list[MaskRegion]:
    """The windows no mask may touch, merged, around each shot and the beep."""
    half = config.protect_ms / 1000.0
    spans = sorted((t - half, t + half) for t in times)
    merged: list[tuple[float, float]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return [MaskRegion(start=round(s, 4), end=round(e, 4)) for s, e in merged]


def propose_masks(
    regions: list[MaskRegion], protected: list[MaskRegion], config: SpeechMaskConfig
) -> MaskProposal:
    """Speech regions minus the protected windows (pure). A piece left
    shorter than ``min_speech_s`` after the cut is dropped; every overlap
    with a protected window is reported."""
    masks: list[MaskRegion] = []
    overlaps: list[MaskRegion] = []
    for region in regions:
        pieces = [(region.start, region.end)]
        for guard in protected:
            if guard.end <= region.start or guard.start >= region.end:
                continue
            overlaps.append(
                MaskRegion(
                    start=round(max(region.start, guard.start), 4), end=round(min(region.end, guard.end), 4)
                )
            )
            cut: list[tuple[float, float]] = []
            for s, e in pieces:
                if guard.end <= s or guard.start >= e:
                    cut.append((s, e))
                    continue
                if guard.start > s:
                    cut.append((s, guard.start))
                if guard.end < e:
                    cut.append((guard.end, e))
            pieces = cut
        masks += [
            MaskRegion(start=round(s, 3), end=round(e, 3)) for s, e in pieces if e - s >= config.min_speech_s
        ]
    return MaskProposal(regions=masks, protected_overlaps=overlaps)


def labelled_times(fixture: dict) -> list[float]:
    """The beep and every audited shot time in a fixture JSON, WAV seconds."""
    times = [
        float(s["time"])
        for s in fixture.get("shots") or []
        if isinstance(s, dict) and s.get("time") is not None
    ]
    beep = fixture.get("beep_time")
    if beep is not None:
        times.append(float(beep))
    return times


def propose_for_fixture(
    fixture: dict, audio: np.ndarray, sample_rate: int, session: Any, config: SpeechMaskConfig
) -> MaskProposal:
    """The whole pipeline for one fixture's JSON and loaded WAV."""
    audio_16k = to_vad_rate(audio, sample_rate)
    probs = speech_probabilities(audio_16k, session)
    regions = speech_regions(probs, config, duration=len(audio) / sample_rate)
    return propose_masks(regions, protected_windows(labelled_times(fixture), config), config)
