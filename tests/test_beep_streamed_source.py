"""A hosted beep detection reads its source's audio from a presigned URL (#1674).

Beep detection was the last hosted job that downloaded a whole source. On
Cloud Run that download lands in memory, and in run A6 the cache it filled
pushed 4K trims over the limit. Read over HTTP, ffmpeg decodes the audio and
nothing lands on the worker's disk; a read that reports errors is discarded
and detection runs again from a download, as for a trim.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from splitsmith import trim as trim_module
from splitsmith.config import BeepDetection
from splitsmith.match_project import MatchProject
from splitsmith.ui import audio as audio_helpers
from tests.test_hosted_web_only_trims import SOURCE_KEY, _setup, _write_wav
from tests.test_trim_streamed_source import SIGNED, _presigning
from tests.ui._helpers import _wait_for_job


@pytest.fixture
def match_context():  # type: ignore[no-untyped-def]
    """``_setup`` pins the seeded match as a request's alias would; this
    resets it after the test."""
    tokens: list = []
    yield tokens
    for var, token in reversed(tokens):
        var.reset(token)


def _audio_inputs(monkeypatch: pytest.MonkeyPatch, *, fail_url: bool = False) -> list[str]:
    """Fake the source WAV extraction, recording each ``-i`` input; with
    ``fail_url`` a URL read leaves a partial and reports errors."""
    seen: list[str] = []

    def extract(args: list[str], out: Path, ffmpeg_binary: str, *, url: bool = False) -> None:
        source = args[args.index("-i") + 1]
        seen.append(source)
        assert url == source.startswith("https://")
        if fail_url and url:
            out.write_bytes(b"partial")
            raise trim_module.SourceReadError("ffmpeg reported errors reading the source: '<source-url>'")
        _write_wav(out)

    monkeypatch.setattr(audio_helpers, "_run_audio_ffmpeg", extract)
    monkeypatch.setattr(
        audio_helpers.beep_detect,
        "detect_beep",
        lambda audio, sr, cfg: BeepDetection(time=0.5, peak_amplitude=0.4, duration_ms=300.0, confidence=0.2),
    )
    return seen


def _detect(client, video) -> dict:  # type: ignore[no-untyped-def]
    job = client.post(f"/api/shooters/me/stages/1/videos/{video.video_id}/detect-beep?force=true").json()
    return _wait_for_job(client, job["id"])


def test_a_hosted_beep_detection_reads_a_url_and_downloads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, match_context: list
) -> None:
    client, _state, storage, _project, root, video, _cuts = _setup(tmp_path, monkeypatch, match_context)
    signed = _presigning(storage, monkeypatch)
    seen = _audio_inputs(monkeypatch)

    assert _detect(client, video)["status"] == "succeeded"

    assert seen == [SIGNED]
    assert SOURCE_KEY in signed
    assert SOURCE_KEY not in storage.fetched
    assert not (root / video.path).exists()
    assert MatchProject.load(root).stage(1).primary().beep_time == 0.5  # type: ignore[union-attr]


def test_a_failed_streamed_read_detects_again_from_a_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, match_context: list
) -> None:
    client, _state, storage, project, root, video, _cuts = _setup(tmp_path, monkeypatch, match_context)
    _presigning(storage, monkeypatch)
    seen = _audio_inputs(monkeypatch, fail_url=True)

    assert _detect(client, video)["status"] == "succeeded"

    assert seen == [SIGNED, str((root / video.path).resolve())]
    assert SOURCE_KEY in storage.fetched
    wav = audio_helpers.video_audio_path(root, 1, video, project=project)
    assert not list(wav.parent.glob("*partial*"))
    assert MatchProject.load(root).stage(1).primary().beep_time == 0.5  # type: ignore[union-attr]


def test_a_store_without_presigned_urls_downloads_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, match_context: list
) -> None:
    client, _state, storage, _project, root, video, _cuts = _setup(tmp_path, monkeypatch, match_context)
    seen = _audio_inputs(monkeypatch)

    assert _detect(client, video)["status"] == "succeeded"

    assert seen == [str((root / video.path).resolve())]
    assert SOURCE_KEY in storage.fetched


def test_a_url_read_with_stderr_raises_without_the_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    def run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=f"{SIGNED}: Connection reset\n")

    monkeypatch.setattr(audio_helpers.subprocess, "run", run)
    monkeypatch.setattr(audio_helpers.shutil, "which", lambda b: b)
    with pytest.raises(trim_module.SourceReadError) as err:
        audio_helpers._run_audio_ffmpeg(["-i", SIGNED], tmp_path / "out.wav", "ffmpeg", url=True)
    assert "s3cret" not in str(err.value)
    # A local read keeps ignoring warnings on stderr.
    audio_helpers._run_audio_ffmpeg(["-i", "local.mp4"], tmp_path / "out.wav", "ffmpeg")


def _samples(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


@pytest.mark.integration
def test_the_audio_read_from_a_url_matches_the_local_extraction(
    tmp_path: Path, synthetic_source_video: Path
) -> None:
    """Real ffmpeg over real HTTP: the whole-source WAV and a window's WAV
    decode to the same samples as from a local copy."""
    from tests.test_proxy import _RangeServer

    def extract(source: Path | str, out: Path, window: tuple[float, float] | None) -> None:
        args, on_disk = audio_helpers._source_input(source)
        seek = ["-ss", str(window[0]), "-t", str(window[1] - window[0])] if window else []
        audio_helpers._run_audio_ffmpeg(
            [*seek, *args, "-ac", "1", "-ar", "48000", "-vn"], out, "ffmpeg", url=on_disk is None
        )

    for window in (None, (15.0, 20.0)):
        name = "whole" if window is None else "window"
        extract(synthetic_source_video, tmp_path / f"{name}-file.wav", window)
        with _RangeServer(synthetic_source_video) as server:
            extract(server.url, tmp_path / f"{name}-url.wav", window)
        assert _samples(tmp_path / f"{name}-url.wav") == _samples(tmp_path / f"{name}-file.wav")


@pytest.mark.integration
def test_an_audio_read_from_a_url_raises_when_the_stream_cannot_resume(
    tmp_path: Path, synthetic_source_video: Path
) -> None:
    from tests.test_proxy import _RangeServer

    size = synthetic_source_video.stat().st_size
    with _RangeServer(synthetic_source_video, drop_after=size // 4) as server:
        args, _ = audio_helpers._source_input(server.url)
        with pytest.raises(trim_module.SourceReadError):
            audio_helpers._run_audio_ffmpeg(
                [*args, "-ac", "1", "-ar", "48000", "-vn"], tmp_path / "url.wav", "ffmpeg", url=True
            )
    assert server.dropped
