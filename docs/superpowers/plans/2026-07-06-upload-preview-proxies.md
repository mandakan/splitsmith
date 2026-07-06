# Upload Preview/Proxy Videos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a lightweight 480p, keyframe-dense proxy for every hosted upload and stream it in the Ingest stage-assignment view (with transparent source fallback), so flipping between clips is fast.

**Architecture:** A hosted-only `generate_proxy` worker job, dispatched on every raw upload, transcodes `raw/<name>.<ext>` -> `raw_proxy/<name>.mp4` in object storage, keyed purely on the raw storage path + tenant prefix (no project needed). The video stream endpoint gains a `kind=proxy` mode that serves the proxy if it exists and falls back to the source otherwise, so playback is always correct and the SPA never relies on a session-start snapshot. A `proxy_ready` flag (derived from one `storage.list` per project load) drives a cosmetic "generating" badge.

**Tech Stack:** Python 3.11+, FastAPI, Procrastinate job queue, boto3/S3 (Cloudflare R2), ffmpeg (libx264), Pydantic; React + TypeScript SPA (Vite, pnpm).

## Global Constraints

- Python 3.11+, type hints everywhere; Pydantic for data models; `pathlib.Path` for paths, never strings; f-strings.
- Black line length 110; Ruff clean; imports grouped stdlib / third-party / local; no relative imports beyond a single dot.
- New copy/comments use a single ASCII dash `-`, never `--` or em dash. Grep added lines before committing.
- Detection/analysis logic stays out of `cli.py`; pure functions take data + config and return results (no file I/O inside pure logic beyond the ffmpeg subprocess wrapper, mirroring `trim.py`/`thumbnail.py`).
- Tunable parameters live in `config.py` as Pydantic models with defaults.
- Mock ffmpeg in unit tests (no shell-out); real-ffmpeg tests are marked `@pytest.mark.integration`.
- No new third-party dependencies.
- Hosted-only: everything degrades to a no-op when `state.storage is None` (local mode).
- SPA has no test runner: verify frontend via `pnpm typecheck` + `pnpm build` + scoped eslint (run from `src/splitsmith/ui_static/`).
- Gates before PR: `ruff check`, `black --check`, `pytest`, and `pytest -m docker` (storage/job paths).

---

### Task 1: Proxy config, key mapping, and transcode function

**Files:**
- Create: `src/splitsmith/proxy.py`
- Modify: `src/splitsmith/config.py` (add `ProxyConfig`)
- Test: `tests/test_proxy.py`

**Interfaces:**
- Produces:
  - `class ProxyConfig(BaseModel)` in `config.py` with fields `height: int = 480`, `crf: int = 30`, `preset: str = "veryfast"`, `gop: int = 15`, `audio_bitrate: str = "96k"`, `video_codec: str = "libx264"`.
  - `proxy_key_for(raw_path: str) -> str` in `proxy.py` - maps `raw/<name>.<ext>` to `raw_proxy/<name>.mp4`; raises `ValueError` if `raw_path` does not start with `raw/`.
  - `transcode_proxy(input_path: Path, output_path: Path, config: ProxyConfig, *, ffmpeg_binary: str, runner=subprocess.run) -> None` - builds+runs the ffmpeg command; raises `ProxyError` on non-zero exit.
  - `class ProxyError(RuntimeError)`.

- [ ] **Step 1: Write failing tests for `proxy_key_for`**

```python
# tests/test_proxy.py
import subprocess
from pathlib import Path

import pytest

from splitsmith.config import ProxyConfig
from splitsmith.proxy import ProxyError, proxy_key_for, transcode_proxy


def test_proxy_key_for_maps_prefix_and_forces_mp4():
    assert proxy_key_for("raw/GX010123.MP4") == "raw_proxy/GX010123.mp4"
    assert proxy_key_for("raw/clip.mov") == "raw_proxy/clip.mp4"


def test_proxy_key_for_rejects_non_raw_path():
    with pytest.raises(ValueError):
        proxy_key_for("exports/foo.mp4")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_proxy.py -v`
Expected: FAIL (ImportError / module not found).

- [ ] **Step 3: Implement `ProxyConfig` in `config.py`**

Add near the other config models (mirror their style - `BaseModel` subclass with field defaults and a short docstring):

