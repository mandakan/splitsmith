"""Voter E -- per-candidate CLIP visual probe (issue #183).

Mirrors the Voter D / PANN structure: a runtime that lazy-loads model
weights, a feature-computation function that takes
``(video_path, candidate_times)`` and returns CLIP image embeddings, and
a scoring function that runs the trained linear probe head to produce
per-candidate ``P(shot)``.

Frames are extracted via ffmpeg (fast pre-seek + accurate fine seek)
and cached on disk keyed on the source video's stat fingerprint, so
re-runs over the same video are fast.

The probe head is a ``sklearn`` ``LogisticRegression`` trained binary
on shots vs cross_bay candidates. See
``scripts/build_ensemble_artifacts.py``.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

CLIP_VISUAL_MODEL_ID = "openai/clip-vit-base-patch32"
CLIP_VISUAL_EMBED_DIM = 512
DEFAULT_FRAME_OFFSETS: tuple[float, ...] = (0.0,)
DEFAULT_FRAME_CACHE_DIR = Path.home() / ".cache" / "splitsmith" / "voter_e_frames"


@dataclass
class VisualRuntime:
    """Loaded heavy state for Voter E.

    ``probe`` is the trained sklearn classifier (``LogisticRegression``)
    that maps a CLIP image embedding to a per-candidate shot probability.
    Cleared / replaced when the calibration is rebuilt.
    """

    model: Any
    processor: Any
    probe: Any
    model_id: str = CLIP_VISUAL_MODEL_ID
    device: str = "cpu"
    frame_cache_dir: Path = field(default_factory=lambda: DEFAULT_FRAME_CACHE_DIR)


def load_visual_runtime(
    probe: Any,
    *,
    model_id: str = CLIP_VISUAL_MODEL_ID,
    device: str | None = None,
    frame_cache_dir: Path | None = None,
) -> VisualRuntime:
    """Materialise the CLIP model + processor; bind the supplied probe head.

    First call downloads ~600 MB to the HF cache. Reuse the returned
    runtime across detections. ``probe`` is loaded by the caller (typically
    via ``calibration.load_voter_e_probe``) so this function stays a pure
    model-init helper.
    """
    import torch
    from transformers import CLIPModel, CLIPProcessor

    if device is None:
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"

    model = CLIPModel.from_pretrained(model_id).to(device).eval()
    processor = CLIPProcessor.from_pretrained(model_id)
    return VisualRuntime(
        model=model,
        processor=processor,
        probe=probe,
        model_id=model_id,
        device=device,
        frame_cache_dir=frame_cache_dir or DEFAULT_FRAME_CACHE_DIR,
    )


def _video_fingerprint(video_path: Path) -> str:
    """Stable cache key for a source video.

    Uses ``(absolute_path, size, mtime_ns)`` so a re-import or in-place
    edit of the same file invalidates the cache. Cheap; no hashing of
    file contents.
    """
    st = video_path.stat()
    raw = f"{video_path.resolve()}::{st.st_size}::{st.st_mtime_ns}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def _frame_path(
    cache_dir: Path, fingerprint: str, time_s: float, offset_s: float
) -> Path:
    time_ms = int(round(time_s * 1000))
    offset_ms = int(round(offset_s * 1000))
    return cache_dir / fingerprint / f"t{time_ms:09d}_o{offset_ms:+05d}.jpg"


def _extract_frame(
    video_path: Path, source_time: float, dest: Path, *, ffmpeg: str = "ffmpeg"
) -> bool:
    """Extract a single frame at ``source_time`` (seconds in source video).

    Two-step seek: fast pre-seek to ``source_time - 2.0``, then accurate
    fine seek 2.0 s into the decoded stream. Lands within a frame of the
    target without decoding the whole file.
    """
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    pre_seek = max(0.0, source_time - 2.0)
    fine_seek = source_time - pre_seek
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{pre_seek:.3f}",
        "-i",
        str(video_path),
        "-ss",
        f"{fine_seek:.3f}",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-y",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and dest.exists()


def compute_visual_features(
    video_path: Path,
    source_times: np.ndarray,
    runtime: VisualRuntime,
    *,
    frame_offsets: Sequence[float] = DEFAULT_FRAME_OFFSETS,
    batch_size: int = 32,
) -> np.ndarray:
    """Extract frames + return CLIP image embeddings.

    ``source_times`` are absolute timestamps inside the source video,
    in seconds. ``frame_offsets`` are seconds added to each timestamp
    (e.g. ``(0.0,)`` for v0; ``(0.0, 0.030, 0.080)`` for the deferred
    multi-frame variant in #184).

    Returns shape ``(N, len(frame_offsets) * embed_dim)`` -- offsets are
    flattened along the last axis so the linear probe sees them as
    independent features.

    Per-candidate frames are cached under
    ``runtime.frame_cache_dir/<video_fingerprint>/`` keyed on
    ``(time_ms, offset_ms)``.
    """
    import torch
    from PIL import Image

    n = int(np.asarray(source_times).shape[0])
    n_offsets = len(frame_offsets)
    if n == 0:
        return np.zeros((0, n_offsets * CLIP_VISUAL_EMBED_DIM), dtype=np.float32)
    if not video_path.exists():
        raise FileNotFoundError(f"Voter E source video not found: {video_path}")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Voter E requires ffmpeg on $PATH")

    fingerprint = _video_fingerprint(video_path)
    times = np.asarray(source_times, dtype=np.float64)

    frame_paths: list[Path] = []
    missing: list[tuple[Path, float]] = []
    for t in times:
        for off in frame_offsets:
            p = _frame_path(runtime.frame_cache_dir, fingerprint, float(t), float(off))
            frame_paths.append(p)
            if not p.exists():
                missing.append((p, float(t) + float(off)))

    for dest, t_seek in missing:
        if not _extract_frame(video_path, t_seek, dest):
            raise RuntimeError(
                f"Voter E: ffmpeg frame extraction failed for {video_path} at "
                f"t={t_seek:.3f}s (target {dest.name})"
            )

    embeds: list[np.ndarray] = []
    for i in range(0, len(frame_paths), batch_size):
        chunk_paths = frame_paths[i : i + batch_size]
        images = [Image.open(p).convert("RGB") for p in chunk_paths]
        inputs = runtime.processor(images=images, return_tensors="pt").to(runtime.device)
        with torch.no_grad():
            feats = runtime.model.get_image_features(**inputs).pooler_output
            feats = feats / feats.norm(dim=-1, keepdim=True)
        embeds.append(feats.cpu().numpy().astype(np.float32))
    flat = np.vstack(embeds)
    return flat.reshape(n, n_offsets * CLIP_VISUAL_EMBED_DIM)


def score_visual_candidates(
    features: np.ndarray, runtime: VisualRuntime
) -> np.ndarray:
    """Predict ``P(shot)`` per candidate from CLIP image embeddings.

    Returns shape ``(N,)`` ``float32``. ``runtime.probe`` is expected to
    be a sklearn classifier with ``predict_proba``.
    """
    if features.size == 0:
        return np.zeros(0, dtype=np.float32)
    return runtime.probe.predict_proba(features)[:, 1].astype(np.float32)


def candidate_times_in_source(
    audit_clip_times: np.ndarray,
    *,
    audit_beep_in_clip: float,
    source_beep_time: float,
) -> np.ndarray:
    """Map candidate times from the audit clip to the source video.

    The ensemble produces candidate times relative to the audit clip's
    timeline (``cand.time``); Voter E needs source-video timestamps to
    extract frames from the original file. The beep is the shared anchor:
    ``source_t = source_beep_time + (clip_t - audit_beep_in_clip)``.

    Pure-function helper; no I/O.
    """
    return source_beep_time + (np.asarray(audit_clip_times, dtype=np.float64) - audit_beep_in_clip)


def temp_frame_cache_dir(prefix: str = "splitsmith-voter-e-") -> Path:
    """Caller-managed temp cache for one-shot probes (e.g. tests)."""
    return Path(tempfile.mkdtemp(prefix=prefix))
