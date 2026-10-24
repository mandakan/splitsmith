"""A hosted trim job cuts from a presigned URL, not a download (#1674).

The audit trim reads one window of its source. Read over HTTP with ``-ss``
before ``-i``, ffmpeg fetches about that window's bytes and nothing lands on
the worker's disk; on Cloud Run that disk is memory, where a downloaded
source plus the cut ran workers out of it (run A5). A read that reports
errors is discarded and cut again from a download, as the proxy does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith import trim as trim_module
from splitsmith.match_project import MatchProject
from tests.test_hosted_web_only_trims import SOURCE_KEY, _keys, _setup
from tests.ui._helpers import _wait_for_job

SIGNED = "https://bucket.example/raw/VID1.mp4?X-Amz-Signature=s3cret"


@pytest.fixture
def match_context():  # type: ignore[no-untyped-def]
    """``_setup`` pins the seeded match as a request's alias would; this
    resets it after the test."""
    tokens: list = []
    yield tokens
    for var, token in reversed(tokens):
        var.reset(token)


def _presigning(storage, monkeypatch: pytest.MonkeyPatch) -> list[str]:  # type: ignore[no-untyped-def]
    signed: list[str] = []

    def presign(key: str, **kwargs: object) -> str:
        signed.append(key)
        return SIGNED

    monkeypatch.setattr(type(storage), "supports_presigned_get", property(lambda self: True))
    monkeypatch.setattr(storage, "presign_get_url", presign)
    return signed


def _inputs(monkeypatch: pytest.MonkeyPatch, *, fail_url: bool = False) -> list[object]:
    """Wrap the faked cut to record each input; ``fail_url`` makes a URL read
    report errors the way ffmpeg does."""
    seen: list[object] = []
    fake = trim_module.trim_video

    def recording(**kwargs: object) -> trim_module.TrimResult:
        seen.append(kwargs["input_path"])
        if fail_url and isinstance(kwargs["input_path"], str):
            Path(kwargs["output_path"]).write_bytes(b"partial")  # type: ignore[arg-type]
            raise trim_module.SourceReadError("ffmpeg reported errors reading the source: '<source-url>'")
        return fake(**kwargs)

    monkeypatch.setattr(trim_module, "trim_video", recording)
    return seen


def test_a_hosted_trim_cuts_from_a_url_and_downloads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, match_context: list
) -> None:
    client, _state, storage, project, root, video, cuts = _setup(tmp_path, monkeypatch, match_context)
    signed = _presigning(storage, monkeypatch)
    seen = _inputs(monkeypatch)

    job = client.post("/api/shooters/me/stages/1/trim?force=true").json()
    assert _wait_for_job(client, job["id"])["status"] == "succeeded"

    assert seen == [SIGNED]
    assert signed == [SOURCE_KEY]
    assert SOURCE_KEY not in storage.fetched
    assert not (root / video.path).exists()
    keys = _keys(project, root, video)
    assert storage.exists(keys["params"]) and storage.exists(keys["web"]) and storage.exists(keys["wav"])
    assert MatchProject.load(root).stage(1).primary().processed["trim"] is True  # type: ignore[union-attr]


def test_a_failed_streamed_read_cuts_again_from_a_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, match_context: list
) -> None:
    client, _state, storage, project, root, video, cuts = _setup(tmp_path, monkeypatch, match_context)
    _presigning(storage, monkeypatch)
    seen = _inputs(monkeypatch, fail_url=True)

    job = client.post("/api/shooters/me/stages/1/trim?force=true").json()
    assert _wait_for_job(client, job["id"])["status"] == "succeeded"

    assert seen == [SIGNED, (root / video.path).resolve()]
    assert SOURCE_KEY in storage.fetched
    trim = root / "trimmed"
    assert not list(trim.glob("*partial*"))
    keys = _keys(project, root, video)
    assert storage.exists(keys["web"]) and storage.exists(keys["wav"])


def test_a_local_mirror_is_cut_from_disk_without_presigning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, match_context: list
) -> None:
    """A worker that already holds the source (gaspode's disk cache, a beep
    job before it on this execution) reads it from disk."""
    client, _state, storage, project, root, video, cuts = _setup(tmp_path, monkeypatch, match_context)
    signed = _presigning(storage, monkeypatch)
    seen = _inputs(monkeypatch)
    local = root / video.path
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"SOURCEBYTES")

    job = client.post("/api/shooters/me/stages/1/trim?force=true").json()
    assert _wait_for_job(client, job["id"])["status"] == "succeeded"

    assert seen == [local.resolve()]
    assert signed == []


def test_a_store_without_presigned_urls_downloads_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, match_context: list
) -> None:
    client, _state, storage, project, root, video, cuts = _setup(tmp_path, monkeypatch, match_context)
    seen = _inputs(monkeypatch)

    job = client.post("/api/shooters/me/stages/1/trim?force=true").json()
    assert _wait_for_job(client, job["id"])["status"] == "succeeded"

    assert seen == [(root / video.path).resolve()]
    assert SOURCE_KEY in storage.fetched