```python
class ProxyConfig(BaseModel):
    """Low-res, keyframe-dense preview proxy for fast scrub/seek in Ingest."""

    height: int = 480
    crf: int = 30
    preset: str = "veryfast"
    gop: int = 15
    audio_bitrate: str = "96k"
    video_codec: str = "libx264"
```

- [ ] **Step 4: Implement `proxy.py`**

```python
"""Preview/proxy video generation (hosted Ingest fast-scrub).

Pure ffmpeg wrapper mirroring ``trim.py``: paths + config in, shells ffmpeg
via an injected runner, raises on failure. No storage or project I/O here.
"""

import subprocess
from pathlib import Path
from typing import Callable

from .config import ProxyConfig

RAW_PREFIX = "raw/"
PROXY_PREFIX = "raw_proxy/"


class ProxyError(RuntimeError):
    """ffmpeg failed to produce a proxy."""


def proxy_key_for(raw_path: str) -> str:
    """Map a raw upload key to its proxy key: raw/<name>.<ext> -> raw_proxy/<name>.mp4."""
    if not raw_path.startswith(RAW_PREFIX):
        raise ValueError(f"expected a {RAW_PREFIX!r} key, got {raw_path!r}")
    name = Path(raw_path[len(RAW_PREFIX) :]).with_suffix(".mp4").as_posix()
    return f"{PROXY_PREFIX}{name}"


def transcode_proxy(
    input_path: Path,
    output_path: Path,
    config: ProxyConfig,
    *,
    ffmpeg_binary: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    """Transcode ``input_path`` to a low-res, dense-GOP, faststart MP4 proxy."""
    cmd = [
        ffmpeg_binary,
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        f"scale=-2:{config.height}",
        "-c:v",
        config.video_codec,
        "-preset",
        config.preset,
        "-crf",
        str(config.crf),
        "-g",
        str(config.gop),
        "-keyint_min",
        str(config.gop),
        "-sc_threshold",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        config.audio_bitrate,
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = runner(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProxyError(f"ffmpeg proxy transcode failed: {result.stderr}")
```

Note: check `src/splitsmith/trim.py`'s `trim_video` for the exact `runner(...)` call convention used in this codebase and match it (arg order, `capture_output`/`text`, how it reads `returncode`/`stderr`). Adjust the call above only if `trim.py` differs.

- [ ] **Step 5: Add the argv unit test (mock runner) and the ValueError test already written**

```python
def test_transcode_proxy_builds_expected_argv():
    calls = {}

    def fake_runner(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    transcode_proxy(
        Path("/in.mp4"),
        Path("/out.mp4"),
        ProxyConfig(),
        ffmpeg_binary="ffmpeg",
        runner=fake_runner,
    )
    cmd = calls["cmd"]
    assert "scale=-2:480" in cmd
    assert cmd[cmd.index("-crf") + 1] == "30"
    assert cmd[cmd.index("-g") + 1] == "15"
    assert cmd[cmd.index("-keyint_min") + 1] == "15"
    assert cmd[cmd.index("-sc_threshold") + 1] == "0"
    assert "+faststart" in cmd
    assert cmd[-1] == "/out.mp4"
    assert cmd[cmd.index("-i") + 1] == "/in.mp4"


def test_transcode_proxy_raises_on_ffmpeg_error():
    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    with pytest.raises(ProxyError):
        transcode_proxy(
            Path("/in.mp4"),
            Path("/out.mp4"),
            ProxyConfig(),
            ffmpeg_binary="ffmpeg",
            runner=fake_runner,
        )
```

- [ ] **Step 6: Run unit tests to verify pass**

Run: `uv run pytest tests/test_proxy.py -v`
Expected: PASS (all non-integration tests).

- [ ] **Step 7: Add the integration test (real ffmpeg)**

Reuse an existing short fixture video. Grep `tests/` for an existing small `.mp4`/`.mov` fixture (e.g. under `tests/fixtures/`); use its path. Do NOT synthesize a fake video.

```python
@pytest.mark.integration
def test_transcode_proxy_produces_smaller_valid_mp4(tmp_path):
    from splitsmith.runtime import process_runtime

    src = Path("tests/fixtures/<EXISTING_SHORT_CLIP>.mp4")  # replace with a real fixture
    out = tmp_path / "proxy.mp4"
    transcode_proxy(
        src, out, ProxyConfig(),
        ffmpeg_binary=process_runtime().ffmpeg_binary,
    )
    assert out.exists() and out.stat().st_size > 0
    assert out.stat().st_size < src.stat().st_size
```

