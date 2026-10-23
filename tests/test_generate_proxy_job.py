"""``generate_proxy`` job body + ``_dispatch_proxy_job`` dispatch helper.

The body transcodes a preview via ``splitsmith.proxy.transcode_proxy``
(monkeypatched here so no ffmpeg runs) and uploads the result under the
``raw_proxy/`` prefix. A store with presigned GET URLs hands ffmpeg the
URL (#1674); one without, a failed presign, or a streamed read that went
wrong takes the download path. Every test stubs the transcode and asserts
the storage plumbing, the phases and the result shape.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from splitsmith.proxy import ProxySourceReadError, proxy_key_for
from splitsmith.storage import FilesystemStorage
from splitsmith.ui.jobs import JobBodyRegistry, JobCancelled
from splitsmith.ui.server import _PROXY_SOURCE_URL_TTL_S, AppState, _dispatch_proxy_job, register_job_bodies


class _StubTimer:
    def __init__(self) -> None:
        self.phases: list[str] = []
        self.meta: dict[str, object] = {}

    @contextmanager
    def phase(self, name: str):  # type: ignore[override]
        self.phases.append(name)
        yield

    def set_meta(self, **kw: object) -> None:
        self.meta.update(kw)


class _StubHandle:
    """Duck-typed JobHandle that records the final result payload."""

    def __init__(self) -> None:
        self.id = "stub-proxy-job"
        self.timer = _StubTimer()
        self.result: dict[str, Any] | None = None

    def update(self, *, progress: float | None = None, message: str | None = None) -> None:
        pass

    def check_cancel(self) -> None:
        pass

    def set_result(self, payload: dict[str, Any]) -> None:
        self.result = payload


class _FakeJobBackend:
    """Records submitted jobs; ``active`` drives ``find_active``."""

    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []
        self.active: Any = None
        self.bodies = JobBodyRegistry()

    async def submit(
        self,
        *,
        kind: str,
        args: dict[str, Any] | None = None,
        stage_number: int | None = None,
        shooter_slug: str | None = None,
        video_id: str | None = None,
    ) -> None:
        self.submitted.append(
            {
                "kind": kind,
                "args": args,
                "stage_number": stage_number,
                "shooter_slug": shooter_slug,
                "video_id": video_id,
            }
        )

    async def find_active(
        self,
        *,
        kind: str | None = None,
        stage_number: int | None = None,
        shooter_slug: str | None = None,
        video_id: str | None = None,
    ) -> Any:
        return self.active


def _state_with_storage(tmp_path: Path) -> AppState:
    state = AppState()
    state.storage = FilesystemStorage(tmp_path)
    register_job_bodies(state)
    return state


# --- job body -----------------------------------------------------------


def test_generate_proxy_transcodes_and_uploads(tmp_path: Path, monkeypatch) -> None:
    state = _state_with_storage(tmp_path)
    storage = state.storage
    assert storage is not None
    storage.write_bytes("raw/clip.mov", b"fake source bytes")

    def fake_transcode(inp: Path, outp: Path, _config, *, ffmpeg_binary: str, runner) -> None:
        # The source was fetched to a local temp file before transcode.
        assert Path(inp).read_bytes() == b"fake source bytes"
        Path(outp).write_bytes(b"PROXYDATA")

    monkeypatch.setattr("splitsmith.proxy.transcode_proxy", fake_transcode)

    body = state.job_bodies.get("generate_proxy")
    handle = _StubHandle()
    body(handle, raw_path="raw/clip.mov")

    proxy_key = proxy_key_for("raw/clip.mov")  # raw_proxy/clip.mp4
    assert storage.exists(proxy_key)
    assert storage.read_bytes(proxy_key) == b"PROXYDATA"
    assert handle.result == {"proxy_key": proxy_key, "size_bytes": len(b"PROXYDATA")}
    # No presigned URLs on a filesystem store: the download path, timed as before.
    assert handle.timer.phases == ["download", "transcode", "upload"]
    assert handle.timer.meta["source_read"] == "download"


def test_generate_proxy_skips_when_proxy_exists(tmp_path: Path, monkeypatch) -> None:
    state = _state_with_storage(tmp_path)
    storage = state.storage
    assert storage is not None
    storage.write_bytes("raw/clip.mov", b"src")
    proxy_key = proxy_key_for("raw/clip.mov")
    storage.write_bytes(proxy_key, b"existing")

    called: list[int] = []
    monkeypatch.setattr("splitsmith.proxy.transcode_proxy", lambda *a, **k: called.append(1))

    body = state.job_bodies.get("generate_proxy")
    handle = _StubHandle()
    body(handle, raw_path="raw/clip.mov")

    assert called == []  # never shelled the transcode
    assert handle.result == {"proxy_key": proxy_key, "skipped": "exists"}
    assert storage.read_bytes(proxy_key) == b"existing"  # untouched


def test_generate_proxy_no_storage_is_noop() -> None:
    state = AppState()  # storage stays None (local mode)
    register_job_bodies(state)

    body = state.job_bodies.get("generate_proxy")
    handle = _StubHandle()
    body(handle, raw_path="raw/clip.mov")

    assert handle.result == {"skipped": "no-storage"}


# --- streamed source (#1674) --------------------------------------------

_URL = "https://r2.example/raw/clip.mov?X-Amz-Signature=secret"


class _PresigningStorage(FilesystemStorage):
    """A filesystem store that hands out presigned GET URLs, like R2, and
    records every whole-object read so a test can tell a streamed job from a
    downloaded one."""

    def __init__(self, root: Path, *, presign_error: Exception | None = None) -> None:
        super().__init__(root)
        self.presign_error = presign_error
        self.presigned: list[tuple[str, int]] = []
        self.streamed_reads: list[str] = []

    @property
    def supports_presigned_get(self) -> bool:
        return True

    def presign_get_url(self, path: str, *, expires_in: int, content_type=None, disposition="inline") -> str:
        if self.presign_error is not None:
            raise self.presign_error
        self.presigned.append((path, expires_in))
        return _URL

    def open_stream(self, path: str):
        self.streamed_reads.append(path)
        return super().open_stream(path)


def _state_with_presigning_storage(tmp_path: Path, **kw: Any) -> tuple[AppState, _PresigningStorage]:
    state = AppState()
    storage = _PresigningStorage(tmp_path, **kw)
    state.storage = storage
    register_job_bodies(state)
    storage.write_bytes("raw/clip.mov", b"fake source bytes")
    return state, storage


def test_generate_proxy_streams_from_a_presigned_url(tmp_path: Path, monkeypatch) -> None:
    state, storage = _state_with_presigning_storage(tmp_path)
    inputs: list[object] = []

    def fake_transcode(inp, outp: Path, _config, *, ffmpeg_binary: str, runner) -> None:
        inputs.append(inp)
        Path(outp).write_bytes(b"PROXYDATA")

    monkeypatch.setattr("splitsmith.proxy.transcode_proxy", fake_transcode)

    handle = _StubHandle()
    state.job_bodies.get("generate_proxy")(handle, raw_path="raw/clip.mov")

    # ffmpeg got the URL itself, and the object was never downloaded.
    assert inputs == [_URL]
    assert storage.presigned == [("raw/clip.mov", _PROXY_SOURCE_URL_TTL_S)]
    assert storage.streamed_reads == []
    # One phase replaces download + transcode; the meta says which path ran.
    assert handle.timer.phases == ["stream_transcode", "upload"]
    assert handle.timer.meta["source_read"] == "url"
    proxy_key = proxy_key_for("raw/clip.mov")
    assert storage.read_bytes(proxy_key) == b"PROXYDATA"
    assert handle.result == {"proxy_key": proxy_key, "size_bytes": len(b"PROXYDATA")}


def test_presigned_url_outlives_a_long_transcode() -> None:
    # The store checks the signature on every range request, including a
    # reconnect late in the transcode; an hour-scale TTL is the floor.
    assert _PROXY_SOURCE_URL_TTL_S >= 3600


def test_generate_proxy_falls_back_to_download_when_the_stream_fails(tmp_path: Path, monkeypatch) -> None:
    state, storage = _state_with_presigning_storage(tmp_path)
    inputs: list[object] = []

    def fake_transcode(inp, outp: Path, _config, *, ffmpeg_binary: str, runner) -> None:
        inputs.append(inp)
        if isinstance(inp, str):
            # A streamed read that exited 0 with a truncated output: the
            # partial file is on disk and must not be uploaded.
            Path(outp).write_bytes(b"TRUNCATED")
            raise ProxySourceReadError("Stream ends prematurely")
        assert Path(inp).read_bytes() == b"fake source bytes"
        assert not Path(outp).exists()
        Path(outp).write_bytes(b"PROXYDATA")

    monkeypatch.setattr("splitsmith.proxy.transcode_proxy", fake_transcode)

    handle = _StubHandle()
    state.job_bodies.get("generate_proxy")(handle, raw_path="raw/clip.mov")

    assert inputs[0] == _URL and isinstance(inputs[1], Path)
    assert storage.streamed_reads == ["raw/clip.mov"]
    assert handle.timer.phases == ["stream_transcode", "download", "transcode", "upload"]
    assert handle.timer.meta["source_read"] == "url_failed"
    assert storage.read_bytes(proxy_key_for("raw/clip.mov")) == b"PROXYDATA"


def test_generate_proxy_downloads_when_presign_fails(tmp_path: Path, monkeypatch) -> None:
    state, storage = _state_with_presigning_storage(tmp_path, presign_error=RuntimeError("no creds"))
    inputs: list[object] = []

    def fake_transcode(inp, outp: Path, _config, *, ffmpeg_binary: str, runner) -> None:
        inputs.append(inp)
        Path(outp).write_bytes(b"PROXYDATA")

    monkeypatch.setattr("splitsmith.proxy.transcode_proxy", fake_transcode)

    handle = _StubHandle()
    state.job_bodies.get("generate_proxy")(handle, raw_path="raw/clip.mov")

    assert len(inputs) == 1 and isinstance(inputs[0], Path)
    assert handle.timer.phases == ["download", "transcode", "upload"]
    assert handle.timer.meta["source_read"] == "download"


def test_generate_proxy_cancel_during_stream_is_not_a_fallback(tmp_path: Path, monkeypatch) -> None:
    state, storage = _state_with_presigning_storage(tmp_path)

    def fake_transcode(inp, outp: Path, _config, *, ffmpeg_binary: str, runner) -> None:
        raise JobCancelled()

    monkeypatch.setattr("splitsmith.proxy.transcode_proxy", fake_transcode)

    with pytest.raises(JobCancelled):
        state.job_bodies.get("generate_proxy")(_StubHandle(), raw_path="raw/clip.mov")
    assert storage.streamed_reads == []  # a cancel never starts the download


# --- dispatch helper ----------------------------------------------------


def test_dispatch_submits_generate_proxy(tmp_path: Path) -> None:
    state = AppState()
    state.storage = FilesystemStorage(tmp_path)
    fake = _FakeJobBackend()
    state.jobs = fake

    asyncio.run(_dispatch_proxy_job(state, "raw/clip.mov"))

    assert len(fake.submitted) == 1
    job = fake.submitted[0]
    assert job["kind"] == "generate_proxy"
    assert job["video_id"] == "raw/clip.mov"
    assert job["args"] == {"raw_path": "raw/clip.mov"}


def test_dispatch_dedupes_when_active(tmp_path: Path) -> None:
    state = AppState()
    state.storage = FilesystemStorage(tmp_path)
    fake = _FakeJobBackend()
    fake.active = object()  # a proxy job is already in flight for this key
    state.jobs = fake

    asyncio.run(_dispatch_proxy_job(state, "raw/clip.mov"))

    assert fake.submitted == []


def test_dispatch_noop_without_storage() -> None:
    state = AppState()  # storage None -> local mode, nothing to proxy
    fake = _FakeJobBackend()
    state.jobs = fake

    asyncio.run(_dispatch_proxy_job(state, "raw/clip.mov"))

    assert fake.submitted == []
