"""A hosted worker reuses the audit trim it already cut (#1678).

The trim cache keys on ``trim mtime >= source mtime``. On a worker the
source is a mirror of a storage object, so its local mtime has to stand
for the object's version, not for when this disk last downloaded or
touched it: a cache-hit recency bump or a re-download after the source
cache evicted it made every later job on that worker (the confirm's
``shot_detect``, a secondary's re-detection) re-cut a 4K trim whose beep
had not moved.

ffmpeg is faked as in ``test_trim_storage_cache.py``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.source_cache import sweep_source_cache
from splitsmith.storage import FilesystemStorage
from splitsmith.ui import audio
from splitsmith.ui.audio import ensure_video_audit_trim, trimmed_video_path
from tests.media_bytes import tiny_mp4

STAGE = 1
STAGE_TIME = 10.0
BEEP_TIME = 3.5
SCOPE = "matches/m1/shooters/me"
RAW = Path("raw/headcam.mp4")


@pytest.fixture
def cuts(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    made: list[Path] = []
    monkeypatch.setattr("splitsmith.trim.select_audit_encoder", lambda *a, **k: "libx264")

    def fake_trim_video(**kwargs: object) -> None:
        dest = Path(kwargs["output_path"])  # type: ignore[arg-type]
        made.append(dest)
        dest.write_bytes(tiny_mp4(duration_s=1.0))

    def fake_web(input_path: Path, output_path: Path, config: object, **kwargs: object) -> None:
        Path(output_path).write_bytes(tiny_mp4(duration_s=2.0))

    monkeypatch.setattr("splitsmith.trim.trim_video", fake_trim_video)
    monkeypatch.setattr("splitsmith.trim.transcode_web_trim", fake_web)
    return made


def _worker_project(tmp_path: Path) -> tuple[MatchProject, StageVideo, Path]:
    """A worker's view of one shooter: the source lives in storage, uploaded
    an hour ago, and the worker's projects dir starts empty."""
    backing = tmp_path / "tenant"
    (backing / RAW).parent.mkdir(parents=True)
    (backing / RAW).write_bytes(b"raw source bytes")
    uploaded = time.time() - 3600
    os.utime(backing / RAW, (uploaded, uploaded))
    root = tmp_path / "worker" / "me"
    project = MatchProject.init(root, name="m")
    video = StageVideo(path=RAW, role="primary")
    project.stages = [
        StageEntry(stage_number=STAGE, stage_name="One", time_seconds=STAGE_TIME, videos=[video])
    ]
    project.bind_storage(FilesystemStorage(backing), scope=SCOPE)
    return project, video, root


def _job(project: MatchProject, video: StageVideo, root: Path) -> None:
    """What every trim-reading job body does: resolve the source, then ensure the trim."""
    source = project.resolve_video_path(root, video.path)
    ensure_video_audit_trim(root, STAGE, video, source, BEEP_TIME, STAGE_TIME, project=project)


def _age(path: Path, seconds: float) -> None:
    st = path.stat()
    os.utime(path, (st.st_atime - seconds, st.st_mtime - seconds))


def test_a_second_job_on_the_same_worker_reuses_the_trim(tmp_path: Path, cuts: list[Path]) -> None:
    project, video, root = _worker_project(tmp_path)
    _job(project, video, root)
    assert len(cuts) == 1
    # The next job starts a little later; its source resolve is a cache hit.
    _age(trimmed_video_path(root, STAGE, video, project=project), 60)
    _job(project, video, root)
    assert len(cuts) == 1, "the unchanged trim was cut again"


def test_a_re_downloaded_source_does_not_stale_the_trim(tmp_path: Path, cuts: list[Path]) -> None:
    """The per-job sweep evicts the (large) source and keeps the (small)
    trim; the next job downloads the source again, after the trim was cut."""
    project, video, root = _worker_project(tmp_path)
    _job(project, video, root)
    _age(trimmed_video_path(root, STAGE, video, project=project), 60)
    (root / RAW).unlink()
    _job(project, video, root)
    assert len(cuts) == 1, "the re-downloaded source made the unchanged trim look stale"


def test_a_cache_hit_still_counts_as_recent_use_for_the_sweep(tmp_path: Path, cuts: list[Path]) -> None:
    """The recency the LRU sweep reads survives the fix: a source a job just
    used outlives a file nothing has touched for an hour."""
    project, video, root = _worker_project(tmp_path)
    project.resolve_video_path(root, video.path)
    idle = root / "raw" / "idle.mp4"
    idle.write_bytes(b"x" * len(b"raw source bytes"))
    then = time.time() - 3600
    os.utime(idle, (then, then))
    os.utime(root / RAW, (then - 10, (root / RAW).stat().st_mtime))
    project.resolve_video_path(root, video.path)  # the cache hit marks it used now

    sweep_source_cache(root / "raw", max_bytes=len(b"raw source bytes"))

    assert (root / RAW).exists()
    assert not idle.exists()


def test_a_replaced_storage_object_still_re_cuts(tmp_path: Path, cuts: list[Path]) -> None:
    """The mirror's mtime is the object's version: a source uploaded again
    after the trim was cut makes the trim stale, as a desktop edit would."""
    project, video, root = _worker_project(tmp_path)
    _job(project, video, root)
    trim = trimmed_video_path(root, STAGE, video, project=project)
    _age(trim, 120)
    backing = tmp_path / "tenant"
    (backing / RAW).write_bytes(b"replaced source bytes")
    (root / RAW).unlink()
    _job(project, video, root)
    assert len(cuts) == 2


def _stored_keys(tmp_path: Path) -> set[str]:
    base = tmp_path / "tenant" / SCOPE
    return {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}


def test_a_stored_invalidation_drops_the_storage_copies(tmp_path: Path, cuts: list[Path]) -> None:
    """A hosted reader serves a trim, its rendition and its audit WAV by
    existence in storage: once the beep moved they go there too."""
    project, video, root = _worker_project(tmp_path)
    _job(project, video, root)
    wav = audio.video_audit_audio_path(root, STAGE, video, project=project)
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(b"RIFF")
    project._storage.write_bytes(f"{SCOPE}/audio/{wav.name}", b"RIFF")  # type: ignore[union-attr]
    before = _stored_keys(tmp_path)
    assert {k.split("/")[0] for k in before} == {"trimmed", "audio"}
    assert len(before) == 4  # trim, params, web rendition, audit WAV

    audio.invalidate_video_audit_trim(root, STAGE, video, project=project)
    assert _stored_keys(tmp_path) == before, "a plain invalidation reaches into storage"

    audio.invalidate_video_audit_trim(root, STAGE, video, project=project, stored=True)
    assert _stored_keys(tmp_path) == set()


def test_trim_params_current_reads_the_stored_sidecar(tmp_path: Path, cuts: list[Path]) -> None:
    """The detect job's skip decides by the trim's params, from storage when
    this worker has no copy, and never downloads the trim to decide."""
    project, video, root = _worker_project(tmp_path)
    video.beep_time = BEEP_TIME
    _job(project, video, root)
    trim = trimmed_video_path(root, STAGE, video, project=project)
    assert audio.trim_params_current(root, STAGE, video, STAGE_TIME, project=project)
    assert not audio.trim_params_current(root, STAGE, video, STAGE_TIME + 1, project=project)

    trim.unlink()
    audio.trim_params_path(trim).unlink()
    assert audio.trim_params_current(root, STAGE, video, STAGE_TIME, project=project)
    assert not audio.trim_params_current(root, STAGE, video, 25.0, project=project)
    assert not trim.exists(), "the check downloaded the trim"