If no suitable small video fixture exists, skip this test with a clear `pytest.skip` reason rather than fabricating one, and note it in the commit message.

- [ ] **Step 8: Run integration test (best effort)**

Run: `uv run pytest tests/test_proxy.py -m integration -v`
Expected: PASS (or SKIP if no fixture). Non-integration suite must be green regardless.

- [ ] **Step 9: Format, lint, commit**

```bash
uv run black src/splitsmith/proxy.py src/splitsmith/config.py tests/test_proxy.py
uv run ruff check src/splitsmith/proxy.py src/splitsmith/config.py tests/test_proxy.py
git add src/splitsmith/proxy.py src/splitsmith/config.py tests/test_proxy.py
git commit -m "feat: add proxy transcode + key mapping + config (#561)"
```

---

### Task 2: `generate_proxy` job body, registration, and dispatch on upload

**Files:**
- Modify: `src/splitsmith/ui/server.py` (add body in `register_job_bodies`, register it; add `_dispatch_proxy_job`; call it from both upload paths)
- Test: `tests/ui/test_generate_proxy_job.py` (or the existing hosted-app test module pattern - grep for how other job/upload tests are structured, e.g. tests using moto/MinIO storage)

**Interfaces:**
- Consumes: `proxy_key_for`, `transcode_proxy`, `ProxyConfig` (Task 1).
- Produces:
  - Job kind string `"generate_proxy"` with body `_run_generate_proxy(handle, *, raw_path: str) -> None`.
  - Helper `async def _dispatch_proxy_job(state, raw_key: str) -> None` (dedupe via `find_active`, then `submit`), called after successful single-shot and multipart uploads.
  - Job result dict shape: `{"proxy_key": str, "size_bytes": int}` on success, or `{"skipped": "no-storage" | "exists"}`.

- [ ] **Step 1: Write a failing test that uploading dispatches a proxy job**

Mirror the existing hosted-app test setup (grep `tests/` for `upload` + a fixture that builds `create_app` with an S3/moto or in-memory `Storage`; reuse that fixture verbatim). The test: POST a small file to `/api/me/raw/upload`, then assert a `generate_proxy` job exists for that key (via `state.jobs.list()` or the jobs API), and that running it writes the `raw_proxy/...` object into storage.

```python
def test_upload_dispatches_and_runs_generate_proxy(hosted_client, storage, state):
    # upload a tiny real fixture clip
    with open("tests/fixtures/<EXISTING_SHORT_CLIP>.mp4", "rb") as fh:
        resp = hosted_client.post("/api/me/raw/upload", files={"file": ("clip.mp4", fh, "video/mp4")})
    assert resp.status_code == 200
    key = resp.json()["path"]  # e.g. "raw/clip.mp4"

    jobs = [j for j in state.jobs.list() if j.kind == "generate_proxy"]
    assert len(jobs) == 1

    # run the job body directly and assert the proxy object lands
    from splitsmith.proxy import proxy_key_for
    run_job_body(state, "generate_proxy", raw_path=key)  # helper mirroring how other job-body tests invoke bodies
    assert storage.exists(proxy_key_for(key))
```

