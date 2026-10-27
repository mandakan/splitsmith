"""A storage-backed project keeps no full-resolution trim in storage (#1683).

On 4K footage the audit trim is several times its source (939 MB against
178 MB for one HFO window) and nothing but the job that cut it reads it:
browsers scrub the 720p rendition. So storage holds the params sidecar, the
rendition and the audit WAV, and a later job reads those instead of pulling
a trim. These tests run the real trim and shot-detect job bodies against a
storage that records every byte fetch; ffmpeg and the ensemble are faked.
"""

from __future__ import annotations

import json
import time
import wave
from pathlib import Path

import pytest

from splitsmith import trim as trim_module
from splitsmith.match_project import MatchProject
from splitsmith.ui import audio as audio_helpers
from splitsmith.ui import server as server_mod
from splitsmith.ui.context import current_match_id, current_match_root
from tests.media_bytes import tiny_mp4
from tests.test_api_source_fetch_guard import _hosted_source_only
from tests.ui._helpers import _wait_for_job

SOURCE_KEY = "raw/VID1.mp4"
WAV_FRAMES = 48000  # the 1 s fake clip: the WAV length check reads it


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x10\x00" * WAV_FRAMES)


@pytest.fixture(autouse=True)
def _reset_match_context():  # type: ignore[no-untyped-def]
    """``_setup`` pins the seeded match the way a request's alias does, so
    the test reads the project the job bodies see."""
    tokens: list = []
    yield tokens
    for var, token in reversed(tokens):
        var.reset(token)


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tokens: list):  # type: ignore[no-untyped-def]
    client, match_root, storage = _hosted_source_only(tmp_path)
    state = client.app.state.splitsmith_state
    (match_id,) = state.matches.known_ids()
    tokens.append((current_match_id, current_match_id.set(match_id)))
    tokens.append((current_match_root, current_match_root.set(match_root)))
    project = state.shooter_project("me")
    root = state.shooter_root("me")
    video = project.stage(1).primary()
    assert video is not None
    cuts: list[Path] = []

    def fake_trim_video(**kwargs: object) -> trim_module.TrimResult:
        dest = Path(kwargs["output_path"])  # type: ignore[arg-type]
        cuts.append(dest)
        dest.write_bytes(tiny_mp4(duration_s=1.0))
        return trim_module.TrimResult(output_path=dest, start_time=0.0, end_time=15.0)

    monkeypatch.setattr(trim_module, "select_audit_encoder", lambda *a, **k: "libx264")
    monkeypatch.setattr(trim_module, "trim_video", fake_trim_video)
    monkeypatch.setattr(
        trim_module, "transcode_web_trim", lambda i, o, c, **k: Path(o).write_bytes(tiny_mp4(duration_s=1.0))
    )
    monkeypatch.setattr(audio_helpers, "_extract_audio", lambda src, dest, sr, ffmpeg: _write_wav(Path(dest)))
    return client, state, storage, project, root, video, cuts


def _keys(project: MatchProject, root: Path, video) -> dict[str, str]:  # type: ignore[no-untyped-def]
    trim = audio_helpers.trimmed_video_path(root, 1, video, project=project)
    wav = audio_helpers.video_audit_audio_path(root, 1, video, project=project)
    return {
        "trim": audio_helpers._storage_trim_key(project, trim),  # type: ignore[dict-item]
        "params": audio_helpers._storage_trim_key(project, audio_helpers.trim_params_path(trim)),  # type: ignore[dict-item]
        "web": audio_helpers._storage_trim_key(project, trim_module.web_trim_path(trim)),  # type: ignore[dict-item]
        "wav": audio_helpers._storage_audio_key(project, wav),  # type: ignore[dict-item]
    }


