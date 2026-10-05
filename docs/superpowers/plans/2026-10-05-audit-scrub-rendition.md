# Audit Scrubs the 720p Rendition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The local Audit screen (desktop and phone layouts, every camera angle) plays the trim's existing 720p `_web.mp4` rendition instead of the ~150 Mbit/s 4K audit trim, with a full-resolution switch and an automatic fallback on a playback error.

**Architecture:** One freshness rule (`audio.fresh_web_trim`) decides whether a local rendition is current. The server exposes it twice: local `kind=web` serves it, and each video dict carries `scrub_version` naming it. The SPA picks the kind in one pure function (`lib/scrubSource.ts`) behind one hook (`lib/useScrubSource.ts`) that owns the preference and the failed set; Audit and MobileAudit call it wherever they pinned `kind=trim`.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest; React + TypeScript, vitest, Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-05-audit-scrub-rendition-design.md`

## Global Constraints

- No encode change: `WebTrimConfig` stays as it is (GOP 30). Do not touch `trim.transcode_web_trim` or the audit trim.
- `kind=trim` never substitutes the rendition, in either mode. Existing test `test_trim_kind_never_serves_web_rendition` must stay green.
- Never add a key to the trim's `.params.json`: `ensure_video_audit_trim` compares it with exact dict equality and every trim would re-cut.
- Fresh rendition = file non-empty and `st_mtime >= trim's st_mtime` (the rule `_ensure_web_trim` already uses). One helper, used everywhere.
- `scrub_version` format is the same as `trim_version`: `f"{st.st_mtime_ns:x}-{st.st_size:x}"`.
- Settings route is local only: 404 when `_hosted_mode_active()`.
- No new dependencies. Black line length 110, ruff, type hints everywhere, `pathlib.Path`.
- SPA: primitives only; no arbitrary `text-[...]`; menu copy is plain, no issue numbers in the UI.
- Python tests: `uv run pytest <file> -n0 -q` while iterating; full suite (`uv run pytest -q`) before the last commit. SPA: `cd src/splitsmith/ui_static && corepack pnpm vitest run <file>`; `corepack pnpm typecheck && corepack pnpm lint` before each SPA commit.
- Every new test gets a mutation drill: remove the change, watch the test fail, restore. Commit before drilling; restore by re-applying the edit, never `git checkout -- file` over uncommitted work.

## Review Focus

1. **A trim re-cut while Audit is open.** The re-cut deletes trim + rendition and writes the trim first; for a while the rendition is missing or older than the new trim. Expected: `scrub_version` is null in that window, `kind=web` serves the new trim, never the old window's rendition. Pinned in Task 1 (stale rendition test) and Task 2 (version goes null when stale).
2. **Rendition present, trim missing** (a swept trim, a disk cleanup). Expected: no `scrub_version`; `kind=web` falls through to the source like `auto`, never serves an orphan rendition with no trim to anchor it. Pinned in Task 1.
3. **Hosted payloads and hosted `kind=web`** must not change. Expected: hosted stream branch untouched (existing presign tests green); settings route 404s hosted. Pinned in Task 3; existing hosted tests are the guard.
4. **A rendition that errors in the browser** (truncated file, codec trouble). Expected: one fallback to `kind=trim` for that video only, not a loop and not for other angles. Pinned in Task 4 (hook) and Task 6 (MobileAudit page).
5. **Switching cameras or toggling the switch mid-play.** Expected: the URL changes, the player remounts (VideoPanel keys on `videoSrc`), offsets unchanged because `planServedClip`'s offset is not touched. Pinned in Task 5 (URL per kind) and by the browser check in Task 7.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/splitsmith/ui/audio.py` | modify | `fresh_web_trim(trimmed)`; `_ensure_web_trim` reuses it |
| `src/splitsmith/ui/server.py` | modify | local `kind=web` branch; `_scrub_version_for`; payload field; `/api/settings/scrub` |
| `src/splitsmith/user_config.py` | modify | `GlobalPrefs.full_res_scrub` |
| `tests/test_scrub_rendition.py` | create | all Python tests for this plan |
| `src/splitsmith/ui_static/src/lib/api.ts` | modify | `StageVideo.scrub_version`, `getScrubSettings`, `setScrubSettings` |
| `src/splitsmith/ui_static/src/lib/scrubSource.ts` (+ `.test.ts`) | create | pure kind choice |
| `src/splitsmith/ui_static/src/lib/useScrubSource.ts` (+ `.test.tsx`) | create | preference + failed set + URL helper |
| `src/splitsmith/ui_static/src/components/VideoPanel.tsx` | modify | `onPlaybackError` prop |
| `src/splitsmith/ui_static/src/components/audit/TransportLine.tsx` | modify | optional full-resolution toggle |
| `src/splitsmith/ui_static/src/pages/Audit.tsx` | modify | wire the hook into `videoSrc`, the toggle, the fallback |
| `src/splitsmith/ui_static/src/pages/MobileAudit.tsx` | modify | same wiring for the phone layout |
| `CLAUDE.md` | modify | #1031 section: local `kind=web`, `scrub_version`, the audit players' rule |

---

### Task 1: Freshness helper and local `kind=web`

