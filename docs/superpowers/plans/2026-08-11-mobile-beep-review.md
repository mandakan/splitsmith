# Mobile Beep Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first mobile write surface: beep review from a phone against the hosted app, working on both hosted-native matches and desktop-pushed mirrors, with mark-state-only writes on mirrors (desktop re-derives on its next pull).

**Architecture:** Lift the read_only_mirror 403 for exactly two beep write endpoints; skip the trim/shot-detect job chain when the match origin is `desktop`; have desktop push generate a small audio snippet + peaks JSON per unconfirmed video so the phone has media to review with; extract the queue/mutation logic from `BeepReview.tsx` into a shared `useBeepQueue` hook and add a `MobileBeepReview` card pager that replaces `DesktopGate` on the route.

**Tech Stack:** FastAPI + Starlette middleware (server.py), pydantic models, ffmpeg (AAC snippet extraction), React 18 + TypeScript + Tailwind (ui_static, pnpm only), vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-11-mobile-beep-review-design.md`

## Global Constraints

- New copy and comments use a single ASCII dash "-", never em dashes and never "--". ASCII punctuation only.
- ui_static is pnpm-only; never touch npm or package-lock.json.
- No new dependencies (Python or JS) without consulting the user first.
- Mobile UI: 44 px minimum touch targets, WCAG 2.2 AA, status never carried by color alone, respect prefers-reduced-motion.
- Overlays follow the body-Portal + z-token + useDialogFocus architecture (PR #519); never inline fixed overlays.
- Run gates before any PR: `ruff check`, `black --check`, `pytest`, and in ui_static `pnpm typecheck && pnpm test` plus scoped eslint. Docker smoke (`pytest -m docker -n0`) because hosted-server behavior changes.
- Implementation branch: `feat/mobile-beep-review` off `origin/main`. Commit after every task.
- All server work is in `src/splitsmith/ui/server.py` unless stated; line numbers reference origin/main at 6e62a1e and will drift - anchor by the quoted code, not the number.

---

### Task 1: Mirror write gate exemption for the two beep write paths

**Files:**
- Modify: `src/splitsmith/ui/server.py:6377-6382` (the read_only_mirror gate inside `_match_id_alias`)
- Test: `tests/test_mirror_read_only.py`

**Interfaces:**
- Produces: mirrors accept `POST match/beep-queue/confirm` and `POST shooters/{slug}/stages/{n}/videos/{vid}/beep` through the alias; every other write still 403s. Later tasks rely on these two paths reaching their handlers on mirrors.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mirror_read_only.py` (reuse the existing `hosted_env` / `hosted_app` fixtures and the module's `_seed_mirror(client, match_id, name)` helper, same as `test_mirror_add_shooter_blocked` at line 113):

```python
def test_mirror_beep_confirm_passes_gate(hosted_env, hosted_app):
    """The gate no longer 403s beep-queue confirm on a mirror.

    Only the middleware is under test: with no shooter seeded the handler
    itself 404s, which proves the request got past the 403."""
    client, _ = hosted_app
    login(client)
    match_id = "01JMIRRBEEPGATE0000000001"
    _seed_mirror(client, match_id, "gate-confirm")
    resp = client.post(
        f"/api/matches/{match_id}/match/beep-queue/confirm",
        json={"slug": "ghost", "stage_number": 1, "video_id": "v1"},
    )
    assert resp.status_code != 403, resp.text


def test_mirror_beep_override_passes_gate(hosted_env, hosted_app):
    client, _ = hosted_app
    login(client)
    match_id = "01JMIRRBEEPGATE0000000002"
    _seed_mirror(client, match_id, "gate-override")
    resp = client.post(
        f"/api/matches/{match_id}/shooters/ghost/stages/1/videos/v1/beep",
        json={"beep_time": 1.25},
    )
    assert resp.status_code != 403, resp.text


def test_mirror_destructive_beep_paths_still_blocked(hosted_env, hosted_app):
    """detect-beep, beep-window, select, and snap stay read-only on mirrors."""
    client, _ = hosted_app
    login(client)
    match_id = "01JMIRRBEEPGATE0000000003"
    _seed_mirror(client, match_id, "gate-blocked")
    blocked = [
        ("POST", f"/api/matches/{match_id}/shooters/g/stages/1/videos/v1/detect-beep", None),
        ("PUT", f"/api/matches/{match_id}/shooters/g/stages/1/videos/v1/beep-window",
         {"start": 0.0, "end": 5.0}),
        ("POST", f"/api/matches/{match_id}/shooters/g/stages/1/videos/v1/beep/select",
         {"time": 1.0}),
        ("POST", f"/api/matches/{match_id}/shooters/g/stages/1/videos/v1/beep/snap",
         {"time": 1.0}),
        ("POST", f"/api/matches/{match_id}/shooters/g/stages/1/beep", {"beep_time": 1.0}),
    ]
    for method, url, body in blocked:
        resp = client.request(method, url, json=body)
        assert resp.status_code == 403, f"{method} {url} -> {resp.status_code}"
        assert resp.json()["detail"] == "read_only_mirror"
```

Note the legacy primary-only `POST shooters/{slug}/stages/{n}/beep` shim stays blocked - the SPA only calls the per-video route.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mirror_read_only.py -k beep -n0 -x -q`
Expected: the two `passes_gate` tests FAIL with 403; the `still_blocked` test may already pass.

- [ ] **Step 3: Implement the exemption**

In `server.py`, directly above the `@app.middleware("http")` decorator of `_match_id_alias` (around line 6318), add a module-scope-free local constant (it can live at the same closure level as the middleware; `re` is already imported at the top of server.py - verify, add if missing):

```python
    # Slice 3 (mobile beep review): the only two beep writes a mirror
    # accepts. Everything else beep-shaped (detect-beep, beep-window,
    # select, snap, the legacy primary shim) needs source audio or fires
    # jobs, and stays read-only on mirrors.
    _mirror_beep_write_re = re.compile(r"^shooters/[^/]+/stages/\d+/videos/[^/]+/beep$")
```

Then extend the gate condition at 6377-6382 to:

```python
            if (
                owner_row.origin == "desktop"
                and request.method not in ("GET", "HEAD", "OPTIONS")
                and not (
                    rest == "match/shares"
                    or rest.startswith("match/shares/")
                    or (request.method == "POST" and rest == "match/beep-queue/confirm")
                    or (request.method == "POST" and _mirror_beep_write_re.match(rest) is not None)
                )
            ):
                return JSONResponse(status_code=403, content={"detail": "read_only_mirror"})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mirror_read_only.py -n0 -q`
Expected: PASS (all, including the pre-existing mirror tests - the exemption must not widen anything else).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_mirror_read_only.py
git commit -m "feat(sync): allow beep confirm/override writes on mirror matches"
```

---

### Task 2: Mark-state-only override on mirrors (no job chain)

**Files:**
- Modify: `src/splitsmith/ui/server.py:9602-9611` (`override_beep_for_video`)
- Test: `tests/test_mirror_read_only.py`

**Interfaces:**
- Consumes: Task 1's gate exemption (requests reach the handler).
- Produces: on a mirror, `override_beep_for_video` writes beep fields + processed flags and returns the project JSON without submitting any job. Desktop pull picks the change up through the existing sync merge; no sync-side change needed.

- [ ] **Step 1: Write the failing test**

The test needs a mirror with a real shooter/stage/video. Seed the project doc through the same sync-doc PUT surface `_seed_mirror` uses for the match doc (mimic `tests/hosted_helpers.seed_match` / the slice-2 seed helper in `tests/test_mirror_read_only.py` - extend `_seed_mirror` or add a sibling `_seed_mirror_with_video` helper in the module):

```python
def _seed_mirror_with_video(client, match_id: str, name: str) -> None:
    """Mirror match with one shooter 'alice', stage 1, one primary video."""
    _seed_mirror(client, match_id, name)
    project_doc = {
        "competitor_name": "Alice",
        "stages": [
            {
                "stage_number": 1,
                "stage_name": "Stage 1",
                "time_seconds": 12.5,
                "videos": [
                    {
                        "video_id": "vid1",
                        "path": "videos/stage1.mp4",
                        "role": "primary",
                        "beep_time": 2.0,
                        "beep_source": "auto",
                        "beep_confidence": 0.4,
                        "beep_reviewed": False,
                        "processed": {"beep": True, "trim": True, "shot_detect": True},
                    }
                ],
            }
        ],
    }
    resp = client.put(
        f"/api/sync/matches/{match_id}/docs/project/alice",
        json={"body": project_doc, "expected_version": 0},
    )
    assert resp.status_code in (200, 201), resp.text
```

Match the PUT path and body envelope to what `_seed_mirror` already does for the match doc (open `tests/test_mirror_read_only.py:37` and copy its exact shape, including `expected_version` - required since PR #818). If the project-doc PUT rejects unknown/missing fields, start from a minimal valid project doc produced by `MatchProject(...).model_dump(mode="json")` in the test instead of a hand-rolled dict.

```python
def test_mirror_override_marks_state_only(hosted_env, hosted_app):
    client, _ = hosted_app
    login(client)
    match_id = "01JMIRRBEEPSTATE000000001"
    _seed_mirror_with_video(client, match_id, "state-only")

    resp = client.post(
        f"/api/matches/{match_id}/shooters/alice/stages/1/videos/vid1/beep",
        json={"beep_time": 3.75},
    )
    assert resp.status_code == 200, resp.text
    video = resp.json()["stages"][0]["videos"][0]
    assert video["beep_time"] == 3.75
    assert video["beep_source"] == "manual"
    assert video["processed"]["trim"] is False
    assert video["processed"]["shot_detect"] is False

    jobs = client.get("/api/me/jobs").json()
    assert jobs["jobs"] == [], f"mirror override must not enqueue jobs: {jobs}"


def test_mirror_confirm_sets_reviewed(hosted_env, hosted_app):
    client, _ = hosted_app
    login(client)
    match_id = "01JMIRRBEEPSTATE000000002"
    _seed_mirror_with_video(client, match_id, "confirm-flag")
    resp = client.post(
        f"/api/matches/{match_id}/match/beep-queue/confirm",
        json={"slug": "alice", "stage_number": 1, "video_id": "vid1"},
    )
    assert resp.status_code == 200, resp.text
    jobs = client.get("/api/me/jobs").json()
    assert jobs["jobs"] == []
```

Check the actual `/api/me/jobs` response shape in `tests/` (slice 1, PR #811, has jobs-page tests - copy its assertion pattern for "no jobs").

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mirror_read_only.py -k state_only -n0 -x -q`
Expected: FAIL - either a trim job appears in the jobs list, or `_maybe_chain_trim` raises trying to submit against a mirror.

- [ ] **Step 3: Implement the chain skip**

In `override_beep_for_video` (server.py:9602-9611), change the chain block:

```python
        project, stage, video = _resolve_stage_video(slug, stage_number, video_id)
        if req.beep_time is not None and req.beep_time < 0.0:
            raise HTTPException(status_code=400, detail="beep_time must be >= 0")
        _apply_beep_override(slug, project, stage, video, req.beep_time)
        project.save(state.shooter_root(slug))
        # Mirrors mark state only: no raw media exists hosted-side, so
        # there is nothing to trim or detect against. Desktop re-derives
        # on its next sync pull (bidirectional sync design).
        if req.beep_time is not None and current_match_origin.get() != "desktop":
            await _maybe_chain_trim(slug, stage, video)
            await _advance_sequential_chain(state, slug, project, video, stage_number)
        return JSONResponse(project.model_dump(mode="json"))
```

`current_match_origin` is the ContextVar defined at server.py:1017 and set by the alias middleware; the `or "local"` read pattern is at server.py:12352-12357. `confirm_beep_in_queue` needs no change - it never enqueues.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mirror_read_only.py -n0 -q && pytest tests/test_ui_server.py -k "beep" -q`
Expected: PASS, including the existing local-mode beep tests (the chain still fires when origin is not `desktop`).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_mirror_read_only.py
git commit -m "feat(sync): mirror beep override marks state only, no job chain"
```

---

### Task 3: Honest queue media descriptor (+ origin, trim_stale, snippet_ready)

**Files:**
- Modify: `src/splitsmith/ui/server.py:3892-3935` (BeepQueue models), `server.py:13045-13185` (`get_beep_queue`)
- Test: `tests/test_ui_server.py`, `tests/test_mirror_read_only.py`

**Interfaces:**
- Produces (later tasks depend on these exact names):
  - `BeepQueueItem` gains `snippet_ready: bool = False` and `trim_stale: bool = False`.
  - `BeepQueueResponse` gains `origin: str = "local"`.
  - `_proxy_ready` returns False for hosted non-`raw/` paths.
  - Snippet R2 key shape: `matches/<match_id>/shooters/<slug>/beep_review/<video_id>.m4a` and `...<video_id>.peaks.json` (Tasks 4-6 must match it byte-for-byte).

- [ ] **Step 1: Write the failing tests**

In `tests/test_ui_server.py`, next to `test_beep_queue_proxy_ready_local_mode_always_true` (line 8867), the local-mode behavior is already covered. Add hosted-side coverage in `tests/test_mirror_read_only.py`:

```python
def test_mirror_beep_queue_media_flags(hosted_env, hosted_app):
    """Mirror queue items report honest media: no proxy, snippet only when
    both R2 objects exist, origin=desktop, trim_stale from processed."""
    client, _ = hosted_app
    login(client)
    match_id = "01JMIRRBEEPQUEUE00000001"
    _seed_mirror_with_video(client, match_id, "queue-flags")

    resp = client.get(f"/api/matches/{match_id}/match/beep-queue")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["origin"] == "desktop"
    item = body["stages"][0]["items"][0]
    assert item["proxy_ready"] is False  # was falsely True before this task
    assert item["snippet_ready"] is False  # nothing uploaded yet
    assert item["trim_stale"] is False  # processed.trim is True in the seed

    # Upload both snippet objects into the fake storage, then re-query.
    storage = _hosted_storage(hosted_app)  # see note below
    base = f"matches/{match_id}/shooters/alice/beep_review/vid1"
    storage.put_bytes(f"{base}.m4a", b"fake-audio")
    storage.put_bytes(f"{base}.peaks.json", b"{}")
    item = client.get(f"/api/matches/{match_id}/match/beep-queue").json()["stages"][0]["items"][0]
    assert item["snippet_ready"] is True
```

Note: find how existing hosted tests reach the storage fake (grep `tests/` for `storage.put` / the hosted_app fixture body in `tests/conftest.py`) and write `_hosted_storage` accordingly; if the fake exposes a different write method name, use that. If no write helper exists, add snippet objects through whatever seam `test_beep_queue_proxy_ready_*` style tests use for `raw_proxy/` objects.

Also add a `trim_stale` positive case: re-seed with `"processed": {"beep": True, "trim": False, "shot_detect": False}` and `beep_time: 2.0`, assert `item["trim_stale"] is True`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mirror_read_only.py -k queue_flags -n0 -x -q`
Expected: FAIL - `proxy_ready` is True and `origin`/`snippet_ready`/`trim_stale` are missing keys.

- [ ] **Step 3: Implement**

Models (server.py:3892-3935):

```python
class BeepQueueItem(BaseModel):
    ...  # existing fields unchanged
    proxy_ready: bool
    snippet_ready: bool = False
    trim_stale: bool = False


class BeepQueueResponse(BaseModel):
    ...  # existing fields unchanged
    origin: str = "local"
```

In `get_beep_queue` (server.py:13088-13104), fix `_proxy_ready` and add snippet lookup:

```python
        _storage = state.storage
        _proxy_keys: set[str] = set()
        if _storage is not None:
            _proxy_keys = {obj.path for obj in _storage.list("raw_proxy/")}

        def _proxy_ready(path_str: str) -> bool:
            if _storage is None:
                return True
            if not path_str.startswith("raw/"):
                # Hosted but not a hosted-native upload (a desktop-pushed
                # mirror): there is no proxy object to stream.
                return False
            return proxy_key_for(path_str) in _proxy_keys

        # Snippet artifacts pushed by desktop for unconfirmed videos
        # (slice 3). One list per request covers every shooter.
        _match_id = current_match_id.get()
        _snippet_keys: set[str] = set()
        if _storage is not None and _match_id:
            _snippet_keys = {
                obj.path for obj in _storage.list(f"matches/{_match_id}/shooters/")
            }

        def _snippet_ready(slug: str, video_id: str) -> bool:
            if not _snippet_keys:
                return False
            base = f"matches/{_match_id}/shooters/{slug}/beep_review/{video_id}"
            return f"{base}.m4a" in _snippet_keys and f"{base}.peaks.json" in _snippet_keys
```

In the item construction (server.py:13161-13177) add:

```python
                            proxy_ready=_proxy_ready(video.path.as_posix()),
                            snippet_ready=_snippet_ready(slug, video.video_id),
                            trim_stale=(
                                video.beep_time is not None
                                and not video.processed.get("trim", False)
                            ),
```

And in the response (13180-13185):

```python
        return BeepQueueResponse(
            total_items=total_videos,
            pending_count=total_pending,
            confirmed_count=total_confirmed,
            stages=ordered_stages,
            origin=current_match_origin.get() or "local",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mirror_read_only.py -n0 -q && pytest tests/test_ui_server.py -k beep_queue -q`
Expected: PASS. The local-mode test at test_ui_server.py:8867 must still pass (storage None keeps returning True).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_mirror_read_only.py
git commit -m "fix(beep-queue): honest media flags on mirrors + origin and trim_stale"
```

---

### Task 4: Snippet serving endpoints

**Files:**
- Modify: `src/splitsmith/ui/server.py` (add two GET endpoints next to `video_peaks` at :10250)
- Test: `tests/test_mirror_read_only.py`

**Interfaces:**
- Consumes: snippet key shape from Task 3.
- Produces (frontend Task 7 calls these):
  - `GET /api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep-snippet/audio` -> presigned 307 (or FileResponse) of the m4a, 404 `beep_snippet_not_available` when absent.
  - `GET /api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep-snippet/peaks` -> JSON body of the peaks file (shape defined in Task 5), 404 when absent.

- [ ] **Step 1: Write the failing tests**

```python
def test_beep_snippet_endpoints_serve_pushed_artifacts(hosted_env, hosted_app):
    client, _ = hosted_app
    login(client)
    match_id = "01JMIRRBEEPSNIP000000001"
    _seed_mirror_with_video(client, match_id, "snippet-serve")
    storage = _hosted_storage(hosted_app)
    base = f"matches/{match_id}/shooters/alice/beep_review/vid1"
    peaks_doc = {"snippet_start": 1.0, "duration": 10.0, "bins": 4,
                 "peaks": [0.1, 0.9, 0.2, 0.1], "beep_time": 2.0,
                 "candidates": [{"time": 2.0, "confidence": 0.4}]}
    storage.put_bytes(f"{base}.m4a", b"fake-aac-bytes")
    storage.put_bytes(f"{base}.peaks.json", json.dumps(peaks_doc).encode())

    peaks = client.get(
        f"/api/matches/{match_id}/shooters/alice/stages/1/videos/vid1/beep-snippet/peaks"
    )
    assert peaks.status_code == 200, peaks.text
    assert peaks.json()["snippet_start"] == 1.0

    audio = client.get(
        f"/api/matches/{match_id}/shooters/alice/stages/1/videos/vid1/beep-snippet/audio",
        follow_redirects=False,
    )
    assert audio.status_code in (200, 307), audio.text


def test_beep_snippet_404_when_absent(hosted_env, hosted_app):
    client, _ = hosted_app
    login(client)
    match_id = "01JMIRRBEEPSNIP000000002"
    _seed_mirror_with_video(client, match_id, "snippet-missing")
    resp = client.get(
        f"/api/matches/{match_id}/shooters/alice/stages/1/videos/vid1/beep-snippet/audio"
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mirror_read_only.py -k snippet -n0 -x -q`
Expected: FAIL with 404 route-not-found on both (endpoints don't exist).

- [ ] **Step 3: Implement**

Add next to `video_peaks` (server.py:10250), following the `serve_media` pattern (signature at server.py:325-332) and the mirror-then-read pattern at server.py:10862:

```python
    def _beep_snippet_key(slug: str, video_id: str, suffix: str) -> str | None:
        match_id = current_match_id.get()
        if state.storage is None or not match_id:
            return None
        return f"matches/{match_id}/shooters/{slug}/beep_review/{video_id}{suffix}"

    @app.get(
        "/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep-snippet/audio",
        response_model=None,
    )
    def beep_snippet_audio(
        slug: str, stage_number: int, video_id: str
    ) -> FileResponse | RedirectResponse:
        """Serve the pushed beep review audio snippet for a mirror video."""
        _resolve_stage_video(slug, stage_number, video_id)  # 404 on unknown video
        key = _beep_snippet_key(slug, video_id, ".m4a")
        if key is None or not state.storage.exists(key):
            raise HTTPException(status_code=404, detail="beep_snippet_not_available")
        local = state.shooter_root(slug) / "beep_review" / f"{video_id}.m4a"
        return serve_media(state.storage, key, local, content_type="audio/mp4")

    @app.get("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep-snippet/peaks")
    def beep_snippet_peaks(slug: str, stage_number: int, video_id: str) -> JSONResponse:
        """Return the pushed peaks JSON for a mirror video's beep snippet."""
        _resolve_stage_video(slug, stage_number, video_id)
        key = _beep_snippet_key(slug, video_id, ".peaks.json")
        if key is None or not state.storage.exists(key):
            raise HTTPException(status_code=404, detail="beep_snippet_not_available")
        local = state.shooter_root(slug) / "beep_review" / f"{video_id}.peaks.json"
        if not local.exists():
            MatchProject._mirror_from_storage(state.storage, key, local)
        return JSONResponse(json.loads(local.read_text(encoding="utf-8")))
```

These are GETs, so the mirror gate never applies; no middleware change needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mirror_read_only.py -n0 -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_mirror_read_only.py
git commit -m "feat(beep-queue): serve pushed beep snippet audio and peaks"
```

---

### Task 5: Desktop snippet generation module

**Files:**
- Create: `src/splitsmith/sync/beep_snippets.py`
- Test: `tests/test_sync_beep_snippets.py`

**Interfaces:**
- Consumes: `load_match_or_legacy` (match_model), `MatchProject.load`, `project.resolve_video_path`, `waveform.ensure_peaks(audio_path: Path, bins: int) -> PeaksResult` (waveform.py:104; PeaksResult fields: duration, sample_rate, bins, peaks).
- Produces (Task 6 consumes): `generate_beep_snippets(match_root: Path, *, ffmpeg_binary: str = "ffmpeg") -> BeepSnippetReport` writing `<shooter_root>/beep_review/<video_id>.m4a` + `<video_id>.peaks.json`. Peaks JSON shape: `{"snippet_start", "duration", "sample_rate", "bins", "peaks", "beep_time", "candidates": [{"time", "confidence"}], "input_hash"}`.

- [ ] **Step 1: Write the failing test**

`tests/test_sync_beep_snippets.py` - build a tiny real source clip with ffmpeg's sine generator so the cut + peaks path is honest (same style as the repo's trim tests; ffmpeg is on PATH in CI and locally via ffmpeg-full):

```python
import json
import subprocess
from pathlib import Path

import pytest

from splitsmith.sync.beep_snippets import generate_beep_snippets

from .test_sync_plan import _build_match_root  # reuse the plan tests' match-tree builder


def _make_source(path: Path, seconds: float = 20.0) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"sine=frequency=1000:duration={seconds}",
         "-c:a", "aac", str(path)],
        check=True,
    )


def _seed(tmp_path: Path, *, beep_time=6.0, reviewed=False):
    """Match tree with one shooter, one stage, one primary video backed by
    a real 20 s sine source ffmpeg can cut."""
    match_root, shooter_root, project = _build_match_root(tmp_path)  # adapt to helper's real shape
    video = project.stages[0].videos[0]
    video.beep_time = beep_time
    video.beep_reviewed = reviewed
    project.save(shooter_root)
    src = shooter_root / str(video.path)
    src.parent.mkdir(parents=True, exist_ok=True)
    _make_source(src)
    return match_root, shooter_root, project, video


def test_generates_snippet_for_unreviewed_video(tmp_path: Path):
    match_root, shooter_root, _project, video = _seed(tmp_path)

    report = generate_beep_snippets(match_root)
    assert report.generated == 1 and not report.errors
    out = shooter_root / "beep_review"
    m4a = out / f"{video.video_id}.m4a"
    peaks = json.loads((out / f"{video.video_id}.peaks.json").read_text())
    assert m4a.stat().st_size > 0
    assert peaks["snippet_start"] == pytest.approx(1.0)  # 6.0 - 5.0 margin
    assert peaks["duration"] == pytest.approx(10.0, abs=1.0)  # margin both sides
    assert peaks["beep_time"] == 6.0
    assert len(peaks["peaks"]) == peaks["bins"]


def test_skips_when_inputs_unchanged_and_regenerates_on_change(tmp_path: Path):
    match_root, shooter_root, project, video = _seed(tmp_path)
    assert generate_beep_snippets(match_root).generated == 1

    second = generate_beep_snippets(match_root)
    assert second.generated == 0 and second.skipped == 1

    video.beep_time = 8.5
    project.save(shooter_root)
    third = generate_beep_snippets(match_root)
    assert third.generated == 1


def test_reviewed_video_gets_no_snippet_and_stale_one_is_removed(tmp_path: Path):
    match_root, shooter_root, project, video = _seed(tmp_path)
    assert generate_beep_snippets(match_root).generated == 1  # snippet exists

    video.beep_reviewed = True
    project.save(shooter_root)
    report = generate_beep_snippets(match_root)
    assert report.generated == 0 and report.removed == 1
    out = shooter_root / "beep_review"
    assert not (out / f"{video.video_id}.m4a").exists()
    assert not (out / f"{video.video_id}.peaks.json").exists()
```

Adapt `_build_match_root` to whatever `tests/test_sync_plan.py` actually provides (its tests build a bare match tree on disk - reuse, do not reinvent). `_make_source` writes the source at the video's registered relative path; the `-f lavfi -i sine` + `-c:a aac` command shown above produces a container ffmpeg can cut regardless of the `.mp4` extension.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sync_beep_snippets.py -x -q`
Expected: FAIL with `ModuleNotFoundError: splitsmith.sync.beep_snippets`.

- [ ] **Step 3: Implement the module**

`src/splitsmith/sync/beep_snippets.py`:

```python
"""Generate beep review snippets desktop-side before a push (slice 3).

For every unconfirmed queue-worthy video (primary or secondary, stage not
skipped, beep not yet reviewed) this cuts a short mono AAC snippet around
the beep candidates plus a peaks JSON for the same range, into
``<shooter_root>/beep_review/``. The push plan uploads whatever exists
there; hosted serves it so a phone can review beeps on a mirror match.

Skip logic is an ``input_hash`` stored inside the peaks JSON - a digest of
the fields that shape the snippet. Unchanged inputs mean no ffmpeg run and
untouched mtimes, so the push plan's size+mtime check skips the upload too.
Videos that become reviewed get their snippet files removed so they stop
being pushed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from ..match_model import load_match_or_legacy
from ..match_project import MatchProject
from ..waveform import ensure_peaks

logger = logging.getLogger(__name__)

SNIPPET_MARGIN_S = 5.0
DEFAULT_WINDOW_END_S = 30.0
MIN_SNIPPET_S = 2.0
PEAK_BINS = 600
SNIPPET_SAMPLE_RATE = 16000


class BeepSnippetReport(BaseModel):
    generated: int = 0
    skipped: int = 0
    removed: int = 0
    errors: list[str] = Field(default_factory=list)


def _window(video) -> tuple[float, float]:
    """Snippet range in source seconds: candidates and beep +- margin,
    else the default detection window from t=0."""
    times = [c.time for c in (video.beep_candidates or [])]
    if video.beep_time is not None:
        times.append(video.beep_time)
    if times:
        start = max(0.0, min(times) - SNIPPET_MARGIN_S)
        end = max(times) + SNIPPET_MARGIN_S
    else:
        start, end = 0.0, DEFAULT_WINDOW_END_S
    return start, max(end, start + MIN_SNIPPET_S)


def _input_hash(video, start: float, end: float) -> str:
    payload = {
        "video_id": video.video_id,
        "beep_time": video.beep_time,
        "candidates": [c.time for c in (video.beep_candidates or [])],
        "start": round(start, 3),
        "end": round(end, 3),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _cut(
    ffmpeg_binary: str, src: Path, dest: Path, start: float, dur: float, codec: list[str]
) -> None:
    cmd = [
        ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(SNIPPET_SAMPLE_RATE), *codec, str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def generate_beep_snippets(
    match_root: Path, *, ffmpeg_binary: str = "ffmpeg"
) -> BeepSnippetReport:
    report = BeepSnippetReport()
    match, shooter_roots = load_match_or_legacy(match_root)
    for slug in match.shooters:
        shooter_root = shooter_roots[slug]
        try:
            project = MatchProject.load(shooter_root)
        except FileNotFoundError:
            continue
        out_dir = shooter_root / "beep_review"
        for stage in project.stages:
            if stage.skipped:
                continue
            for video in stage.videos:
                if video.role not in ("primary", "secondary"):
                    continue
                m4a = out_dir / f"{video.video_id}.m4a"
                peaks_path = out_dir / f"{video.video_id}.peaks.json"
                if video.beep_reviewed:
                    removed = False
                    for stale in (m4a, peaks_path):
                        if stale.exists():
                            stale.unlink()
                            removed = True
                    if removed:
                        report.removed += 1
                    continue
                start, end = _window(video)
                digest = _input_hash(video, start, end)
                if peaks_path.exists():
                    try:
                        if json.loads(peaks_path.read_text())["input_hash"] == digest and m4a.exists():
                            report.skipped += 1
                            continue
                    except (json.JSONDecodeError, KeyError, OSError):
                        pass  # unreadable - regenerate
                src = project.resolve_video_path(shooter_root, video.path)
                if not src.exists():
                    report.errors.append(f"{slug}/{video.video_id}: source missing: {src}")
                    continue
                out_dir.mkdir(parents=True, exist_ok=True)
                wav_tmp = out_dir / f"{video.video_id}.tmp.wav"
                try:
                    _cut(ffmpeg_binary, src, m4a, start, end - start,
                         ["-c:a", "aac", "-b:a", "48k"])
                    _cut(ffmpeg_binary, src, wav_tmp, start, end - start, [])
                    peaks = ensure_peaks(wav_tmp, PEAK_BINS)
                    peaks_path.write_text(json.dumps({
                        "snippet_start": start,
                        "duration": peaks.duration,
                        "sample_rate": peaks.sample_rate,
                        "bins": peaks.bins,
                        "peaks": peaks.peaks,
                        "beep_time": video.beep_time,
                        "candidates": [
                            {"time": c.time, "confidence": c.confidence}
                            for c in (video.beep_candidates or [])
                        ],
                        "input_hash": digest,
                    }), encoding="utf-8")
                    report.generated += 1
                except subprocess.CalledProcessError as exc:
                    stderr = (exc.stderr or b"").decode("utf-8", "replace")[-500:]
                    report.errors.append(f"{slug}/{video.video_id}: ffmpeg failed: {stderr}")
                finally:
                    wav_tmp.unlink(missing_ok=True)
                    if wav_tmp.with_suffix(".peaks.json").exists():
                        wav_tmp.with_suffix(".peaks.json").unlink()
    return report
```

Check whether `ensure_peaks` writes a sidecar cache next to its input (read waveform.py:104 onward); the `finally` block above cleans one up if so - drop that cleanup if it does not.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sync_beep_snippets.py -q`
Expected: PASS. If it fails on a stale slim-ffmpeg shell, re-exec the shell (ffmpeg-full is PATH-prepended in dotfiles).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/sync/beep_snippets.py tests/test_sync_beep_snippets.py
git commit -m "feat(sync): generate beep review snippets for unconfirmed videos"
```

---

### Task 6: Push plan + run_push integration

**Files:**
- Modify: `src/splitsmith/sync/plan.py` (`_remote_key` at :76, media scan at :197-210), `src/splitsmith/sync/push.py` (`run_push` plan phase at :124-128)
- Test: `tests/test_sync_plan.py`, `tests/test_sync_push.py` (or wherever `run_push` is covered - locate with `grep -rn "run_push" tests/`)

**Interfaces:**
- Consumes: `generate_beep_snippets` (Task 5), snippet key shape (Task 3).
- Produces: `build_push_plan` includes `beep_review/*.m4a` and `beep_review/*.peaks.json` as MediaItems with remote key `matches/<id>/shooters/<slug>/beep_review/<basename>`; `run_push` regenerates snippets before planning.

- [ ] **Step 1: Write the failing tests**

In `tests/test_sync_plan.py`, mirroring `test_fresh_plan_emits_docs_and_media_with_exact_remote_keys` (line 112):

```python
def test_plan_includes_beep_review_artifacts(tmp_path: Path):
    match_root, shooter_root, match_id, slug = ...  # same builder the existing tests use
    out = shooter_root / "beep_review"
    out.mkdir()
    (out / "vid1.m4a").write_bytes(b"aac")
    (out / "vid1.peaks.json").write_text("{}")
    (out / "notes.txt").write_text("ignored")  # only .m4a / .peaks.json enter the plan

    plan = build_push_plan(match_root, sync_state=SyncState())
    keys = {m.remote_key for m in plan.media}
    assert f"matches/{match_id}/shooters/{slug}/beep_review/vid1.m4a" in keys
    assert f"matches/{match_id}/shooters/{slug}/beep_review/vid1.peaks.json" in keys
    assert not any(k.endswith("notes.txt") for k in keys)


def test_plan_skips_unchanged_beep_review_artifacts(tmp_path: Path):
    ...  # record size+mtime in sync_state.items, re-plan, assert media_skipped grew
```

And a `run_push` test asserting `generate_beep_snippets` runs before planning: monkeypatch `splitsmith.sync.push.generate_beep_snippets` to a spy that drops a file into `beep_review/`, run `run_push` against a stub client, assert the spy was called and the file was uploaded (follow the existing `run_push` test's stub-client pattern).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sync_plan.py -k beep_review -x -q`
Expected: FAIL - no beep_review keys in the plan.

- [ ] **Step 3: Implement**

`plan.py` - generalize `_remote_key` (update its two existing call sites at :205):

```python
def _remote_key(match_id: str, slug: str, basename: str, *, subdir: str = "trimmed") -> str:
    return f"matches/{match_id}/shooters/{slug}/{subdir}/{basename}"
```

Add after the trimmed scan (inside the per-slug loop, after line 210):

```python
        beep_review_dir = shooter_root / "beep_review"
        if beep_review_dir.is_dir():
            for artifact in sorted(beep_review_dir.iterdir()):
                if artifact.suffix != ".m4a" and not artifact.name.endswith(".peaks.json"):
                    continue
                remote_key = _remote_key(
                    match.match_id, slug, artifact.name, subdir="beep_review"
                )
                item = _plan_media_item(artifact, remote_key, sync_state)
                if item is None:
                    media_skipped += 1
                else:
                    media.append(item)
```

`push.py` - in `run_push`'s plan phase (:124-128):

```python
from .beep_snippets import generate_beep_snippets

    with _timed_phase(timings, timer, "plan"):
        snippet_report = generate_beep_snippets(match_root)
        for err in snippet_report.errors:
            logger.warning("beep snippet generation: %s", err)
        plan = build_push_plan(match_root, sync_state=sync_state)
```

Snippet errors are warnings, not push blockers - a missing source file must not stop the docs push (check push.py's existing logger name; add `logger = logging.getLogger(__name__)` if the module has none).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sync_plan.py tests/test_sync_beep_snippets.py -q && pytest tests/ -k "run_push or sync_push" -q`
Expected: PASS, including all pre-existing plan/push tests (the `_remote_key` signature change must not alter trimmed keys).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/sync/plan.py src/splitsmith/sync/push.py tests/test_sync_plan.py
git add tests/  # any run_push test file touched
git commit -m "feat(sync): push beep review snippets with the media plan"
```

---

### Task 7: api.ts types + useBeepQueue hook extraction

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (:1682-1716 types, add two helpers near getVideoPeaks at :3162)
- Create: `src/splitsmith/ui_static/src/lib/useBeepQueue.ts`
- Modify: `src/splitsmith/ui_static/src/pages/BeepReview.tsx` (consume the hook; behavior unchanged)
- Test: `src/splitsmith/ui_static/src/lib/useBeepQueue.test.ts`

**Interfaces:**
- Consumes: Task 3 response fields, Task 4 endpoints.
- Produces (Task 8 consumes exactly these):

```ts
// api.ts additions
export interface BeepQueueItem { ...; snippet_ready: boolean; trim_stale: boolean; }
export interface BeepQueueResponse { ...; origin: string; }
export interface BeepSnippetPeaks {
  snippet_start: number; duration: number; bins: number; peaks: number[];
  beep_time: number | null;
  candidates: { time: number; confidence: number | null }[];
}
beepSnippetAudioUrl: (slug: string, stageNumber: number, videoId: string) => string
getBeepSnippetPeaks: (slug: string, stageNumber: number, videoId: string) => Promise<BeepSnippetPeaks>

// useBeepQueue.ts
export function useBeepQueue(): {
  data: BeepQueueResponse | null;
  flatItems: BeepQueueItem[];
  pendingItems: BeepQueueItem[];
  active: BeepQueueItem | null;
  activeKey: string | null;
  setActiveKey: (k: string | null) => void;
  isMirror: boolean;               // data?.origin === "desktop"
  busy: boolean;
  error: string | null;
  setError: (e: string | null) => void;
  redetecting: boolean;
  redetectPct: number | null;
  reload: () => Promise<void>;
  confirm: (item: BeepQueueItem, draftTime?: number) => Promise<void>;
  redetect: (item: BeepQueueItem) => Promise<void>;   // NO dialog inside - callers gate it
  skip: () => void;
  prevItem: () => void;
  nextItem: () => void;
}
export const DESTRUCTIVE_RERUN_WARNING: string;  // shared copy for desktop dialog + mobile sheet
export function keyOf(item: BeepQueueItem): string;
```

- [ ] **Step 1: Write the failing test**

`src/splitsmith/ui_static/src/lib/useBeepQueue.test.ts` (vitest + @testing-library/react `renderHook`; mock `./api` with `vi.mock`):

```ts
import { renderHook, waitFor, act } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { useBeepQueue } from "./useBeepQueue";
import * as api from "./api";

vi.mock("./api", () => ({
  api: {
    getBeepQueue: vi.fn(),
    confirmBeepInQueue: vi.fn(),
    overrideBeepForVideo: vi.fn(),
    detectBeepForVideo: vi.fn(),
    pollJob: vi.fn(),
  },
  ApiError: class ApiError extends Error { detail = "boom"; },
}));

const item = (over: Partial<api.BeepQueueItem> = {}): api.BeepQueueItem => ({
  slug: "alice", shooter_name: "Alice", stage_number: 1, stage_name: "S1",
  role: "primary", video_id: "v1", video_path: "videos/s1.mp4",
  beep_time: 2, beep_confidence: 0.4, beep_reviewed: false,
  status: "low_confidence", alt_candidates: [], proxy_ready: false,
  snippet_ready: true, trim_stale: false, ...over,
});

const queue = (items: api.BeepQueueItem[], origin = "desktop"): api.BeepQueueResponse => ({
  total_items: items.length, pending_count: items.length, confirmed_count: 0,
  origin,
  stages: [{ stage_number: 1, stage_name: "S1", items, total_videos: items.length, confirmed: 0 }],
});

describe("useBeepQueue", () => {
  beforeEach(() => vi.clearAllMocks());

  it("loads the queue, selects the first pending item, reports isMirror", async () => {
    vi.mocked(api.api.getBeepQueue).mockResolvedValue(queue([item()]));
    const { result } = renderHook(() => useBeepQueue());
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(result.current.active?.video_id).toBe("v1");
    expect(result.current.isMirror).toBe(true);
  });

  it("confirm with a draft calls override first, then confirm, then advances", async () => {
    const items = [item(), item({ video_id: "v2" })];
    vi.mocked(api.api.getBeepQueue).mockResolvedValue(queue(items));
    vi.mocked(api.api.overrideBeepForVideo).mockResolvedValue({} as never);
    vi.mocked(api.api.confirmBeepInQueue).mockResolvedValue(
      queue([item({ status: "confirmed", beep_reviewed: true }), items[1]]),
    );
    const { result } = renderHook(() => useBeepQueue());
    await waitFor(() => expect(result.current.active).not.toBeNull());
    await act(() => result.current.confirm(items[0], 3.5));
    expect(api.api.overrideBeepForVideo).toHaveBeenCalledWith("alice", 1, "v1", 3.5);
    expect(api.api.confirmBeepInQueue).toHaveBeenCalledWith(
      expect.objectContaining({ slug: "alice", time: 3.5, source: "manual" }),
    );
    expect(result.current.active?.video_id).toBe("v2");
  });

  it("confirm without a draft never calls override", async () => {
    vi.mocked(api.api.getBeepQueue).mockResolvedValue(queue([item()]));
    vi.mocked(api.api.confirmBeepInQueue).mockResolvedValue(queue([]));
    const { result } = renderHook(() => useBeepQueue());
    await waitFor(() => expect(result.current.active).not.toBeNull());
    await act(() => result.current.confirm(item()));
    expect(api.api.overrideBeepForVideo).not.toHaveBeenCalled();
  });
});
```

The hook uses `useSearchParams` for the `?focus=` deep link, so wrap `renderHook` in a `MemoryRouter` wrapper (copy the wrapper pattern from an existing router-dependent test in ui_static; if none exists, `wrapper: ({children}) => <MemoryRouter>{children}</MemoryRouter>` in a `.tsx` test file).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm test -- useBeepQueue`
Expected: FAIL - module `./useBeepQueue` does not exist.

- [ ] **Step 3: Implement**

1. `api.ts`: add `snippet_ready: boolean; trim_stale: boolean;` to `BeepQueueItem` (:1682-1701), `origin: string;` to `BeepQueueResponse` (:1711-1716), the `BeepSnippetPeaks` interface, and next to `getVideoPeaks` (:3162):

```ts
  beepSnippetAudioUrl: (slug: string, stageNumber: number, videoId: string) =>
    scopeRequestPath(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep-snippet/audio`,
    ),
  getBeepSnippetPeaks: (slug: string, stageNumber: number, videoId: string) =>
    request<BeepSnippetPeaks>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/videos/${encodeURIComponent(videoId)}/beep-snippet/peaks`,
    ),
```

2. `useBeepQueue.ts`: move, verbatim where possible, from `BeepReview.tsx`: the `keyOf` helper, `nextPendingKey`, the data/activeKey/busy/error/redetecting/redetectPct state, the initial load effect, the `flatItems` / `pendingItems` memos (:121-131), the focus-param + first-pending selection effect (:136-159), `confirm` (:170-202), `redetect` minus the `confirmDialog` gate (:209-255 - delete lines 211-226, keep the rest), `skip` (:257-262), `prevItem` / `nextItem` (:264-276). Add `isMirror: data?.origin === "desktop"` and export

```ts
export const DESTRUCTIVE_RERUN_WARNING =
  "This discards any kept shots on this stage and re-runs trim and shot detection.";
```

(match the existing warning copy in BeepReview.tsx:884-885 exactly - reuse that string, do not invent a new one).

3. `BeepReview.tsx`: delete the moved code, call `const q = useBeepQueue()`, destructure, and wrap `q.redetect` in the existing `confirmDialog` gate (the deleted lines 211-226 move into a local `redetectWithDialog` in the component). Keyboard effect (:279-303) stays in the component. The rendered output must not change.

- [ ] **Step 4: Run tests and typecheck**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test -- useBeepQueue && pnpm test`
Expected: PASS, including all pre-existing BeepReview-adjacent tests.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/lib/useBeepQueue.ts src/splitsmith/ui_static/src/lib/useBeepQueue.test.tsx src/splitsmith/ui_static/src/pages/BeepReview.tsx
git commit -m "refactor(ui): extract useBeepQueue hook from BeepReview"
```

---

### Task 8: MobileBeepReview card pager + route branch

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/MobileBeepReview.tsx`
- Create: `src/splitsmith/ui_static/src/components/MobileConfirmSheet.tsx`
- Modify: `src/splitsmith/ui_static/src/App.tsx:274` (route branch)
- Test: `src/splitsmith/ui_static/src/pages/MobileBeepReview.test.tsx`

**Interfaces:**
- Consumes: `useBeepQueue`, `DESTRUCTIVE_RERUN_WARNING`, `api.beepSnippetAudioUrl`, `api.getBeepSnippetPeaks`, `api.videoStreamUrl`, `BeepWaveformPicker` (components/BeepSection.tsx:693, props: slug, stageNumber, videoId, videoBeepTime, draftSourceTime, onPick, setError, ...), `useIsMobile` (lib/useIsMobile.ts:20).
- Produces: `/match/:matchId/beep-review` renders MobileBeepReview below 768 px, desktop BeepReview otherwise; DesktopGate removed from this route.

- [ ] **Step 1: Write the failing test**

`MobileBeepReview.test.tsx` (mock `../lib/useBeepQueue` and `../lib/api`; jsdom + matchMedia is not needed since the component is rendered directly):

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MobileBeepReview } from "./MobileBeepReview";
import { DESTRUCTIVE_RERUN_WARNING } from "@/lib/useBeepQueue";
import * as hook from "@/lib/useBeepQueue";

vi.mock("@/lib/useBeepQueue", async (orig) => ({
  ...(await orig()),
  useBeepQueue: vi.fn(),
}));
vi.mock("@/components/BeepSection", () => ({
  BeepWaveformPicker: () => <div data-testid="waveform-picker" />,
}));
vi.mock("@/lib/api", () => ({
  api: {
    beepSnippetAudioUrl: () => "/snippet.m4a",
    getBeepSnippetPeaks: vi.fn().mockResolvedValue({
      snippet_start: 1, duration: 10, bins: 4, peaks: [0.1, 0.9, 0.2, 0.1],
      beep_time: 2, candidates: [],
    }),
    videoStreamUrl: () => "/proxy.mp4",
  },
}));

const item = (over = {}) => ({
  slug: "alice", shooter_name: "Alice", stage_number: 1, stage_name: "S1",
  role: "primary", video_id: "v1", video_path: "videos/s1.mp4",
  beep_time: 2, beep_confidence: 0.4, beep_reviewed: false,
  status: "low_confidence", alt_candidates: [], proxy_ready: false,
  snippet_ready: true, trim_stale: false, ...over,
});

const hookState = (over = {}) => ({
  data: { total_items: 2, pending_count: 2, confirmed_count: 0, origin: "desktop", stages: [] },
  flatItems: [item(), item({ video_id: "v2" })],
  pendingItems: [item(), item({ video_id: "v2" })],
  active: item(), activeKey: "alice::1::v1", setActiveKey: vi.fn(),
  isMirror: true, busy: false, error: null, setError: vi.fn(),
  redetecting: false, redetectPct: null, reload: vi.fn(),
  confirm: vi.fn(), redetect: vi.fn(), skip: vi.fn(),
  prevItem: vi.fn(), nextItem: vi.fn(), ...over,
});

describe("MobileBeepReview", () => {
  beforeEach(() => vi.clearAllMocks());

  it("mirror item with a snippet renders the audio player, no video, no Re-detect", () => {
    vi.mocked(hook.useBeepQueue).mockReturnValue(hookState());
    render(<MobileBeepReview />);
    expect(screen.getByText(/video available on desktop/i)).toBeInTheDocument();
    expect(document.querySelector("video")).toBeNull();
    expect(screen.queryByRole("button", { name: /re-detect/i })).toBeNull();
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
  });

  it("hosted-native item with a proxy renders video + waveform picker", () => {
    vi.mocked(hook.useBeepQueue).mockReturnValue(
      hookState({ active: item({ proxy_ready: true, snippet_ready: false }), isMirror: false }),
    );
    render(<MobileBeepReview />);
    expect(document.querySelector("video")).not.toBeNull();
    expect(screen.getByTestId("waveform-picker")).toBeInTheDocument();
  });

  it("no media renders the desktop fallback with Confirm disabled", () => {
    vi.mocked(hook.useBeepQueue).mockReturnValue(
      hookState({ active: item({ proxy_ready: false, snippet_ready: false }) }),
    );
    render(<MobileBeepReview />);
    expect(screen.getByText(/review this beep on desktop/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /confirm beep/i })).toBeDisabled();
  });

  it("confirming a placed draft goes through the destructive warning sheet", async () => {
    const state = hookState();
    vi.mocked(hook.useBeepQueue).mockReturnValue(state);
    render(<MobileBeepReview />);
    fireEvent.click(screen.getByRole("button", { name: /use 2\.00s/i, hidden: true }) ??
      screen.getByText(/\+10 ms/));  // place a draft via an alt pill or a nudge
    fireEvent.click(screen.getByRole("button", { name: /apply new time/i }));
    expect(screen.getByText(DESTRUCTIVE_RERUN_WARNING)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /apply and confirm/i }));
    expect(state.confirm).toHaveBeenCalledWith(expect.objectContaining({ video_id: "v1" }),
      expect.any(Number));
  });
});
```

Adjust the draft-placement gesture in the last test to whatever the implemented component exposes (an alt pill when `alt_candidates` is non-empty, else the `+10 ms` nudge on the existing beep) - the assertion that the sheet shows `DESTRUCTIVE_RERUN_WARNING` before `confirm` fires is the contract under test. Follow the component-test style already in ui_static (grep for an existing `*.test.tsx` using `vi.mock` of a hook).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm test -- MobileBeepReview`
Expected: FAIL - module does not exist.

- [ ] **Step 3: Implement the components**

`MobileConfirmSheet.tsx` - a bottom sheet on the overlay architecture (body Portal, z tokens, `useDialogFocus` - copy the Portal + token usage from an existing dialog component, e.g. the one `useConfirm` renders, and keep it small):

```tsx
import { createPortal } from "react-dom";
import { useDialogFocus } from "@/lib/useDialogFocus"; // confirm actual path via the useConfirm dialog's imports

export function MobileConfirmSheet({
  open, title, body, confirmLabel, onConfirm, onCancel,
}: {
  open: boolean; title: string; body: string; confirmLabel: string;
  onConfirm: () => void; onCancel: () => void;
}) {
  const ref = useDialogFocus(open, onCancel); // Esc + focus trap per overlay architecture
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-dialog flex items-end bg-black/50" onClick={onCancel}>
      <div
        ref={ref} role="dialog" aria-modal="true" aria-label={title}
        className="w-full rounded-t-xl border-t border-rule bg-surface p-5 pb-8 motion-safe:animate-in motion-safe:slide-in-from-bottom"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-2 font-display text-base font-bold uppercase text-ink">{title}</div>
        <p className="mb-5 text-sm text-muted">{body}</p>
        <div className="flex gap-3">
          <button type="button" onClick={onCancel}
            className="min-h-11 flex-1 rounded border border-rule px-4 text-sm text-ink">
            Cancel
          </button>
          <button type="button" onClick={onConfirm}
            className="min-h-11 flex-1 rounded bg-led px-4 text-sm font-bold text-black">
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
```

Verify the z token name (`z-dialog`) and `useDialogFocus` signature against the existing dialog stack (PR #519 / #536 components) and match them exactly; verify `bg-led`/`text-ink`/`border-rule`/`bg-surface` exist in styles/index.css before using (CSS var token rule).

`MobileBeepReview.tsx` - one card per active item:

```tsx
import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { BeepQueueItem, BeepSnippetPeaks } from "@/lib/api";
import { useBeepQueue, DESTRUCTIVE_RERUN_WARNING, keyOf } from "@/lib/useBeepQueue";
import { BeepWaveformPicker } from "@/components/BeepSection";
import { MobileConfirmSheet } from "@/components/MobileConfirmSheet";
import { Kicker } from "@/components/ui/Kicker"; // match BeepReview's actual import path

const NUDGE_S = 0.01; // +-10 ms fine steppers
const PLAY_AROUND_S = 1.5;

export function MobileBeepReview() {
  const q = useBeepQueue();
  const [draft, setDraft] = useState<number | null>(null);
  const [sheet, setSheet] = useState<null | "confirm" | "redetect">(null);
  useEffect(() => setDraft(null), [q.activeKey]);

  if (!q.data) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading beep queue...
      </div>
    );
  }
  const item = q.active;
  if (!item) {
    return (
      <div className="px-5 py-10 text-center text-sm text-muted" role="status">
        All quiet - every beep is confirmed.
      </div>
    );
  }
  const position = q.pendingItems.findIndex((it) => keyOf(it) === keyOf(item));
  const effective = draft ?? item.beep_time;

  const doConfirm = () => {
    if (draft != null) setSheet("confirm"); // picking a new time is destructive
    else void q.confirm(item);
  };

  return (
    <div className="mx-auto max-w-md px-4 pb-24 pt-4">
      <header className="mb-3 flex items-center justify-between">
        <Kicker>Beep review</Kicker>
        <span className="text-sm text-muted" aria-live="polite">
          {position >= 0 ? `${position + 1} of ${q.pendingItems.length}` : "confirmed"}
        </span>
      </header>
      <div className="rounded-lg border border-rule bg-surface p-4">
        <div className="mb-1 text-sm font-bold text-ink">
          {item.shooter_name} - stage {item.stage_number}
          {item.role === "secondary" ? " (secondary)" : ""}
        </div>
        <StatusLine item={item} />
        <MediaArea item={item} draft={draft} onPick={setDraft} setError={q.setError} />
        {effective != null ? (
          <NudgeRow value={effective} onNudge={(d) => setDraft((effective ?? 0) + d)} />
        ) : null}
        {item.trim_stale ? (
          <p className="mt-2 text-xs text-muted" role="status">
            Awaiting desktop re-process - results refresh after the next desktop sync.
          </p>
        ) : null}
        <div className="mt-4 flex flex-col gap-2">
          <button type="button" disabled={q.busy || (item.beep_time == null && draft == null)}
            onClick={doConfirm}
            className="min-h-11 rounded bg-led text-sm font-bold text-black disabled:opacity-40">
            {draft != null ? "Apply new time and confirm" : "Confirm beep"}
          </button>
          <div className="flex gap-2">
            <button type="button" onClick={q.skip}
              className="min-h-11 flex-1 rounded border border-rule text-sm text-ink">
              Skip
            </button>
            {!q.isMirror ? (
              <button type="button" disabled={q.busy} onClick={() => setSheet("redetect")}
                className="min-h-11 flex-1 rounded border border-rule text-sm text-ink">
                Re-detect
              </button>
            ) : null}
          </div>
          <div className="flex gap-2">
            <button type="button" onClick={q.prevItem} aria-label="Previous item"
              className="min-h-11 flex-1 rounded border border-rule text-sm text-ink">
              Prev
            </button>
            <button type="button" onClick={q.nextItem} aria-label="Next item"
              className="min-h-11 flex-1 rounded border border-rule text-sm text-ink">
              Next
            </button>
          </div>
        </div>
        {q.error ? <p className="mt-3 text-sm text-danger" role="alert">{q.error}</p> : null}
      </div>
      <MobileConfirmSheet
        open={sheet === "confirm"}
        title="Apply new beep time?"
        body={DESTRUCTIVE_RERUN_WARNING}
        confirmLabel="Apply and confirm"
        onConfirm={() => { setSheet(null); void q.confirm(item, draft ?? undefined); }}
        onCancel={() => setSheet(null)}
      />
      <MobileConfirmSheet
        open={sheet === "redetect"}
        title="Re-detect this beep?"
        body={DESTRUCTIVE_RERUN_WARNING}
        confirmLabel="Re-detect"
        onConfirm={() => { setSheet(null); void q.redetect(item); }}
        onCancel={() => setSheet(null)}
      />
    </div>
  );
}
```

Sub-components in the same file:

- `StatusLine({item})` - text status ("Missing beep" / "Low confidence (0.40)" / "Unreviewed" / "Confirmed"), never color-only.
- `MediaArea({item, draft, onPick, setError})` - the source pick:
  - `item.proxy_ready`: `<video controls playsInline src={api.videoStreamUrl(item.slug, item.video_path, "proxy")} />` plus `<BeepWaveformPicker slug={item.slug} stageNumber={item.stage_number} videoId={item.video_id} videoBeepTime={item.beep_time} draftSourceTime={draft} onPick={onPick} setError={setError} />`.
  - else `item.snippet_ready`: `<SnippetPlayer item={item} draft={draft} onPick={onPick} />` plus a one-line "Video available on desktop" note.
  - else: "Review this beep on desktop - no media was pushed for this video." and the Confirm button stays disabled via `mediaAvailable` (pass a flag up or compute `item.proxy_ready || item.snippet_ready` where Confirm's `disabled` is set; wire it - do not leave Confirm enabled without media).
- `SnippetPlayer({item, draft, onPick})` - fetches `api.getBeepSnippetPeaks(item.slug, item.stage_number, item.video_id)` in a `useEffect`, renders:
  - an `<audio ref={audioRef} src={api.beepSnippetAudioUrl(...)} preload="metadata" />`,
  - a "Play around beep" button: seeks `audioRef.current.currentTime = Math.max(0, (t - peaks.snippet_start) - PLAY_AROUND_S / 2)` where `t = draft ?? item.beep_time ?? peaks.candidates[0]?.time`, plays, and pauses after `PLAY_AROUND_S * 1000` ms via `setTimeout` (clear on unmount),
  - a peaks strip: an SVG of `peaks.peaks` bars in a fixed-height (`h-24`) full-width box; a tap maps `clientX` fraction to `peaks.snippet_start + fraction * peaks.duration` and calls `onPick(sourceTime)`; markers: current `item.beep_time` (line + label) and `draft` (distinct line + label, not color-only - use dashed vs solid),
  - alt candidate pills: `item.alt_candidates.map(c => <button className="min-h-11 ...">Use {c.time.toFixed(2)}s</button>)` calling `onPick(c.time)`.
- `NudgeRow({value, onNudge})` - "-10 ms" / value readout (`value.toFixed(3)`s) / "+10 ms" buttons calling `onNudge(-NUDGE_S)` / `onNudge(NUDGE_S)`.

`App.tsx` (:274): replace the DesktopGate line with a branch component (define next to the router or inline above `App`):

```tsx
import { MobileBeepReview } from "@/pages/MobileBeepReview";
import { useIsMobile } from "@/lib/useIsMobile";

function BeepReviewRoute() {
  const isMobile = useIsMobile();
  return isMobile ? <MobileBeepReview /> : <BeepReview />;
}
// route:
<Route path="beep-review" element={<BeepReviewRoute />} />
```

Remove `DesktopGate` from this route only; other routes keep it.

- [ ] **Step 4: Run tests, typecheck, and visual check**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test`
Expected: PASS.

Visual check per the UI-verification recipe (Playwright MCP hangs on live SSE - use a bounded headless screenshot with domcontentloaded against the dev server, phone viewport 390x844, route `/match/<id>/beep-review`). Confirm: card layout, 44 px targets, sheet slides from bottom, focus ring visible, reduced-motion honored (animate classes are `motion-safe:`).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/MobileBeepReview.tsx src/splitsmith/ui_static/src/pages/MobileBeepReview.test.tsx src/splitsmith/ui_static/src/components/MobileConfirmSheet.tsx src/splitsmith/ui_static/src/App.tsx
git commit -m "feat(ui): mobile beep review card pager replaces DesktopGate"
```

---

### Task 9: Staleness chip in mobile results

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.tsx` (project fetch already at :106, stageEntry extraction at :130)
- Test: extend `MobileBeepReview.test.tsx` coverage is done (card note shipped in Task 8); add a ResultsStage-level test only if ResultsStage already has one to extend - otherwise cover with a small render test `ResultsStage.trimstale.test.tsx` mocking its api calls.

**Interfaces:**
- Consumes: `stageEntry.videos[].processed.trim` and `beep_time` from the `MatchProject` payload ResultsStage already fetches (api.ts:62 `processed` shape).

- [ ] **Step 1: Write the failing test**

Follow ResultsStage's existing test setup if present (`grep -rn "ResultsStage" src/splitsmith/ui_static/src --include="*.test.tsx"`); if none, mock `api.getStageCoach`, `api.getProject`, `api.getMatchCoachDistributions` and render `ResultsStage` inside a `MemoryRouter` with an outlet-context provider, asserting:

```tsx
it("shows the awaiting-desktop-reprocess chip when a video's trim is stale", async () => {
  // project fixture: stage 3 with one video { beep_time: 2.0, processed: { beep: true, trim: false, shot_detect: false } }
  render(<ResultsStageUnderTest />);
  expect(await screen.findByText(/awaiting desktop re-process/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm test -- trimstale`
Expected: FAIL - text not found.

- [ ] **Step 3: Implement**

In `ResultsStageInner`, where `stageEntry` is already extracted (:130-132), keep it in state:

```tsx
const [trimStale, setTrimStale] = useState(false);
// inside the existing project-result handling:
setTrimStale(
  (stageEntry?.videos ?? []).some(
    (v) => v.beep_time != null && !v.processed.trim,
  ),
);
```

Render next to the stage header (near the `Stage {pad2(stage)}` heading around :233-237):

```tsx
{trimStale ? (
  <span
    role="status"
    className="ml-2 inline-flex min-h-6 items-center rounded border border-rule px-2 text-xs text-muted"
  >
    Awaiting desktop re-process
  </span>
) : null}
```

Text chip, not color-coded - consistent with the accessibility constraint. Reuse `StatusPill` (components/ui/StatusPill.tsx:48) instead of the bare span if its variants fit a neutral text chip; check its props before deciding.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/ResultsStage.tsx src/splitsmith/ui_static/src/pages/ResultsStage.trimstale.test.tsx
git commit -m "feat(ui): awaiting-desktop-reprocess chip on stale stages"
```

---

### Task 10: Full gates, docker smoke, PR

**Files:** none new.

- [ ] **Step 1: Python gates**

Run: `ruff check src tests && black --check src tests && pytest -q`
Expected: clean. Local suite has ~21 known env-dependent failures that are green in CI - verify any failure against main before treating it as yours (never check-and-merge in one command).

- [ ] **Step 2: Docker smoke**

Run: `PATH="$HOME/.claude-tmp/bin:$PATH" pytest -m docker -n0 -q`
(docker is not on the non-interactive PATH; the symlink workaround is required, and `-n0` because multiple docker files.)
Expected: clean - hosted mirror-gate behavior changed, so the live-Postgres path must be exercised.

- [ ] **Step 3: Frontend gates**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test && pnpm exec eslint src/lib/useBeepQueue.ts src/pages/MobileBeepReview.tsx src/components/MobileConfirmSheet.tsx src/pages/BeepReview.tsx src/pages/ResultsStage.tsx src/lib/api.ts src/App.tsx`
Expected: clean.

- [ ] **Step 4: Open the PR**

```bash
git push -u origin feat/mobile-beep-review
gh pr create --title "feat: mobile beep review (mobile operator surfaces slice 3)" --body "..."
```

PR body: link the spec (`docs/superpowers/specs/2026-08-11-mobile-beep-review-design.md`), summarize the four backend changes (gate exemption, chain skip, honest media flags, snippet serving), the push artifact, and the UI. Do not auto-merge: merge-when-green is not enforced on this repo - watch checks with `gh run watch` and verify green before merging.

- [ ] **Step 5: Staging E2E (acceptance, after merge + staging deploy)**

Per the spec's acceptance section, on staging (`my.staging.splitsmith.app`, config swap recipe and device-flow token minting are in the hosted-staging memory):
1. Desktop-push a match that has unconfirmed beeps - verify `beep_review/` objects appear in the push log.
2. From a phone viewport, open `/match/<id>/beep-review`: snippet plays, waveform renders, alt pills work.
3. Confirm one item; override one item with a nudged draft - both succeed, no jobs appear in `/api/me/jobs`, stage shows the awaiting-desktop-re-process chip.
4. Desktop pull: SyncReport shows the beep group merged and reprocess count > 0; desktop re-derives; push again.
5. Phone results now fresh, chip cleared, final re-push is a no-op (re-push-0).

---

## Self-review notes (already applied)

- Spec section 1 (gate) -> Task 1; section 2 (mark-state-only) -> Task 2; section 3 (push artifact) -> Tasks 5-6; section 4 (honest descriptor) -> Task 3; section 5 (mobile UI) -> Tasks 7-8; section 6 (staleness badge) -> Tasks 8 (card note) + 9 (results chip); testing section -> per-task tests + Task 10.
- Deviation from spec, deliberate: queue items carry `snippet_ready` flags and the SPA builds API URLs through `beepSnippetAudioUrl` / `getBeepSnippetPeaks` (the `videoStreamUrl` convention) instead of embedding URL strings in the response. The spec's intent - the SPA never guesses R2 keys - holds: keys live server-side only.
- The `_seed_mirror_with_video` helper and `_hosted_storage` accessor must be adapted to the real fixture surfaces in `tests/test_mirror_read_only.py` / `tests/conftest.py`; the plan pins the assertions, not the fixture plumbing.