def _store_products(storage, project: MatchProject, root: Path, video, *, wav_first: bool = False) -> None:  # type: ignore[no-untyped-def]
    """What a finished trim job leaves in storage for the current beep."""
    keys = _keys(project, root, video)
    params = audio_helpers._current_trim_params(
        primary_beep_time=video.beep_time, stage_time_seconds=project.stage(1).time_seconds, project=project
    )
    storage.write_bytes(keys["params"], (json.dumps(params, indent=2) + "\n").encode("utf-8"))
    wav_src = root.parent / "made.wav"
    _write_wav(wav_src)
    if wav_first:
        storage.write_bytes(keys["wav"], wav_src.read_bytes())
        time.sleep(1.1)  # storage times have one-second resolution
        storage.write_bytes(keys["web"], tiny_mp4(duration_s=1.0))
    else:
        storage.write_bytes(keys["web"], tiny_mp4(duration_s=1.0))
        storage.write_bytes(keys["wav"], wav_src.read_bytes())


def test_a_cut_leaves_no_full_trim_in_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _reset_match_context: list
) -> None:
    client, _state, storage, project, root, video, cuts = _setup(tmp_path, monkeypatch, _reset_match_context)

    job = client.post("/api/shooters/me/stages/1/trim?force=true").json()
    assert _wait_for_job(client, job["id"])["status"] == "succeeded"

    keys = _keys(project, root, video)
    assert len(cuts) == 1
    assert not storage.exists(keys["trim"])
    assert storage.exists(keys["params"]) and storage.exists(keys["web"]) and storage.exists(keys["wav"])


def test_a_trim_job_over_current_stored_products_needs_no_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _reset_match_context: list
) -> None:
    """A confirm chains a trim job over a cut that is still current. With no
    full trim in storage to pull, cutting again is all it could do; it finds
    the products current instead and fetches nothing."""
    client, _state, storage, project, root, video, cuts = _setup(tmp_path, monkeypatch, _reset_match_context)
    _store_products(storage, project, root, video)
    storage.fetched.clear()

    job = client.post("/api/shooters/me/stages/1/trim").json()
    assert _wait_for_job(client, job["id"])["status"] == "succeeded"

    assert cuts == []
    assert SOURCE_KEY not in storage.fetched
    assert MatchProject.load(root).stage(1).primary().processed["trim"] is True  # type: ignore[union-attr]


def test_a_trim_job_cuts_when_the_stored_wav_is_older_than_the_rendition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _reset_match_context: list
) -> None:
    client, _state, storage, project, root, video, cuts = _setup(tmp_path, monkeypatch, _reset_match_context)
    _store_products(storage, project, root, video, wav_first=True)

    job = client.post("/api/shooters/me/stages/1/trim").json()
    assert _wait_for_job(client, job["id"])["status"] == "succeeded"

    assert len(cuts) == 1


class _StopError(Exception):
    pass


def test_shot_detection_reads_the_published_wav_without_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _reset_match_context: list
) -> None:
    """Before #1683 every shot detection downloaded the whole source and
    pulled the trim from storage; with neither needed it reads the WAV."""
    client, state, storage, project, root, video, cuts = _setup(tmp_path, monkeypatch, _reset_match_context)
    _store_products(storage, project, root, video)
    storage.fetched.clear()
    seen: list[int] = []

    def detect_stage(**kwargs: object) -> None:
        seen.append(len(kwargs["audio"]))  # type: ignore[arg-type]
        raise _StopError("stop before the ensemble")

    monkeypatch.setattr(server_mod, "_ensemble_runtime_is_warm", lambda *_a, **_k: True)
    monkeypatch.setattr(state.compute, "detect_stage", detect_stage)

    job = client.post("/api/shooters/me/stages/1/shot-detect").json()
    _wait_for_job(client, job["id"])

    assert seen == [WAV_FRAMES]
    assert cuts == []
    assert SOURCE_KEY not in storage.fetched


def test_shot_detection_cuts_from_the_source_when_nothing_is_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _reset_match_context: list
) -> None:
    client, state, storage, _project, _root, _video, cuts = _setup(
        tmp_path, monkeypatch, _reset_match_context
    )
    seen: list[int] = []

    def detect_stage(**kwargs: object) -> None:
        seen.append(len(kwargs["audio"]))  # type: ignore[arg-type]
        raise _StopError("stop before the ensemble")

    monkeypatch.setattr(server_mod, "_ensemble_runtime_is_warm", lambda *_a, **_k: True)
    monkeypatch.setattr(state.compute, "detect_stage", detect_stage)

    job = client.post("/api/shooters/me/stages/1/shot-detect").json()
    _wait_for_job(client, job["id"])

    assert len(cuts) == 1
    assert SOURCE_KEY in storage.fetched
    assert seen == [WAV_FRAMES]