**Files:**
- Modify: `src/splitsmith/ui/audio.py` (add `fresh_web_trim` above `_ensure_web_trim`, ~line 891; change the first check inside `_ensure_web_trim`)
- Modify: `src/splitsmith/ui/server.py` (`stream_video`'s local branch, the block starting `# local mode: existing disk-based serving`)
- Create: `tests/test_scrub_rendition.py`

**Interfaces:**
- Produces: `splitsmith.ui.audio.fresh_web_trim(trimmed: Path) -> Path | None` -- the rendition path when it is on local disk, non-empty and not older than `trimmed`; `None` otherwise (including when `trimmed` itself is missing). Never touches storage.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scrub_rendition.py`:

```python
"""The Audit screen scrubs the trim's 720p rendition (#1192, #1191).

Local ``kind=web`` serves a *fresh* rendition from disk (non-empty, not
older than the trim), else the trim, else the source. ``kind=trim`` never
substitutes. ``scrub_version`` on the project payload names the same file.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from splitsmith import trim as trim_module
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui import audio as audio_helpers
from splitsmith.ui.server import create_app


def _bootstrap(tmp_path: Path) -> tuple[TestClient, str, Path, Path]:
    """A one-stage local match; returns (client, match base URL, trim path, web path).

    Neither the trim nor the rendition exists yet; the source does.
    """
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Scrub Match")
    (shooter_root / "raw").mkdir(parents=True, exist_ok=True)
    (shooter_root / "raw" / "v.mp4").write_bytes(b"source bytes")
    project = MatchProject.load(shooter_root)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="S1",
            time_seconds=30.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=8.0)],
        )
    ]
    project.save(shooter_root)
    stamped = MatchProject.load(shooter_root)
    trim = audio_helpers.trimmed_video_path(shooter_root, 1, stamped.stages[0].videos[0], project=stamped)
    trim.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(project_root=root, project_name="Scrub Match")
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    return TestClient(app), f"/api/matches/{match_id}", trim, trim_module.web_trim_path(trim)


def _age(path: Path, seconds: int) -> None:
    """Move ``path``'s mtime ``seconds`` into the past."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - seconds * 1_000_000_000))


def _stream(client: TestClient, base: str, kind: str) -> bytes:
    resp = client.get(f"{base}/shooters/me/videos/stream", params={"path": "raw/v.mp4", "kind": kind})
    assert resp.status_code == 200, resp.text
    return resp.content


# --- fresh_web_trim -----------------------------------------------------------


def test_fresh_web_trim_accepts_a_rendition_cut_after_the_trim(tmp_path: Path) -> None:
    trim = tmp_path / "stage1_cam_x_trimmed.mp4"
    trim.write_bytes(b"trim")
    _age(trim, 10)
    web = trim_module.web_trim_path(trim)
    web.write_bytes(b"web")
    assert audio_helpers.fresh_web_trim(trim) == web


def test_fresh_web_trim_rejects_a_rendition_older_than_the_trim(tmp_path: Path) -> None:
    trim = tmp_path / "stage1_cam_x_trimmed.mp4"
    web = trim_module.web_trim_path(trim)
    web.write_bytes(b"web")
    _age(web, 10)
    trim.write_bytes(b"re-cut trim")
    assert audio_helpers.fresh_web_trim(trim) is None


def test_fresh_web_trim_rejects_an_empty_rendition(tmp_path: Path) -> None:
    trim = tmp_path / "stage1_cam_x_trimmed.mp4"
    trim.write_bytes(b"trim")
    _age(trim, 10)
    trim_module.web_trim_path(trim).write_bytes(b"")
    assert audio_helpers.fresh_web_trim(trim) is None


def test_fresh_web_trim_needs_the_trim(tmp_path: Path) -> None:
    trim = tmp_path / "stage1_cam_x_trimmed.mp4"
    trim_module.web_trim_path(trim).write_bytes(b"orphan web")
    assert audio_helpers.fresh_web_trim(trim) is None


# --- stream_video, local ------------------------------------------------------


def test_local_web_kind_serves_a_fresh_rendition(tmp_path: Path) -> None:
    client, base, trim, web = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    _age(trim, 10)
    web.write_bytes(b"web bytes")
    assert _stream(client, base, "web") == b"web bytes"


def test_local_web_kind_serves_the_trim_when_the_rendition_is_stale(tmp_path: Path) -> None:
    client, base, trim, web = _bootstrap(tmp_path)
    web.write_bytes(b"old window")
    _age(web, 10)
    trim.write_bytes(b"re-cut trim")
    assert _stream(client, base, "web") == b"re-cut trim"


def test_local_web_kind_serves_the_trim_without_a_rendition(tmp_path: Path) -> None:
    client, base, trim, _web = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    assert _stream(client, base, "web") == b"trim bytes"


def test_local_web_kind_ignores_an_orphan_rendition(tmp_path: Path) -> None:
    """No trim: the rendition has nothing to anchor it, so the source plays."""
    client, base, _trim, web = _bootstrap(tmp_path)
    web.write_bytes(b"orphan web")
    assert _stream(client, base, "web") == b"source bytes"


def test_local_trim_kind_never_serves_the_rendition(tmp_path: Path) -> None:
    client, base, trim, web = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    _age(trim, 10)
    web.write_bytes(b"web bytes")
    assert _stream(client, base, "trim") == b"trim bytes"
    assert _stream(client, base, "auto") == b"trim bytes"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_scrub_rendition.py -n0 -q`
Expected: the four `fresh_web_trim` tests fail with `AttributeError: module 'splitsmith.ui.audio' has no attribute 'fresh_web_trim'`; `test_local_web_kind_serves_a_fresh_rendition` fails (`b"trim bytes" != b"web bytes"`); the other three stream tests pass (they pin existing behaviour).

If `scaffold_match` does not create a `me` alias route or the URL shape differs, copy the URL shape from `tests/test_trim_version.py::test_versioned_trim_url_streams_the_trim`, which uses the same bootstrap.

- [ ] **Step 3: Implement `fresh_web_trim` and reuse it in `_ensure_web_trim`**

In `src/splitsmith/ui/audio.py`, directly above `def _ensure_web_trim(`:

```python
def fresh_web_trim(trimmed: Path) -> Path | None:
    """The web rendition of the audit trim at ``trimmed`` when it is on
    local disk and current, else ``None`` (#1192).

    Current means non-empty and not older than the trim: a re-cut writes
    the trim first and the rendition after it, so an older rendition
    covers the previous window. No trim means no rendition either -- it
    would have nothing to anchor it. Local files only; the one freshness
    rule ``_ensure_web_trim``, ``stream_video?kind=web`` and the payload's
    ``scrub_version`` share.
    """
    from .. import trim as trim_module

    web = trim_module.web_trim_path(trimmed)
    try:
        web_st = web.stat()
        trim_st = trimmed.stat()
    except OSError:
        return None
    if web_st.st_size == 0 or web_st.st_mtime < trim_st.st_mtime:
        return None
    return web
```

In `_ensure_web_trim`, replace

```python
    if web.exists() and web.stat().st_size > 0 and web.stat().st_mtime >= trimmed.stat().st_mtime:
        return web
```

with

```python
    if fresh_web_trim(trimmed) is not None:
        return web
```

- [ ] **Step 4: Implement local `kind=web` in `stream_video`**

In `src/splitsmith/ui/server.py`, `stream_video`'s local branch, replace the comment and the `if trimmed.exists():` block:

```python
        # local mode: disk-based serving. ``web`` serves the trim's fresh
        # 720p rendition when there is one (#1192: the Audit screen scrubs
        # it), else falls back like ``auto``; ``trim`` never substitutes.
        served_path: Path | None = None
        if kind in ("auto", "trim", "web") and stage is not None:
            # Per-video short-GOP trim is keyed per role: each angle has
            # its own scrub clip cut around its own beep.
            trimmed = audio_helpers.pull_trimmed_video(root, stage.stage_number, video, project=project)
            if trimmed.exists():
                served_path = trimmed.resolve()
                if kind == "web":
                    web = audio_helpers.fresh_web_trim(trimmed)
                    if web is not None:
                        served_path = web.resolve()
            elif _is_mirror():
```

Leave the `elif _is_mirror():` body and everything after it unchanged. Also update the route docstring's `web` bullet to: "``web``: the trim's 720p faststart rendition (#1031). Hosted streams it from object storage; local serves it from disk when it is fresh (#1192). Falls back to the trim, then the source."

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_scrub_rendition.py tests/test_media_presign_serving.py tests/test_trim_version.py -n0 -q`
Expected: all pass. Then `uv run pytest -q -k "web_trim or ensure_web or backfill"` -- all pass (the `_ensure_web_trim` refactor is behaviour-preserving).

- [ ] **Step 6: Commit, then mutation drill**

```bash
git add src/splitsmith/ui/audio.py src/splitsmith/ui/server.py tests/test_scrub_rendition.py
git commit -m "feat(audit): local kind=web serves the trim's fresh rendition (#1192)"
```

Drill, one at a time, re-running the file each time and restoring by re-applying the edit:
1. Delete the `if kind == "web":` block in `stream_video` -> `test_local_web_kind_serves_a_fresh_rendition` fails.
2. In `fresh_web_trim`, drop `or web_st.st_mtime < trim_st.st_mtime` -> the stale tests fail.
3. Drop `web_st.st_size == 0 or` -> the empty test fails.

---

### Task 2: `scrub_version` on the project payload

**Files:**
- Modify: `src/splitsmith/ui/server.py` (add `_scrub_version_for` after `_trim_version_for` ~line 7861; set it in the payload loop next to `video_dict["trim_version"]` ~line 9497)
- Test: `tests/test_scrub_rendition.py`

**Interfaces:**
- Consumes: `audio_helpers.fresh_web_trim` (Task 1), `audio_helpers.resolve_trim_for_read`.
- Produces: each stage video dict in `GET /api/matches/{id}/shooters/{slug}/project` carries `scrub_version: str | None`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_scrub_rendition.py`)

```python
# --- scrub_version on the payload ---------------------------------------------


def _videos(client: TestClient, base: str) -> dict:
    resp = client.get(f"{base}/shooters/me/project")
    assert resp.status_code == 200, resp.text
    return resp.json()["stages"][0]["videos"][0]


def test_scrub_version_is_none_without_a_rendition(tmp_path: Path) -> None:
    client, base, trim, _web = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    assert _videos(client, base)["scrub_version"] is None


def test_scrub_version_names_a_fresh_rendition(tmp_path: Path) -> None:
    client, base, trim, web = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    _age(trim, 10)
    web.write_bytes(b"web bytes")
    st = web.stat()
    video = _videos(client, base)
    assert video["scrub_version"] == f"{st.st_mtime_ns:x}-{st.st_size:x}"
    assert video["scrub_version"] != video["trim_version"]


def test_scrub_version_is_none_while_the_rendition_is_stale(tmp_path: Path) -> None:
    """Mid re-cut: the new trim is written, its rendition is not yet."""
    client, base, trim, web = _bootstrap(tmp_path)
    web.write_bytes(b"old window")
    _age(web, 10)
    trim.write_bytes(b"re-cut trim")
    assert _videos(client, base)["scrub_version"] is None


def test_scrub_version_is_none_for_an_orphan_rendition(tmp_path: Path) -> None:
    client, base, _trim, web = _bootstrap(tmp_path)
    web.write_bytes(b"orphan web")
    assert _videos(client, base)["scrub_version"] is None


def test_scrub_version_moves_when_the_rendition_is_recut(tmp_path: Path) -> None:
    client, base, trim, web = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    _age(trim, 20)
    web.write_bytes(b"first web")
    _age(web, 10)
    first = _videos(client, base)["scrub_version"]
    web.write_bytes(b"other web")
    second = _videos(client, base)["scrub_version"]
    assert first is not None and second is not None and first != second
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_scrub_rendition.py -n0 -q -k scrub_version`
Expected: FAIL with `KeyError: 'scrub_version'`.

- [ ] **Step 3: Implement**

After `_trim_version_for` in `src/splitsmith/ui/server.py`:

```python
def _scrub_version_for(root: Path, stage_number: int, video: StageVideo, project: MatchProject) -> str | None:
    """Identity of the trim's fresh 720p rendition on local disk, or None (#1192).

    The Audit players ask for ``kind=web`` with this in the URL when it is
    set, and keep ``kind=trim`` otherwise. Same trim resolver as
    :func:`_trim_version_for` and the same freshness rule as
    ``stream_video``'s local ``kind=web`` branch
    (:func:`audio.fresh_web_trim`), so the version names the bytes behind
    the URL. Local files only: no storage call.
    """
    try:
        trim = audio_helpers.resolve_trim_for_read(root, stage_number, video, project=project)
        if trim is None:
            return None
        web = audio_helpers.fresh_web_trim(trim)
        if web is None:
            return None
        st = web.stat()
    except OSError:
        return None
    return f"{st.st_mtime_ns:x}-{st.st_size:x}"
```

In the payload loop, after `video_dict["trim_version"] = _trim_version_for(root, int(n), video, project)`:

```python
                video_dict["scrub_version"] = _scrub_version_for(root, int(n), video, project)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scrub_rendition.py tests/test_trim_version.py -n0 -q`
Expected: all pass.

- [ ] **Step 5: Commit, then mutation drill**

```bash
git add src/splitsmith/ui/server.py tests/test_scrub_rendition.py
git commit -m "feat(audit): scrub_version names the trim's fresh rendition (#1192)"
```

Drill: replace the body of `_scrub_version_for` after the `try` with `return _trim_version_for(root, stage_number, video, project)` -> `test_scrub_version_is_none_without_a_rendition` and `..._names_a_fresh_rendition` fail. Restore.

---

### Task 3: `GlobalPrefs.full_res_scrub` and `/api/settings/scrub`

**Files:**
- Modify: `src/splitsmith/user_config.py` (`GlobalPrefs`, after `auto_sync_enabled`)
- Modify: `src/splitsmith/ui/server.py` (request model next to `GlobalAutoSyncRequest` ~line 5867; routes next to `put_global_auto_sync` ~line 8765)
- Test: `tests/test_scrub_rendition.py`

**Interfaces:**
- Produces: `GET /api/settings/scrub` -> `{"full_res_scrub": bool}`; `PUT /api/settings/scrub` body `{"full_res_scrub": bool}` (extra keys -> 422) -> same shape. Both 404 hosted.

- [ ] **Step 1: Write the failing tests** (append)

```python
# --- /api/settings/scrub ------------------------------------------------------


def test_scrub_setting_defaults_off_and_round_trips(tmp_path: Path) -> None:
    client, _base, _trim, _web = _bootstrap(tmp_path)
    assert client.get("/api/settings/scrub").json() == {"full_res_scrub": False}
    resp = client.put("/api/settings/scrub", json={"full_res_scrub": True})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"full_res_scrub": True}
    assert client.get("/api/settings/scrub").json() == {"full_res_scrub": True}

    from splitsmith import user_config

    assert user_config.load_global_prefs().full_res_scrub is True


def test_scrub_setting_rejects_unknown_fields(tmp_path: Path) -> None:
    client, _base, _trim, _web = _bootstrap(tmp_path)
    assert client.put("/api/settings/scrub", json={"full_res_scrub": True, "x": 1}).status_code == 422


def test_scrub_setting_is_local_only(tmp_path: Path, monkeypatch) -> None:
    from splitsmith.ui import server

    client, _base, _trim, _web = _bootstrap(tmp_path)
    monkeypatch.setattr(server, "_hosted_mode_active", lambda: True)
    assert client.get("/api/settings/scrub").status_code == 404
    assert client.put("/api/settings/scrub", json={"full_res_scrub": True}).status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_scrub_rendition.py -n0 -q -k scrub_setting`
Expected: FAIL (404 from the GET in the first test, since the route does not exist; the 422 test gets 404 too).

- [ ] **Step 3: Implement**

`src/splitsmith/user_config.py`, in `GlobalPrefs` after `auto_sync_enabled: bool = True`:

```python
    # The Audit screen scrubs the trim's 720p rendition (#1192); this
    # machine-level switch brings back the full-resolution trim.
    full_res_scrub: bool = False
```

`src/splitsmith/ui/server.py`, after `class GlobalAutoSyncRequest`:

```python
class ScrubSettingsRequest(BaseModel):
    """Body for PUT /api/settings/scrub: the Audit full-resolution switch."""

    model_config = ConfigDict(extra="forbid")

    full_res_scrub: bool
```

After `put_global_auto_sync`:

```python
    @app.get("/api/settings/scrub")
    async def get_scrub_settings() -> JSONResponse:
        """Whether the Audit screen scrubs the full-resolution trim (#1192)."""
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        return JSONResponse({"full_res_scrub": user_config.load_global_prefs().full_res_scrub})

    @app.put("/api/settings/scrub")
    async def put_scrub_settings(req: ScrubSettingsRequest) -> JSONResponse:
        """The Audit transport menu's "Full-resolution video" switch."""
        if _hosted_mode_active():
            raise HTTPException(status_code=404, detail="not found")
        prefs = user_config.load_global_prefs()
        prefs.full_res_scrub = req.full_res_scrub
        user_config.save_global_prefs(prefs)
        return JSONResponse({"full_res_scrub": prefs.full_res_scrub})
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scrub_rendition.py tests/test_auto_sync_service.py -n0 -q` and `uv run python scripts/ci/assert_slim_import_surface.py` (if it needs arguments, read its docstring; the point is that no new top-level import reached `create_app`).
Expected: all pass.

- [ ] **Step 5: Commit, then mutation drill**

```bash
git add src/splitsmith/user_config.py src/splitsmith/ui/server.py tests/test_scrub_rendition.py
git commit -m "feat(audit): a machine-level full-resolution scrub switch (#1192)"
```

Drill: remove the `if _hosted_mode_active()` guard from the GET -> `test_scrub_setting_is_local_only` fails. Restore.

---

### Task 4: SPA -- API surface, `scrubSource`, `useScrubSource`

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`StageVideo` interface after `trim_version`; methods after `setGlobalAutoSync`)
- Create: `src/splitsmith/ui_static/src/lib/scrubSource.ts`, `src/splitsmith/ui_static/src/lib/scrubSource.test.ts`
- Create: `src/splitsmith/ui_static/src/lib/useScrubSource.ts`, `src/splitsmith/ui_static/src/lib/useScrubSource.test.tsx`

**Interfaces:**
- Consumes: server fields from Tasks 2-3.
- Produces:
  - `StageVideo.scrub_version?: string | null`
  - `api.getScrubSettings(): Promise<{ full_res_scrub: boolean }>`, `api.setScrubSettings(fullRes: boolean): Promise<{ full_res_scrub: boolean }>`
  - `scrubSource(args: { trimVersion: string | null | undefined; scrubVersion: string | null | undefined; fullRes: boolean; failed: boolean }): ScrubChoice` where `ScrubChoice = { kind: "web" | "trim"; version: string | null }`
  - `useScrubSource(): { fullRes: boolean; available: boolean; setFullRes: (v: boolean) => void; choose: (video: Pick<StageVideo, "path" | "trim_version" | "scrub_version">) => ScrubChoice; markFailed: (path: string) => void }`. `available` is true in local mode once the setting loaded (the menu item renders only then).

- [ ] **Step 1: API additions**

In `lib/api.ts`, in `interface StageVideo` after `trim_version?: string | null;`:

```ts
  /** Identity of the trim's fresh 720p rendition on local disk, null when
   *  there is none. The Audit players stream ``kind=web`` with it instead
   *  of the full-resolution trim (scrubbing a 4K trim stalls). */
  scrub_version?: string | null;
```

After `setGlobalAutoSync`:

```ts
  getScrubSettings: () => request<{ full_res_scrub: boolean }>("/api/settings/scrub"),
  setScrubSettings: (fullRes: boolean) =>
    request<{ full_res_scrub: boolean }>("/api/settings/scrub", {
      method: "PUT",
      json: { full_res_scrub: fullRes },
    }),
```

- [ ] **Step 2: Write the failing `scrubSource` test**

`lib/scrubSource.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { scrubSource } from "./scrubSource";

describe("scrubSource", () => {
  const base = { trimVersion: "t1", scrubVersion: "w1", fullRes: false, failed: false };

  it.each([
    ["a fresh rendition plays", base, { kind: "web", version: "w1" }],
    ["no rendition keeps the trim", { ...base, scrubVersion: null }, { kind: "trim", version: "t1" }],
    ["an undefined rendition keeps the trim", { ...base, scrubVersion: undefined }, { kind: "trim", version: "t1" }],
    ["the full-resolution switch keeps the trim", { ...base, fullRes: true }, { kind: "trim", version: "t1" }],
    ["a failed rendition falls back to the trim", { ...base, failed: true }, { kind: "trim", version: "t1" }],
    ["a trim without a version still plays", { ...base, scrubVersion: null, trimVersion: undefined }, { kind: "trim", version: null }],
  ] as const)("%s", (_name, args, expected) => {
    expect(scrubSource(args)).toEqual(expected);
  });
});
```

Run: `cd src/splitsmith/ui_static && corepack pnpm vitest run src/lib/scrubSource.test.ts`
Expected: FAIL, cannot resolve `./scrubSource`.

- [ ] **Step 3: Implement `scrubSource`**

`lib/scrubSource.ts`:

```ts
/**
 * Which file the Audit players stream where they would pin the trim.
 *
 * The audit trim is a full-resolution scrub cache: from a 4K headcam it
 * runs ~150 Mbit/s, which stalls software decode, a NAS, and Chromium's
 * low-end device mode (playback ends after ~2 s). Its 720p rendition is
 * cut from the trim and shares its timeline frame for frame, so offsets
 * do not change. The rendition is used only when the server named a fresh
 * one (``scrub_version``), the user has not asked for full resolution,
 * and it has not already failed to play on this page.
 */
export interface ScrubChoice {
  kind: "web" | "trim";
  version: string | null;
}

export function scrubSource(args: {
  trimVersion: string | null | undefined;
  scrubVersion: string | null | undefined;
  fullRes: boolean;
  failed: boolean;
}): ScrubChoice {
  const { trimVersion, scrubVersion, fullRes, failed } = args;
  if (scrubVersion && !fullRes && !failed) return { kind: "web", version: scrubVersion };
  return { kind: "trim", version: trimVersion ?? null };
}
```

Run the test again. Expected: PASS.

- [ ] **Step 4: Write the failing hook test**

`lib/useScrubSource.test.tsx`:

```tsx
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { useScrubSource } from "@/lib/useScrubSource";

const mode = vi.hoisted(() => ({ value: { mode: "local", resolved: true } }));
vi.mock("@/lib/features", async (orig) => ({
  ...(await orig<typeof import("@/lib/features")>()),
  useDeploymentMode: () => mode.value,
}));
vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, getScrubSettings: vi.fn(), setScrubSettings: vi.fn() } };
});

