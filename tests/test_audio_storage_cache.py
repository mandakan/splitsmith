"""Tests for the hosted-mode audio cache push/pull.

The audio cache (Phase 1) is the seam that makes stateless detection
workers viable: worker A extracts ``stage<N>_cam_<video_id>.wav``
once, pushes it to S3, and worker B's next detection on the same
file pulls the 5 MB WAV instead of downloading the 500 MB raw to
re-extract.

These tests stub ffmpeg via a ``subprocess.run`` monkeypatch so they
don't depend on a real binary, and use ``FilesystemStorage`` against
``tmp_path`` as the storage backend -- the Protocol shape is the
same as ``S3Storage`` (proven by ``test_s3_storage.py``), so behaviour
parity carries over.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from splitsmith.storage import FilesystemStorage
from splitsmith.ui.audio import ensure_video_audio
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo


@pytest.fixture
def fake_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Replace ``subprocess.run`` so ffmpeg invocations land in a list.

    Whatever ``-i <src>`` / ``<dest>`` ffmpeg would have produced, the
    fake writes a sentinel byte to the dest path so the rest of the
    pipeline sees a real (if tiny) WAV. Returns the list of invoked
    arg-vectors so tests can assert "ffmpeg ran" or "ffmpeg did NOT
    run" without inspecting subprocess internals.
    """
    invocations: list[list[str]] = []
    real_which = __import__("shutil").which

    def fake_which(binary: str) -> str | None:
        # Pretend ffmpeg is installed so the early ``shutil.which``
        # guard doesn't bail. Other binaries fall through to real.
        if binary == "ffmpeg":
            return "/usr/bin/ffmpeg"
        return real_which(binary)

    monkeypatch.setattr("splitsmith.ui.audio.shutil.which", fake_which)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        invocations.append(list(cmd))
        # The dest is the last arg in the ffmpeg invocation built by
        # ensure_video_audio (-vn <dest>).
        dest = Path(cmd[-1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"WAVDATA")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("splitsmith.ui.audio.subprocess.run", fake_run)
    return invocations


def _project_with_stage_video(root: Path, video_path: Path) -> tuple[MatchProject, StageVideo]:
    project = MatchProject.init(root, name="audio-test")
    video = StageVideo(path=video_path, role="primary")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=10.0,
            videos=[video],
        )
    ]
    project.save(root)
    return project, video


def test_local_mode_no_storage_runs_ffmpeg_and_never_touches_storage(
    tmp_path: Path, fake_ffmpeg: list[list[str]]
) -> None:
    """Regression guard for desktop / CLI mode: with no storage bound
    the cache helpers are no-ops and the existing ffmpeg path runs
    unchanged. No method on a Storage object is invoked because no
    Storage is present.
    """
    root = tmp_path / "p"
    source = tmp_path / "raw" / "v.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video bytes")
    project, video = _project_with_stage_video(root, source)

    # Local mode == bind never called == project._storage is None.
    assert project._storage is None
    result = ensure_video_audio(root, 1, video, source, project=project)

    assert result.exists()
    assert result.read_bytes() == b"WAVDATA"
    assert len(fake_ffmpeg) == 1  # ffmpeg ran exactly once


def test_storage_cache_hit_skips_ffmpeg(tmp_path: Path, fake_ffmpeg: list[list[str]]) -> None:
    """A WAV already present in the storage cache must be downloaded
    instead of re-extracted. This is the cold-worker path: a fresh
    container picks up a detection job for a previously-processed
    video and saves the ffmpeg cost.
    """
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = tmp_path / "raw" / "v.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video bytes")
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope="matches/m1/shooters/me")

    # Pre-populate the storage cache with the WAV another worker
    # would have produced.
    audio_basename = f"stage1_cam_{video.video_id}.wav"
    cached_key = f"matches/m1/shooters/me/audio/{audio_basename}"
    storage.write_bytes(cached_key, b"PRECACHED")

    result = ensure_video_audio(root, 1, video, source, project=project)

    assert result.read_bytes() == b"PRECACHED"
    assert len(fake_ffmpeg) == 0  # ffmpeg never ran