def test_a_native_audit_wav_comes_from_the_rendition_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _reset_match_context: list
) -> None:
    """The audit route's reader dates a hosted-native WAV against the stored
    rendition, as on a mirror, and never downloads the rendition or reads
    its params."""
    _client, _state, storage, project, root, video, _cuts = _setup(
        tmp_path, monkeypatch, _reset_match_context
    )
    _store_products(storage, project, root, video)
    storage.fetched.clear()

    result = audio_helpers.published_audit_audio(root, 1, video.beep_time, project=project)

    keys = _keys(project, root, video)
    assert result is not None and result.trimmed is True
    assert keys["web"] not in storage.fetched
    # A native trim is cut with the project's buffers: the request reads no
    # params sidecar for its anchor (a mirror's may differ, so it does).
    assert keys["params"] not in storage.fetched
    assert result.beep_in_clip == pytest.approx(min(video.beep_time, project.trim_pre_buffer_seconds))


def test_a_native_stage_never_trimmed_still_reads_the_source_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _reset_match_context: list
) -> None:
    """No rendition and no trim: the full source WAV, as before. Only a
    mirror, which has no source, stops at the missing rendition."""
    _client, _state, storage, project, root, video, _cuts = _setup(
        tmp_path, monkeypatch, _reset_match_context
    )
    full = audio_helpers.video_audio_path(root, 1, video, project=project)
    wav_src = root.parent / "full.wav"
    _write_wav(wav_src)
    storage.write_bytes(audio_helpers._storage_audio_key(project, full), wav_src.read_bytes())  # type: ignore[arg-type]

    result = audio_helpers.published_audit_audio(root, 1, video.beep_time, project=project)

    assert result is not None and result.trimmed is False


@pytest.mark.parametrize("cloud_run", [True, False])
def test_a_memory_backed_worker_keeps_no_full_trim_after_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _reset_match_context: list, cloud_run: bool
) -> None:
    """Cloud Run's disk is memory: once the products are published, the about
    1 GB a 4K trim takes would only crowd the next job. A box with a real
    disk keeps it for a later job to reuse."""
    if cloud_run:
        monkeypatch.setenv("CLOUD_RUN_EXECUTION", "splitsmith-worker-staging-abc")
    else:
        monkeypatch.delenv("CLOUD_RUN_EXECUTION", raising=False)
    client, _state, storage, project, root, video, cuts = _setup(tmp_path, monkeypatch, _reset_match_context)

    job = client.post("/api/shooters/me/stages/1/trim?force=true").json()
    assert _wait_for_job(client, job["id"])["status"] == "succeeded"

    keys = _keys(project, root, video)
    assert len(cuts) == 1
    assert storage.exists(keys["web"]) and storage.exists(keys["wav"])
    assert audio_helpers.trimmed_video_path(root, 1, video, project=project).exists() is not cloud_run


def test_shot_detection_on_a_memory_backed_worker_drops_the_trim_it_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _reset_match_context: list
) -> None:
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "splitsmith-worker-staging-abc")
    client, state, _storage, project, root, video, cuts = _setup(tmp_path, monkeypatch, _reset_match_context)
    seen: list[int] = []

    def detect_stage(**kwargs: object) -> None:
        seen.append(len(kwargs["audio"]))  # type: ignore[arg-type]
        raise _StopError("stop before the ensemble")

    monkeypatch.setattr(server_mod, "_ensemble_runtime_is_warm", lambda *_a, **_k: True)
    monkeypatch.setattr(state.compute, "detect_stage", detect_stage)

    job = client.post("/api/shooters/me/stages/1/shot-detect").json()
    _wait_for_job(client, job["id"])

    assert len(cuts) == 1 and seen == [WAV_FRAMES]
    assert not audio_helpers.trimmed_video_path(root, 1, video, project=project).exists()