const cam = (path: string) => ({ path, trim_version: `t-${path}`, scrub_version: `w-${path}` });

beforeEach(() => {
  mode.value = { mode: "local", resolved: true };
  vi.mocked(api.getScrubSettings).mockReset().mockResolvedValue({ full_res_scrub: false });
  vi.mocked(api.setScrubSettings).mockReset().mockResolvedValue({ full_res_scrub: true });
});

describe("useScrubSource", () => {
  it("chooses the rendition by default and is available locally", async () => {
    const { result } = renderHook(() => useScrubSource());
    await waitFor(() => expect(result.current.available).toBe(true));
    expect(result.current.choose(cam("a"))).toEqual({ kind: "web", version: "w-a" });
  });

  it("falls back to the trim for the failed video only", async () => {
    const { result } = renderHook(() => useScrubSource());
    act(() => result.current.markFailed("a"));
    expect(result.current.choose(cam("a"))).toEqual({ kind: "trim", version: "t-a" });
    expect(result.current.choose(cam("b"))).toEqual({ kind: "web", version: "w-b" });
  });

  it("the switch persists and keeps every video on the trim", async () => {
    const { result } = renderHook(() => useScrubSource());
    await waitFor(() => expect(result.current.available).toBe(true));
    act(() => result.current.setFullRes(true));
    expect(api.setScrubSettings).toHaveBeenCalledWith(true);
    expect(result.current.choose(cam("a"))).toEqual({ kind: "trim", version: "t-a" });
  });

  it("loads a saved switch", async () => {
    vi.mocked(api.getScrubSettings).mockResolvedValue({ full_res_scrub: true });
    const { result } = renderHook(() => useScrubSource());
    await waitFor(() => expect(result.current.fullRes).toBe(true));
  });

  it("hosted never asks the server and offers no switch", async () => {
    mode.value = { mode: "hosted", resolved: true };
    const { result } = renderHook(() => useScrubSource());
    await Promise.resolve();
    expect(api.getScrubSettings).not.toHaveBeenCalled();
    expect(result.current.available).toBe(false);
  });

  it("a failed settings read leaves the rendition on and the switch hidden", async () => {
    vi.mocked(api.getScrubSettings).mockRejectedValue(new Error("down"));
    const { result } = renderHook(() => useScrubSource());
    await waitFor(() => expect(api.getScrubSettings).toHaveBeenCalled());
    expect(result.current.available).toBe(false);
    expect(result.current.choose(cam("a")).kind).toBe("web");
  });
});
```

Run: `corepack pnpm vitest run src/lib/useScrubSource.test.tsx`
Expected: FAIL, cannot resolve `@/lib/useScrubSource`.

- [ ] **Step 5: Implement the hook**

`lib/useScrubSource.ts`:

```ts
/**
 * The Audit players' scrub source: the full-resolution preference (a
 * machine-level setting, local mode only) plus the videos whose rendition
 * failed to play on this page. ``choose`` is what a player calls where it
 * used to pin ``kind=trim``; ``markFailed`` is its ``error`` handler.
 */