def test_extraction_pushes_to_storage_when_cold(tmp_path: Path, fake_ffmpeg: list[list[str]]) -> None:
    """When neither the local nor storage cache has the WAV, ffmpeg
    runs and the result is pushed up so the next worker hits the
    cache. This is the warm-up path that primes the cache.
    """
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = tmp_path / "raw" / "v.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video bytes")
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope="matches/m1/shooters/me")

    result = ensure_video_audio(root, 1, video, source, project=project)

    assert result.read_bytes() == b"WAVDATA"
    assert len(fake_ffmpeg) == 1
    # The freshly-extracted WAV landed in the storage cache.
    cached_key = f"matches/m1/shooters/me/audio/stage1_cam_{video.video_id}.wav"
    assert storage.exists(cached_key)
    assert storage.read_bytes(cached_key) == b"WAVDATA"


def test_local_cache_hit_skips_storage_and_ffmpeg(tmp_path: Path, fake_ffmpeg: list[list[str]]) -> None:
    """When the local WAV already exists and is newer than the source,
    neither storage nor ffmpeg is consulted. The cheapest path stays
    cheap -- repeated detection on the same worker reuses the local
    WAV exactly as it did pre-PR.
    """
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = tmp_path / "raw" / "v.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video bytes")
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope="matches/m1/shooters/me")

    # First call extracts + pushes.
    first = ensure_video_audio(root, 1, video, source, project=project)
    assert len(fake_ffmpeg) == 1

    # Second call must find the local WAV and short-circuit.
    second = ensure_video_audio(root, 1, video, source, project=project)
    assert second == first
    assert len(fake_ffmpeg) == 1  # unchanged


def test_storage_push_failure_does_not_break_extraction(
    tmp_path: Path,
    fake_ffmpeg: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network blip during the post-extraction push must NOT fail
    the detection job. The local WAV is the source of truth for the
    current job; the cache push is best-effort.
    """
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)

    # Make upload_stream raise so the push fails. The local
    # extraction must still succeed.
    def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("simulated R2 outage")

    monkeypatch.setattr(storage, "upload_stream", boom)

    root = tmp_path / "p"
    source = tmp_path / "raw" / "v.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video bytes")
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope="matches/m1/shooters/me")

    result = ensure_video_audio(root, 1, video, source, project=project)
    assert result.read_bytes() == b"WAVDATA"


def test_storage_pull_torn_file_falls_through_to_ffmpeg(
    tmp_path: Path,
    fake_ffmpeg: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If open_stream raises mid-copy, the helper must clean up the
    torn local file and let ffmpeg run. Otherwise the next call
    would happily serve a half-downloaded WAV.
    """
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = tmp_path / "raw" / "v.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video bytes")
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope="matches/m1/shooters/me")
    audio_basename = f"stage1_cam_{video.video_id}.wav"
    cached_key = f"matches/m1/shooters/me/audio/{audio_basename}"
    storage.write_bytes(cached_key, b"PRECACHED")

    # Sabotage the pull: open_stream raises after exists() says True.
    def boom(path: str) -> object:
        raise RuntimeError("connection reset")

    monkeypatch.setattr(storage, "open_stream", boom)

    result = ensure_video_audio(root, 1, video, source, project=project)

    # ffmpeg ran because the pull failed, and the local file is the
    # ffmpeg output, not a half-downloaded PRECACHED.
    assert result.read_bytes() == b"WAVDATA"
    assert len(fake_ffmpeg) == 1


def test_bind_storage_without_scope_disables_audio_cache(
    tmp_path: Path, fake_ffmpeg: list[list[str]]
) -> None:
    """When storage is bound but scope is None (e.g. a non-match
    request path), the audio cache stays off. The raw-video resolver
    still works (it doesn't need scope); only derived artifacts do.
    """
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = tmp_path / "raw" / "v.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video bytes")
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope=None)

    ensure_video_audio(root, 1, video, source, project=project)

    # ffmpeg ran; nothing landed in storage (no scope -> no key).
    assert len(fake_ffmpeg) == 1
    assert list(storage.list("")) == []
