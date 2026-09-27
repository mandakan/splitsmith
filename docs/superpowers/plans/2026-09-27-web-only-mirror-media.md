# Web-only Mirror Media Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Desktop pushes stop uploading full-resolution trims by default (a per-match switch restores them), full trims already in R2 are removed by the next push once their rendition is there, and hosted plays and waveforms a mirror from `_web.mp4` + `.params.json`.

**Architecture:** A `full_media` flag in `auto_sync.json` feeds `build_push_plan` and a new gc step in `run_push`; the hosted delete route widens to exactly `*_trimmed.mp4`. On hosted, three read paths (clip anchor, `stream_video`, `ensure_audit_audio`) fall back to the rendition when the full trim object is absent and the match is a mirror. SyncCard gets a Web / Full switch and a pending-removal line.

**Tech Stack:** Python 3.11, FastAPI, httpx, moto S3 (tests), ffmpeg (integration tests), React + vitest.

**Spec:** `docs/superpowers/specs/2026-09-27-web-only-mirror-media-design.md`

## Global Constraints

- `uv` only; Black 110, ruff clean; type hints; `pathlib.Path`.
- No new dependencies. No module-level `splitsmith.db` import on the local `create_app` path.
- Hosted-native matches (origin `hosted`) keep today's behaviour exactly: `kind=trim` never substitutes the rendition.
- The hosted delete route never deletes `_web.mp4` or `.params.json`.
- A clip without a rendition keeps uploading its full trim.
- UI primitives only (`Segmented`); ASCII punctuation, no dashes as punctuation in copy.
- Targeted test files with `-n0`; never the full suite on this Mac.

## Review Focus

- A clip whose `_web.mp4` transcode failed: its full trim must still upload and never be gc'd. (Task 1 and Task 2 tests.)
- Toggling full media off then on: removed trims re-upload on the next push. (Task 2.)
- A hosted-native match with no trim: `kind=trim` still 404s, `kind=auto` still goes to source. (Task 3 guard test.)
- A mirror trim cut with a non-default pre-buffer: the anchor must come from `.params.json`, not the project default. (Task 3.)
- Status: web-only must not show "N files changed" forever for the skipped full trims. (Task 1.)

---

### Task 1: Push plan honours `full_media`

**Files:**
- Modify: `src/splitsmith/sync/auto_state.py` (`AutoSyncPrefs.full_media`)
- Modify: `src/splitsmith/sync/plan.py` (`build_push_plan`)
- Modify: `src/splitsmith/sync/push.py` (`run_push`), `src/splitsmith/sync/run.py` (`run_sync` preflight)
- Modify: `src/splitsmith/ui/server.py` (`get_match_sync_status`)
- Test: `tests/test_sync_plan.py`, `tests/test_sync_auto_state.py`

**Interfaces:**
- Produces: `AutoSyncPrefs.full_media: bool = False`; `build_push_plan(match_root, *, sync_state, full_media: bool = True)` (the pure function keeps the old default; callers that act for a match pass the per-match value); `plan.TRIMMED_SUFFIX = "_trimmed.mp4"`, `plan.WEB_SUFFIX = "_web.mp4"`; `plan.web_key_for(trim_key: str) -> str`.

- [ ] **Step 1: Failing tests** (append to `tests/test_sync_plan.py`)

```python
def test_web_only_plan_skips_the_full_trim_but_keeps_params_and_web(tmp_path: Path) -> None:
    root, slug = _build_basic_match(tmp_path)
    (root / "shooters" / slug / "trimmed" / WEB_NAME).write_bytes(b"w" * 64)

    plan = build_push_plan(root, sync_state=SyncState(), full_media=False)

    names = sorted(m.remote_key.rsplit("/", 1)[1] for m in plan.media if "/trimmed/" in m.remote_key)
    assert not any(n.endswith("_trimmed.mp4") for n in names)
    assert any(n.endswith("_web.mp4") for n in names)
    assert any(n.endswith(".params.json") for n in names)


def test_web_only_plan_keeps_the_full_trim_when_there_is_no_rendition(tmp_path: Path) -> None:
    """A failed transcode must never leave hosted with nothing to play."""
    root, _ = _build_basic_match(tmp_path)

    plan = build_push_plan(root, sync_state=SyncState(), full_media=False)

    assert any(m.remote_key.endswith("_trimmed.mp4") for m in plan.media)
```

