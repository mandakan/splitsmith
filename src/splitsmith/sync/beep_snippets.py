"""Generate beep review snippets desktop-side before a push (slice 3).

For every unconfirmed queue-worthy video (primary or secondary, stage not
skipped, beep not yet reviewed) this cuts a short mono AAC snippet around
the beep candidates plus a peaks JSON for the same range, into
``<shooter_root>/beep_review/``. The push plan uploads whatever exists
there; hosted serves it so a phone can review beeps on a mirror match.

Skip logic is an ``input_hash`` stored inside the peaks JSON - a digest of
the fields that shape the snippet. Unchanged inputs mean no ffmpeg run and
untouched mtimes, so the push plan's size+mtime check skips the upload too.
Videos that become reviewed get their snippet files removed so they stop
being pushed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from ..match_model import load_match_or_legacy
from ..match_project import MatchProject, StageVideo
from ..waveform import cache_path, ensure_peaks

SNIPPET_MARGIN_S = 5.0
DEFAULT_WINDOW_END_S = 30.0
MIN_SNIPPET_S = 2.0
PEAK_BINS = 600
SNIPPET_SAMPLE_RATE = 16000


class BeepSnippetReport(BaseModel):
    generated: int = 0
    skipped: int = 0
    removed: int = 0
    errors: list[str] = Field(default_factory=list)


def _window(video: StageVideo) -> tuple[float, float]:
    """Snippet range in source seconds: candidates and beep +/- margin;
    else the video's own beep search window, if it has one; else the
    default detection window from t=0."""
    times = [c.time for c in (video.beep_candidates or [])]
    if video.beep_time is not None:
        times.append(video.beep_time)
    if times:
        start = max(0.0, min(times) - SNIPPET_MARGIN_S)
        end = max(times) + SNIPPET_MARGIN_S
    elif video.beep_window is not None:
        start = max(0.0, video.beep_window[0])
        end = video.beep_window[1]
    else:
        start, end = 0.0, DEFAULT_WINDOW_END_S
    return start, max(end, start + MIN_SNIPPET_S)


def _input_hash(video: StageVideo, start: float, end: float) -> str:
    payload = {
        "video_id": video.video_id,
        "beep_time": video.beep_time,
        "candidates": [c.time for c in (video.beep_candidates or [])],
        "start": round(start, 3),
        "end": round(end, 3),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _cut(
    ffmpeg_binary: str,
    src: Path,
    dest: Path,
    start: float,
    dur: float,
    codec: list[str],
) -> None:
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{dur:.3f}",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SNIPPET_SAMPLE_RATE),
        *codec,
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def generate_beep_snippets(match_root: Path, *, ffmpeg_binary: str = "ffmpeg") -> BeepSnippetReport:
    """Cut a review snippet + peaks JSON per unreviewed queue-worthy video.

    Walks every shooter in ``match_root`` (redesign-era or legacy, via
    ``load_match_or_legacy``) and every non-skipped stage's primary and
    secondary videos. A video with ``beep_reviewed`` set gets its snippet
    files removed (it no longer needs review); otherwise the snippet is
    (re)generated unless its ``input_hash`` already matches what is on
    disk. Errors (missing source, ffmpeg failure) are collected per video
    rather than aborting the whole run.
    """
    report = BeepSnippetReport()
    match, shooter_roots = load_match_or_legacy(match_root)
    for slug in match.shooters:
        shooter_root = shooter_roots[slug]
        try:
            project = MatchProject.load(shooter_root)
        except FileNotFoundError:
            continue
        out_dir = shooter_root / "beep_review"
        for stage in project.stages:
            if stage.skipped:
                continue
            for video in stage.videos:
                if video.role not in ("primary", "secondary"):
                    continue
                _process_video(
                    slug=slug,
                    project=project,
                    shooter_root=shooter_root,
                    out_dir=out_dir,
                    video=video,
                    ffmpeg_binary=ffmpeg_binary,
                    report=report,
                )
    return report


def _process_video(
    *,
    slug: str,
    project: MatchProject,
    shooter_root: Path,
    out_dir: Path,
    video: StageVideo,
    ffmpeg_binary: str,
    report: BeepSnippetReport,
) -> None:
    m4a = out_dir / f"{video.video_id}.m4a"
    peaks_path = out_dir / f"{video.video_id}.peaks.json"

    if video.beep_reviewed:
        removed = False
        for stale in (m4a, peaks_path):
            if stale.exists():
                stale.unlink()
                removed = True
        if removed:
            report.removed += 1
        return

    start, end = _window(video)
    digest = _input_hash(video, start, end)
    if peaks_path.exists():
        try:
            existing = json.loads(peaks_path.read_text(encoding="utf-8"))
            if existing.get("input_hash") == digest and m4a.exists():
                report.skipped += 1
                return
        except (json.JSONDecodeError, OSError):
            pass  # unreadable - regenerate

    src = project.resolve_video_path(shooter_root, video.path)
    if not src.exists():
        report.errors.append(f"{slug}/{video.video_id}: source missing: {src}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    wav_tmp = out_dir / f"{video.video_id}.tmp.wav"
    try:
        _cut(ffmpeg_binary, src, m4a, start, end - start, ["-c:a", "aac", "-b:a", "48k"])
        _cut(ffmpeg_binary, src, wav_tmp, start, end - start, [])
        peaks = ensure_peaks(wav_tmp, PEAK_BINS)
        peaks_path.write_text(
            json.dumps(
                {
                    "snippet_start": start,
                    "duration": peaks.duration,
                    "sample_rate": peaks.sample_rate,
                    "bins": peaks.bins,
                    "peaks": peaks.peaks,
                    "beep_time": video.beep_time,
                    "candidates": [
                        {"time": c.time, "confidence": c.confidence} for c in (video.beep_candidates or [])
                    ],
                    "input_hash": digest,
                }
            ),
            encoding="utf-8",
        )
        report.generated += 1
    except (subprocess.CalledProcessError, OSError) as exc:
        # OSError covers a missing ffmpeg binary (FileNotFoundError) as
        # well as other exec failures - none of those carry .stderr, so
        # fall back to an empty message rather than crashing the push.
        stderr = (getattr(exc, "stderr", b"") or b"").decode("utf-8", "replace")[-500:]
        report.errors.append(f"{slug}/{video.video_id}: ffmpeg failed: {stderr}")
    finally:
        # ensure_peaks (waveform.py) caches its result next to its input as
        # ``<stem>.peaks-<bins>.json`` (see waveform.cache_path), not
        # ``<stem>.peaks.json`` - clean up the actual cache path, not a
        # guessed one, so the temp wav's sidecar doesn't linger next to the
        # real ``<video_id>.peaks.json`` output.
        wav_tmp.unlink(missing_ok=True)
        wav_cache = cache_path(wav_tmp, PEAK_BINS)
        wav_cache.unlink(missing_ok=True)