import { useCallback, useEffect, useState } from "react";

import { api, type StageVideo } from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import { scrubSource, type ScrubChoice } from "@/lib/scrubSource";

export function useScrubSource() {
  const { mode, resolved } = useDeploymentMode();
  const [fullRes, setFullResState] = useState(false);
  const [available, setAvailable] = useState(false);
  const [failed, setFailed] = useState<ReadonlySet<string>>(() => new Set());

  useEffect(() => {
    if (!resolved || mode !== "local") return;
    let alive = true;
    api
      .getScrubSettings()
      .then((s) => {
        if (!alive) return;
        setFullResState(s.full_res_scrub);
        setAvailable(true);
      })
      .catch(() => {
        // The switch stays hidden; the rendition still plays.
      });
    return () => {
      alive = false;
    };
  }, [mode, resolved]);

  const setFullRes = useCallback((value: boolean) => {
    setFullResState(value);
    void api.setScrubSettings(value).catch(() => {
      // Kept for this page; the next load reads the saved value.
    });
  }, []);

  const markFailed = useCallback((path: string) => {
    setFailed((prev) => (prev.has(path) ? prev : new Set(prev).add(path)));
  }, []);

  const choose = useCallback(
    (video: Pick<StageVideo, "path" | "trim_version" | "scrub_version">): ScrubChoice =>
      scrubSource({
        trimVersion: video.trim_version,
        scrubVersion: video.scrub_version,
        fullRes,
        failed: failed.has(video.path),
      }),
    [fullRes, failed],
  );

  return { fullRes, available, setFullRes, choose, markFailed };
}
```

- [ ] **Step 6: Run, typecheck, lint**

Run: `corepack pnpm vitest run src/lib/scrubSource.test.ts src/lib/useScrubSource.test.tsx && corepack pnpm typecheck && corepack pnpm lint`
Expected: PASS.

- [ ] **Step 7: Commit, then mutation drill**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/lib/scrubSource.ts src/splitsmith/ui_static/src/lib/scrubSource.test.ts src/splitsmith/ui_static/src/lib/useScrubSource.ts src/splitsmith/ui_static/src/lib/useScrubSource.test.tsx
git commit -m "feat(audit): choose the scrub source in one place (#1192)"
```

