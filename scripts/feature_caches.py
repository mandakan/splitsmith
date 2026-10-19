"""Are a fixture's CLAP / PANN feature caches made from the WAV it has now?

``tests/fixtures/.cache/<stem>_{clap,pann}.npz`` hold one row per detected
candidate. A re-audit can re-cut a fixture's clip on a new clock; the old
cache then still has as many rows as the new clip has candidates more often
than not, so a row-count check (all the build had) passes it with features
taken from other audio. A cache now carries ``wav_sha256``, the digest of
the WAV it was extracted from (the digest ``audio.lock.json`` addresses in
R2), and a mismatch is stale.

A cache written before the stamp existed is checked once against fresh
candidate times: equal times mean the same audio, and the cache is stamped
in place; anything else is stale. :func:`ensure_features` re-extracts what is
stale or missing when asked (CLAP and PANN load once), and otherwise says
which fixtures need it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np

from splitsmith.beep.detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.shot_detect import detect_shots

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
CACHE_DIR = FIXTURES_DIR / ".cache"
KINDS = ("clap", "pann")
# Candidate times are deterministic for the same samples; anything beyond
# float noise means different audio.
_TIME_TOL_S = 1e-6


def cache_path(stem: str, kind: str) -> Path:
    return CACHE_DIR / f"{stem}_{kind}.npz"


def stamp_of(path: Path) -> str | None:
    with np.load(path, allow_pickle=True) as z:
        return str(z["wav_sha256"]) if "wav_sha256" in z.files else None


def stamp(path: Path, wav_sha256: str) -> None:
    """Rewrite ``path`` with ``wav_sha256`` added, every other array as it was."""
    with np.load(path, allow_pickle=True) as z:
        arrays = {k: z[k] for k in z.files if k != "wav_sha256"}
    np.savez(path, **arrays, wav_sha256=np.array(wav_sha256))


def candidate_times(stem: str) -> np.ndarray:
    """The candidate times the build and the extractors both detect."""
    truth = json.loads((FIXTURES_DIR / f"{stem}.json").read_text())
    audio, sr = load_audio(FIXTURES_DIR / f"{stem}.wav")
    shots = detect_shots(
        audio,
        sr,
        truth["beep_time"],
        truth["stage_time_seconds"],
        ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0),
    )
    return np.array([s.scoring_time for s in shots], dtype=np.float64)


def times_match(cached: np.ndarray, fresh: np.ndarray) -> bool:
    return cached.shape == fresh.shape and bool(np.all(np.abs(cached - fresh) <= _TIME_TOL_S))


def cache_state(stem: str, wav_sha256: str, *, fresh_times: Callable[[], np.ndarray]) -> str:
    """``ok``, ``stale`` or ``missing`` for the pair of caches. An unstamped
    pair is checked against ``fresh_times()`` and stamped when it matches."""
    paths = [cache_path(stem, k) for k in KINDS]
    if not all(p.exists() for p in paths):
        return "missing"
    stamps = [stamp_of(p) for p in paths]
    if all(s == wav_sha256 for s in stamps):
        return "ok"
    if any(s is not None and s != wav_sha256 for s in stamps):
        return "stale"
    times = fresh_times()
    for p in paths:
        with np.load(p, allow_pickle=True) as z:
            if not times_match(z["times"].astype(np.float64), times):
                return "stale"
    for p in paths:
        stamp(p, wav_sha256)
    return "ok"


def _extract(stems: list[str], digests: dict[str, str], log: Callable[[str], None]) -> None:
    import extract_audio_embeddings as pann_x
    import extract_clap_features as clap_x
    from panns_inference import AudioTagging
    from transformers import ClapModel, ClapProcessor

    log(f"  loading CLAP ({clap_x.CLAP_MODEL_ID}) and PANN CNN14 for {len(stems)} fixture(s)...")
    model = ClapModel.from_pretrained(clap_x.CLAP_MODEL_ID)
    processor = ClapProcessor.from_pretrained(clap_x.CLAP_MODEL_ID)
    model.eval()
    tagger = AudioTagging(checkpoint_path=None, device="cpu")
    for i, stem in enumerate(stems, 1):
        log(f"  [{i}/{len(stems)}] extracting {stem}")
        clap_x.extract_for_fixture(stem, force=True, model=model, processor=processor)
        pann_x.extract_for_fixture(stem, force=True, audio_tagger=tagger)
        for kind in KINDS:
            p = cache_path(stem, kind)
            if p.exists():
                stamp(p, digests[stem])


def ensure_features(
    stems: list[str],
    digests: dict[str, str],
    *,
    extract: bool,
    log: Callable[[str], None] = print,
) -> list[str]:
    """Make every fixture's caches match its WAV, or say which do not.

    Returns the stems still missing or stale: empty when ``extract`` ran (a
    fixture with no candidates has nothing to cache and stays listed).
    """
    todo = []
    for stem in stems:
        state = cache_state(stem, digests[stem], fresh_times=lambda s=stem: candidate_times(s))
        if state != "ok":
            todo.append(stem)
            log(f"  {stem}: feature caches {state}")
    if todo and extract:
        _extract(todo, digests, log)
        todo = [s for s in todo if not all(stamp_of(cache_path(s, k)) == digests[s] for k in KINDS)]
    return todo