and in `tests/test_sync_auto_state.py`:

```python
def test_full_media_defaults_off_and_round_trips(tmp_path: Path) -> None:
    assert load_auto_prefs(tmp_path).full_media is False
    update_auto_prefs(tmp_path, lambda p: setattr(p, "full_media", True))
    assert load_auto_prefs(tmp_path).full_media is True
```

- [ ] **Step 2: Run, expect FAIL** (`TypeError: unexpected keyword 'full_media'`; `AttributeError`)

`uv run pytest tests/test_sync_plan.py tests/test_sync_auto_state.py -n0 -q`

- [ ] **Step 3: Implement**

`auto_state.py`, in `AutoSyncPrefs` after `enabled`:

```python
    #: Upload full-resolution audit trims too (spec 2026-09-27 v1.1). Off:
    #: hosted plays the 720p rendition and full trims leave R2.
    full_media: bool = False
```

`plan.py`: module constants and helper near the globs:

```python
TRIMMED_SUFFIX = "_trimmed.mp4"
WEB_SUFFIX = "_web.mp4"


def web_key_for(trim_key: str) -> str:
    """The rendition's remote key for a full trim's remote key."""
    return trim_key[: -len(TRIMMED_SUFFIX)] + WEB_SUFFIX
```

`build_push_plan(match_root: Path, *, sync_state: SyncState, full_media: bool = True)`; in the trimmed loop replace `candidates = [clip_path]` with:

```python
                web = clip_path.with_name(clip_path.name.replace(TRIMMED_SUFFIX, WEB_SUFFIX))
                has_web = web.exists() and web.stat().st_size > 0
                # Web-only (v1.1): the rendition + params stand in for the
                # trim on hosted. A clip whose transcode failed keeps its
                # full trim so hosted never has neither.
                candidates = [clip_path] if (full_media or not has_web) else []
```

and use `has_web` for the existing web append (drop the duplicated `web = ...` / exists check below it). Update the module docstring's first paragraph to mention the flag.

`push.py` `run_push(..., full_media: bool | None = None)`: at the top of the plan phase

```python
        if full_media is None:
            full_media = load_auto_prefs(match_root).full_media
```

and pass `full_media=full_media` to `build_push_plan`. `run.py` `run_sync(..., full_media: bool | None = None)`: resolve the same way once, pass it to the preflight `build_push_plan` and to `run_push`. Import `load_auto_prefs` from `.auto_state` in both.

`server.py` `get_match_sync_status`: build the plan with `full_media=load_auto_prefs(match_root).full_media`.

- [ ] **Step 4: Run, expect PASS**

`uv run pytest tests/test_sync_plan.py tests/test_sync_auto_state.py tests/test_sync_push.py tests/test_sync_local_endpoints.py tests/test_sync_integration.py -n0 -q`

`test_sync_push.py` / integration tests that call `run_push` on a match with a full trim and no rendition keep uploading it (no-rendition fallback); any test whose match has a `_web.mp4` and asserts a full-trim upload must pass `full_media=True` explicitly. Fix such tests that way, never by changing the defaults.

- [ ] **Step 5: Commit** `feat(sync): push plan skips full trims unless full media is on`

---

### Task 2: Remove full trims from R2 in the push gc; widen the hosted delete route

**Files:**
- Modify: `src/splitsmith/sync/push.py` (gc phase)
- Modify: `src/splitsmith/ui/sync_api.py` (`delete_media`)
- Test: `tests/test_sync_push.py`, `tests/test_sync_media_api.py`

**Interfaces:**
- Consumes: `TRIMMED_SUFFIX`, `web_key_for` (Task 1); `full_media` resolved in `run_push` (Task 1).

- [ ] **Step 1: Failing tests** (`tests/test_sync_push.py`; reuse `_build_match` / `_FakeHosted` and read `_build_match` first for the trimmed file names)