Drill: in `scrubSource` drop `&& !failed` -> the failed row and "falls back to the trim for the failed video only" fail. Restore.

---

### Task 5: Desktop Audit -- URL, fallback, switch

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/VideoPanel.tsx` (props interface ~line 54; the primary `<video>`'s `onError` ~line 492)
- Modify: `src/splitsmith/ui_static/src/components/audit/TransportLine.tsx` (props; overflow menu)
- Modify: `src/splitsmith/ui_static/src/pages/Audit.tsx` (`videoSrc` ~line 1613; `<TransportLine>` ~line 2040; `<VideoPanel>` ~line 2147)
- Test: `src/splitsmith/ui_static/src/components/VideoPanel.error.test.tsx`, `src/splitsmith/ui_static/src/components/audit/AuditChrome.test.tsx`

**Interfaces:**
- Consumes: `useScrubSource` (Task 4).
- Produces: `VideoPanel` prop `onPlaybackError?: () => void` (called from the primary `<video>`'s `error` event); `TransportLine` props `fullResVideo?: boolean` and `onToggleFullResVideo?: () => void` (the item renders only when the toggle callback is given).

- [ ] **Step 1: Write the failing component tests**

Append to `components/VideoPanel.error.test.tsx` inside the `describe`:

```tsx
  it("reports a playback error to the page", () => {
    const onPlaybackError = vi.fn();
    const { container } = render(
      <VideoPanel {...props} videoSrc="/stream?kind=web&v=w" onPlaybackError={onPlaybackError} />,
    );
    fireEvent.error(primaryVideo(container));
    expect(onPlaybackError).toHaveBeenCalledTimes(1);
  });
