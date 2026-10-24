"""Audio extraction + caching for the production UI.

The CLI does this inline in ``cli.py:_extract_or_load_audio`` (caching the WAV
next to the source video). The production UI prefers project-local caching so
the project directory stays self-contained. Cache filenames are keyed by the
video's ``video_id`` for every role, so swapping the primary on a stage can
never serve a different video's audio under the new primary's name:

  <project>/audio/stage<N>_cam_<video_id>.wav        -- full source WAV
  <project>/audio/stage<N>_cam_<video_id>_audit.wav  -- audit WAV (post-trim)
  <project>/trimmed/stage<N>_cam_<video_id>_trimmed.mp4 -- short-GOP audit MP4

The audit screen (#15) prefers the audit WAV when a trimmed clip exists,
falling back to the full WAV. Caches are invalidated when their source's
mtime changes.

Projects created before schema v2 used role-based legacy names
(``stage<N>_primary.wav`` etc.) which couldn't distinguish primaries across
reassignment. The v1->v2 migration in ``project.py`` deletes those files on
open so the next access re-extracts under the new naming.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import derived
from ..beep import detect as beep_detect
from ..config import BeepDetectConfig, BeepDetection
from ..match_project import MatchProject, StageVideo
from ..media_check import valid_mp4, valid_mp4_cached
from ..storage import StorageObject
from ..trim import audit_trim_matches, audit_trim_name, audit_trim_params, trim_params_path
from ..trim import source_mtime as trim_source_mtime
from .context import current_match_origin

logger = logging.getLogger(__name__)

# Standard trim buffer (config.OutputConfig.trim_buffer_seconds default).
# Used to derive the beep's position inside a trimmed clip when no trim
# metadata sidecar exists. Custom buffers will round-trip incorrectly here;
# wire trim through the UI to record exact metadata.
DEFAULT_TRIM_BUFFER_SECONDS: float = 5.0


@dataclass(frozen=True)
class AuditAudioResult:
    """What :func:`ensure_audit_audio` resolves to.

    ``audio_path`` is the WAV the audit screen should read. ``beep_in_clip``
    is where the beep falls in that clip's local timeline (used as the
    waveform's beep marker). ``trimmed`` is True when the audio came from a
    short-GOP trimmed MP4; False when the audit is operating on the full
    source for the lack of a trim.
    """

    audio_path: Path
    beep_in_clip: float | None
    trimmed: bool


class AudioExtractionError(RuntimeError):
    """ffmpeg or ffprobe failed during audio extraction."""


def video_audio_path(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    *,
    project: MatchProject | None = None,
) -> Path:
    """Resolve the cached audio-WAV path for ``video`` on ``stage_number``.

    Always keyed by ``video_id`` so swapping the stage's primary cannot
    alias to a previous primary's cached audio.
    """
    audio_dir = project.audio_path(project_root) if project else project_root / "audio"
    return audio_dir / f"stage{stage_number}_cam_{video.video_id}.wav"


def legacy_video_id(video: StageVideo) -> str:
    """Pre-take-spec ``video_id``: hash of the source path only.

    Thin alias for :attr:`StageVideo.legacy_video_id`, kept because the
    cache fallbacks below and their tests read as functions. The hash
    itself lives on the model so ``cleanup`` can ask the same question
    without a core module importing this UI one.
    """
    return video.legacy_video_id


def _legacy_cache_fallback(
    project: MatchProject | None,
    video: StageVideo,
    new_path: Path,
    legacy_path: Path,
) -> Path | None:
    """Return ``legacy_path`` when it can safely stand in for ``new_path``.

    Read-only fallback for caches orphaned by the take-spec id change:
    the legacy file must exist (non-empty), differ from the new-keyed
    name, and the video's source path must be registered exactly once
    across the whole project (all stages + the unassigned tray). A
    multi-stage single take registers one path on N stages and the
    legacy artifact carries one unknown stage's content - ambiguity
    means no fallback. Writes never target the legacy name.
    """
    if project is None or legacy_path == new_path:
        return None
    if not (legacy_path.exists() and legacy_path.stat().st_size > 0):
        return None
    registrations = 0
    for stage in project.stages:
        for v in stage.videos:
            if v.path == video.path:
                registrations += 1
    for v in project.unassigned_videos:
        if v.path == video.path:
            registrations += 1
    if registrations != 1:
        return None
    return legacy_path


def primary_audio_path(
    project_root: Path,
    stage_number: int,
    *,
    project: MatchProject,
) -> Path:
    """Resolve the cached audio path for ``stage_number``'s current primary.

    Convenience wrapper: looks up the primary ``StageVideo`` on the stage
    and returns ``video_audio_path`` for it. Raises ``KeyError`` when the
    stage doesn't exist or has no primary -- callers must gate on a real
    primary before using this.
    """
    primary = project.stage(stage_number).primary()
    if primary is None:
        raise KeyError(f"stage {stage_number} has no primary video")
    return video_audio_path(project_root, stage_number, primary, project=project)


def ensure_primary_audio(
    project_root: Path,
    stage_number: int,
    source_video: Path | str,
    *,
    sample_rate: int = 48000,
    ffmpeg_binary: str = "ffmpeg",
    project: MatchProject,
) -> Path:
    """Extract a mono WAV for the stage's primary video if not already cached.

    Thin wrapper around :func:`ensure_video_audio` that resolves the stage's
    primary ``StageVideo`` and routes through the per-video cache key. Kept
    as a named entry point so monkeypatch surfaces and existing call-sites
    keep working without threading the ``StageVideo`` through.
    """
    primary = project.stage(stage_number).primary()
    if primary is None:
        raise KeyError(f"stage {stage_number} has no primary video")
    return ensure_video_audio(
        project_root,
        stage_number,
        primary,
        source_video,
        sample_rate=sample_rate,
        ffmpeg_binary=ffmpeg_binary,
        project=project,
    )


def ensure_video_audio(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    source_video: Path | str,
    *,
    sample_rate: int = 48000,
    ffmpeg_binary: str = "ffmpeg",
    project: MatchProject | None = None,
) -> Path:
    """Extract a mono WAV for ``video`` if not already cached.

    Cache lands at ``<audio_dir>/stage<N>_cam_<video_id>.wav`` regardless of
    role. Re-extracts when the source mtime is newer than the cache.

    ``source_video`` may be a presigned URL (``str``, #1674): ffmpeg reads it
    over HTTP and nothing lands on this disk. A cached WAV then has no source
    to date it against, as for a trim cut from a URL, and a read that reports
    errors raises ``trim.SourceReadError``.
    """
    audio_path = video_audio_path(project_root, stage_number, video, project=project)
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    # Legacy-id read fallback: the take-spec video_id change orphaned
    # WAVs keyed by the old path-only hash. When the new-keyed WAV is
    # absent, serve the legacy one if the registration is unambiguous
    # (single-registration gate in _legacy_cache_fallback) and it is not
    # stale - an unreachable source counts as not-stale because the
    # cache is then the only thing we can serve at all. Fresh
    # extractions below keep writing the new-keyed name. Remove once a
    # cache migration tool exists.
    if not (audio_path.exists() and audio_path.stat().st_size > 0):
        legacy_wav = audio_path.with_name(f"stage{stage_number}_cam_{legacy_video_id(video)}.wav")
        legacy = _legacy_cache_fallback(project, video, audio_path, legacy_wav)
        if legacy is not None:
            if isinstance(source_video, str):
                return legacy
            src_probe = source_video.resolve()
            if not src_probe.exists() or legacy.stat().st_mtime >= src_probe.stat().st_mtime:
                return legacy

    src_args, src_resolved = _source_input(source_video)

    # Storage cache (hosted mode): another worker may have already
    # extracted this WAV. Pulling 5 MB from S3 beats running ffmpeg
    # against a 500 MB raw video; local mode has no storage and skips it.
    # A raw source's audio can end before its video, so the WAV is held to
    # being readable and non-empty only.
    _publish_audio(
        project,
        audio_path,
        fresh=lambda: _cached_against(audio_path, src_resolved),
        extract=lambda out: _run_audio_ffmpeg(
            [*src_args, "-ac", "1", "-ar", str(sample_rate), "-vn"],
            out,
            ffmpeg_binary,
            url=src_resolved is None,
        ),
    )
    return audio_path


def video_window_audio_path(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    start_s: float,
    end_s: float,
    *,
    project: MatchProject | None = None,
) -> Path:
    """Cache path for a window-sliced WAV. The window rides in the name
    (milliseconds) so a changed window is a new cache slot, and the
    basename carries the video_id so the storage-cache key helpers work
    unchanged."""
    audio_dir = project.audio_path(project_root) if project else project_root / "audio"
    return audio_dir / (
        f"stage{stage_number}_cam_{video.video_id}"
        f"_win_{int(round(start_s * 1000))}_{int(round(end_s * 1000))}.wav"
    )


def ensure_video_window_audio(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    source_video: Path | str,
    start_s: float,
    end_s: float,
    *,
    sample_rate: int = 48000,
    ffmpeg_binary: str = "ffmpeg",
    project: MatchProject | None = None,
) -> Path:
    """Extract the [start_s, end_s) mono slice of source_video if not cached.

    -ss before -i seeks on the demuxer (fast on long files, and over a
    presigned URL a range request); detection adds start_s back onto every
    returned timestamp so results stay in source-absolute seconds.
    """
    audio_path = video_window_audio_path(project_root, stage_number, video, start_s, end_s, project=project)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    src_args, src_resolved = _source_input(source_video)
    _publish_audio(
        project,
        audio_path,
        fresh=lambda: _cached_against(audio_path, src_resolved),
        extract=lambda out: _run_audio_ffmpeg(
            [
                "-ss",
                str(start_s),
                "-t",
                str(end_s - start_s),
                *src_args,
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-vn",
            ],
            out,
            ffmpeg_binary,
            url=src_resolved is None,
        ),
    )
    return audio_path


def take_audio_path(
    project_root: Path,
    storage_path: str,
    *,
    project: MatchProject | None = None,
) -> Path:
    """Cache path for a take-level 8 kHz mono WAV.

    Keyed by a blake2s(storage_path) hash so the name stays short and
    collision-free across files with the same basename in different folders.
    The 8 kHz rate keeps the file small (about 1.8 MB/min) while retaining
    enough resolution for a whole-file envelope waveform.

      <audio_dir>/take_<blake2s(storage_path, digest_size=6).hexdigest()>.wav
    """
    audio_dir = project.audio_path(project_root) if project else project_root / "audio"
    h = hashlib.blake2s(storage_path.encode(), digest_size=6).hexdigest()
    return audio_dir / f"take_{h}.wav"


def ensure_take_audio(
    project_root: Path,
    storage_path: str,
    source: Path,
    *,
    sample_rate: int = 8000,
    ffmpeg_binary: str = "ffmpeg",
    project: MatchProject | None = None,
) -> Path:
    """Extract a mono 8 kHz WAV for the whole take if not already cached.

    Same extract/cache/storage-push pattern as :func:`ensure_video_audio`.
    The low sample rate keeps the take-level file small; it is only used
    for the overview waveform, not for beep detection (which uses the
    per-stage window WAV at 48 kHz).
    """
    audio_path = take_audio_path(project_root, storage_path, project=project)
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    src_resolved = source.resolve()
    if not src_resolved.exists():
        raise FileNotFoundError(f"video missing on disk: {src_resolved}")

    _publish_audio(
        project,
        audio_path,
        fresh=lambda: _not_older(audio_path, src_resolved),
        extract=lambda out: _run_audio_ffmpeg(
            ["-i", str(src_resolved), "-ac", "1", "-ar", str(sample_rate), "-vn"], out, ffmpeg_binary
        ),
    )
    return audio_path


def trimmed_video_path(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    *,
    project: MatchProject,
) -> Path:
    """Resolve the cached audit-mode short-GOP MP4 for ``video`` on a stage.

    Keyed by ``video_id`` for every role so each angle has its own scrub
    clip cut around its own beep.
    """
    return project.trimmed_path(project_root) / audit_trim_name(stage_number, video.video_id)


def trimmed_primary_path(project_root: Path, stage_number: int, *, project: MatchProject) -> Path:
    """Resolve the cached short-GOP trimmed MP4 path for a stage's primary."""
    primary = project.stage(stage_number).primary()
    if primary is None:
        raise KeyError(f"stage {stage_number} has no primary video")
    return trimmed_video_path(project_root, stage_number, primary, project=project)


def video_audit_audio_path(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    *,
    project: MatchProject,
) -> Path:
    """Cache path for ``video``'s audit WAV (extracted from its trimmed MP4)."""
    audio_dir = project.audio_path(project_root)
    return audio_dir / f"stage{stage_number}_cam_{video.video_id}_audit.wav"


def audit_audio_path(
    project_root: Path,
    stage_number: int,
    *,
    project: MatchProject,
) -> Path:
    """Cache path for the current primary's audit WAV on ``stage_number``."""
    primary = project.stage(stage_number).primary()
    if primary is None:
        raise KeyError(f"stage {stage_number} has no primary video")
    return video_audit_audio_path(project_root, stage_number, primary, project=project)


def ensure_audit_audio(
    project_root: Path,
    stage_number: int,
    resolve_primary_source: Callable[[], Path],
    primary_beep_time: float | None,
    *,
    project: MatchProject,
    sample_rate: int = 48000,
    ffmpeg_binary: str = "ffmpeg",
    web_fallback: bool = False,
) -> AuditAudioResult:
    """Resolve the WAV the audit screen should serve for ``stage_number``.

    ``web_fallback`` (a web-only mirror, spec 2026-09-27 v1.1): with no
    full trim, extract the audit audio from the ``_web.mp4`` rendition,
    which covers the trim's exact window, before falling back to the
    source (which a mirror never has).

    Prefers the trimmed clip's audio (short-GOP MP4 produced by
    :mod:`splitsmith.trim`). When the trimmed clip is missing, falls back
    to the full primary WAV so the audit screen still loads -- you'll see
    the entire source clip with the original (slow) scrub feel. Either way
    the result is mtime-cached.

    ``resolve_primary_source`` is a thunk, not a ``Path``, so the source is
    only materialized on the no-trim fallback. In hosted mode resolving it
    mirrors the whole source from R2 to local disk (#589); the common
    trimmed path pulls only the small trim + WAV from storage and must never
    trigger that download (#592).
    """
    trimmed_video = trimmed_primary_path(project_root, stage_number, project=project)
    # Hosted: the trim was cut on a worker, not here. Pull it down so the
    # audit screen serves the trimmed-window waveform instead of falling
    # back to the full source WAV (wrong extent + wrong beep position).
    # No-op in local mode (no storage bound).
    if not trimmed_video.exists():
        _try_pull_trim_from_storage(project, trimmed_video)
    # If a prior trim was interrupted, ensure_audit_trim leaves no .partial
    # behind, but be defensive: ignore an obviously-broken trim by checking
    # for a non-empty file. Anything more sophisticated (full ffprobe round
    # trip) would slow down the hot path; the partial file is now atomic.
    if trimmed_video.exists() and trimmed_video.stat().st_size > 0:
        audio_path = audit_audio_path(project_root, stage_number, project=project)
        # Cut from the trim, never pulled from storage: a job runs this when
        # the readers found storage's copy missing or older than the trim,
        # and a pulled copy of the same length passed the length check and
        # was published again unchanged (review, 2026-10-14). As long as
        # the trim, and pushed for the API to read.
        _publish_audio(
            project,
            audio_path,
            fresh=lambda: _not_older(audio_path, trimmed_video),
            extract=lambda out: _extract_audio(trimmed_video, out, sample_rate, ffmpeg_binary),
            expect_seconds=_own_media_seconds(trimmed_video),
            pull_storage=False,
        )
        # Beep position inside the trimmed clip = beep_in_source minus
        # trim_start. trim_start = max(0, beep - pre_buffer); so:
        #   beep_in_clip = min(beep_in_source, pre_buffer)
        beep_in_clip = (
            min(primary_beep_time, project.trim_pre_buffer_seconds) if primary_beep_time is not None else None
        )
        return AuditAudioResult(audio_path=audio_path, beep_in_clip=beep_in_clip, trimmed=True)

    if web_fallback:
        web = try_pull_web_trim(project, trimmed_video)
        if web is not None:
            audio_path = audit_audio_path(project_root, stage_number, project=project)
            # Never pulled from storage here: a WAV there may have been cut
            # from an older rendition (the readers check that against the
            # rendition's own upload). Pushed once cut: this runs in a job,
            # often on the worker, and the API serves what storage holds.
            _publish_audio(
                project,
                audio_path,
                fresh=lambda: _not_older(audio_path, web),
                extract=lambda out: _extract_audio(web, out, sample_rate, ffmpeg_binary),
                expect_seconds=_own_media_seconds(web),
                pull_storage=False,
            )
            # The anchor is the trim's, from the pushed params sidecar: the
            # trim may have been cut with a different pre-buffer than the
            # project's current setting.
            pre_buffer = trim_pre_buffer_seconds_for(trimmed_video, default=project.trim_pre_buffer_seconds)
            beep_in_clip = min(primary_beep_time, pre_buffer) if primary_beep_time is not None else None
            return AuditAudioResult(audio_path=audio_path, beep_in_clip=beep_in_clip, trimmed=True)

    fallback = ensure_primary_audio(
        project_root,
        stage_number,
        resolve_primary_source(),
        sample_rate=sample_rate,
        ffmpeg_binary=ffmpeg_binary,
        project=project,
    )
    return AuditAudioResult(
        audio_path=fallback,
        beep_in_clip=primary_beep_time,
        trimmed=False,
    )


#: Pads a hole in the audio timestamps with silence. Two clips joined with
#: ffmpeg leave one (a 0.59 s hole 22 s into a Pixel stage); without this the
#: decode closes it, the WAV comes out short of the clip, and every sample
#: after the join sits early against the video.
_FILL_GAPS = "aresample=async=1:first_pts=0"


def _extract_audio(
    source: Path,
    dest: Path,
    sample_rate: int,
    ffmpeg_binary: str,
) -> None:
    """Run ffmpeg to drop a mono WAV at ``dest`` from ``source``."""
    if not shutil.which(ffmpeg_binary):
        raise AudioExtractionError(f"ffmpeg binary not found: {ffmpeg_binary}")
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-af",
        _FILL_GAPS,
        "-vn",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise AudioExtractionError(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc


def _source_input(source: Path | str) -> tuple[list[str], Path | None]:
    """ffmpeg's input arguments for a source on this disk or at a presigned
    URL, and the file a cached WAV is dated against (``None`` for a URL)."""
    from ..proxy import HTTP_INPUT_ARGS

    if isinstance(source, str):
        return [*HTTP_INPUT_ARGS, "-i", source], None
    resolved = source.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"video missing on disk: {resolved}")
    return ["-i", str(resolved)], resolved


def _cached_against(path: Path, source: Path | None) -> bool:
    """``path`` is a usable cache: not older than ``source``, or, with no
    source on this disk to date it by, present and non-empty."""
    if source is not None:
        return _not_older(path, source)
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _run_audio_ffmpeg(args: list[str], out: Path, ffmpeg_binary: str, *, url: bool = False) -> None:
    """ffmpeg ``args`` writing a WAV to ``out`` (a partial: see
    :func:`_publish_audio`). With ``url`` the input is a presigned URL: a
    failed or noisy read raises ``trim.SourceReadError`` with the URL
    redacted, since a dropped read can exit 0 with a short WAV."""
    from .. import trim as trim_module
    from ..proxy import redact_urls

    if not shutil.which(ffmpeg_binary):
        raise AudioExtractionError(f"ffmpeg binary not found: {ffmpeg_binary}")
    cmd = [ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-y", *args, str(out)]
    try:
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        if url:
            detail = redact_urls(exc.stderr or exc.stdout)
            raise trim_module.SourceReadError(f"ffmpeg failed (exit {exc.returncode}): {detail!r}") from None
        raise AudioExtractionError(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc
    if url and (completed.stderr or "").strip():
        raise trim_module.SourceReadError(
            f"ffmpeg reported errors reading the source: {redact_urls(completed.stderr).strip()!r}"
        )


def _not_older(path: Path, source: Path) -> bool:
    """``path`` exists and is not older than ``source``."""
    try:
        return path.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        return False


def _own_media_seconds(path: Path) -> float | None:
    """The length of a trim or rendition we cut ourselves (its ``mvhd``),
    which the audio cut from it must match; None when it does not parse.
    Not for raw camera files, whose audio may legitimately end early."""
    check = valid_mp4_cached(path)
    return check.duration_s if check.ok else None


def _publish_audio(
    project: MatchProject | None,
    audio_path: Path,
    *,
    fresh: Callable[[], bool],
    extract: Callable[[Path], None],
    expect_seconds: float | None = None,
    pull_storage: bool = True,
) -> None:
    """Publish ``audio_path`` whole (``derived.produce``): from the storage
    cache when it holds a usable copy, else by ``extract``; pushed up when
    extracted here. ffmpeg exits 0 on an input still being written and
    leaves a short WAV, which was served once (2026-10-13), so a WAV shorter
    than ``expect_seconds`` is never published, from storage or ffmpeg."""
    from_storage = False

    def check(p: Path) -> str | None:
        return derived.wav_problem(p, expect_seconds=expect_seconds)

    def make(partial: Path) -> None:
        nonlocal from_storage
        if pull_storage and _try_pull_audio_from_storage(project, audio_path, dest=partial):
            problem = check(partial)
            if problem is None:
                from_storage = True
                return
            logger.info("audio cache: refused storage copy of %s: %s", audio_path.name, problem)
            partial.unlink(missing_ok=True)
        extract(partial)

    try:
        produced = derived.produce(audio_path, fresh=fresh, make=make, check=check)
    except derived.DerivedFileError as exc:
        raise AudioExtractionError(str(exc)) from exc
    if produced and not from_storage:
        _try_push_audio_to_storage(project, audio_path)


def ensure_video_audit_wav(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    *,
    project: MatchProject,
    sample_rate: int = 48000,
    ffmpeg_binary: str = "ffmpeg",
) -> Path:
    """Cut ``video``'s audit WAV from its trim, published whole and pushed to
    storage: the trim job's second product (spec 2026-10-14), so the audit
    screen reads it rather than a request extracting it. Raises
    ``FileNotFoundError`` when the trim is not on this disk."""
    trimmed = trimmed_video_path(project_root, stage_number, video, project=project)
    if not _usable_mp4(trimmed):
        raise FileNotFoundError(f"no usable trim for {trimmed.name}")
    audio_path = video_audit_audio_path(project_root, stage_number, video, project=project)
    _publish_audio(
        project,
        audio_path,
        fresh=lambda: _not_older(audio_path, trimmed),
        extract=lambda out: _extract_audio(trimmed, out, sample_rate, ffmpeg_binary),
        expect_seconds=_own_media_seconds(trimmed),
        pull_storage=False,
    )
    return audio_path


# --- Readers: what the request handlers call (spec 2026-10-14) ----------------
#
# A request never derives media: no ffmpeg, no decode of a source, no mirror
# of a large source or trim from storage. These lookups answer with a WAV a
# job already published (on this disk, or downloaded whole from storage), or
# None, and the route then submits the producing job and answers 202.


def _storage_stat(project: MatchProject | None, key: str | None) -> StorageObject | None:
    if key is None or project is None or project._storage is None:
        return None
    try:
        return project._storage.stat(key)
    except Exception as exc:  # noqa: BLE001 -- a storage hiccup is a miss
        logger.info("audio read: storage.stat(%s) raised %s", key, exc)
        return None


def _stored_at(obj: StorageObject | None) -> float | None:
    return obj.last_modified.timestamp() if obj is not None and obj.last_modified is not None else None


def _published_wav(project: MatchProject | None, wav: Path, *, cut_from: StorageObject | None = None) -> bool:
    """``wav`` is on this disk, or downloaded whole from storage now. With
    ``cut_from`` (the clip the WAV is cut from, as stored), a copy is current
    only when it is not older than that clip: a WAV of an older window must
    not be served for a re-cut one."""
    since = _stored_at(cut_from)
    try:
        st = wav.stat()
        if st.st_size > 0 and (since is None or st.st_mtime >= since) and derived.wav_problem(wav) is None:
            return True
    except OSError:
        pass
    key = _storage_audio_key(project, wav)
    remote = _storage_stat(project, key)
    if remote is None:
        return False
    stored = _stored_at(remote)
    if since is not None and (stored is None or stored < since):
        return False
    return derived.download(project._storage, key, wav, check=derived.wav_problem)  # type: ignore[union-attr]


def published_audit_audio(
    project_root: Path,
    stage_number: int,
    primary_beep_time: float | None,
    *,
    project: MatchProject,
) -> AuditAudioResult | None:
    """The audit WAV :func:`ensure_audit_audio` would serve, if a job has
    published it; None when it still has to be made. On a web-only project
    (:func:`stores_web_only`) the stored rendition dates the WAV when there
    is no stored trim."""
    trimmed_video = trimmed_primary_path(project_root, stage_number, project=project)
    audio_path = audit_audio_path(project_root, stage_number, project=project)
    from .. import trim as trim_module

    web = trim_module.web_trim_path(trimmed_video)
    web_only = stores_web_only(project)
    if _usable_mp4(trimmed_video):
        clip: StorageObject | None = None
        if _not_older(audio_path, trimmed_video) and derived.wav_problem(audio_path) is None:
            return _trimmed_result(audio_path, primary_beep_time, project.trim_pre_buffer_seconds)
        clip = _storage_stat(project, _storage_trim_key(project, trimmed_video))
        if clip is None and web_only:
            clip = _storage_stat(project, _storage_trim_key(project, web))
        if clip is not None and _published_wav(project, audio_path, cut_from=clip):
            return _trimmed_result(audio_path, primary_beep_time, project.trim_pre_buffer_seconds)
        return None
    trim_stored = _storage_stat(project, _storage_trim_key(project, trimmed_video))
    if trim_stored is not None:
        if _published_wav(project, audio_path, cut_from=trim_stored):
            return _trimmed_result(audio_path, primary_beep_time, project.trim_pre_buffer_seconds)
        return None
    web_stored = _storage_stat(project, _storage_trim_key(project, web)) if web_only else None
    if web_only and (web_stored is not None or _usable_mp4(web)):
        if _published_wav(project, audio_path, cut_from=web_stored):
            if current_match_origin.get() != "desktop":
                # A hosted-native trim is cut with the project's buffers, as
                # a stored trim's anchor above assumes: no params read.
                return _trimmed_result(audio_path, primary_beep_time, project.trim_pre_buffer_seconds)
            # A mirror's anchor is the trim's, from the pushed params sidecar:
            # the desktop may have cut it with a different pre-buffer than the
            # project's current setting.
            refresh_trim_params(project, trimmed_video)
            pre_buffer = trim_pre_buffer_seconds_for(trimmed_video, default=project.trim_pre_buffer_seconds)
            return _trimmed_result(audio_path, primary_beep_time, pre_buffer)
        return None
    if current_match_origin.get() == "desktop":
        # A mirror has no source WAV to fall back to.
        return None
    # No trim anywhere: the primary's full source WAV, as ensure_audit_audio
    # falls back to.
    primary = project.stage(stage_number).primary()
    if primary is None:
        return None
    full = published_video_audio(project_root, stage_number, primary, project=project)
    if full is None:
        return None
    return AuditAudioResult(audio_path=full, beep_in_clip=primary_beep_time, trimmed=False)


def _trimmed_result(audio_path: Path, beep: float | None, pre_buffer: float) -> AuditAudioResult:
    # Beep position inside the trimmed clip = beep_in_source minus
    # trim_start. trim_start = max(0, beep - pre_buffer); so:
    #   beep_in_clip = min(beep_in_source, pre_buffer)
    return AuditAudioResult(
        audio_path=audio_path,
        beep_in_clip=min(beep, pre_buffer) if beep is not None else None,
        trimmed=True,
    )


def published_video_audio(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    *,
    project: MatchProject,
    source: Path | None = None,
) -> Path | None:
    """``video``'s full source WAV (:func:`ensure_video_audio`), if a job has
    published it. ``source``, when it is on this disk, must not be newer."""
    wav = video_audio_path(project_root, stage_number, video, project=project)
    if source is not None:
        try:
            if wav.stat().st_mtime < source.stat().st_mtime:
                return None
        except OSError:
            pass
    return wav if _published_wav(project, wav) else None


def published_take_audio(
    project_root: Path, storage_path: str, *, project: MatchProject | None = None
) -> Path | None:
    """A raw file's take WAV (:func:`ensure_take_audio`), if published."""
    wav = take_audio_path(project_root, storage_path, project=project)
    return wav if _published_wav(project, wav) else None


def _storage_audio_key(project: MatchProject | None, local_wav: Path) -> str | None:
    """Compute the storage key for an extracted audio WAV.

    Returns ``None`` in local mode (no storage bound) or when no
    per-project scope is set -- both cases skip the storage cache.
    The key mirrors the local layout under the project's scope:
    ``<scope>/audio/<basename>``. The basename already includes the
    ``video_id``, so collisions across shooters in different matches
    are prevented by ``<scope>``.
    """
    if project is None or project._storage is None or project._storage_scope is None:
        return None
    return f"{project._storage_scope}/audio/{local_wav.name}"


def _try_pull_audio_from_storage(
    project: MatchProject | None, local_wav: Path, *, dest: Path | None = None
) -> bool:
    """If the WAV exists in the project's storage cache, download it
    into ``dest`` (a partial of ``local_wav``, whose name keys the cache)
    and return True. Returns False when no storage is bound, the key is
    absent, or the download fails.

    Best-effort by design: a storage hiccup falls through to ffmpeg
    rather than failing the detection job. Logs at INFO so the
    operator can spot a misbehaving backend without it being noisy.
    """
    key = _storage_audio_key(project, local_wav)
    if key is None:
        return False
    storage = project._storage  # type: ignore[union-attr]
    try:
        if not storage.exists(key):
            return False
    except Exception as exc:
        logger.info("audio cache: storage.exists(%s) raised %s", key, exc)
        return False
    target = dest if dest is not None else local_wav
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with storage.open_stream(key) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return True
    except Exception as exc:
        logger.info("audio cache: pull from %s failed: %s", key, exc)
        # Leave nothing half-written behind so the ffmpeg fallback
        # doesn't trip over a torn file.
        target.unlink(missing_ok=True)
        return False


def _try_push_audio_to_storage(project: MatchProject | None, local_wav: Path) -> None:
    """Push a freshly-extracted WAV to the project's storage cache.

    Best-effort: a push failure logs and returns; the worker still
    has the local WAV and can serve the current job. The next
    worker on this project will re-extract -- annoying but not
    incorrect.
    """
    key = _storage_audio_key(project, local_wav)
    if key is None:
        return
    storage = project._storage  # type: ignore[union-attr]
    try:
        with local_wav.open("rb") as f:
            storage.upload_stream(key, f)
    except Exception as exc:
        logger.info("audio cache: push to %s failed: %s", key, exc)


def _current_trim_params(
    *,
    primary_beep_time: float,
    stage_time_seconds: float,
    project: MatchProject,
) -> dict[str, float]:
    return audit_trim_params(
        beep_time=primary_beep_time,
        stage_time_seconds=stage_time_seconds,
        pre_buffer_seconds=project.trim_pre_buffer_seconds,
        post_buffer_seconds=project.trim_post_buffer_seconds,
    )


def invalidate_video_audit_trim(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    *,
    project: MatchProject,
    stored: bool = False,
) -> None:
    """Delete the cached trim, audit WAV, and params sidecar for ``video``.

    Called after beep_time changes (manual override) or stage_time changes
    (scoreboard re-import) so the next trim job runs from scratch instead
    of trusting a now-stale cache. Idempotent if any file is missing.

    ``stored`` also deletes the storage cache's copies (trim, params, web
    rendition, audit WAV): a hosted reader keys on their existence, so a
    trim cut for the old beep would play under the new one until the next
    cut lands. The trim and detect job bodies pass it. No job of either kind
    is queued for a desktop mirror today (its routes and confirms chain
    none), and the copies of a mirror belong to the desktop that pushed
    them, so a desktop-origin match never drops them here either.
    Best-effort; a no-op without storage (local mode).
    """
    if stored and current_match_origin.get() != "desktop":
        _drop_stored_trim(project, project_root, stage_number, video)
    from .. import trim as trim_module

    output = trimmed_video_path(project_root, stage_number, video, project=project)
    wav = video_audit_audio_path(project_root, stage_number, video, project=project)
    params = trim_params_path(output)
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    web = trim_module.web_trim_path(output)
    targets = [output, wav, params, partial, web, *_web_partials(web)]
    # Also sweep the pre-take-spec legacy-keyed trim + sidecar: the
    # legacy read fallback (resolve_trim_for_read) would otherwise keep
    # serving a cut for the OLD beep/stage time after this invalidation.
    legacy_mp4 = output.with_name(f"stage{stage_number}_cam_{legacy_video_id(video)}_trimmed.mp4")
    if legacy_mp4 != output:
        targets += [legacy_mp4, trim_params_path(legacy_mp4)]
    for p in targets:
        try:
            if p.exists() or p.is_symlink():
                p.unlink()
        except OSError:
            pass


def _drop_stored_trim(
    project: MatchProject, project_root: Path, stage_number: int, video: StageVideo
) -> None:
    """Delete ``video``'s trim, params sidecar, web rendition and audit WAV
    from the storage cache. No-op in local mode."""
    from .. import trim as trim_module

    output = trimmed_video_path(project_root, stage_number, video, project=project)
    wav = video_audit_audio_path(project_root, stage_number, video, project=project)
    keys = [
        _storage_trim_key(project, output),
        _storage_trim_key(project, trim_params_path(output)),
        _storage_trim_key(project, trim_module.web_trim_path(output)),
        _storage_audio_key(project, wav),
    ]
    storage = project._storage
    for key in keys:
        if key is None or storage is None:
            continue
        try:
            storage.delete(key)
        except Exception as exc:  # noqa: BLE001 - best-effort, like the local sweep
            logger.info("trim cache: delete %s failed: %s", key, exc)


def audit_trim_current(
    project: MatchProject | None,
    trim: Path,
    params: dict[str, float],
    *,
    source: Path | None = None,
) -> bool:
    """Whether the cut at ``trim`` is current, locally or in the storage
    cache, by the one rule (:func:`splitsmith.trim.audit_trim_matches`, which
    :func:`ensure_video_audit_trim` reuses a cut by): its params sidecar
    records exactly ``params`` and the cut is not older than ``source`` when
    that is on this disk. A local trim with its sidecar answers for itself;
    otherwise the stored cut answers, by its object's time and its stored
    sidecar (a few bytes). A web-only project stores the rendition in the
    trim's place (#1683); a legacy full trim still counts while it is
    there. Never downloads the trim."""
    from .. import trim as trim_module

    params_file = trim_params_path(trim)
    try:
        if trim.exists() and trim.stat().st_size > 0 and params_file.exists():
            return audit_trim_matches(trim, params, source=source)
    except OSError:
        return False
    mp4_key = _storage_trim_key(project, trim)
    params_key = _storage_trim_key(project, params_file)
    if mp4_key is None or params_key is None:
        return False
    storage = project._storage  # type: ignore[union-attr]
    newer_source = trim_source_mtime(source)
    try:
        meta = storage.stat(mp4_key) or storage.stat(
            _storage_trim_key(project, trim_module.web_trim_path(trim))
        )
        if meta is None:
            return False
        if newer_source is not None and (
            meta.last_modified is None or meta.last_modified.timestamp() < newer_source
        ):
            return False
        return json.loads(storage.read_bytes(params_key)) == params
    except Exception as exc:  # noqa: BLE001 - a miss only costs a cache-hit trim job
        logger.info("trim cache: params check for %s failed: %s", trim.name, exc)
        return False


def trim_params_current(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    stage_time_seconds: float,
    *,
    project: MatchProject,
    source: Path | None = None,
) -> bool:
    """Whether ``video``'s trim is in place (locally or in the storage cache)
    and current for its beep, stage time and buffers and ``source``: the
    rule :func:`ensure_video_audit_trim` reuses a trim by
    (:func:`audit_trim_current`). Never downloads the trim."""
    if video.beep_time is None:
        return False
    current = _current_trim_params(
        primary_beep_time=video.beep_time, stage_time_seconds=stage_time_seconds, project=project
    )
    output = trimmed_video_path(project_root, stage_number, video, project=project)
    return audit_trim_current(project, output, current, source=source)


def stored_trim_current(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    stage_time_seconds: float,
    *,
    project: MatchProject,
) -> bool:
    """Whether a web-only project's storage holds everything the trim job
    publishes for ``video``, cut for its current beep, stage time and buffers
    (#1683): the params sidecar, the rendition, and the audit WAV not older
    than the rendition. A job that finds them needs neither the source nor a
    cut; with no full trim in storage, the cut is all it would otherwise do."""
    from .. import trim as trim_module

    if not stores_web_only(project):
        return False
    if not trim_params_current(project_root, stage_number, video, stage_time_seconds, project=project):
        return False
    output = trimmed_video_path(project_root, stage_number, video, project=project)
    web = _stored_at(_storage_stat(project, _storage_trim_key(project, trim_module.web_trim_path(output))))
    wav_path = video_audit_audio_path(project_root, stage_number, video, project=project)
    wav = _stored_at(_storage_stat(project, _storage_audio_key(project, wav_path)))
    return web is not None and wav is not None and wav >= web


class TrimSuperseded(Exception):  # noqa: N818 - a state, not a failure
    """The beep moved while the trim was cutting; the cut was dropped unpublished."""


def _mp4_problem(path: Path) -> str | None:
    check = valid_mp4(path)
    return None if check.ok else check.reason


def _usable_mp4(path: Path) -> bool:
    try:
        return path.stat().st_size > 0 and valid_mp4_cached(path).ok
    except OSError:
        return False


def _storage_trim_key(project: MatchProject | None, local_mp4: Path) -> str | None:
    """Compute the storage key for an audit-trim MP4.

    Returns ``None`` in local mode (no storage bound) or when no
    per-project scope is set -- both cases skip the storage cache.
    Mirrors :func:`_storage_audio_key`: the key is
    ``<scope>/trimmed/<basename>`` and the basename already carries the
    ``video_id``, so two shooters in different matches can't collide.
    """
    if project is None or project._storage is None or project._storage_scope is None:
        return None
    return f"{project._storage_scope}/trimmed/{local_mp4.name}"


def stores_web_only(project: MatchProject | None) -> bool:
    """Whether ``project``'s trims live in storage as the web rendition and
    its params sidecar alone, with no full-resolution trim (#1683).

    True for every storage-backed project: a desktop mirror by design (spec
    2026-09-27 v1.1), a hosted-native match because its full trim (about
    three times its source on 4K footage) is read by nothing but the job
    that cut it, and costs egress on every write from the worker. A legacy
    full trim already in storage is still served while it is there."""
    return _storage_trim_key(project, Path("probe.mp4")) is not None


def release_local_trim(
    project_root: Path, stage_number: int, video: StageVideo, *, project: MatchProject
) -> None:
    """Delete ``video``'s full trim from this worker once its products are
    published, when the worker's disk is memory (Cloud Run). A web-only
    project's storage never holds the trim (#1683) and every later job reads
    the stored rendition and WAV, so the about 1 GB a 4K trim takes would only
    crowd the next job until the per-job sweep reached it. A box with a real
    disk keeps it: a later job there reuses it instead of cutting again."""
    from ..source_cache import memory_backed_disk

    if not stores_web_only(project) or not memory_backed_disk():
        return
    output = trimmed_video_path(project_root, stage_number, video, project=project)
    with derived.locked(output, "cut"):
        output.unlink(missing_ok=True)


def _try_pull_trim_from_storage(project: MatchProject | None, local_mp4: Path) -> bool:
    """If the audit trim exists in the project's storage cache, download
    it (and its params sidecar) into place and return True.

    Unlike the WAV cache, the trim's validity is checked against the
    ``*.params.json`` sidecar in :func:`ensure_video_audit_trim`, so we
    pull the sidecar alongside the MP4 -- the caller's existing cache_hit
    check then decides whether the pulled trim is trustworthy or must be
    re-cut. The sidecar is optional: a torn/legacy cache without one is
    treated as a miss by the validation and re-cut.

    Best-effort: a storage hiccup falls through to ffmpeg rather than
    failing the job. On any failure the half-written MP4 is removed so the
    re-cut path doesn't trip over a torn file.
    """
    key = _storage_trim_key(project, local_mp4)
    if key is None:
        return False
    storage = project._storage  # type: ignore[union-attr]
    try:
        if not storage.exists(key):
            return False
    except Exception as exc:
        logger.info("trim cache: storage.exists(%s) raised %s", key, exc)
        return False
    params_local = trim_params_path(local_mp4)
    params_key = f"{key.rsplit('/', 1)[0]}/{params_local.name}"
    # Whole or not at all (``derived.download``): a request reading the trim
    # mid-pull took a half file for the real one.
    if not derived.download(
        storage, key, local_mp4, fresh=lambda: _usable_mp4(local_mp4), check=_mp4_problem
    ):
        return False
    # Pull the sidecar when present; its absence just forces a re-cut.
    derived.download(storage, params_key, params_local)
    return True


def refresh_trim_params(project: MatchProject | None, trim_path: Path) -> None:
    """Bring the local params sidecar of a web-only mirror's trim in line
    with storage (spec 2026-09-27 v1.1). The desktop re-pushes it whenever it
    re-trims around a moved beep; a changed sidecar also drops the local
    rendition, so nothing local serves the old window. The audit WAV cut
    from it is refused by the readers once the new rendition is stored
    (older than its clip) and made again by a job. One small GET per call;
    a storage failure keeps whatever is local."""
    from .. import trim as trim_module

    params = trim_params_path(trim_path)
    key = _storage_trim_key(project, params)
    if key is None:
        return
    storage = project._storage  # type: ignore[union-attr]
    try:
        remote = storage.read_bytes(key)
    except Exception as exc:
        logger.info("web trim cache: params read %s failed: %s", key, exc)
        return
    try:
        local = params.read_bytes()
    except OSError:
        local = None
    if local == remote:
        return
    derived.produce(params, fresh=lambda: False, make=lambda p: p.write_bytes(remote))
    trim_module.web_trim_path(trim_path).unlink(missing_ok=True)


def try_pull_web_trim(project: MatchProject | None, trim_path: Path) -> Path | None:
    """The rendition standing in for the audit trim at ``trim_path`` on a
    web-only mirror (spec 2026-09-27 v1.1): refresh the params sidecar, then
    pull ``_web.mp4`` when not already local. Returns the local rendition
    path, or None when there is no rendition. Best-effort like the trim
    pull: a storage failure reads as "no rendition"."""
    from .. import trim as trim_module

    refresh_trim_params(project, trim_path)
    web = trim_module.web_trim_path(trim_path)
    if web.exists() and web.stat().st_size > 0:
        return web
    key = _storage_trim_key(project, web)
    if key is None:
        return None
    storage = project._storage  # type: ignore[union-attr]
    # Whole or not at all: on 2026-10-13 a request extracted the audit WAV
    # from a rendition another request was still streaming into this path,
    # and the phone was served a stage 17.7 s short.
    if derived.download(storage, key, web, fresh=lambda: _usable_mp4(web), check=_mp4_problem):
        return web
    return None


def _try_push_trim_to_storage(project: MatchProject | None, local_mp4: Path) -> None:
    """Push a freshly-cut audit trim to the project's storage cache (its
    params sidecar went up before it). Only when its web rendition could not
    be cut (#1683): the full trim is then the stage's one playable clip.

    Best-effort: a push failure logs and returns; the local trim is the
    source of truth for the current job.
    """
    key = _storage_trim_key(project, local_mp4)
    if key is None:
        return
    storage = project._storage  # type: ignore[union-attr]
    try:
        with local_mp4.open("rb") as f:
            storage.upload_stream(key, f)
    except Exception as exc:
        logger.info("trim cache: push to %s failed: %s", key, exc)


def _push_trim_params(project: MatchProject | None, local_mp4: Path) -> None:
    """Push the params sidecar of a freshly-cut trim: with the rendition, it
    is what a web-only project's storage holds of the trim (#1683), the
    window and beep anchor every reader and the next job's reuse check go
    by. Best-effort like the trim push."""
    params_local = trim_params_path(local_mp4)
    key = _storage_trim_key(project, params_local)
    if key is None or not params_local.exists():
        return
    try:
        project._storage.write_bytes(key, params_local.read_bytes())  # type: ignore[union-attr]
    except Exception as exc:
        logger.info("trim cache: params push to %s failed: %s", key, exc)


def _web_partials(web: Path) -> list[Path]:
    """Every partial a rendition writer can leave: per-process ones
    (``trim.unique_partial``) and the old shared name."""
    old_style = web.with_name(f"{web.stem}.partial{web.suffix}")
    return [*web.parent.glob(f"{web.stem}.*partial{web.suffix}"), old_style]


def _try_pull_web_trim_from_storage(project: MatchProject | None, web: Path) -> bool:
    """Mirror the web rendition down from the storage cache if it is there.
    Torn downloads are removed so a half file is never served or trusted."""
    key = _storage_trim_key(project, web)
    if key is None:
        return False
    storage = project._storage  # type: ignore[union-attr]
    return derived.download(storage, key, web, fresh=lambda: _usable_mp4(web), check=_mp4_problem)


def _try_push_web_trim_to_storage(project: MatchProject | None, web: Path) -> None:
    key = _storage_trim_key(project, web)
    if key is None:
        return
    storage = project._storage  # type: ignore[union-attr]
    try:
        with web.open("rb") as f:
            storage.upload_stream(key, f)
    except Exception as exc:
        logger.info("web trim cache: push to %s failed: %s", key, exc)


def fresh_rendition(
    trim: tuple[int, float] | None,
    web: tuple[int, float] | None,
    *,
    trim_required: bool,
) -> bool:
    """Whether a trim's web rendition is current (#1192, #1209).

    Each fact is ``(size_bytes, mtime_seconds)`` or ``None`` when absent.
    Current means present, non-empty and not older than the trim when there
    is one: a re-cut writes the trim first and the rendition after it, so an
    older rendition covers the previous window. Equal mtimes are current (R2
    timestamps have one-second resolution). With no trim, the rendition is
    current only when the trim is not ``trim_required``: false on a web-only
    project (:func:`stores_web_only`), whose storage holds no trim by design
    and whose re-cut deletes the rendition before it cuts. The one rule
    local files, ``storage.stat`` and the presence listing all go through.
    """
    if web is None or web[0] == 0:
        return False
    if trim is None:
        return not trim_required
    return web[1] >= trim[1]


def fresh_web_trim(trimmed: Path) -> Path | None:
    """The web rendition of the audit trim at ``trimmed`` when it is on
    local disk and current, else ``None`` (#1192).

    Current means non-empty and not older than the trim: a re-cut writes
    the trim first and the rendition after it, so an older rendition
    covers the previous window. No trim means no rendition either -- it
    would have nothing to anchor it. Local files only; the one freshness
    rule ``_ensure_web_trim``, ``stream_video?kind=web`` and the payload's
    ``scrub_version`` share.
    """
    from .. import trim as trim_module

    web = trim_module.web_trim_path(trimmed)
    try:
        web_st = web.stat()
        trim_st = trimmed.stat()
    except OSError:
        return None
    if not fresh_rendition(
        (trim_st.st_size, trim_st.st_mtime), (web_st.st_size, web_st.st_mtime), trim_required=True
    ):
        return None
    # A corrupt rendition is absent: the next _ensure_web_trim cuts a new one.
    if not valid_mp4_cached(web).ok:
        return None
    return web


def _ensure_web_trim(
    project: MatchProject,
    trimmed: Path,
    *,
    ffmpeg_binary: str = "ffmpeg",
    runner: object | None = None,
) -> Path | None:
    """Make sure the audit trim at ``trimmed`` has its web rendition (#1031).

    Order: a fresh local file wins; then the storage cache; then a
    transcode from the trim itself, pushed up afterwards. Best-effort
    throughout -- the trim is the job's product, and a hosted player
    without the rendition falls back to the trim. Returns the rendition's
    path when one is in place, else ``None``.
    """
    from .. import trim as trim_module

    web = trim_module.web_trim_path(trimmed)
    if fresh_web_trim(trimmed) is not None:
        return web
    if web.exists():
        web.unlink()
    if _try_pull_web_trim_from_storage(project, web):
        if valid_mp4(web).ok:
            return web
        logger.warning("web trim: stored rendition of %s fails its check; cutting a new one", trimmed.name)
        web.unlink()
    try:
        trim_module.write_web_rendition(
            trimmed,
            trim_module.web_trim_config(),
            ffmpeg_binary=ffmpeg_binary,
            **({"runner": runner} if runner is not None else {}),
        )
    except trim_module.FFmpegError as exc:
        logger.warning("web trim: %s: %s", trimmed.name, exc)
        return None
    _try_push_web_trim_to_storage(project, web)
    return web


def web_trim_available(project: MatchProject | None, local_mp4: Path) -> bool:
    """Whether the web rendition for the audit trim at ``local_mp4`` exists
    locally or in the storage cache, without downloading it. The hosted
    beep-anchor reports ``kind="web"`` on this, so it must agree with
    what ``stream_video?kind=web`` will find."""
    from .. import trim as trim_module

    web = trim_module.web_trim_path(local_mp4)
    if web.exists() and web.stat().st_size > 0:
        return True
    key = _storage_trim_key(project, web)
    if key is None:
        return False
    storage = project._storage  # type: ignore[union-attr]
    try:
        return storage.exists(key)
    except Exception as exc:
        logger.info("web trim cache: storage.exists(%s) raised %s", key, exc)
        return False


def trim_available(project: MatchProject | None, local_mp4: Path) -> bool:
    """Whether a usable audit trim exists for ``local_mp4`` -- locally or
    in the storage cache -- without downloading it.

    Lets metadata callers (the beep-in-clip position) agree with what the
    byte-serving path (``stream_video?kind=trim``) will produce, paying
    only a cheap ``storage.exists`` HEAD in hosted mode. In local mode
    (no storage) this collapses to the plain local non-empty check.
    """
    if local_mp4.exists() and local_mp4.stat().st_size > 0:
        return True
    key = _storage_trim_key(project, local_mp4)
    if key is None:
        return False
    storage = project._storage  # type: ignore[union-attr]
    try:
        return storage.exists(key)
    except Exception as exc:
        logger.info("trim cache: storage.exists(%s) raised %s", key, exc)
        return False


def resolve_trim_for_read(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    *,
    project: MatchProject,
) -> Path | None:
    """Resolve the local audit-trim file read paths should use, or None.

    Returns the new-keyed trim when present (non-empty), else the
    pre-take-spec legacy-keyed trim when the registration is unambiguous
    (see _legacy_cache_fallback), else None. Legacy-id read fallback:
    the take-spec video_id change orphaned trims cut under the old
    path-only hash; without the fallback resolution misses a valid trim
    on disk and falls back to the source, which 424s when the source
    lives on unplugged external storage.

    This is the ONE fallback-aware resolver shared by the byte server
    (:func:`pull_trimmed_video` behind ``stream_video``) and the
    beep-anchor metadata (the server's ``_video_beep_in_clip``). Both
    must agree on which file backs the clip - if the anchor checks only
    the new-keyed name while the bytes come from the legacy trim, every
    coach/audit marker is offset by beep - pre_buffer (source-anchored
    numbers over a trim-anchored clip). Read-only; writers stay on the
    new-keyed names. Local files only - hosted storage probing stays
    with the callers. Remove the legacy leg once a cache migration tool
    exists.
    """
    output = trimmed_video_path(project_root, stage_number, video, project=project)
    if output.exists() and output.stat().st_size > 0:
        return output
    legacy_mp4 = output.with_name(f"stage{stage_number}_cam_{legacy_video_id(video)}_trimmed.mp4")
    return _legacy_cache_fallback(project, video, output, legacy_mp4)


def stage_trim_for_read(project: MatchProject, root: Path, stage_number: int) -> tuple[Path | None, float]:
    """The stage's trim on disk and the clip-local beep time: the lossless
    export trim when it exists, else the audit trim through
    :func:`resolve_trim_for_read` (the one resolver that also knows the
    pre-take-spec legacy file names), else nothing. ``(None, 0.0)`` with
    no primary or no beep. The export preview and the footage sort's
    reference panel both read through this, so they agree on which file
    stands for a stage."""
    from ..export_naming import stage_file_base

    stage = project.stage(stage_number)
    primary = stage.primary()
    if primary is None or primary.beep_time is None:
        return None, 0.0
    beep = min(project.trim_pre_buffer_seconds, primary.beep_time)
    base = stage_file_base(stage_number, stage.stage_name)
    lossless = project.exports_path(root) / f"{base}_trimmed.mp4"
    if lossless.exists():
        return lossless, beep
    return resolve_trim_for_read(root, stage_number, primary, project=project), beep


def trim_pre_buffer_seconds_for(trim_path: Path, *, default: float) -> float:
    """Pre-buffer the trim at ``trim_path`` was actually cut with.

    Reads the params sidecar beside the MP4 (written for new-keyed and
    legacy-keyed trims alike); falls back to ``default`` (the project's
    current setting) when the sidecar is missing or unreadable. The
    beep anchor must match the served bytes, not the current knob - a
    legacy trim may predate a buffer-setting change.
    """
    try:
        params = json.loads(trim_params_path(trim_path).read_text(encoding="utf-8"))
        return float(params["pre_buffer_seconds"])
    except (OSError, ValueError, KeyError, TypeError):
        return default


def pull_trimmed_video(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    *,
    project: MatchProject,
) -> Path:
    """Resolve ``video``'s audit-trim path, pulling it from the storage
    cache first when it isn't already local (hosted mode).

    The API process uses this before serving the scrub clip: a worker
    cut the trim into its own ephemeral filesystem and pushed it up, so
    the API has to mirror it down to serve the bytes. Never invokes
    ffmpeg -- when neither the local copy nor the storage cache has the
    trim, the returned path simply doesn't exist and the caller falls
    back to the source clip (or 404s for an explicit ``kind=trim``),
    exactly as before this seam. No-op in local mode.

    Routes through :func:`resolve_trim_for_read` after the storage pull
    so the served bytes and the beep-anchor metadata always name the
    same file (new-keyed first, unambiguous legacy trim second).
    """
    output = trimmed_video_path(project_root, stage_number, video, project=project)
    if not (output.exists() and output.stat().st_size > 0):
        _try_pull_trim_from_storage(project, output)
    resolved = resolve_trim_for_read(project_root, stage_number, video, project=project)
    return resolved if resolved is not None else output


def ensure_video_audit_trim(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    source: Path | str,
    beep_time: float,
    stage_time_seconds: float,
    *,
    project: MatchProject,
    ffmpeg_binary: str = "ffmpeg",
    runner: object | None = None,
    force: bool = False,
    still_current: Callable[[], bool] | None = None,
) -> Path:
    """Produce the audit-mode short-GOP trim for ``video`` if not cached.

    The trim window is ``[max(0, beep - pre_buffer), beep + stage_time +
    post_buffer]``. Cache key is the source mtime *and* a sidecar params
    JSON; when the beep, stage time, or buffer settings change, the next
    call sees a params mismatch and re-trims even though the source file
    is untouched. ``force`` skips the cache check and re-cuts: the Footage
    sheet's Advanced "Re-cut trim", for a source repaired in place whose
    mtime a copy or restore did not advance. ``still_current`` is asked
    once the cut passed its check: False drops it unpublished and raises
    :class:`TrimSuperseded`.

    ``source`` may be a ``str`` URL (#1674, ``MatchProject.source_url``): the
    cut reads only its window, and a failed read raises
    ``trim.SourceReadError`` unwrapped so the caller can cut again from a
    download. With no source on this disk the cache check has no source to
    date the trim against, as for any hosted source not mirrored here.
    """
    from .. import trim as trim_module

    output = trimmed_video_path(project_root, stage_number, video, project=project)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = trim_module.unique_partial(output)
    params_file = trim_params_path(output)
    current_params = _current_trim_params(
        primary_beep_time=beep_time,
        stage_time_seconds=stage_time_seconds,
        project=project,
    )

    src_resolved: Path | str
    if isinstance(source, str):
        src_resolved = source
    else:
        src_resolved = source.resolve()
        if not src_resolved.exists():
            raise FileNotFoundError(f"video missing on disk: {src_resolved}")

    # One cut at a time per trim: a second cut swept the first's freshly
    # published trim (and every partial beside it) while readers used it.
    with derived.locked(output, "cut"):
        # Storage cache (hosted mode): another worker may have already cut
        # this trim. Pull the MP4 + params sidecar into place so the cache_hit
        # check below can validate + reuse it instead of re-running ffmpeg
        # against the (large) source. Local mode skips this -- the key
        # resolves to None and the helper returns without touching the network.
        if not output.exists():
            _try_pull_trim_from_storage(project, output)

        # The one currency rule (``trim.audit_trim_matches``, which the
        # compare grid and ``trim_params_current`` read by too): the sidecar
        # records this beep, stage time and buffers, and the cut is not older
        # than the source. An unreadable sidecar is a miss.
        cache_hit = (
            not force
            and audit_trim_matches(
                output, current_params, source=src_resolved if isinstance(src_resolved, Path) else None
            )
            and valid_mp4_cached(output).ok
        )
        if cache_hit:
            # Cleanup any orphaned partial from a prior crashed run.
            for leftover in output.parent.glob(f"{output.stem}.*partial{output.suffix}"):
                leftover.unlink(missing_ok=True)
            # A trim cut before the web rendition existed (#1031) gets
            # one now, from the trim itself -- no re-cut of the source.
            _publish_web_trim(project, output, ffmpeg_binary=ffmpeg_binary, runner=runner)
            return output

        # Stale / missing final / params mismatch -> sweep and re-run. The web
        # rendition goes too: it covers the old window.
        web = trim_module.web_trim_path(output)
        for p in (output, partial, params_file, web, *_web_partials(web)):
            if p.exists():
                p.unlink()
        # And storage's copies (#1683): a web-only reader takes a stored
        # rendition as current with no trim to date it against, and the
        # rendition step below would pull the old one back as this cut's.
        if stores_web_only(project) and current_match_origin.get() != "desktop":
            _drop_stored_trim(project, project_root, stage_number, video)

        encoder = trim_module.select_audit_encoder(project.trim_audit_encoder, ffmpeg_binary=ffmpeg_binary)
        trim_kwargs: dict[str, object] = {
            "input_path": src_resolved,
            "output_path": partial,
            "beep_time": beep_time,
            "stage_time": stage_time_seconds,
            "pre_buffer_seconds": project.trim_pre_buffer_seconds,
            "post_buffer_seconds": project.trim_post_buffer_seconds,
            "mode": "audit",
            "video_encoder": encoder,
            "ffmpeg_binary": ffmpeg_binary,
            "overwrite": True,
        }
        if runner is not None:
            trim_kwargs["runner"] = runner
        try:
            trim_module.trim_video(**trim_kwargs)
        except trim_module.SourceReadError:
            # A URL read went wrong: the caller cuts again from a download.
            partial.unlink(missing_ok=True)
            raise
        except trim_module.FFmpegError as exc:
            # ffmpeg may have written some bytes before bailing; delete them.
            if partial.exists():
                partial.unlink()
            raise AudioExtractionError(str(exc)) from exc

        check = valid_mp4(partial)
        if not check.ok:
            partial.unlink(missing_ok=True)
            raise AudioExtractionError(f"trim failed its check: {check.reason}")
        # The cut took minutes; a beep moved meanwhile makes it a trim of the
        # wrong window, which must never be published (#1694 review).
        if still_current is not None and not still_current():
            partial.unlink(missing_ok=True)
            raise TrimSuperseded(f"beep moved while cutting {output.name}")
        partial.replace(output)
        derived.produce(
            params_file,
            fresh=lambda: False,
            make=lambda p: p.write_text(json.dumps(current_params, indent=2) + "\n", encoding="utf-8"),
        )
        # Storage gets the params and the rendition, not the full trim
        # (#1683). Best-effort; local mode no-ops.
        _push_trim_params(project, output)
        _publish_web_trim(project, output, ffmpeg_binary=ffmpeg_binary, runner=runner)
        return output


def _publish_web_trim(
    project: MatchProject,
    trimmed: Path,
    *,
    ffmpeg_binary: str,
    runner: object | None,
) -> None:
    """Cut ``trimmed``'s rendition, which a web-only project's storage holds
    in place of the trim (#1683). When it cannot be cut, the full trim goes
    up instead, so the stage still has a clip to play; the readers fall back
    to it as they did before the rendition existed."""
    if _ensure_web_trim(project, trimmed, ffmpeg_binary=ffmpeg_binary, runner=runner) is None:
        _try_push_trim_to_storage(project, trimmed)


def ensure_audit_trim(
    project_root: Path,
    stage_number: int,
    primary_source: Path,
    primary_beep_time: float,
    stage_time_seconds: float,
    *,
    project: MatchProject,
    ffmpeg_binary: str = "ffmpeg",
    runner: object | None = None,
) -> Path:
    """Produce the audit-mode trimmed MP4 for the stage's primary if not cached.

    Thin wrapper around :func:`ensure_video_audit_trim` that resolves the
    stage's primary and routes through the per-video cache key.
    """
    primary = project.stage(stage_number).primary()
    if primary is None:
        raise KeyError(f"stage {stage_number} has no primary video")
    return ensure_video_audit_trim(
        project_root,
        stage_number,
        primary,
        primary_source,
        primary_beep_time,
        stage_time_seconds,
        project=project,
        ffmpeg_binary=ffmpeg_binary,
        runner=runner,
    )


def _offset_detection(result: BeepDetection, offset_s: float) -> BeepDetection:
    """Shift a slice-relative detection back into source-absolute time."""
    if offset_s == 0.0:
        return result
    shifted = result.model_copy(deep=True)
    shifted.time += offset_s
    for c in shifted.candidates:
        c.time += offset_s
    return shifted


def detect_primary_beep(
    project_root: Path,
    stage_number: int,
    source_video: Path | str,
    *,
    config: BeepDetectConfig | None = None,
    ffmpeg_binary: str = "ffmpeg",
    project: MatchProject | None = None,
    window: tuple[float, float] | None = None,
) -> BeepDetection:
    """Run ``beep_detect.detect_beep`` against the primary's cached audio.

    Audio extraction happens transparently if not yet cached. Returns the
    ``BeepDetection`` result; the caller is responsible for persisting the
    relevant fields to the ``StageVideo``.

    When ``window`` is set the search is restricted to the [start_s, end_s)
    slice; the window replaces the leading-window heuristic (search_window_s
    is neutralised so it cannot re-cap the slice). Every returned timestamp
    is offset back into source-absolute time.
    """
    if window is not None:
        if project is None:
            raise ValueError("detect_primary_beep: project is required when window is set")
        primary = project.stage(stage_number).primary()
        if primary is None:
            raise KeyError(f"stage {stage_number} has no primary video")
        audio_path = ensure_video_window_audio(
            project_root,
            stage_number,
            primary,
            source_video,
            window[0],
            window[1],
            ffmpeg_binary=ffmpeg_binary,
            project=project,
        )
        audio, sr = beep_detect.load_audio(audio_path)
        cfg = (config or BeepDetectConfig()).model_copy(update={"search_window_s": 0.0})
        return _offset_detection(beep_detect.detect_beep(audio, sr, cfg), window[0])
    audio_path = ensure_primary_audio(
        project_root,
        stage_number,
        source_video,
        ffmpeg_binary=ffmpeg_binary,
        project=project,
    )
    audio, sr = beep_detect.load_audio(audio_path)
    cfg = config or BeepDetectConfig()
    return beep_detect.detect_beep(audio, sr, cfg)


def detect_video_beep(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    source: Path | str,
    *,
    config: BeepDetectConfig | None = None,
    ffmpeg_binary: str = "ffmpeg",
    project: MatchProject | None = None,
    window: tuple[float, float] | None = None,
) -> BeepDetection:
    """Run ``beep_detect.detect_beep`` against ``video``'s cached audio.

    Generic over role: primary delegates to :func:`detect_primary_beep`
    so existing monkeypatches + cache filenames keep working;
    non-primary cameras run the same detector against the per-video
    audio cache (``stage<N>_cam_<video_id>.wav``). Caller persists the
    fields onto ``video``.

    When ``window`` is set the search is restricted to the [start_s, end_s)
    slice regardless of role; the window replaces the leading-window
    heuristic (search_window_s is neutralised so it cannot re-cap the
    slice). Every returned timestamp is offset back into source-absolute
    time.
    """
    if window is not None:
        audio_path = ensure_video_window_audio(
            project_root,
            stage_number,
            video,
            source,
            window[0],
            window[1],
            ffmpeg_binary=ffmpeg_binary,
            project=project,
        )
        audio, sr = beep_detect.load_audio(audio_path)
        cfg = (config or BeepDetectConfig()).model_copy(update={"search_window_s": 0.0})
        return _offset_detection(beep_detect.detect_beep(audio, sr, cfg), window[0])
    if video.role == "primary":
        return detect_primary_beep(
            project_root,
            stage_number,
            source,
            config=config,
            ffmpeg_binary=ffmpeg_binary,
            project=project,
        )
    audio_path = ensure_video_audio(
        project_root,
        stage_number,
        video,
        source,
        ffmpeg_binary=ffmpeg_binary,
        project=project,
    )
    audio, sr = beep_detect.load_audio(audio_path)
    cfg = config or BeepDetectConfig()
    return beep_detect.detect_beep(audio, sr, cfg)