Grep for how existing tests invoke a job body synchronously (there may be a helper, or they call `state.jobs.run_job(...)`). Match that mechanism instead of `run_job_body` if the codebase provides one.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/ui/test_generate_proxy_job.py -v`
Expected: FAIL (no `generate_proxy` kind registered; no dispatch).

- [ ] **Step 3: Implement `_run_generate_proxy` inside `register_job_bodies`**

Place it beside the other bodies (near `_run_trim`, `server.py` ~2026). Use `state` (closed over), `process_runtime().ffmpeg_binary`, and `_cancellable_runner(handle)` (`server.py:229`). Pattern (mirror `_run_trim`'s use of `handle.timer.phase`, `handle.update`, `handle.set_result`):

```python
def _run_generate_proxy(handle: JobHandle, *, raw_path: str) -> None:
    from ..proxy import ProxyConfig, proxy_key_for, transcode_proxy

    storage = state.storage
    if storage is None:
        handle.set_result({"skipped": "no-storage"})
        handle.update(progress=1.0, message="Skipped (local mode)")
        return

    proxy_key = proxy_key_for(raw_path)
    if storage.exists(proxy_key):
        handle.set_result({"proxy_key": proxy_key, "skipped": "exists"})
        handle.update(progress=1.0, message="Proxy already present")
        return

    import shutil
    import tempfile

    handle.update(progress=0.1, message="Fetching source...")
    with tempfile.TemporaryDirectory() as td:
        tmp_in = Path(td) / "src"
        tmp_out = Path(td) / "proxy.mp4"
        with handle.timer.phase("download"):
            with storage.open_stream(raw_path) as src, open(tmp_in, "wb") as dst:
                shutil.copyfileobj(src, dst)
        handle.check_cancel()
        handle.update(progress=0.4, message="Transcoding preview...")
        with handle.timer.phase("transcode"):
            transcode_proxy(
                tmp_in,
                tmp_out,
                ProxyConfig(),
                ffmpeg_binary=process_runtime().ffmpeg_binary,
                runner=_cancellable_runner(handle),
            )
        handle.update(progress=0.85, message="Uploading preview...")
        with handle.timer.phase("upload"):
            with open(tmp_out, "rb") as fh:
                size = storage.upload_stream(proxy_key, fh)
    handle.set_result({"proxy_key": proxy_key, "size_bytes": size})
    handle.update(progress=1.0, message="Preview ready")
```

Confirm `Path` is imported at module scope in `server.py` (it is used widely); if `transcode_proxy`'s `runner` signature needs the `_cancellable_runner` output shape, verify `_cancellable_runner` returns a callable compatible with `runner(cmd, capture_output=True, text=True)` and adjust `transcode_proxy`'s call site expectations if the cancellable runner has a different contract (match what `ensure_video_audit_trim` passes).

- [ ] **Step 4: Register the kind**

Next to the other registrations (`server.py` ~2802-2807):

```python
state.jobs.bodies.register("generate_proxy", _run_generate_proxy)
```

- [ ] **Step 5: Add `_dispatch_proxy_job` and call it from both upload paths**

Add a module-level (or `create_app`-scoped, matching where upload endpoints live) async helper:

```python
async def _dispatch_proxy_job(state, raw_key: str) -> None:
    if state.storage is None:
        return
    try:
        existing = state.jobs.find_active(kind="generate_proxy", video_id=raw_key)
        if existing is not None:
            return
        await state.jobs.submit(kind="generate_proxy", video_id=raw_key, args={"raw_path": raw_key})
    except Exception:  # proxy is an optimization; never fail the upload
        logger.exception("failed to dispatch generate_proxy for %s", raw_key)
```

Call `await _dispatch_proxy_job(state, key)` at the end of `upload_raw_video` (`server.py:5649`) just before returning, and at the end of the multipart `.../complete` handler (`server.py:5811`) once the object is assembled. Use the same `key`/`path` variable each handler already computes (`raw/{name}`). Match `find_active`'s real signature (grep `def find_active`); if it requires `stage_number`, pass `stage_number=None`.

- [ ] **Step 6: Run the job test to verify pass**

Run: `uv run pytest tests/ui/test_generate_proxy_job.py -v`
Expected: PASS.

- [ ] **Step 7: Add dedupe + no-storage tests**

```python
def test_generate_proxy_is_idempotent(state, storage):
    from splitsmith.proxy import proxy_key_for
    key = "raw/clip.mp4"
    storage.write_bytes(key, open("tests/fixtures/<EXISTING_SHORT_CLIP>.mp4", "rb").read())
    run_job_body(state, "generate_proxy", raw_path=key)
    first = storage.stat(proxy_key_for(key))
    result = run_job_body(state, "generate_proxy", raw_path=key)  # second run
    assert result.get("skipped") == "exists"


def test_dispatch_noop_without_storage(state_local):
    # state_local.storage is None -> dispatch is a no-op, no job created
    import asyncio
    asyncio.run(_dispatch_proxy_job(state_local, "raw/clip.mp4"))
    assert not any(j.kind == "generate_proxy" for j in state_local.jobs.list())