```

Append to the `describe("TransportLine", ...)` block in `components/audit/AuditChrome.test.tsx`:

```tsx
  it("offers the full-resolution switch only when the page passes one", () => {
    renderLine();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.queryByRole("menuitemcheckbox", { name: /full-resolution video/i })).toBeNull();
  });

  it("toggles full-resolution video from the overflow menu", () => {
    const onToggleFullResVideo = vi.fn();
    renderLine({ fullResVideo: false, onToggleFullResVideo });
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    const item = screen.getByRole("menuitemcheckbox", { name: /full-resolution video/i });
    expect(item).toHaveAttribute("aria-checked", "false");
    fireEvent.click(item);
    expect(onToggleFullResVideo).toHaveBeenCalledTimes(1);
  });
```

Run: `corepack pnpm vitest run src/components/VideoPanel.error.test.tsx src/components/audit/AuditChrome.test.tsx`
Expected: the three new tests fail (the type error on the unknown props is fine at this point; vitest still runs). The "only when the page passes one" test may pass already; it pins the default.

- [ ] **Step 2: Implement `VideoPanel.onPlaybackError`**

In the props interface after `videoSrc: string;`:

```tsx
  /** Called when the primary player errors. The Audit page uses it to
   *  fall back from the 720p rendition to the full-resolution trim. */
  onPlaybackError?: () => void;
```

Destructure `onPlaybackError` with the other props and, in the primary `<video>`'s `onError`, add as the last statement:

```tsx
                  onPlaybackError?.();
```

- [ ] **Step 3: Implement the `TransportLine` item**

Props, after `onToggleKAuto: () => void;`:

```tsx
  /** Full-resolution scrubbing (local mode). The item renders only when
   *  ``onToggleFullResVideo`` is given. */
  fullResVideo?: boolean;
  onToggleFullResVideo?: () => void;