```python
def _add_web(root: Path) -> None:
    for trim in (root / "shooters").glob("*/trimmed/*_trimmed.mp4"):
        trim.with_name(trim.name.replace("_trimmed.mp4", "_web.mp4")).write_bytes(b"w" * 32)


def test_web_only_push_removes_full_trims_whose_rendition_is_on_hosted(tmp_path: Path) -> None:
    root, match_id = _build_match(tmp_path)
    _add_web(root)
    fake = _FakeHosted()
    run_push(root, client=fake.clients(), full_media=True)
    trim_keys = [k for k in load_sync_state(root).items if k.endswith("_trimmed.mp4")]
    assert trim_keys

    report = run_push(root, client=fake.clients(), full_media=False)

    for key in trim_keys:
        assert f"media_delete:{key}" in fake.calls
        assert key not in load_sync_state(root).items
    assert report.media_deleted == len(trim_keys)
    assert not any(c.startswith("media_delete:") and c.endswith("_web.mp4") for c in fake.calls)


def test_a_full_trim_without_a_pushed_rendition_is_never_removed(tmp_path: Path) -> None:
    root, _ = _build_match(tmp_path)
    fake = _FakeHosted()
    run_push(root, client=fake.clients(), full_media=True)
    run_push(root, client=fake.clients(), full_media=False)
    assert not any(c.startswith("media_delete:") for c in fake.calls)


def test_full_trim_removal_failure_is_retried_and_full_media_reuploads(tmp_path: Path) -> None:
    root, _ = _build_match(tmp_path)
    _add_web(root)
    fake = _FakeHosted()
    run_push(root, client=fake.clients(), full_media=True)
    trim_keys = [k for k in load_sync_state(root).items if k.endswith("_trimmed.mp4")]

    fake.delete_status = 500
    run_push(root, client=fake.clients(), full_media=False)
    assert set(trim_keys) <= set(load_sync_state(root).items)

    fake.delete_status = 200
    run_push(root, client=fake.clients(), full_media=False)
    assert not set(trim_keys) & set(load_sync_state(root).items)

    fake.calls.clear()
    run_push(root, client=fake.clients(), full_media=True)
    for key in trim_keys:
        assert f"media_create:{key}" in fake.calls
```

`tests/test_sync_media_api.py`: replace `test_delete_media_rejects_trimmed_keys` with

```python
def test_delete_media_removes_a_full_trim_but_never_its_rendition_or_params(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    """Web-only mirrors (v1.1): the desktop removes full trims once the
    rendition is on R2. The rendition and the params sidecar are what the
    mirror plays and anchors from, so this route can never delete them."""
    client, sender, captured = hosted_app_with_storage
    storage = _login_and_adopt(client, sender, captured)
    base = f"matches/{MATCH_ID}/shooters/{SLUG}/trimmed/stage1_cam_abc123"

    storage.write_bytes(f"{base}_trimmed.mp4", b"full")
    resp = client.post(DELETE_URL, json={"key": f"{base}_trimmed.mp4"})
    assert resp.status_code == 200, resp.text
    assert not storage.exists(f"{base}_trimmed.mp4")

    for keep in (f"{base}_web.mp4", f"{base}_trimmed.params.json"):
        assert client.post(DELETE_URL, json={"key": keep}).status_code == 422, keep
```

- [ ] **Step 2: Run, expect FAIL** (no deletes issued; route 422s the full trim)

`uv run pytest tests/test_sync_push.py tests/test_sync_media_api.py -n0 -q`

- [ ] **Step 3: Implement**

`push.py` gc phase, after the beep_review block and before `if stale:` saves (fold into one list so the save logic stays single):

```python
        if not full_media:
            # Web-only (v1.1): a full trim whose rendition is on hosted is a
            # redundant copy there; the desktop keeps the original.
            stale += [
                key
                for key in list(sync_state.items)
                if key.endswith(TRIMMED_SUFFIX) and web_key_for(key) in sync_state.items
            ]
```

(The existing loop deletes each key, drops it from `sync_state.items` on success and keeps it on failure; reuse it unchanged. Update its comment to cover both cases.)

`sync_api.py` `delete_media`: replace the beep_review-only guard with

```python
    name = body.key.rsplit("/", 1)[1]
    if "/beep_review/" not in body.key and not (
        "/trimmed/" in body.key and name.endswith("_trimmed.mp4")
    ):
        raise HTTPException(status_code=422, detail="delete is beep_review or full-trim only")
```