```

Adapt fixture names to the codebase's actual hosted/local app fixtures.

- [ ] **Step 8: Run, format, lint, commit**

```bash
uv run pytest tests/ui/test_generate_proxy_job.py -v
uv run black src/splitsmith/ui/server.py tests/ui/test_generate_proxy_job.py
uv run ruff check src/splitsmith/ui/server.py tests/ui/test_generate_proxy_job.py
git add src/splitsmith/ui/server.py tests/ui/test_generate_proxy_job.py
git commit -m "feat: generate_proxy job + dispatch on upload (#561)"
```

---

### Task 3: `kind=proxy` streaming with source fallback

**Files:**
- Modify: `src/splitsmith/ui/server.py` - `stream_video` (`server.py:9233`) and the match-scoped alias (`server.py:11073`)
- Test: `tests/ui/test_stream_proxy.py`

**Interfaces:**
- Consumes: `proxy_key_for` (Task 1); the `generate_proxy` job output (Task 2).
- Produces: `GET /api/shooters/{slug}/videos/stream?path=...&kind=proxy` and the `/api/match/...` alias serve the proxy object when it exists, else fall back to the existing source path unchanged.

- [ ] **Step 1: Write failing tests**

Using the hosted test app with a registered project video whose `path` is `raw/clip.mp4`:

```python
def test_stream_proxy_serves_proxy_when_present(hosted_client, storage):
    from splitsmith.proxy import proxy_key_for
    storage.write_bytes(proxy_key_for("raw/clip.mp4"), b"PROXYBYTES")
    resp = hosted_client.get("/api/shooters/<slug>/videos/stream", params={"path": "raw/clip.mp4", "kind": "proxy"})
    assert resp.status_code == 200
    assert resp.content == b"PROXYBYTES"


def test_stream_proxy_falls_back_to_source_when_absent(hosted_client, storage):
    # no raw_proxy object; must serve the source bytes, not 404
    resp = hosted_client.get("/api/shooters/<slug>/videos/stream", params={"path": "raw/clip.mp4", "kind": "proxy"})
    assert resp.status_code == 200
    assert len(resp.content) > 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/ui/test_stream_proxy.py -v`
Expected: FAIL (kind=proxy not handled; likely 400/422 or serves source only).

- [ ] **Step 3: Implement the `proxy` branch in `stream_video`**

After the path is validated against `project.find_video(...)` and before the `kind=trim`/source resolution, add:

```python
if kind == "proxy":
    from ..proxy import proxy_key_for

    storage = state.storage
    proxy_key = proxy_key_for(video.path) if video.path.startswith("raw/") else None
    if storage is not None and proxy_key is not None and storage.exists(proxy_key):
        local = project.mirror_storage_object(root, proxy_key)  # see note
        return FileResponse(local, ...)  # same FileResponse args the source branch uses
    # else fall through to the normal source resolution below
```

Implementation notes:
- Extend the endpoint's `kind` validation to accept `"proxy"` (grep how `kind` is currently constrained - a `Literal`/`Query` enum or an `if kind not in {...}` guard; add `"proxy"`).
- For mirroring the proxy object to local disk, reuse the same mechanism `kind=trim`/source uses. The source path uses `project.resolve_video_path` (which mirrors `video.path`); for an arbitrary storage key like the proxy, mirror it the same way the trim branch pulls `audio_helpers.pull_trimmed_video` does (grep `_mirror_from_storage` / `pull_trimmed_video` in `project.py`/`audio.py` and reuse the lowest-level "download this key to a temp/cache path" helper). If no key-addressed mirror helper is exposed, add a tiny local helper that streams `storage.open_stream(proxy_key)` into a cache file under `root` and returns the path, mirroring `_mirror_from_storage`'s temp+rename.
- Falling through (not returning) when the proxy is absent gives the transparent source fallback.

- [ ] **Step 4: Apply the same branch to the `/api/match/...` alias**

The alias handler (`server.py:11073`) delegates to the same logic or duplicates it. If it calls a shared inner function, the branch is already covered; otherwise add the identical `kind == "proxy"` branch there. Confirm the alias accepts a `kind` query param (add it if missing, defaulting to `auto`).

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/ui/test_stream_proxy.py -v`
Expected: PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
uv run black src/splitsmith/ui/server.py tests/ui/test_stream_proxy.py
uv run ruff check src/splitsmith/ui/server.py tests/ui/test_stream_proxy.py
git add src/splitsmith/ui/server.py tests/ui/test_stream_proxy.py
git commit -m "feat: kind=proxy video streaming with source fallback (#561)"
```

---

### Task 4: `proxy_ready` in project serialization + raw-delete cleanup

**Files:**
- Modify: `src/splitsmith/ui/server.py` - the project serialization used by `getProject`/the match-project payload (grep for where `StageVideo`s are serialized to the API response; likely a `_serialize_project`/`project.to_api_dict` path), and `DELETE /api/me/raw/{filename}` (`server.py:5925`)
- Modify: `src/splitsmith/ui/project.py` if `StageVideo`'s serialized shape is produced there
- Test: `tests/ui/test_proxy_ready.py`

**Interfaces:**
- Consumes: `proxy_key_for` (Task 1).
- Produces: every serialized `StageVideo` in the ingest/project payload carries `proxy_ready: bool`. Hosted: computed from a single `storage.list("raw_proxy/")` per request. Local (`storage is None`): always `True`. Deleting a raw video also deletes its proxy object.

- [ ] **Step 1: Write failing tests**

```python
def test_proxy_ready_true_when_proxy_exists(hosted_client, storage):
    from splitsmith.proxy import proxy_key_for
    storage.write_bytes(proxy_key_for("raw/clip.mp4"), b"x")
    proj = hosted_client.get("/api/shooters/<slug>/project").json()  # or getProject route
    vids = [v for st in proj["stages"] for v in st["videos"]] + proj.get("unassigned_videos", [])
    v = next(v for v in vids if v["path"] == "raw/clip.mp4")
    assert v["proxy_ready"] is True


