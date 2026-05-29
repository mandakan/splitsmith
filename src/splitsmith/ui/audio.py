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

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import beep_detect
from ..config import BeepDetectConfig, BeepDetection
from .project import MatchProject, StageVideo

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
    source_video: Path,
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
    source_video: Path,
    *,
    sample_rate: int = 48000,
    ffmpeg_binary: str = "ffmpeg",
    project: MatchProject | None = None,
) -> Path:
    """Extract a mono WAV for ``video`` if not already cached.

    Cache lands at ``<audio_dir>/stage<N>_cam_<video_id>.wav`` regardless of
    role. Re-extracts when the source mtime is newer than the cache.
    """
    audio_path = video_audio_path(project_root, stage_number, video, project=project)
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    src_resolved = source_video.resolve()
    if not src_resolved.exists():
        raise FileNotFoundError(f"video missing on disk: {src_resolved}")

    if audio_path.exists() and audio_path.stat().st_mtime >= src_resolved.stat().st_mtime:
        return audio_path

    # Storage cache (hosted mode): another worker may have already
    # extracted this WAV. Pulling 5 MB from S3 beats running ffmpeg
    # against a 500 MB raw video. Local mode skips this -- ``project``
    # has no storage bound, so the key resolves to ``None`` and the
    # helper returns False without touching the network.
    if _try_pull_audio_from_storage(project, audio_path):
        return audio_path

    if not shutil.which(ffmpeg_binary):
        raise AudioExtractionError(f"ffmpeg binary not found: {ffmpeg_binary}")

    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src_resolved),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-vn",
        str(audio_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise AudioExtractionError(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc
    # Push the freshly-extracted WAV up to the storage cache so the
    # next worker that touches this project can skip ffmpeg. Best-
    # effort: a network blip during push doesn't fail the job.
    _try_push_audio_to_storage(project, audio_path)
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
    return project.trimmed_path(project_root) / f"stage{stage_number}_cam_{video.video_id}_trimmed.mp4"


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
    primary_source: Path,
    primary_beep_time: float | None,
    *,
    project: MatchProject,
    sample_rate: int = 48000,
    ffmpeg_binary: str = "ffmpeg",
) -> AuditAudioResult:
    """Resolve the WAV the audit screen should serve for ``stage_number``.

    Prefers the trimmed clip's audio (short-GOP MP4 produced by
    :mod:`splitsmith.trim`). When the trimmed clip is missing, falls back
    to the full primary WAV so the audit screen still loads -- you'll see
    the entire source clip with the original (slow) scrub feel. Either way
    the result is mtime-cached.
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
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        if not audio_path.exists() or audio_path.stat().st_mtime < trimmed_video.stat().st_mtime:
            # Try the storage cache before invoking ffmpeg. The audit
            # WAV is derived from the trimmed MP4, but its content is
            # determined by the trim params; a worker that already
            # produced this exact WAV pushed it under the same key.
            if not _try_pull_audio_from_storage(project, audio_path):
                _extract_audio(trimmed_video, audio_path, sample_rate, ffmpeg_binary)
                _try_push_audio_to_storage(project, audio_path)
        # Beep position inside the trimmed clip = beep_in_source minus
        # trim_start. trim_start = max(0, beep - pre_buffer); so:
        #   beep_in_clip = min(beep_in_source, pre_buffer)
        beep_in_clip = (
            min(primary_beep_time, project.trim_pre_buffer_seconds) if primary_beep_time is not None else None
        )
        return AuditAudioResult(audio_path=audio_path, beep_in_clip=beep_in_clip, trimmed=True)

    fallback = ensure_primary_audio(
        project_root,
        stage_number,
        primary_source,
        sample_rate=sample_rate,
        ffmpeg_binary=ffmpeg_binary,
        project=project,
    )
    return AuditAudioResult(
        audio_path=fallback,
        beep_in_clip=primary_beep_time,
        trimmed=False,
    )


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
        "-vn",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise AudioExtractionError(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc


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


def _try_pull_audio_from_storage(project: MatchProject | None, local_wav: Path) -> bool:
    """If the WAV exists in the project's storage cache, download it
    into ``local_wav`` and return True. Returns False when no
    storage is bound, the key is absent, or the download fails.

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
    try:
        local_wav.parent.mkdir(parents=True, exist_ok=True)
        with storage.open_stream(key) as src, local_wav.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return True
    except Exception as exc:
        logger.info("audio cache: pull from %s failed: %s", key, exc)
        # Leave nothing half-written behind so the ffmpeg fallback
        # doesn't trip over a torn file.
        try:
            local_wav.unlink()
        except FileNotFoundError:
            pass
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


def trim_params_path(output: Path) -> Path:
    """Sidecar JSON next to the trimmed MP4 recording the inputs that
    produced it. Used to invalidate the cache when beep_time, stage_time,
    or the buffer settings change without the source file moving."""
    return output.with_name(f"{output.stem}.params.json")


def _current_trim_params(
    *,
    primary_beep_time: float,
    stage_time_seconds: float,
    project: MatchProject,
) -> dict[str, float]:
    return {
        "beep_time": round(float(primary_beep_time), 4),
        "stage_time_seconds": round(float(stage_time_seconds), 4),
        "pre_buffer_seconds": float(project.trim_pre_buffer_seconds),
        "post_buffer_seconds": float(project.trim_post_buffer_seconds),
    }


def invalidate_video_audit_trim(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    *,
    project: MatchProject,
) -> None:
    """Delete the cached trim, audit WAV, and params sidecar for ``video``.

    Called after beep_time changes (manual override) or stage_time changes
    (scoreboard re-import) so the next trim job runs from scratch instead
    of trusting a now-stale cache. Idempotent if any file is missing.
    """
    output = trimmed_video_path(project_root, stage_number, video, project=project)
    wav = video_audit_audio_path(project_root, stage_number, video, project=project)
    params = trim_params_path(output)
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    for p in (output, wav, params, partial):
        try:
            if p.exists() or p.is_symlink():
                p.unlink()
        except OSError:
            pass


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
    try:
        local_mp4.parent.mkdir(parents=True, exist_ok=True)
        with storage.open_stream(key) as src, local_mp4.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        # Pull the sidecar when present; its absence just forces a re-cut.
        try:
            if storage.exists(params_key):
                with storage.open_stream(params_key) as src, params_local.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        except Exception as exc:
            logger.info("trim cache: params pull from %s failed: %s", params_key, exc)
        return True
    except Exception as exc:
        logger.info("trim cache: pull from %s failed: %s", key, exc)
        for torn in (local_mp4, params_local):
            try:
                torn.unlink()
            except FileNotFoundError:
                pass
        return False


def _try_push_trim_to_storage(project: MatchProject | None, local_mp4: Path) -> None:
    """Push a freshly-cut audit trim (+ its params sidecar) to the
    project's storage cache so the API process can serve the scrub clip
    and the next worker can skip the ffmpeg re-cut.

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
        params_local = trim_params_path(local_mp4)
        if params_local.exists():
            params_key = f"{key.rsplit('/', 1)[0]}/{params_local.name}"
            storage.write_bytes(params_key, params_local.read_bytes())
    except Exception as exc:
        logger.info("trim cache: push to %s failed: %s", key, exc)


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
    """
    output = trimmed_video_path(project_root, stage_number, video, project=project)
    if not (output.exists() and output.stat().st_size > 0):
        _try_pull_trim_from_storage(project, output)
    return output


def ensure_video_audit_trim(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    source: Path,
    beep_time: float,
    stage_time_seconds: float,
    *,
    project: MatchProject,
    ffmpeg_binary: str = "ffmpeg",
    runner: object | None = None,
) -> Path:
    """Produce the audit-mode short-GOP trim for ``video`` if not cached.

    The trim window is ``[max(0, beep - pre_buffer), beep + stage_time +
    post_buffer]``. Cache key is the source mtime *and* a sidecar params
    JSON; when the beep, stage time, or buffer settings change, the next
    call sees a params mismatch and re-trims even though the source file
    is untouched.
    """
    from .. import trim as trim_module

    output = trimmed_video_path(project_root, stage_number, video, project=project)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    params_file = trim_params_path(output)
    current_params = _current_trim_params(
        primary_beep_time=beep_time,
        stage_time_seconds=stage_time_seconds,
        project=project,
    )

    src_resolved = source.resolve()
    if not src_resolved.exists():
        raise FileNotFoundError(f"video missing on disk: {src_resolved}")

    # Storage cache (hosted mode): another worker may have already cut
    # this trim. Pull the MP4 + params sidecar into place so the cache_hit
    # check below can validate + reuse it instead of re-running ffmpeg
    # against the (large) source. Local mode skips this -- the key
    # resolves to None and the helper returns without touching the network.
    if not output.exists():
        _try_pull_trim_from_storage(project, output)

    cache_hit = (
        output.exists() and output.stat().st_mtime >= src_resolved.stat().st_mtime and params_file.exists()
    )
    if cache_hit:
        try:
            existing_params = json.loads(params_file.read_text(encoding="utf-8"))
            if existing_params == current_params:
                # Cleanup any orphaned .partial from a prior crashed run.
                if partial.exists():
                    partial.unlink()
                return output
        except (OSError, ValueError):
            # Treat unreadable sidecar as cache miss.
            pass

    # Stale / missing final / params mismatch -> sweep and re-run.
    for p in (output, partial, params_file):
        if p.exists():
            p.unlink()

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
    except trim_module.FFmpegError as exc:
        # ffmpeg may have written some bytes before bailing; delete them.
        if partial.exists():
            partial.unlink()
        raise AudioExtractionError(str(exc)) from exc

    partial.replace(output)
    params_file.write_text(json.dumps(current_params, indent=2) + "\n", encoding="utf-8")
    # Push the freshly-cut trim up so the API can serve the scrub clip and
    # the next worker can skip the re-cut. Best-effort; local mode no-ops.
    _try_push_trim_to_storage(project, output)
    return output


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


def detect_primary_beep(
    project_root: Path,
    stage_number: int,
    source_video: Path,
    *,
    config: BeepDetectConfig | None = None,
    ffmpeg_binary: str = "ffmpeg",
    project: MatchProject | None = None,
) -> BeepDetection:
    """Run ``beep_detect.detect_beep`` against the primary's cached audio.

    Audio extraction happens transparently if not yet cached. Returns the
    ``BeepDetection`` result; the caller is responsible for persisting the
    relevant fields to the ``StageVideo``.
    """
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
    source: Path,
    *,
    config: BeepDetectConfig | None = None,
    ffmpeg_binary: str = "ffmpeg",
    project: MatchProject | None = None,
) -> BeepDetection:
    """Run ``beep_detect.detect_beep`` against ``video``'s cached audio.

    Generic over role: primary delegates to :func:`detect_primary_beep`
    so existing monkeypatches + cache filenames keep working;
    non-primary cameras run the same detector against the per-video
    audio cache (``stage<N>_cam_<video_id>.wav``). Caller persists the
    fields onto ``video``.
    """
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