and update the docstring: beep_review snippets (#821) and, for web-only mirrors, full trims; renditions and params sidecars are never deletable here.

- [ ] **Step 4: Run, expect PASS** (same command plus `tests/test_sync_integration.py`)

- [ ] **Step 5: Commit** `feat(sync): web-only pushes remove full trims from hosted`

---

### Task 3: Hosted plays and waveforms a mirror from the rendition

**Files:**
- Modify: `src/splitsmith/ui/audio.py` (new `try_pull_web_trim`, `ensure_audit_audio(web_fallback=...)`)
- Modify: `src/splitsmith/ui/server.py` (`_video_trim_anchor`, `stream_video` hosted branch, `_resolve_audit_audio`)
- Test: `tests/test_sync_integration.py` (integration, real ffmpeg)

**Interfaces:**
- Produces: `audio.try_pull_web_trim(project, trim_path: Path) -> Path | None` (pulls `_web.mp4` and the trim's `.params.json` when absent locally; returns the local web path or None); `ensure_audit_audio(..., web_fallback: bool = False)`; server helper `_is_mirror() -> bool` (`current_match_origin.get() == "desktop"`).

- [ ] **Step 1: Failing tests** (`tests/test_sync_integration.py`, next to the existing tests; uses `tests/synthetic_media.py`)

```python
def _web_only_mirror(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict], tmp_path: Path, *, pre_buffer: float
) -> tuple[TestClient, str, str, str]:
    """Push a match whose only media on hosted is the rendition + params.
    Returns ``(client, match_id, video_path, web_name)``."""
    from tests.synthetic_media import build_synthetic_video, ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    client, sender, captured = hosted_app_with_storage
    match_root, video_path, trimmed_name = _build_local_match(tmp_path)
    shooter_root = match_model.Match.shooter_root(match_root, SLUG)
    project = MatchProject.load(shooter_root)
    project.stages[0].videos[0].beep_time = 12.0
    project.save(shooter_root)
    trimmed = shooter_root / "trimmed" / trimmed_name
    web = trimmed.with_name(trimmed_name.replace("_trimmed.mp4", "_web.mp4"))
    build_synthetic_video(web)
    trimmed.with_name(f"{trimmed.stem}.params.json").write_text(
        json.dumps(
            {"beep_time": 12.0, "stage_time_seconds": 12.0, "pre_buffer_seconds": pre_buffer,
             "post_buffer_seconds": 1.0}
        ),
        encoding="utf-8",
    )
    login(client, sender, EMAIL)
    client.get("/api/me/recent-projects")
    storage: S3Storage = captured["storage"]
    raw_token = client.post("/api/me/desktop-tokens", json={"name": "web box"}).json()["token"]
    sync_http = TestClient(client.app, base_url="http://testserver",
                           headers={"Authorization": f"Bearer {raw_token}"}, follow_redirects=False)
    sync_client = HostedSyncClient(
        http=sync_http, media_http=httpx.Client(transport=httpx.MockTransport(_media_handler(storage)))
    )
    run_push(match_root, client=sync_client, full_media=False)
    match_id = match_model.Match.load(match_root).match_id
    assert not any(k.endswith("_trimmed.mp4") for k in storage.list_keys(f"matches/{match_id}/"))
    return client, match_id, video_path, web.name


def test_web_only_mirror_streams_the_rendition_for_trim_and_auto(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict], tmp_path: Path
) -> None:
    client, match_id, video_path, web_name = _web_only_mirror(hosted_app_with_storage, tmp_path, pre_buffer=2.0)
    for kind in ("trim", "auto", "web"):
        resp = client.get(
            f"/api/matches/{match_id}/shooters/{SLUG}/videos/stream", params={"path": video_path, "kind": kind}
        )
        assert resp.status_code == 307, (kind, resp.text)
        assert web_name in resp.headers["location"], kind


def test_web_only_mirror_anchor_comes_from_the_pushed_params(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict], tmp_path: Path
) -> None:
    """pre_buffer 2.0 differs from the project default, so a default-based
    anchor would land every marker off by the difference."""
    client, match_id, _, _ = _web_only_mirror(hosted_app_with_storage, tmp_path, pre_buffer=2.0)
    coach = client.get(f"/api/matches/{match_id}/shooters/{SLUG}/stages/1/coach")
    assert coach.status_code == 200, coach.text
    (entry,) = [v for v in coach.json()["videos"] if v.get("role") == "primary"]
    assert entry["kind"] == "web"
    assert entry["beep_in_clip"] == 2.0


def test_web_only_mirror_serves_stage_peaks(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict], tmp_path: Path
) -> None:
    client, match_id, _, _ = _web_only_mirror(hosted_app_with_storage, tmp_path, pre_buffer=2.0)
    peaks = client.get(f"/api/matches/{match_id}/shooters/{SLUG}/stages/1/peaks")
    assert peaks.status_code == 200, peaks.text
    assert peaks.json()["trimmed"] is True
```

Before running: confirm the coach route path and its `videos[]` field names (`grep -n '"/api/shooters/{slug}/stages/{stage_number}/coach"' src/splitsmith/ui/server.py` and `_coach_video_entries`), the peaks response keys, and `S3Storage`'s listing method name; adjust the test to the real names without changing what it asserts.

Guard (hosted-native, not a mirror): append to the test file that owns native hosted stream tests (grep `kind": "trim"` under `tests/`), or add here using the existing native-match seeding helper from `tests/hosted_helpers.py`:

```python
def test_hosted_native_match_without_trim_still_404s_kind_trim(...) -> None:
    # seed a native hosted match whose stage has a primary video and a
    # _web.mp4 in storage but no _trimmed.mp4; GET stream kind=trim -> 404
```

Read `tests/hosted_helpers.py` for the native seeding helper and write this test concretely in the same style as the mirror tests above.

- [ ] **Step 2: Run, expect FAIL**

`uv run pytest tests/test_sync_integration.py -n0 -q -k "web_only or native"`

- [ ] **Step 3: Implement**

`audio.py`, after `_try_pull_trim_from_storage`:

```python
def try_pull_web_trim(project: MatchProject | None, trim_path: Path) -> Path | None:
    """The rendition standing in for the audit trim at ``trim_path`` on a
    web-only mirror (v1.1): pull ``_web.mp4`` and the trim's params sidecar
    from storage when not already local. Returns the local rendition path,
    or None when storage has no rendition. Best-effort like the trim pull."""
    from .. import trim as trim_module

    web = trim_module.web_trim_path(trim_path)
    params = trim_params_path(trim_path)
    key = _storage_trim_key(project, web)
    if key is None:
        return web if web.exists() and web.stat().st_size > 0 else None
    storage = project._storage  # type: ignore[union-attr]
    params_key = f"{key.rsplit('/', 1)[0]}/{params.name}"
    try:
        web.parent.mkdir(parents=True, exist_ok=True)
        if not (web.exists() and web.stat().st_size > 0):
            if not storage.exists(key):
                return None
            with storage.open_stream(key) as src, web.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        if not params.exists() and storage.exists(params_key):
            with storage.open_stream(params_key) as src, params.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        return web
    except Exception as exc:
        logger.info("web trim cache: pull from %s failed: %s", key, exc)
        web.unlink(missing_ok=True)
        return None
```

`ensure_audit_audio(..., web_fallback: bool = False)`: after the existing trimmed branch and before the source fallback:

```python
    if web_fallback:
        # Web-only mirror (v1.1): the rendition covers the trim's exact
        # window, so its audio is the audit audio and the anchor is the
        # trim's (from the pushed params sidecar).
        web = try_pull_web_trim(project, trimmed_video)
        if web is not None:
            audio_path = audit_audio_path(project_root, stage_number, project=project)
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            if not audio_path.exists() or audio_path.stat().st_mtime < web.stat().st_mtime:
                if not _try_pull_audio_from_storage(project, audio_path):
                    _extract_audio(web, audio_path, sample_rate, ffmpeg_binary)
                    _try_push_audio_to_storage(project, audio_path)
            pre_buffer = trim_pre_buffer_seconds_for(trimmed_video, default=project.trim_pre_buffer_seconds)
            beep_in_clip = min(primary_beep_time, pre_buffer) if primary_beep_time is not None else None
            return AuditAudioResult(audio_path=audio_path, beep_in_clip=beep_in_clip, trimmed=True)
```

`server.py`:
- `_is_mirror()` next to `_may_mint_shot_ids`: `return current_match_origin.get() == "desktop"`.
- `_resolve_audit_audio`: pass `web_fallback=_is_mirror()`.
- `_video_trim_anchor`, before the final `return (video.beep_time, "source", None)`:

```python
        if _is_mirror() and audio_helpers.try_pull_web_trim(project, trimmed) is not None:
            # Web-only mirror (v1.1): the rendition + params stand in for
            # the trim; _video_clip_anchor promotes the kind to "web".
            if video.beep_time is None:
                return (None, "trim", trimmed)
            pre_buffer = audio_helpers.trim_pre_buffer_seconds_for(
                trimmed, default=project.trim_pre_buffer_seconds
            )
            return (min(video.beep_time, pre_buffer), "trim", trimmed)
```

  Check `_video_clip_anchor`'s promotion (`web_trim_available`) returns True for this case; it does when the rendition is local or in storage.
- `stream_video` hosted branch, inside `if kind in ("auto", "trim", "web") and stage is not None:` after the full-trim `serve_media` and before the `kind == "trim"` 404:

```python
                if _is_mirror():
                    # Web-only mirror (v1.1): the rendition is the trim.
                    web_resp = _hosted_web_redirect(storage, project, root, stage, video)  # type: ignore[arg-type]
                    if web_resp is not None:
                        return web_resp
```

- [ ] **Step 4: Run, expect PASS**

`uv run pytest tests/test_sync_integration.py tests/test_mirror_read_only.py tests/test_ui_server.py -n0 -q`

- [ ] **Step 5: Slim import check** (build `create_app()` with `splitsmith.db`/`sqlalchemy` imports blocked, as on the v1 branch) and commit `feat(hosted): web-only mirrors play and waveform from the rendition`

---

### Task 4: SyncCard Web / Full switch and pending-removal line

**Files:**
- Modify: `src/splitsmith/ui/auto_sync.py` (`status_for`), `src/splitsmith/ui/server.py` (`AutoSyncSettingRequest`, `put_match_auto_sync`)
- Modify: `src/splitsmith/ui_static/src/lib/api.ts`, `src/splitsmith/ui_static/src/components/match/SyncCard.tsx`
- Test: `tests/test_auto_sync_service.py`, `src/splitsmith/ui_static/src/components/match/SyncCard.test.tsx`

**Interfaces:**
- Produces: `status_for` adds `full_media: bool` and `full_trims_on_hosted: int` (count of `sync_state.items` keys ending `_trimmed.mp4`); `PUT /api/match/sync/auto` accepts `{"enabled"?: bool | null, "full_media"?: bool}` and only changes fields present; `api.setAutoSync(patch: { enabled?: boolean | null; full_media?: boolean })`.

- [ ] **Step 1: Failing tests**

`tests/test_auto_sync_service.py`:

```python
def test_status_reports_full_media_and_full_trims_on_hosted(tmp_path: Path) -> None:
    svc, root = _service(tmp_path, lambda prefs: {}, [])
    state = SyncState(
        last_synced_at=datetime(2026, 9, 27, tzinfo=UTC),
        items={
            "matches/m/shooters/a/trimmed/s1_cam_x_trimmed.mp4": {"sha256": "a", "size": 1, "mtime_ns": 1},
            "matches/m/shooters/a/trimmed/s1_cam_x_web.mp4": {"sha256": "b", "size": 1, "mtime_ns": 1},
        },
    )
    save_sync_state(root, state)
    status = svc.status_for(root)
    assert status["full_media"] is False
    assert status["full_trims_on_hosted"] == 1


def test_put_auto_changes_only_the_fields_sent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_SYNC", "1")
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    assert client.put("/api/match/sync/auto", json={"enabled": False}).status_code == 200
    body = client.put("/api/match/sync/auto", json={"full_media": True}).json()
    assert body["full_media"] is True and body["setting"] is False
```

(Adjust the `items` literal to `SyncedItem(...)` if `SyncState` does not coerce dicts.)

`SyncCard.test.tsx`:

```tsx
it("switches a match to full media and says what web-only will remove", async () => {
  vi.mocked(api.getSyncStatus).mockResolvedValue(makeStatus());
  vi.mocked(api.getAutoSync).mockResolvedValue(makeAuto({ full_media: false, full_trims_on_hosted: 3 }));
  vi.mocked(api.setAutoSync).mockResolvedValue(makeAuto({ full_media: true, full_trims_on_hosted: 3 }));
  render(<SyncCard jobs={[]} matchId="m1" />);
  expect(await screen.findByText(/3 full trims will be removed from hosted/)).toBeInTheDocument();
  const group = screen.getByRole("group", { name: "Media on hosted" });
  fireEvent.click(within(group).getByRole("button", { name: "Full" }));
  await waitFor(() => expect(api.setAutoSync).toHaveBeenCalledWith({ full_media: true }));
});
```

(`makeAuto` gains `full_media: false, full_trims_on_hosted: 0` defaults; the existing test asserting `setAutoSync(false)` changes to `setAutoSync({ enabled: false })`.)

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement**

- `status_for`: add `"full_media": auto.full_media` and `"full_trims_on_hosted": sum(1 for k in sync_state.items if k.endswith("_trimmed.mp4"))`.
- `AutoSyncSettingRequest`: `model_config = ConfigDict(extra="forbid")`, fields `enabled: bool | None = None`, `full_media: bool | None = None`; `put_match_auto_sync` uses `req.model_fields_set`: set `enabled` only when `"enabled" in req.model_fields_set`, `full_media` only when present and not None. `put_global_auto_sync` keeps reading `enabled`.
- `api.ts`: `AutoSyncStatus` gains `full_media: boolean; full_trims_on_hosted: number`; `setAutoSync(patch)` sends the patch object.
- `SyncCard.tsx`: `handleAutoChange` sends `{ enabled: on ? null : false }`; new `handleMediaChange(full: boolean)` sends `{ full_media: full }`. Beside the Sync mode control, when `auto` is loaded and the match has synced:

```tsx
<Segmented
  label="Media on hosted"
  value={auto.full_media ? "full" : "web"}
  options={[
    { value: "web", label: "Web" },
    { value: "full", label: "Full" },
  ]}
  onChange={(v) => void handleMediaChange(v === "full")}
/>
```

  In `SyncStatusLine`, before the final "Synced" branch:

```tsx
  if (auto && !auto.full_media && auto.full_trims_on_hosted > 0) {
    const n = auto.full_trims_on_hosted;
    return (
      <p className={lineClass}>
        <RefreshCw className="size-3.5 shrink-0" aria-hidden="true" />
        {n} full trim{n === 1 ? "" : "s"} will be removed from hosted on the next sync
      </p>
    );
  }
```

- [ ] **Step 4: Run, expect PASS**

`uv run pytest tests/test_auto_sync_service.py -n0 -q` and `cd src/splitsmith/ui_static && pnpm vitest run src/components/match && pnpm typecheck`

- [ ] **Step 5: Commit** `feat(ui): web or full media per match on SyncCard`

---

### Task 5: Docs and verification

- [ ] **Step 1:** CLAUDE.md: in "Hosted playback streams the web rendition (#1031)", add a paragraph: web-only mirrors (v1.1) have no `_trimmed.mp4` on R2 by default; on a mirror only, `kind=trim` and `kind=auto` fall back to the rendition, the anchor comes from the pushed `.params.json`, and audit audio is extracted from the rendition; hosted-native matches keep "`kind=trim` never substitutes". In "Desktop auto-sync", replace the closing "Automatic pushes still upload full trims" paragraph with the `full_media` flag, the gc rule (only when the rendition key is recorded) and the delete route's widened, rendition-safe allowlist.
- [ ] **Step 2:** Spec status line: `Status: implemented`.
- [ ] **Step 3:** Run every test file touched in Tasks 1-4 plus `tests/test_sync_integration.py`, `tests/test_mirror_read_only.py`, `tests/test_ui_server.py`; SPA `pnpm vitest run` and `pnpm typecheck`. Report counts.
- [ ] **Step 4:** Commit `docs: web-only mirror media`.