def test_proxy_ready_false_when_absent(hosted_client):
    proj = hosted_client.get("/api/shooters/<slug>/project").json()
    v = next(v for st in proj["stages"] for v in st["videos"] if v["path"] == "raw/clip.mp4")
    assert v["proxy_ready"] is False


def test_delete_raw_also_deletes_proxy(hosted_client, storage):
    from splitsmith.proxy import proxy_key_for
    storage.write_bytes("raw/clip.mp4", b"x")
    storage.write_bytes(proxy_key_for("raw/clip.mp4"), b"y")
    hosted_client.delete("/api/me/raw/clip.mp4")
    assert not storage.exists(proxy_key_for("raw/clip.mp4"))
```

Adjust the project route/fixture and the video-locating code to the codebase's real shapes.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/ui/test_proxy_ready.py -v`
Expected: FAIL (`proxy_ready` key absent; proxy survives delete).

- [ ] **Step 3: Compute `proxy_ready` during serialization**

Where the project is serialized for the API (grep the handler that returns the project dict consumed by `api.getProject`):

```python
proxy_keys: set[str] = set()
if state.storage is not None:
    proxy_keys = {obj for obj in state.storage.list("raw_proxy/")}  # match .list()'s return shape (keys vs objects)
# for each serialized StageVideo `v` with storage-relative path `v.path`:
#   ready = True if state.storage is None else (proxy_key_for(v.path) in proxy_keys if v.path.startswith("raw/") else True)
#   set the serialized dict's "proxy_ready" = ready
```

Do the `.list("raw_proxy/")` once per request, not per video. Confirm what `Storage.list(prefix)` returns (full keys including the tenant prefix, or relative?) by reading `storage.py`; `proxy_key_for` returns the tenant-relative key (`raw_proxy/...`), so normalize the listing to tenant-relative before the `in` check. If serialization happens in a Pydantic model (`StageVideo`), add an optional `proxy_ready: bool = True` field and populate it in the handler after `model_dump()`, or thread the computed set into the serializer - whichever matches the existing pattern for request-derived fields.

- [ ] **Step 4: Delete the proxy on raw delete**

In `DELETE /api/me/raw/{filename}` (`server.py:5925`), after deleting the raw object, best-effort delete the proxy:

```python
from ..proxy import proxy_key_for
try:
    state.storage.delete(proxy_key_for(f"raw/{name}"))
except Exception:
    logger.exception("failed to delete proxy for %s", name)
```

Match the handler's existing `name`/key variable and its storage-None guard.

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/ui/test_proxy_ready.py -v`
Expected: PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
uv run black src/splitsmith/ui/server.py src/splitsmith/ui/project.py tests/ui/test_proxy_ready.py
uv run ruff check src/splitsmith/ui/server.py src/splitsmith/ui/project.py tests/ui/test_proxy_ready.py
git add -A
git commit -m "feat: proxy_ready flag in project payload + proxy cleanup on delete (#561)"
```

---