```

Destructure both in the second `const { ... } = props;` line. In the overflow `<Menu>`, after the K auto-step button and before `{menuExtra}`:

```tsx
          {onToggleFullResVideo ? (
            <button
              type="button"
              role="menuitemcheckbox"
              aria-checked={Boolean(fullResVideo)}
              className={ITEM}
              onClick={onToggleFullResVideo}
            >
              Full-resolution video
              <span className="ml-auto text-sm text-muted">{fullResVideo ? "on" : "off"}</span>
            </button>
          ) : null}
```

Run the two test files. Expected: PASS.

- [ ] **Step 4: Wire Audit**

In `pages/Audit.tsx`:

Import: `import { useScrubSource } from "@/lib/useScrubSource";`

Near the other page hooks (above `servedPlan`):

```tsx
  // The trim pin plays the 720p rendition when the server named a fresh
  // one (see lib/scrubSource.ts); a playback error falls back per video.
  const scrub = useScrubSource();
```

Replace the `videoSrc` expression's trimmed branch. Current:

```tsx
        ? api.videoStreamUrl(
            slug,
            activeVideo.path,
            servedPlan.kind,
            servedPlan.kind === "trim" ? activeVideo.trim_version : null,
          )
```

New:

```tsx
        ? servedPlan.kind === "trim"
          ? (() => {
              const choice = scrub.choose(activeVideo);
              return api.videoStreamUrl(slug, activeVideo.path, choice.kind, choice.version);
            })()
          : api.videoStreamUrl(slug, activeVideo.path, servedPlan.kind, null)
```

Extend the comment above `videoSrc` with one line: "A pinned trim may be served as its 720p rendition (``kind=web``, ``scrub_version``); the offset is the same, the rendition is cut from the trim."

On `<VideoPanel ...>` add:

```tsx
                      onPlaybackError={() => {
                        if (activeVideo && videoSrc.includes("kind=web")) scrub.markFailed(activeVideo.path);
                      }}
```

On `<TransportLine ...>` add:

```tsx
                    fullResVideo={scrub.fullRes}
                    onToggleFullResVideo={scrub.available ? () => scrub.setFullRes(!scrub.fullRes) : undefined}
```

- [ ] **Step 5: Typecheck, lint, run the Audit-adjacent suites**

Run: `corepack pnpm typecheck && corepack pnpm lint && corepack pnpm vitest run src/pages/Audit src/components/audit src/components/VideoPanel src/lib/camPlayback`
Expected: PASS. (`videoSrc` is a string; if it can be `""` the `includes` guard is still correct.)

- [ ] **Step 6: Commit, then mutation drill**

```bash
git add src/splitsmith/ui_static/src/components/VideoPanel.tsx src/splitsmith/ui_static/src/components/VideoPanel.error.test.tsx src/splitsmith/ui_static/src/components/audit/TransportLine.tsx src/splitsmith/ui_static/src/components/audit/AuditChrome.test.tsx src/splitsmith/ui_static/src/pages/Audit.tsx
git commit -m "feat(audit): the desktop Audit scrubs the 720p rendition (#1192)"
```

Drill: remove `onPlaybackError?.();` -> the VideoPanel test fails. Restore. The page wiring itself is verified in the browser (Task 7).

---

### Task 6: MobileAudit -- same choice and fallback

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/MobileAudit.tsx` (`videoUrl` memo ~line 329; the `<video>` ~line 664)
- Test: `src/splitsmith/ui_static/src/pages/MobileAudit.test.tsx`

**Interfaces:**
- Consumes: `useScrubSource` (Task 4).

- [ ] **Step 1: Write the failing tests**

In `pages/MobileAudit.test.tsx`, the `api` mock needs `getScrubSettings`. Add to `apiMock`:

```ts
  getScrubSettings: vi.fn(async () => ({ full_res_scrub: false })),
  setScrubSettings: vi.fn(),
```

