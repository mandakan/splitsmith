"""``generate_proxy`` job body + ``_dispatch_proxy_job`` dispatch helper.

The body downloads the raw object, transcodes a preview via
``splitsmith.proxy.transcode_proxy`` (monkeypatched here so no ffmpeg
runs), and uploads the result under the ``raw_proxy/`` prefix. There is
no small video fixture to transcode against, so every test stubs the
transcode and asserts the storage plumbing + result shape instead.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from splitsmith.proxy import proxy_key_for
from splitsmith.storage import FilesystemStorage
from splitsmith.ui.jobs import JobBodyRegistry
from splitsmith.ui.server import AppState, _dispatch_proxy_job, register_job_bodies


class _StubTimer:
    @contextmanager
    def phase(self, _name: str):  # type: ignore[override]
        yield

    def set_meta(self, **_kw: object) -> None:
        pass


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
        video_id: str | None = None,
    ) -> None:
        self.submitted.append(
            {"kind": kind, "args": args, "stage_number": stage_number, "video_id": video_id}
        )

    async def find_active(
        self,
        *,
        kind: str | None = None,
        stage_number: int | None = None,
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