### Task 5: Frontend types, stream URL, and Jobs kind

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`StageVideo` type ~47-117; `shooterVideoStreamUrl` ~2623)
- Modify: `src/splitsmith/ui_static/src/components/Jobs.tsx` (`KIND_LABEL` ~53-61, `KIND_ICON` ~63-71)

**Interfaces:**
- Produces:
  - `StageVideo.proxy_ready?: boolean`.
  - `shooterVideoStreamUrl(slug: string, path: string, kind?: "auto" | "proxy")` appending `&kind=proxy` when provided.
  - `generate_proxy` entry in `KIND_LABEL` and `KIND_ICON`.

- [ ] **Step 1: Add `proxy_ready` to `StageVideo`**

In `api.ts`, add to the `StageVideo` interface (near `processed`):

```typescript
  /** Hosted only: the fast-scrub proxy exists in storage. Drives the "generating" badge. */
  proxy_ready?: boolean;
```

- [ ] **Step 2: Add optional `kind` to `shooterVideoStreamUrl`**

```typescript
shooterVideoStreamUrl(slug: string, path: string, kind?: "auto" | "proxy"): string {
  const base = `/api/match/shooters/${slug}/videos/stream?path=${encodeURIComponent(path)}`;
  const url = kind && kind !== "auto" ? `${base}&kind=${kind}` : base;
  return scopeRequestPath(url);
}
```

Match the existing body (it currently wraps with `scopeRequestPath` and builds the path the same way - preserve that; only add the `kind` param).

- [ ] **Step 3: Add `generate_proxy` to the Jobs maps**

In `Jobs.tsx`:

```typescript
// KIND_LABEL
generate_proxy: "Generating preview",
// KIND_ICON  (import Film from lucide-react if not already imported)
generate_proxy: Film,
```

- [ ] **Step 4: Verify typecheck + build**

Run (from `src/splitsmith/ui_static/`):
```bash
pnpm typecheck && pnpm build
```
Expected: no type errors; build succeeds.

- [ ] **Step 5: Scoped eslint + commit**

```bash
cd src/splitsmith/ui_static && pnpm exec eslint src/lib/api.ts src/components/Jobs.tsx
cd - && git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/components/Jobs.tsx
git commit -m "feat(ui): proxy_ready type, proxy stream url, generate_proxy job kind (#561)"
```

---

### Task 6: ClipDetail proxy playback + "generating" badge

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/ingest/ClipDetail.tsx` (`<video>` ~325-334, label row ~339)

**Interfaces:**
- Consumes: `shooterVideoStreamUrl(slug, path, "proxy")`, `StageVideo.proxy_ready` (Task 5); `StatusPill` (`components/ui/StatusPill.tsx`).
- Produces: the Ingest player streams the proxy URL (server falls back to source); shows a `StatusPill` "Proxy generating" when `!proxy_ready`; remounts on proxy-ready flip via a key nonce (nonce source wired in Task 7).

- [ ] **Step 1: Point the `<video>` at the proxy URL and add the remount key**

```tsx
<video
  key={`${video.path}:${video.proxy_ready ? "p" : "s"}`}
  controls
  preload="metadata"
  src={api.shooterVideoStreamUrl(slug, video.path, "proxy")}
  ...
/>
```

Using `proxy_ready` in the key means: when a refetch flips this clip's `proxy_ready` from false to true while the user is viewing it, the element remounts and re-requests (now getting the proxy). On clip switch, `video.path` already changes the key.

- [ ] **Step 2: Add the "generating" badge in the label row**

Next to the existing "Streaming source - scrub to identify the stage" label (~339), conditionally render:

```tsx
{video.proxy_ready === false && (
  <StatusPill tone="in-progress" label="Proxy generating" />
)}
```

Confirm `StatusPill`'s prop names/tones by reading `components/ui/StatusPill.tsx` (the in-progress amber tone exists ~29-30, 55-68); match its actual API (it may take `status`/`children` rather than `tone`/`label`).

- [ ] **Step 3: Verify typecheck + build**

Run (from `src/splitsmith/ui_static/`):
```bash
pnpm typecheck && pnpm build
```
Expected: success.

- [ ] **Step 4: Scoped eslint + commit**

```bash
cd src/splitsmith/ui_static && pnpm exec eslint src/pages/ingest/ClipDetail.tsx
cd - && git add src/splitsmith/ui_static/src/pages/ingest/ClipDetail.tsx
git commit -m "feat(ui): stream proxy in ClipDetail with generating badge (#561)"
```

---

### Task 7: Ingest refetch-while-pending watcher

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/ingest/ReviewLayout.tsx` (or `pages/Ingest.tsx` where `onProjectUpdate`/`getProject` lives)