Then add a `describe` (reuse the file's `renderPage`, `ctx`, `projectWithVideo`, `peaksResult`; read how existing tests open the video panel -- the "Video" button gated on `hasVideo` -- and copy that interaction):

```tsx
describe("MobileAudit scrub source", () => {
  const withRendition = () => {
    const p = projectWithVideo();
    Object.assign(p.stages[0].videos[0], { scrub_version: "w-1", processed: { beep: true, shot_detect: false, trim: true } });
    return p;
  };

  it("streams the rendition when the server names one", async () => {
    ctx.value = { ...ctx.value, project: withRendition() };
    renderPage();
    await waitFor(() => expect(apiMock.videoStreamUrl).toHaveBeenCalledWith("alice", "raw/stage3.mp4", "web", "w-1"));
  });

  it("falls back to the trim when the rendition errors", async () => {
    ctx.value = { ...ctx.value, project: withRendition() };
    apiMock.videoStreamUrl.mockImplementation(((_s: string, _p: string, kind: string) => `/video.mp4?kind=${kind}`) as never);
    renderPage();
    // open the video panel exactly as the existing tests do, then:
    const video = await waitFor(() => {
      const el = document.querySelector("video");
      if (!el) throw new Error("no video yet");
      return el;
    });
    expect(video.getAttribute("src")).toBe("/video.mp4?kind=web");
    fireEvent.error(video);
    await waitFor(() => expect(document.querySelector("video")?.getAttribute("src")).toBe("/video.mp4?kind=trim"));
  });
});
```

If the page needs the deployment mode, mock `@/lib/features`'s `useDeploymentMode` to `{ mode: "local", resolved: true }` in this file the same way `useScrubSource.test.tsx` does. Note the default `ctx.value.origin` is `"hosted"`; the scrub choice does not read origin, so leave it.

Run: `corepack pnpm vitest run src/pages/MobileAudit.test.tsx`
Expected: the two new tests FAIL (`videoStreamUrl` called with `"trim"`).

- [ ] **Step 2: Implement**

In `pages/MobileAudit.tsx`: import `useScrubSource`; add `const scrub = useScrubSource();` with the page's other hooks. In the `videoUrl` memo replace

```tsx
    return peaksResult.trimmed
      ? api.videoStreamUrl(slug, primaryVideo.path, "trim", primaryVideo.trim_version)
      : api.videoStreamUrl(slug, primaryVideo.path, "auto");
  }, [primaryVideo, peaksResult, slug]);
```

with

```tsx
    if (!peaksResult.trimmed) return api.videoStreamUrl(slug, primaryVideo.path, "auto");
    // The trim pin may play the 720p rendition (lib/scrubSource.ts).
    const choice = scrub.choose(primaryVideo);
    return api.videoStreamUrl(slug, primaryVideo.path, choice.kind, choice.version);
  }, [primaryVideo, peaksResult, slug, scrub]);
```

On the `<video>` add:

```tsx
              onError={() => {
                if (primaryVideo && videoUrl?.includes("kind=web")) scrub.markFailed(primaryVideo.path);
              }}
```

The memo depends on `scrub` (a new object each render); if that re-renders the `<video>` needlessly, depend on `scrub.choose` instead (it is a stable `useCallback` until the preference or failed set changes).

- [ ] **Step 3: Run, typecheck, lint**

Run: `corepack pnpm vitest run src/pages/MobileAudit.test.tsx src/pages/MobileAudit.desktopCommands.test.tsx && corepack pnpm typecheck && corepack pnpm lint`
Expected: PASS.

- [ ] **Step 4: Commit, then mutation drill**

```bash
git add src/splitsmith/ui_static/src/pages/MobileAudit.tsx src/splitsmith/ui_static/src/pages/MobileAudit.test.tsx
git commit -m "feat(audit): the phone Audit scrubs the 720p rendition (#1192)"
```

Drill: remove the `onError` handler -> "falls back to the trim" fails. Restore.

---

### Task 7: Docs, full suites, browser verification

**Files:**
- Modify: `CLAUDE.md` (section "Hosted playback streams the web rendition (#1031)")

- [ ] **Step 1: CLAUDE.md**

In the #1031 section, replace the sentence

"On the stream routes ``kind=web`` falls back web -> trim -> source and never 404s; ``kind=trim`` never substitutes the rendition (audit scrubbing needs the real GOP)."

with

"On the stream routes ``kind=web`` falls back web -> trim -> source and never 404s, locally too, where it serves the rendition from disk only while ``audio.fresh_web_trim`` says it is current (non-empty, not older than the trim). ``kind=trim`` never substitutes the rendition. The Audit players (``pages/Audit.tsx`` for every angle, ``pages/MobileAudit.tsx``) do not pin ``kind=trim`` blindly: ``lib/useScrubSource`` asks for ``kind=web`` when the video dict carries ``scrub_version`` (same freshness rule, local files only, null hosted), unless ``GlobalPrefs.full_res_scrub`` is on (the transport menu's "Full-resolution video") or the rendition already errored on that page (#1192: a 4K trim at ~150 Mbit/s stalls software decode and ends after ~2 s in Chromium's low-end mode, #1191). The GOP stays 30: GOP 15 measured +35-47 % bytes for ~10 ms of seek."

Also fix the earlier sentence "local mode keeps serving the trim from disk and the anchor stays ``trim``" to "the clip anchor (Results, Coach) stays ``trim`` locally".

- [ ] **Step 2: Full suites**

Run: `uv run pytest -q` (whole suite) and `cd src/splitsmith/ui_static && corepack pnpm vitest run && corepack pnpm typecheck && corepack pnpm lint`.
Expected: all green. Check `git status` for an unintended `uv.lock` change and leave it out of the commit.

- [ ] **Step 3: Browser check on the demo match**

```bash
H=~/.claude-tmp/scrub-verify; rm -rf $H; mkdir -p $H/home
SPLITSMITH_HOME=$H/home uv run python scripts/seed_demo_match.py $H/match --media
SPLITSMITH_HOME=$H/home SPLITSMITH_AUTO_SYNC=0 uv run splitsmith ui --project $H/match --skip-system-check --no-browser --port 5174
```

(run the server in the background; wait for `curl -s localhost:5174/api/health`). Make sure the demo stage is trimmed (the seeder's `--media` chain or Audit's "Trim now"), then confirm `trimmed/*_web.mp4` exists. With Playwright:
1. Open the stage's Audit page; read the `<video>`'s `src` -- it must contain `kind=web&v=`. Fetch that URL and check the bytes equal the `_web.mp4` on disk (size match is enough).
2. Toggle "Full-resolution video" in the transport's More menu; `src` must switch to `kind=trim`; `GET /api/settings/scrub` returns `true`; reload keeps it.
3. Toggle back. Delete the `_web.mp4` and reload: `src` is `kind=trim` (no `scrub_version`).
4. Screenshot the Audit page with the video playing on the rendition; publish it as an Artifact (gaspode is headless) and link it in the PR.

- [ ] **Step 4: #1191 check against the served URL**

Repeat the spike's low-end run (`--enable-low-end-device-mode --disable-accelerated-video-decode`) with Playwright against the Audit page's `<video>` on a 4K high-bitrate trim: build one with the spike recipe from the corpus (`scale=3840:2160,noise=alls=3:allf=t,fps=30`, `ultrafast` CRF 20 GOP 15, ~170 Mbit/s), drop it in as the demo stage's trim, cut its rendition with `trim.transcode_web_trim`, reload, play, and record frames shown vs duration. Expected: plays through on `kind=web`; with the switch on (`kind=trim`) it ends early. Paste both numbers into the PR body.

- [ ] **Step 5: Commit docs**

```bash
git add CLAUDE.md
git commit -m "docs: the Audit players scrub the rendition (#1192)"
```

- [ ] **Step 6: Whole-branch review, then PR**

One fresh reviewer over `git diff origin/main...HEAD` with these claims to verify (treat the implementation report as unverified):
- `fresh_web_trim` is behaviour-identical to the old inline check in `_ensure_web_trim` for every (exists, size, mtime) combination.
- `_scrub_version_for` and the local `kind=web` branch resolve the *same* trim path (`resolve_trim_for_read` vs `pull_trimmed_video`) in local mode, including the legacy-keyed trim fallback; a divergence means the version names different bytes from the URL.
- No hosted code path changed behaviour.
- Each new test fails against `origin/main` (check out the test file onto a pre-change tree with a non-editable install, or re-run the drills).

Then open the PR (`Fixes #1192`, `Fixes #1191`), file the follow-up issue "Hosted Audit on hosted-native matches streams the full trim; scrub the rendition there too" with the spec's out-of-scope paragraph, and squash-merge on green per the repo convention.
