"""Per-candidate audio snippet extraction (issue #98).

Used by the Lab "Step-through" labeling flow: each candidate becomes a
short on-disk WAV the SPA can autoplay/loop, so labeling runs at the
user's typing speed instead of waveform-scrubbing speed.

Cache layout::

    tests/fixtures/.cache/<slug>_snippets/cand<NNN>_pre<P>_post<Q>.wav

The cache lives next to the existing CLAP / PANN feature caches; it is
git-ignored and rebuilt on demand.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import soundfile as sf

DEFAULT_PRE_MS = 100
DEFAULT_POST_MS = 300
CACHE_SUBDIR_SUFFIX = "_snippets"


def _snippet_dir(audit_path: Path) -> Path:
    """Cache dir for a fixture's snippets, sibling to the audit JSON."""
    fixtures_dir = audit_path.parent
    cache_root = fixtures_dir / ".cache"
    return cache_root / f"{audit_path.stem}{CACHE_SUBDIR_SUFFIX}"


def _snippet_path(audit_path: Path, candidate_number: int, pre_ms: int, post_ms: int) -> Path:
    return _snippet_dir(audit_path) / f"cand{candidate_number:03d}_pre{pre_ms}_post{post_ms}.wav"


def _candidate_time(audit_path: Path, candidate_number: int) -> float:
    """Resolve a candidate's time from the audit JSON.

    Looks at ``_candidates_pending_audit.candidates[]`` first; falls
    back to ``shots[]`` so kept positives also have snippets without
    duplicating the candidate row in both places.
    """
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    pending = payload.get("_candidates_pending_audit") or {}
    for c in pending.get("candidates", []):
        if int(c.get("candidate_number", -1)) == candidate_number:
            return float(c["time"])
    for s in payload.get("shots", []):
        if int(s.get("candidate_number", -1)) == candidate_number:
            return float(s["time"])
    raise KeyError(f"candidate_number {candidate_number} not found in {audit_path}")


def extract_snippet(
    audit_path: Path,
    candidate_number: int,
    *,
    pre_ms: int = DEFAULT_PRE_MS,
    post_ms: int = DEFAULT_POST_MS,
    audio_path: Path | None = None,
) -> Path:
    """Materialise the snippet WAV for one candidate. Idempotent + cached.

    ``audio_path`` defaults to the audit JSON's sibling WAV
    (``audit_path.with_suffix(".wav")``); pass an override when the
    fixture's audio lives elsewhere.
    """
    target = _snippet_path(audit_path, candidate_number, pre_ms, post_ms)
    if target.exists():
        return target

    src = audio_path or audit_path.with_suffix(".wav")
    if not src.exists():
        raise FileNotFoundError(f"fixture audio not found: {src}")
    t = _candidate_time(audit_path, candidate_number)

    audio, sr = sf.read(src, always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    n = audio.shape[0]
    start = max(0, int(round((t - pre_ms / 1000.0) * sr)))
    end = min(n, int(round((t + post_ms / 1000.0) * sr)))
    if end <= start:
        raise ValueError(f"empty snippet window for candidate {candidate_number} at t={t}")
    clip = np.ascontiguousarray(audio[start:end], dtype=np.float32)

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    sf.write(tmp, clip, sr, subtype="PCM_16", format="WAV")
    tmp.replace(target)
    return target


def precache_all(
    audit_path: Path,
    *,
    pre_ms: int = DEFAULT_PRE_MS,
    post_ms: int = DEFAULT_POST_MS,
    audio_path: Path | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> int:
    """Build snippets for every candidate in a fixture. Returns count.

    Loads the audio once to amortise the read cost across all
    candidates. Calls ``progress(i, total, candidate_number)`` after
    each write when provided -- shaped to feed JobRegistry.update().
    """
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    pending = payload.get("_candidates_pending_audit") or {}
    candidates = pending.get("candidates", [])
    src = audio_path or audit_path.with_suffix(".wav")
    if not src.exists():
        raise FileNotFoundError(f"fixture audio not found: {src}")
    audio, sr = sf.read(src, always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    n = audio.shape[0]

    out_dir = _snippet_dir(audit_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    pre_n = int(round(pre_ms / 1000.0 * sr))
    post_n = int(round(post_ms / 1000.0 * sr))
    total = len(candidates)
    for i, c in enumerate(candidates):
        cn = int(c.get("candidate_number", -1))
        t = float(c.get("time", -1.0))
        if cn < 0 or t < 0:
            continue
        target = _snippet_path(audit_path, cn, pre_ms, post_ms)
        if not target.exists():
            idx = int(round(t * sr))
            start = max(0, idx - pre_n)
            end = min(n, idx + post_n)
            if end > start:
                clip = np.ascontiguousarray(audio[start:end], dtype=np.float32)
                tmp = target.with_suffix(target.suffix + ".tmp")
                sf.write(tmp, clip, sr, subtype="PCM_16", format="WAV")
                tmp.replace(target)
        if progress is not None:
            progress(i + 1, total, cn)
    return total