**Interfaces:**
- Consumes: `api.getProject(slug)`, the clip model (`model.order` / videos with `proxy_ready`).
- Produces: while any clip has `proxy_ready === false`, the project is refetched every ~5s; stops when none pending. This keeps badges current and (combined with Task 6's key) swaps the source for a clip whose proxy finishes mid-view.

- [ ] **Step 1: Add the watcher effect**

In the component that owns the project state + `onProjectUpdate` (the one that already calls `api.getProject(slug)` - mirror how the trim flow in `Audit.tsx` refetches):

```tsx
useEffect(() => {
  const anyPending = allClipVideos.some((v) => v.proxy_ready === false);
  if (!anyPending) return;
  const id = window.setInterval(async () => {
    try {
      onProjectUpdate(await api.getProject(slug));
    } catch {
      /* transient; next tick retries */
    }
  }, 5000);
  return () => window.clearInterval(id);
}, [allClipVideos, slug, onProjectUpdate]);
```

Derive `allClipVideos` from the existing model (all stage videos + unassigned). Ensure the effect dependency does not cause a tight loop: gate on the boolean `anyPending` (compute it into a stable dep, e.g. `const anyPending = useMemo(...)` and depend on `[anyPending, slug]`), not on a freshly-allocated array each render.

- [ ] **Step 2: Verify typecheck + build**

Run (from `src/splitsmith/ui_static/`):
```bash
pnpm typecheck && pnpm build
```
Expected: success.

- [ ] **Step 3: Manual reasoning check (no test runner)**

Confirm: when `getProject` returns all `proxy_ready: true`, `anyPending` is false and the interval clears (no infinite polling). When a proxy is still generating, exactly one interval runs.

- [ ] **Step 4: Scoped eslint + commit**

```bash
cd src/splitsmith/ui_static && pnpm exec eslint src/pages/ingest/ReviewLayout.tsx
cd - && git add src/splitsmith/ui_static/src/pages/ingest/ReviewLayout.tsx
git commit -m "feat(ui): refetch ingest project while proxies are generating (#561)"
```

---

### Task 8: Full gate sweep + PR

**Files:** none (verification + PR)

- [ ] **Step 1: Backend gates**

```bash
uv run ruff check .
uv run black --check .
uv run pytest
```
Expected: all green. Fix anything red before proceeding.

- [ ] **Step 2: Docker smoke (storage/job paths)**

Ensure `docker` is on PATH (symlink Cellar docker into `~/.claude-tmp/bin` and prepend PATH if the non-interactive shell can't find it), then:
```bash
uv run pytest -m docker
```
Expected: green (or the same pre-existing skips as `main`; this feature adds no migration).

- [ ] **Step 3: Frontend gates**

```bash
cd src/splitsmith/ui_static && pnpm typecheck && pnpm build
```
Expected: success.

- [ ] **Step 4: Dash sweep on added lines**

```bash
git diff main --unified=0 | grep '^+' | grep -nE '-|--' || echo "clean"
```
Expected: `clean` (no em dash / `--` in new copy or comments).

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin feat/upload-preview-proxies
gh pr create --title "feat: preview/proxy videos for uploads (fast stage-assignment streaming)" --body "<summary + Closes #561 + test notes + generated-by footer>"
```

PR body includes: what changed (per-upload hosted proxy job, kind=proxy streaming with fallback, proxy_ready badge), how it was tested (unit + integration + docker), and `Closes #561`.

---

## Self-Review Notes

- Spec coverage: proxy module (T1), job+dispatch on every upload (T2), kind=proxy streaming+fallback (T3), proxy_ready via one LIST + cleanup (T4), FE types/url/jobs (T5), ClipDetail badge+swap (T6), refetch watcher (T7), gates+PR (T8). All spec sections mapped.
- Hosted-only degradation is asserted in T2 (no-storage skip) and T4 (local -> proxy_ready true).
- Grep-and-match instructions are used wherever exact existing signatures (`find_active`, `_cancellable_runner`, `Storage.list` return shape, project serializer, `StatusPill` API) must be confirmed against the codebase at execution time - these are integration seams, not placeholders for feature logic.
- No new dependencies. No migration.
