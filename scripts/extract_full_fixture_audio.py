"""Extract wide-window audio from each fixture's source video.

Issue #87 wants negatives mined from the regions OUTSIDE the audited stage
window: pre-beep "are you ready / stand by", magazine swaps, and the long
post-stage tail (wind, movement, neighbouring-bay shots) where the user
often leaves the camera rolling for minutes. The current
``tests/fixtures/*.wav`` files are pre-trimmed to ~stage-window + 0.5 s
pre-beep + ~1.5 s post-stage, so they don't contain that material.

This script regenerates wider audio per fixture by re-extracting from the
ORIGINAL source ``.mp4`` listed in ``tests/fixtures/full/_sources.yaml``.
The short fixtures + audit JSONs stay untouched -- they remain the unit-test
source of truth. The wide WAVs go under ``tests/fixtures/full/`` with a
sidecar ``*_full.json`` recording the source-time window covered, so
``mine_negatives.py`` can map back to the audit's ``fixture_window_in_source``
+ ``beep_time`` to compute the stage exclusion region.

Default extraction is the WHOLE source video (the user often forgets the
camera on). Override with ``--pre-pad`` / ``--post-pad`` to instead extract
``[fws[0] - pre_pad, fws[1] + post_pad]`` clipped to the source bounds.

Run:
    uv run python scripts/extract_full_fixture_audio.py
    uv run python scripts/extract_full_fixture_audio.py --fixture stage-shots-blacksmith-2026-stage1
    uv run python scripts/extract_full_fixture_audio.py --pre-pad 30 --post-pad 120
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import yaml

from splitsmith.video_probe import ProbeError, probe

FIXTURES_DIR = Path("tests/fixtures")
FULL_DIR = FIXTURES_DIR / "full"
SOURCES_YAML = FULL_DIR / "_sources.yaml"
PROBE_CACHE_DIR = FIXTURES_DIR / ".cache" / "video_probe"
SAMPLE_RATE = 48000  # match splitsmith.beep_detect.load_audio

Runner = Callable[..., subprocess.CompletedProcess]


class ExtractError(RuntimeError):
    """Source resolution or ffmpeg invocation failed."""


def load_sources(path: Path = SOURCES_YAML) -> dict:
    if not path.exists():
        raise ExtractError(
            f"sources map missing: {path}. Seed it from each fixture's audit JSON "
            "'source' field before running."
        )
    return yaml.safe_load(path.read_text()) or {}


def resolve_video(stem: str, sources: dict) -> Path:
    """Locate the source video for fixture ``stem``.

    Override entry wins. Otherwise look up the basename in ``fixtures`` and
    join with ``video_dir``. Raises :class:`ExtractError` when the file is
    missing on disk -- that's the user's cue to fix the YAML or mount the
    drive.
    """
    overrides = sources.get("overrides") or {}
    if stem in overrides:
        candidate = Path(overrides[stem])
    else:
        basename = (sources.get("fixtures") or {}).get(stem)
        if not basename:
            raise ExtractError(
                f"{stem}: no entry in _sources.yaml. Add a 'fixtures' or 'overrides' line."
            )
        video_dir = sources.get("video_dir")
        if not video_dir:
            raise ExtractError(
                f"{stem}: 'video_dir' unset in _sources.yaml and no override given."
            )
        candidate = Path(video_dir) / basename
    if not candidate.exists():
        raise ExtractError(
            f"{stem}: source video not found at {candidate}. "
            "Update _sources.yaml or mount the source drive."
        )
    return candidate


def compute_window(
    duration: float,
    fixture_window: tuple[float, float],
    *,
    pre_pad: float | None,
    post_pad: float | None,
) -> tuple[float, float]:
    """Return ``(start, end)`` in source-time seconds.

    With both pads ``None`` we extract the whole video. Otherwise we widen the
    fixture's stage window symmetrically (or asymmetrically) and clip to source
    bounds.
    """
    if pre_pad is None and post_pad is None:
        return 0.0, duration
    fws_start, fws_end = fixture_window
    pre = pre_pad if pre_pad is not None else 0.0
    post = post_pad if post_pad is not None else 0.0
    start = max(0.0, fws_start - pre)
    end = min(duration, fws_end + post)
    return start, end


def run_ffmpeg_extract(
    src: Path,
    dst: Path,
    start: float,
    duration: float,
    *,
    sample_rate: int = SAMPLE_RATE,
    ffmpeg_binary: str = "ffmpeg",
    overwrite: bool,
    runner: Runner = subprocess.run,
) -> None:
    """Cut ``[start, start+duration]`` of ``src`` to mono WAV at ``sample_rate``."""
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if overwrite else "-n",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(src),
        "-t",
        f"{duration:.3f}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(dst),
    ]
    try:
        runner(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ExtractError(f"ffmpeg binary not found: {ffmpeg_binary}") from exc
    except subprocess.CalledProcessError as exc:
        raise ExtractError(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc


def extract_one(
    stem: str,
    sources: dict,
    *,
    pre_pad: float | None,
    post_pad: float | None,
    overwrite: bool,
    log: Callable[[str], None],
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
) -> dict:
    """Resolve, probe, and extract the wide audio for one fixture.

    Returns the sidecar payload (also written to disk).
    """
    audit_path = FIXTURES_DIR / f"{stem}.json"
    if not audit_path.exists():
        raise ExtractError(f"{stem}: audit JSON missing at {audit_path}")
    audit = json.loads(audit_path.read_text())
    fws = audit.get("fixture_window_in_source")
    if not (isinstance(fws, list) and len(fws) == 2):
        raise ExtractError(
            f"{stem}: audit JSON missing 'fixture_window_in_source' [start, end] "
            "-- can't compute the stage exclusion window for negative mining."
        )

    src = resolve_video(stem, sources)
    out_wav = FULL_DIR / f"{stem}_full.wav"
    out_json = FULL_DIR / f"{stem}_full.json"
    if out_wav.exists() and not overwrite:
        log(f"  {stem}: cached -> {out_wav}")
        if out_json.exists():
            return json.loads(out_json.read_text())

    PROBE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        result = probe(src, cache_dir=PROBE_CACHE_DIR)
    except ProbeError as exc:
        raise ExtractError(f"{stem}: ffprobe failed on {src}: {exc}") from exc
    if not result.duration:
        raise ExtractError(f"{stem}: ffprobe returned no duration for {src}")

    start, end = compute_window(
        result.duration, (float(fws[0]), float(fws[1])), pre_pad=pre_pad, post_pad=post_pad
    )
    duration = end - start
    if duration <= 0.0:
        raise ExtractError(
            f"{stem}: computed extraction window [{start}, {end}] is non-positive"
        )

    FULL_DIR.mkdir(parents=True, exist_ok=True)
    run_ffmpeg_extract(
        src,
        out_wav,
        start,
        duration,
        ffmpeg_binary=ffmpeg_binary,
        overwrite=overwrite,
        runner=runner,
    )

    sidecar = {
        "fixture_stem": stem,
        "source_video": str(src),
        "source_duration": result.duration,
        "full_window_in_source": [start, end],
        "fixture_window_in_source": [float(fws[0]), float(fws[1])],
        "sample_rate": SAMPLE_RATE,
        "extracted_seconds": duration,
    }
    out_json.write_text(json.dumps(sidecar, indent=2) + "\n")
    log(f"  {stem}: {duration:.1f}s @ {SAMPLE_RATE} Hz -> {out_wav.name}")
    return sidecar


def extract_all(
    fixtures: list[str] | None = None,
    *,
    pre_pad: float | None = None,
    post_pad: float | None = None,
    overwrite: bool = False,
    log: Callable[[str], None] = print,
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
) -> list[dict]:
    if not shutil.which(ffmpeg_binary):
        raise ExtractError(f"ffmpeg binary not found: {ffmpeg_binary}")
    sources = load_sources()
    if fixtures is None:
        fixtures = sorted((sources.get("fixtures") or {}).keys()) + sorted(
            (sources.get("overrides") or {}).keys()
        )
        # de-dupe while preserving order
        seen: set[str] = set()
        fixtures = [f for f in fixtures if not (f in seen or seen.add(f))]
    sidecars: list[dict] = []
    for stem in fixtures:
        try:
            sidecars.append(
                extract_one(
                    stem,
                    sources,
                    pre_pad=pre_pad,
                    post_pad=post_pad,
                    overwrite=overwrite,
                    log=log,
                    ffmpeg_binary=ffmpeg_binary,
                    runner=runner,
                )
            )
        except ExtractError as exc:
            log(f"  SKIP {stem}: {exc}")
    return sidecars


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append", help="Fixture stem (repeatable).")
    p.add_argument(
        "--pre-pad",
        type=float,
        default=None,
        help="Seconds to extend BEFORE the audited stage window. "
        "Omit (and --post-pad) to extract the whole source video (default).",
    )
    p.add_argument(
        "--post-pad",
        type=float,
        default=None,
        help="Seconds to extend AFTER the audited stage window. "
        "Omit (and --pre-pad) to extract the whole source video (default).",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-extract even when the _full.wav already exists.",
    )
    p.add_argument("--ffmpeg", default="ffmpeg", help="Path to ffmpeg binary.")
    args = p.parse_args()
    extract_all(
        fixtures=args.fixture or None,
        pre_pad=args.pre_pad,
        post_pad=args.post_pad,
        overwrite=args.overwrite,
        ffmpeg_binary=args.ffmpeg,
    )


if __name__ == "__main__":
    main()
