"""Requests read derived audio; jobs make it (spec 2026-10-14).

On 2026-10-13 a phone opening a stage fired three requests that each made
the stage's audio themselves; one extracted it from a rendition another was
still downloading and the phone was served a short WAV. Now a request reads
what a job published, or queues that job once and answers 202.
"""

from __future__ import annotations

import os
import threading
import time
import wave
from pathlib import Path

import pytest

from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.storage import FilesystemStorage
from splitsmith.ui import audio as audio_helpers
from tests.media_bytes import tiny_mp4
from tests.test_ui_server import _primary_cache_paths, _seed_project_with_primary, _wait_for_job


def _write_wav(path: Path, seconds: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x10\x00" * int(seconds * 8000))


@pytest.fixture
def no_extraction_in_requests(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Fail any ffmpeg extraction, and hold the derive_audio job open until
    the test releases it, so concurrent requests see it active."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a request ran ffmpeg")

    monkeypatch.setattr(audio_helpers, "_extract_audio", refuse)
    monkeypatch.setattr(audio_helpers, "_run_audio_ffmpeg", refuse, raising=False)
    release = threading.Event()

    def held(*args: object, **kwargs: object) -> None:
        release.wait(5)

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", held)
    yield release
    release.set()


def _trim_without_wav(tmp_path: Path):
    client, _ = _seed_project_with_primary(tmp_path)
    _full, audit_wav, trimmed_mp4 = _primary_cache_paths(tmp_path / "match")
    trimmed_mp4.parent.mkdir(parents=True, exist_ok=True)
    trimmed_mp4.write_bytes(tiny_mp4())
    return client, audit_wav


def test_a_missing_audit_wav_is_one_job_and_a_202_for_every_request(
    tmp_path: Path, no_extraction_in_requests: threading.Event
) -> None:
    client, audit_wav = _trim_without_wav(tmp_path)

    answers = [
        client.get(url)
        for url in (
            "/api/shooters/me/stages/1/peaks?bins=8192",
            "/api/shooters/me/stages/1/peaks?bins=20180",
            "/api/shooters/me/stages/1/audio",
        )
    ]

    assert [r.status_code for r in answers] == [202, 202, 202]
    assert all(r.json()["pending"] is True for r in answers)
    assert len({r.json()["job_id"] for r in answers}) == 1
    assert not audit_wav.exists()
    jobs = [j for j in client.get("/api/me/jobs").json() if j["kind"] == "derive_audio"]
    assert len(jobs) == 1


def test_the_published_wav_is_served_once_the_job_made_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, audit_wav = _trim_without_wav(tmp_path)

    def job_makes_it(*args: object, **kwargs: object) -> None:
        _write_wav(audit_wav)

    monkeypatch.setattr(audio_helpers, "ensure_audit_audio", job_makes_it)

    first = client.get("/api/shooters/me/stages/1/peaks?bins=64")
    assert first.status_code == 202
    _wait_for_job(client, first.json()["job_id"])

    second = client.get("/api/shooters/me/stages/1/peaks?bins=64")
    assert second.status_code == 200
    assert second.json()["trimmed"] is True
    assert second.json()["duration"] == pytest.approx(1.0)


def test_a_wav_older_than_its_trim_is_not_served(
    tmp_path: Path, no_extraction_in_requests: threading.Event
) -> None:
    client, audit_wav = _trim_without_wav(tmp_path)
    _write_wav(audit_wav)
    _full, _audit, trimmed_mp4 = _primary_cache_paths(tmp_path / "match")
    later = time.time() + 5
    os.utime(trimmed_mp4, (later, later))

    assert client.get("/api/shooters/me/stages/1/peaks?bins=64").status_code == 202


# --- A desktop-synced mirror reads the WAV the desktop (or a job) pushed ------


def _mirror(tmp_path: Path) -> tuple[Path, MatchProject, FilesystemStorage, str]:
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    project = MatchProject.init(root, name="mirror")
    video = StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=4.0)
    project.stages = [StageEntry(stage_number=1, stage_name="One", time_seconds=10.0, videos=[video])]
    project.save(root)
    scope = "matches/m1/shooters/me"
    project.bind_storage(storage, scope=scope)
    return root, project, storage, f"stage1_cam_{video.video_id}"


def _age(storage: FilesystemStorage, key: str, seconds_ago: float) -> None:
    t = time.time() - seconds_ago
    os.utime(storage.root / key, (t, t))


def test_a_mirror_serves_the_pushed_wav_when_it_is_newer_than_the_rendition(tmp_path: Path) -> None:
    root, project, storage, stem = _mirror(tmp_path)
    scope = "matches/m1/shooters/me"
    storage.write_bytes(f"{scope}/trimmed/{stem}_web.mp4", tiny_mp4())
    wav_src = tmp_path / "pushed.wav"
    _write_wav(wav_src)
    storage.write_bytes(f"{scope}/audio/{stem}_audit.wav", wav_src.read_bytes())
    _age(storage, f"{scope}/trimmed/{stem}_web.mp4", 60)

    result = audio_helpers.published_audit_audio(root, 1, 4.0, project=project, mirror=True)

    assert result is not None and result.trimmed is True
    assert result.audio_path.read_bytes() == wav_src.read_bytes()
    # A request never downloads the rendition to cut audio from it.
    assert not (root / "trimmed" / f"{stem}_web.mp4").exists()


def test_a_mirror_never_serves_a_wav_older_than_the_rendition(tmp_path: Path) -> None:
    root, project, storage, stem = _mirror(tmp_path)
    scope = "matches/m1/shooters/me"
    wav_src = tmp_path / "pushed.wav"
    _write_wav(wav_src)
    storage.write_bytes(f"{scope}/audio/{stem}_audit.wav", wav_src.read_bytes())
    storage.write_bytes(f"{scope}/trimmed/{stem}_web.mp4", tiny_mp4())
    _age(storage, f"{scope}/audio/{stem}_audit.wav", 60)

    assert audio_helpers.published_audit_audio(root, 1, 4.0, project=project, mirror=True) is None
    assert not any((root / "audio").glob("*.wav")) if (root / "audio").exists() else True


def test_a_mirror_with_no_wav_is_pending_and_downloads_nothing(tmp_path: Path) -> None:
    root, project, storage, stem = _mirror(tmp_path)
    storage.write_bytes(f"matches/m1/shooters/me/trimmed/{stem}_web.mp4", tiny_mp4())

    assert audio_helpers.published_audit_audio(root, 1, 4.0, project=project, mirror=True) is None
    assert not (root / "trimmed").exists() or not any((root / "trimmed").iterdir())
