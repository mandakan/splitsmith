# Hosted Audit Scrubs the 720p Rendition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On hosted-native matches the Audit players stream the trim's 720p rendition instead of the full trim, through a new `kind=scrub` pin that both modes share.

**Architecture:** One pure freshness rule (`audio.fresh_rendition`) over `(size, mtime)` facts feeds the local file check, the hosted stream route (`storage.stat`) and the hosted payload (`StoragePresence` listing, which gains object metadata). `stream_video` gets `kind=scrub` (fresh rendition, else trim, else 404); local `kind=web` goes back to falling through to the source. The SPA swaps `"web"` for `"scrub"` in its scrub choice.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest, moto; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-06-hosted-audit-scrub-design.md`

## Global Constraints

- `kind=trim` never substitutes the rendition on a native match. Existing tests `test_trim_kind_never_serves_web_rendition`, `test_hosted_native_match_without_a_trim_still_404s_kind_trim` and `test_local_trim_kind_never_serves_the_rendition` stay green unchanged.
- Hosted `kind=web` behaviour is unchanged; its existing tests stay green unchanged.
- `scrub_version` format stays `f"{mtime_ns:x}-{size:x}"`; hosted takes `mtime_ns` from `last_modified` (`int(last_modified.timestamp() * 1e9)`).
- No new storage calls on the payload route beyond one listing of `<scope>/trimmed/` per request; the stream route's `kind=scrub` uses at most two `storage.stat` calls.
- The alias route `stream_shooter_video` is not changed (no caller sends `scrub`; spec amended).
- No new dependencies. Black 110, ruff, type hints. SPA: primitives only.
- Tests: `uv run pytest <file> -n0 -q` while iterating; the full suite before the last commit. Before a full run, clear the numba cache: `/usr/bin/find .venv \( -name "*.nbi" -o -name "*.nbc" \) -delete` (the `rtk` wrapper refuses a compound `find`). SPA: `cd src/splitsmith/ui_static && corepack pnpm vitest run <file>`, plus `corepack pnpm typecheck` and `corepack pnpm lint`.
- Every new test gets a mutation drill: commit first, break the change, watch the test fail, restore with `/usr/bin/git checkout -- <file>`.

## Review Focus

1. **A hosted re-cut in progress** (trim deleted, rendition deleted, new trim written, rendition not yet). Expected: `scrub_version` null in that window, `kind=scrub` serves the new trim, and with neither object a 404, never the source. Pinned in Task 3 (no-trim 404, stale rendition) and Task 4 (stale -> null).
2. **Equal timestamps.** R2/moto `LastModified` has one-second resolution, so a rendition uploaded within the same second as its trim must count as fresh (`>=`). Pinned in Task 1 (equal mtimes fresh) and Task 4 (same-second write fresh).
3. **A mirror** (desktop-origin match, no trim on R2). Expected: `scrub` redirects to the pushed rendition, and `scrub_version` is set from it. Pinned in Task 3 and Task 4.
4. **Storage listing failure on the payload route.** Expected: `scrub_version` null for that shooter, the payload still returns 200 (a broken listing must not take the project page down). Pinned in Task 4.
5. **An old SPA build against the new server** (sends `kind=web` from Audit). Expected: it still plays (web -> trim -> source), just without the pin. Covered by Task 2's local `web` fall-through test and hosted `web` being unchanged.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/splitsmith/ui/audio.py` | modify | `fresh_rendition` (pure rule); `fresh_web_trim` uses it |
| `src/splitsmith/ui/presence.py` | modify | keep `StorageObject` per key; `object(key)` |
| `src/splitsmith/ui/server.py` | modify | `stream_video` `kind=scrub`; local `web` fall-through; hosted `scrub_version` in the payload |
| `tests/test_scrub_rendition.py` | modify | local `scrub` / `web` contract |
| `tests/test_hosted_scrub.py` | create | `fresh_rendition`, presence metadata, hosted stream and payload |
| `src/splitsmith/ui_static/src/lib/scrubSource.ts`, `lib/api.ts`, `pages/Audit.tsx`, `pages/MobileAudit.tsx` + tests | modify | `"scrub"` in place of `"web"` |
| `CLAUDE.md` | modify | the three kinds |

---

### Task 1: The freshness rule

**Files:**
- Modify: `src/splitsmith/ui/audio.py` (`fresh_web_trim`, line ~891)
- Create: `tests/test_hosted_scrub.py`

**Interfaces:**
- Produces: `audio.fresh_rendition(trim: tuple[int, float] | None, web: tuple[int, float] | None, *, trim_required: bool) -> bool` where each tuple is `(size_bytes, mtime_seconds)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hosted_scrub.py`:

```python
"""Hosted Audit scrubs the 720p rendition (#1209).

``kind=scrub`` is the Audit players' pin in both modes: a fresh rendition,
else the trim, else 404 -- never the source. Fresh is one rule
(``audio.fresh_rendition``) over the trim's and the rendition's size and
mtime, from local files, ``storage.stat`` or the per-request presence
listing. A mirror has no trim on R2 by design, so its rendition is fresh
whenever present.
"""

from __future__ import annotations

from splitsmith.ui import audio as audio_helpers


def test_fresh_rendition_rule() -> None:
    rule = audio_helpers.fresh_rendition
    assert rule((10, 100.0), (5, 101.0), trim_required=True) is True
    assert rule((10, 100.0), (5, 100.0), trim_required=True) is True  # same second
    assert rule((10, 101.0), (5, 100.0), trim_required=True) is False  # older than the trim
    assert rule((10, 100.0), (0, 101.0), trim_required=True) is False  # empty
    assert rule((10, 100.0), None, trim_required=True) is False
    assert rule(None, (5, 101.0), trim_required=True) is False  # nothing to anchor it
    assert rule(None, (5, 101.0), trim_required=False) is True  # a mirror
    assert rule(None, (0, 101.0), trim_required=False) is False
```

Run: `uv run pytest tests/test_hosted_scrub.py -n0 -q`
Expected: FAIL, `AttributeError: ... has no attribute 'fresh_rendition'`.

- [ ] **Step 2: Implement and route `fresh_web_trim` through it**

In `src/splitsmith/ui/audio.py`, directly above `def fresh_web_trim`:

```python
def fresh_rendition(
    trim: tuple[int, float] | None,
    web: tuple[int, float] | None,
    *,
    trim_required: bool,
) -> bool:
    """Whether a trim's web rendition is current (#1192, #1209).

    Each fact is ``(size_bytes, mtime_seconds)`` or ``None`` when absent.
    Current means present, non-empty and -- when ``trim_required`` -- the
    trim present and the rendition not older than it: a re-cut writes the
    trim first and the rendition after it, so an older rendition covers
    the previous window. Equal mtimes are current (R2 timestamps have
    one-second resolution). ``trim_required`` is false only on a mirror,
    which has no trim on R2 by design. The one rule local files,
    ``storage.stat`` and the presence listing all go through.
    """
    if web is None or web[0] == 0:
        return False
    if not trim_required:
        return True
    return trim is not None and web[1] >= trim[1]
```

Replace the body of `fresh_web_trim` after the docstring with:

```python
    from .. import trim as trim_module

    web = trim_module.web_trim_path(trimmed)
    try:
        web_st = web.stat()
        trim_st = trimmed.stat()
    except OSError:
        return None
    if not fresh_rendition(
        (trim_st.st_size, trim_st.st_mtime), (web_st.st_size, web_st.st_mtime), trim_required=True
    ):
        return None
    return web
```

- [ ] **Step 3: Run**

Run: `uv run pytest tests/test_hosted_scrub.py tests/test_scrub_rendition.py -n0 -q`
Expected: PASS (the local freshness tests in `test_scrub_rendition.py` are unchanged and still pass).

- [ ] **Step 4: Commit, then drill**

```bash
git add src/splitsmith/ui/audio.py tests/test_hosted_scrub.py
git commit -m "refactor(audit): one freshness rule for a trim's rendition (#1209)"
```

Drill: change `>=` to `>` -> the same-second assertion fails; drop `or web[0] == 0` -> the empty assertion fails.

---

### Task 2: `kind=scrub` locally; local `kind=web` falls back to the source again

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`stream_video`, the `kind` Literal ~line 14124, the local branch ~14225-14265, the docstring's kind list)
- Modify: `tests/test_scrub_rendition.py`

**Interfaces:**
- Produces: `GET .../videos/stream?kind=scrub` (local): fresh rendition, else trim, else 404.

- [ ] **Step 1: Move the pin tests to `scrub`, pin `web`'s fall-through**

In `tests/test_scrub_rendition.py`:
- In `test_local_web_kind_serves_a_fresh_rendition`, `test_local_web_kind_serves_the_trim_when_the_rendition_is_stale`, `test_local_web_kind_serves_the_trim_without_a_rendition`, change `"web"` to `"scrub"` in the `_stream` calls and rename `web_kind` -> `scrub_kind` in the test names.
- Replace `test_local_web_kind_ignores_an_orphan_rendition` with:

```python
def test_local_scrub_kind_ignores_an_orphan_rendition(tmp_path: Path) -> None:
    """No trim: the rendition has nothing to anchor it. ``scrub`` is a trim
    pin, so it 404s like ``trim`` rather than playing the source."""
    client, base, _trim, web = _bootstrap(tmp_path)
    web.write_bytes(b"orphan web")
    assert _status(client, base, "scrub") == 404
    assert _stream(client, base, "auto") == b"source bytes"


def test_local_web_kind_falls_back_to_the_source(tmp_path: Path) -> None:
    """``web`` means the same as on hosted: the rendition, else the trim,
    else the source -- an older client sending it still plays."""
    client, base, trim, web = _bootstrap(tmp_path)
    assert _stream(client, base, "web") == b"source bytes"
    trim.write_bytes(b"trim bytes")
    assert _stream(client, base, "web") == b"trim bytes"
```

- In `test_local_web_kind_404s_while_a_trim_is_recut`, rename to `test_local_scrub_kind_404s_while_a_trim_is_recut` and use `"scrub"` for the two `web` requests.

Run: `uv run pytest tests/test_scrub_rendition.py -n0 -q`
Expected: the `scrub` tests FAIL with 422 (unknown kind); `test_local_web_kind_falls_back_to_the_source` FAILS (404 today).

- [ ] **Step 2: Implement**

In `stream_video`, change the `kind` parameter to:

```python
        kind: Literal["auto", "trim", "source", "proxy", "web", "scrub"] = Query("auto"),
```

Add to the docstring's kind list, after the `web` bullet:

```
        - ``scrub``: the Audit players' pin (#1209): the trim's fresh 720p
          rendition, else the trim, else 404 -- never the source. A re-cut
          deletes both while it encodes, and a pinned player must error and
          remount on the new version, not play the source under trim
          offsets. On a mirror the pushed rendition stands in for the trim.
```

and change the `web` bullet's local half to "Local serves it from disk when it is fresh (#1192), else the trim, else the source."

In the local branch replace the comment block, the `kinds` tuple, the `if kind == "web":` test and the 404 condition:

```python
        # local mode: disk-based serving. ``web`` and ``scrub`` serve the
        # trim's fresh 720p rendition when there is one, else the trim;
        # ``trim`` never substitutes. ``trim`` and ``scrub`` are pins: no
        # trim is a 404, never the source (#1209).
        served_path: Path | None = None
        if kind in ("auto", "trim", "web", "scrub") and stage is not None:
            # Per-video short-GOP trim is keyed per role: each angle has
            # its own scrub clip cut around its own beep.
            trimmed = audio_helpers.pull_trimmed_video(root, stage.stage_number, video, project=project)
            if trimmed.exists():
                served_path = trimmed.resolve()
                if kind in ("web", "scrub"):
                    web = audio_helpers.fresh_web_trim(trimmed)
                    if web is not None:
                        served_path = web.resolve()
            elif _is_mirror():
```

(the `elif _is_mirror():` body is unchanged) and

```python
        if served_path is None:
            if kind == "trim" or (kind == "scrub" and stage is not None):
                raise HTTPException(
```

- [ ] **Step 3: Run**

Run: `uv run pytest tests/test_scrub_rendition.py tests/test_take_stream_stage.py tests/test_media_presign_serving.py -n0 -q`
Expected: PASS.

- [ ] **Step 4: Commit, then drill**

```bash
git add src/splitsmith/ui/server.py tests/test_scrub_rendition.py
git commit -m "feat(audit): kind=scrub is the Audit pin; local kind=web falls back to the source again (#1209)"
```

Drill: in the 404 condition put back `kind == "web"` for `"scrub"` -> the scrub 404 tests and the web fall-through test fail.

---

### Task 3: `kind=scrub` on hosted

**Files:**
- Modify: `src/splitsmith/ui/server.py` (the hosted branch of `stream_video`, ~14197-14222; a helper next to `_hosted_web_redirect` ~14101)
- Modify: `tests/test_media_presign_serving.py` (append)

**Interfaces:**
- Consumes: `audio.fresh_rendition` (Task 1); `Storage.stat(path) -> StorageObject | None`.
- Produces: `_storage_fact(obj: StorageObject | None) -> tuple[int, float] | None` in `server.py` (Task 4 reuses it).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_media_presign_serving.py`)

```python
# ---------------------------------------------------------------------------
# Tests: kind=scrub, the hosted Audit pin (#1209)
# ---------------------------------------------------------------------------


def test_scrub_kind_redirects_to_a_fresh_rendition(s3_stream_client: tuple[TestClient, S3Storage]) -> None:
    client, storage = s3_stream_client
    storage.write_bytes(_TRIM_KEY, b"TRIMDATA")
    storage.write_bytes(_WEB_KEY, b"WEBDATA")

    resp = client.get(_stream_url(SLUG), params={"path": "raw/clip.mp4", "kind": "scrub"})

    assert resp.status_code == 307
    assert f"stage1_cam_{_VIDEO_ID}_web.mp4" in resp.headers["location"]


def test_scrub_kind_serves_the_trim_over_a_stale_rendition(
    s3_stream_client: tuple[TestClient, S3Storage],
) -> None:
    import time

    client, storage = s3_stream_client
    storage.write_bytes(_WEB_KEY, b"OLD WINDOW")
    time.sleep(1.1)  # R2 / moto LastModified has one-second resolution
    storage.write_bytes(_TRIM_KEY, b"RE-CUT TRIM")

    resp = client.get(_stream_url(SLUG), params={"path": "raw/clip.mp4", "kind": "scrub"})

    assert resp.status_code == 307
    assert "_trimmed.mp4" in resp.headers["location"]


def test_scrub_kind_404s_without_a_trim_on_a_native_match(
    s3_stream_client: tuple[TestClient, S3Storage],
) -> None:
    """Mid re-cut: no trim yet. The pin errors; it never plays the source."""
    client, storage = s3_stream_client
    storage.write_bytes(_WEB_KEY, b"ORPHAN")

    resp = client.get(_stream_url(SLUG), params={"path": "raw/clip.mp4", "kind": "scrub"})

    assert resp.status_code == 404


def test_scrub_kind_on_a_mirror_serves_the_pushed_rendition(
    s3_stream_client: tuple[TestClient, S3Storage], monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith.ui import server as server_mod

    client, storage = s3_stream_client
    monkeypatch.setattr(server_mod, "_is_mirror", lambda: True)
    storage.write_bytes(_WEB_KEY, b"PUSHED WEB")

    resp = client.get(_stream_url(SLUG), params={"path": "raw/clip.mp4", "kind": "scrub"})

    assert resp.status_code == 307
    assert f"stage1_cam_{_VIDEO_ID}_web.mp4" in resp.headers["location"]
```

Run: `uv run pytest tests/test_media_presign_serving.py -n0 -q -k scrub`
Expected: the four FAIL (scrub falls to the source redirect today / 422 before Task 2).

- [ ] **Step 2: Implement**

Below `_hosted_web_redirect` in `server.py` add:

```python
    def _hosted_scrub(
        storage: Storage,
        project: MatchProject,
        root: Path,
        stage: Any,
        video: StageVideo,
    ) -> RedirectResponse:
        """``kind=scrub`` on hosted (#1209): the fresh rendition, else the
        trim, else 404 -- never the source. Two ``stat`` calls, the cost of
        the two ``exists`` the other kinds make."""
        local_mp4 = audio_helpers.trimmed_video_path(root, stage.stage_number, video, project=project)
        web_local = trim_module.web_trim_path(local_mp4)
        trim_key = audio_helpers._storage_trim_key(project, local_mp4)
        web_key = audio_helpers._storage_trim_key(project, web_local)
        if trim_key is None or web_key is None:
            raise HTTPException(status_code=404, detail="trimmed clip not built yet")
        trim_obj = storage.stat(trim_key)
        web_obj = storage.stat(web_key)
        if audio_helpers.fresh_rendition(
            _storage_fact(trim_obj), _storage_fact(web_obj), trim_required=not _is_mirror()
        ):
            return serve_media(storage, web_key, web_local, content_type="video/mp4")
        if trim_obj is not None:
            return serve_media(storage, trim_key, local_mp4, content_type="video/mp4")
        raise HTTPException(status_code=404, detail="trimmed clip not built yet")
```

At module level (next to `_scrub_version_for`) add:

```python
def _storage_fact(obj: StorageObject | None) -> tuple[int, float] | None:
    """``(size, mtime seconds)`` of a storage object for
    :func:`audio.fresh_rendition`; ``None`` when absent or undated."""
    if obj is None or obj.last_modified is None:
        return None
    return (obj.size, obj.last_modified.timestamp())
```

(import `StorageObject` from `..storage` alongside `Storage` if it is not imported yet.)

In the hosted branch of `stream_video`, first thing inside `if is_hosted:`:

```python
            if kind == "scrub" and stage is not None:
                return _hosted_scrub(storage, project, root, stage, video)  # type: ignore[arg-type]
```

- [ ] **Step 3: Run**

Run: `uv run pytest tests/test_media_presign_serving.py tests/test_take_stream_stage.py tests/test_sync_integration.py -n0 -q`
Expected: PASS (existing `web` / `trim` / mirror tests unchanged).

- [ ] **Step 4: Commit, then drill**

```bash
git add src/splitsmith/ui/server.py tests/test_media_presign_serving.py
git commit -m "feat(audit): kind=scrub on hosted serves the fresh rendition, else the trim, else 404 (#1209)"
```

Drills: replace the final `raise` with a source redirect -> the 404 test fails; pass `trim_required=True` always -> the mirror test fails.

---

### Task 4: Presence metadata and `scrub_version` on hosted

**Files:**
- Modify: `src/splitsmith/ui/presence.py`
- Modify: `src/splitsmith/ui/server.py` (`get_project` payload loop ~9507-9560; `_scrub_version_for` ~7902)
- Test: `tests/test_hosted_scrub.py`, `tests/test_media_presign_serving.py`

**Interfaces:**
- Consumes: `_storage_fact` (Task 3), `audio.fresh_rendition` (Task 1).
- Produces: `StoragePresence.object(key: str) -> StorageObject | None` (raises `OSError` like `has_key` when the listing failed).

- [ ] **Step 1: Presence test** (append to `tests/test_hosted_scrub.py`)

```python
def test_presence_object_returns_the_listed_metadata() -> None:
    from datetime import UTC, datetime

    from splitsmith.storage import StorageObject
    from splitsmith.ui.presence import StoragePresence

    when = datetime(2026, 10, 6, tzinfo=UTC)

    class Listing:
        calls = 0

        def list(self, prefix: str):
            Listing.calls += 1
            yield StorageObject(path=f"{prefix}a_web.mp4", size=7, last_modified=when)

        def exists(self, key: str) -> bool:  # pragma: no cover - not reached
            raise AssertionError("no HEAD for an indexed prefix")

    presence = StoragePresence(Listing())  # type: ignore[arg-type]
    obj = presence.object("m/shooters/me/trimmed/a_web.mp4")
    assert obj is not None and (obj.size, obj.last_modified) == (7, when)
    assert presence.object("m/shooters/me/trimmed/missing.mp4") is None
    assert presence.has_key("m/shooters/me/trimmed/a_web.mp4") is True
    assert Listing.calls == 1
```

Run: `uv run pytest tests/test_hosted_scrub.py -n0 -q`
Expected: FAIL, no attribute `object`.

- [ ] **Step 2: Implement `object`**

In `presence.py`:
- type of `self._listed` becomes `dict[str, dict[str, StorageObject] | BaseException]` (import `StorageObject` from `..storage`);
- the listing line becomes `listed = {obj.path: obj for obj in self._storage.list(prefix)}`;
- refactor the lookup into a private `_listing(prefix) -> dict[str, StorageObject]` that builds or re-raises, then:

```python
    def has_key(self, key: str) -> bool:
        """Whether ``key`` exists in storage; raises if its prefix could not be listed."""
        if self._storage is None:
            return False
        prefix = _covering_prefix(key)
        if prefix is None:
            return self._storage.exists(key)
        return key in self._listing(prefix)

    def object(self, key: str) -> StorageObject | None:
        """The listed metadata for ``key`` (size, ``last_modified``), or
        ``None`` when absent; raises like :meth:`has_key` on a failed listing.
        A key under no indexed prefix falls back to one ``stat``."""
        if self._storage is None:
            return None
        prefix = _covering_prefix(key)
        if prefix is None:
            return self._storage.stat(key)
        return self._listing(prefix).get(key)

    def _listing(self, prefix: str) -> dict[str, StorageObject]:
        listed = self._listed.get(prefix)
        if listed is None:
            try:
                listed = {obj.path: obj for obj in self._storage.list(prefix)}  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001 -- remembered and re-raised per lookup
                listed = exc
            self._listed[prefix] = listed
        if isinstance(listed, BaseException):
            raise OSError(f"storage listing of {prefix!r} failed") from listed
        return listed
```

Run: `uv run pytest tests/test_hosted_scrub.py tests/test_presence.py tests/test_share_shooters_presence.py -n0 -q` (use whichever presence test files exist: `ls tests | grep -i presence`).
Expected: PASS.

- [ ] **Step 3: Payload tests** (append to `tests/test_media_presign_serving.py`)

```python
def _project_videos(client: TestClient) -> list[dict]:
    resp = client.get(f"/api/matches/{MATCH_ID}/shooters/{SLUG}/project")
    assert resp.status_code == 200, resp.text
    return [v for s in resp.json()["stages"] for v in s["videos"]]


def test_hosted_scrub_version_names_a_fresh_rendition(s3_stream_client: tuple[TestClient, S3Storage]) -> None:
    client, storage = s3_stream_client
    storage.write_bytes(_TRIM_KEY, b"TRIMDATA")
    storage.write_bytes(_WEB_KEY, b"WEBDATA")
    web = storage.stat(_WEB_KEY)
    assert web is not None and web.last_modified is not None

    [video] = _project_videos(client)

    assert video["scrub_version"] == f"{int(web.last_modified.timestamp() * 1e9):x}-{web.size:x}"


def test_hosted_scrub_version_is_null_for_a_stale_or_missing_rendition(
    s3_stream_client: tuple[TestClient, S3Storage],
) -> None:
    import time

    client, storage = s3_stream_client
    assert _project_videos(client)[0]["scrub_version"] is None
    storage.write_bytes(_WEB_KEY, b"OLD WINDOW")
    time.sleep(1.1)
    storage.write_bytes(_TRIM_KEY, b"RE-CUT TRIM")
    assert _project_videos(client)[0]["scrub_version"] is None


@pytest.mark.parametrize("s3_stream_client", [(1, 2)], indirect=True)
def test_hosted_payload_lists_trimmed_once(
    s3_stream_client: tuple[TestClient, S3Storage], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, storage = s3_stream_client
    storage.write_bytes(_TRIM_KEY, b"TRIMDATA")
    storage.write_bytes(_WEB_KEY, b"WEBDATA")
    prefixes: list[str] = []
    real_list = type(storage).list

    def counting_list(self, prefix: str):
        prefixes.append(prefix)
        return real_list(self, prefix)

    monkeypatch.setattr(type(storage), "list", counting_list)

    _project_videos(client)

    assert [p for p in prefixes if p.endswith("/trimmed/")] == [f"matches/{MATCH_ID}/shooters/{SLUG}/trimmed/"]


def test_hosted_payload_survives_a_failed_listing(
    s3_stream_client: tuple[TestClient, S3Storage], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, storage = s3_stream_client
    real_list = type(storage).list

    def failing_list(self, prefix: str):
        if prefix.endswith("/trimmed/"):
            raise OSError("R2 down")
        return real_list(self, prefix)

    monkeypatch.setattr(type(storage), "list", failing_list)

    assert _project_videos(client)[0]["scrub_version"] is None
```

The S3 key passed to `storage.list` may be the tenant-prefixed or the scope-relative form; read `S3Storage.list` and adjust the expected prefix in `test_hosted_payload_lists_trimmed_once` to the form it receives (the assertion is that exactly one `trimmed/` listing happens).

Run: `uv run pytest tests/test_media_presign_serving.py -n0 -q -k "scrub_version or lists_trimmed or failed_listing"`
Expected: the fresh-rendition test FAILS (null today); `lists_trimmed_once` FAILS (no listing today); the stale/missing and failed-listing tests may pass today (null) -- they pin behaviour that must survive.

- [ ] **Step 4: Implement the hosted `scrub_version`**

At module level in `server.py`, after `_scrub_version_for`:

```python
def _hosted_scrub_version_for(
    presence: StoragePresence, root: Path, stage_number: int, video: StageVideo, project: MatchProject
) -> str | None:
    """``scrub_version`` on a storage-backed project (#1209): the fresh
    rendition from the request's presence listing, same rule as the
    ``kind=scrub`` route. ``None`` when stale, absent or the listing failed
    (the payload never fails on it)."""
    local_mp4 = audio_helpers.trimmed_video_path(root, stage_number, video, project=project)
    trim_key = audio_helpers._storage_trim_key(project, local_mp4)
    web_key = audio_helpers._storage_trim_key(project, trim_module.web_trim_path(local_mp4))
    if trim_key is None or web_key is None:
        return None
    try:
        trim_obj = presence.object(trim_key)
        web_obj = presence.object(web_key)
    except OSError:
        return None
    if web_obj is None or web_obj.last_modified is None:
        return None
    if not audio_helpers.fresh_rendition(
        _storage_fact(trim_obj), _storage_fact(web_obj), trim_required=not _is_mirror()
    ):
        return None
    return f"{int(web_obj.last_modified.timestamp() * 1e9):x}-{web_obj.size:x}"
```

(import `StoragePresence` from `.presence` and `trim as trim_module` if not already imported at module level; follow the existing import style.)

In `get_project`'s payload loop, before the `for stage_dict in payload.get("stages", [])` loop:

```python
        # One listing of this shooter's trimmed/ prefix per request (#1209).
        hosted_scrub = _storage is not None and _storage.supports_presigned_get
        presence = StoragePresence(_storage) if hosted_scrub else None
```

and replace the `scrub_version` line with:

```python
                video_dict["scrub_version"] = (
                    _hosted_scrub_version_for(presence, root, int(n), video, project)
                    if presence is not None and not video.path.is_absolute()
                    else _scrub_version_for(root, int(n), video, project)
                )
```

Update `_scrub_version_for`'s docstring: "Local files only; hosted goes through :func:`_hosted_scrub_version_for`." and its first lines' "``kind=web``" -> "``kind=scrub``".

- [ ] **Step 5: Run**

Run: `uv run pytest tests/test_hosted_scrub.py tests/test_media_presign_serving.py tests/test_scrub_rendition.py tests/test_trim_version.py -n0 -q` and the presence test files.
Expected: PASS.

- [ ] **Step 6: Commit, then drill**

```bash
git add src/splitsmith/ui/presence.py src/splitsmith/ui/server.py tests/test_hosted_scrub.py tests/test_media_presign_serving.py
git commit -m "feat(audit): scrub_version on hosted from one presence listing per request (#1209)"
```

Drills: build `StoragePresence` inside the per-video loop -> `lists_trimmed_once` fails; drop the `except OSError` -> `survives_a_failed_listing` fails.

---

### Task 5: SPA asks for `kind=scrub`

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/scrubSource.ts` (+ `.test.ts`), `lib/useScrubSource.test.tsx`, `lib/auditVideoSrc.test.ts`, `lib/api.ts` (`videoStreamUrl` kind union), `pages/Audit.tsx` and `pages/MobileAudit.tsx` (fallback guards), `pages/MobileAudit.test.tsx`

**Interfaces:**
- Produces: `ScrubChoice.kind: "scrub" | "trim"`.

- [ ] **Step 1: Tests first**

Replace `"web"` with `"scrub"` in the expected kinds of `lib/scrubSource.test.ts`, `lib/useScrubSource.test.tsx` (every `{ kind: "web", ... }` and `.kind).toBe("web")`), `lib/auditVideoSrc.test.ts` (`kind=web&v=w2&stage=2` -> `kind=scrub&v=w2&stage=2`), and `pages/MobileAudit.test.tsx` (`"web", "w-1", 3` -> `"scrub", "w-1", 3`; `/video.mp4?kind=web` -> `/video.mp4?kind=scrub`).

Run: `corepack pnpm vitest run src/lib/scrubSource.test.ts src/lib/useScrubSource.test.tsx src/lib/auditVideoSrc.test.ts src/pages/MobileAudit.test.tsx`
Expected: FAIL (still `web`).

- [ ] **Step 2: Implement**

- `lib/scrubSource.ts`: `kind: "scrub" | "trim"` in `ScrubChoice`; return `{ kind: "scrub", version: scrubVersion }`; in the doc comment say the rendition is served through ``kind=scrub``, the pin that never falls back to the source.
- `lib/api.ts` `videoStreamUrl`: kind union `"auto" | "trim" | "source" | "proxy" | "web" | "scrub"`; doc line "``scrub`` is the Audit players' pin: the fresh rendition, else the trim, else 404."
- `pages/Audit.tsx`: `videoSrc.includes("kind=web")` -> `videoSrc.includes("kind=scrub")`.
- `pages/MobileAudit.tsx`: `videoUrl.includes("kind=web")` -> `videoUrl.includes("kind=scrub")`.

- [ ] **Step 3: Run, typecheck, lint**

Run: `corepack pnpm vitest run src/lib src/pages/Audit src/pages/MobileAudit src/components/audit src/components/VideoPanel && corepack pnpm typecheck && corepack pnpm lint`
Expected: PASS, lint 0 errors.

- [ ] **Step 4: Commit, then drill**

```bash
git add src/splitsmith/ui_static/src
git commit -m "feat(audit): the Audit players ask for kind=scrub (#1209)"
```

Drill: put `"kind=web"` back in Audit's fallback guard -> the MobileAudit/Audit fallback tests that dispatch `error` must fail; if none fails for `Audit.tsx`, add a case to `lib/auditVideoSrc.test.ts` or note it as a ruling (the guard is page wiring).

---

### Task 6: Docs, suites, review

- [ ] **Step 1: CLAUDE.md**

In the "Hosted playback streams the web rendition (#1031)" section replace the sentences from "On the stream routes ``kind=web`` falls back" through "...already errored on the page (#1192: ... #1191)." with:

"On ``stream_video`` the kinds mean one thing in both modes: ``web`` is the streaming rendition, else the trim, else the source, never a 404 (what Results, Coach and Compare rely on); ``trim`` never substitutes the rendition; ``scrub`` is the Audit players' pin (#1209): the fresh rendition, else the trim, else 404, never the source -- a re-cut deletes both while it encodes and a pinned player must error and remount, not play the source under trim offsets. Fresh is one rule, ``audio.fresh_rendition`` (non-empty, not older than the trim, equal timestamps fresh; on a mirror the rendition alone), fed by local files, ``storage.stat`` in the route, and the request's ``StoragePresence`` listing for the payload's ``scrub_version``. ``lib/useScrubSource`` asks for ``scrub`` when the video dict carries ``scrub_version``, unless ``GlobalPrefs.full_res_scrub`` is on (local only; hosted has no switch) or that rendition (path + version) already errored on the page (#1192, #1191)."

- [ ] **Step 2: Full suites**

Clear the numba cache, then `uv run pytest -q`, then `corepack pnpm vitest run`, `typecheck`, `lint`. Known SPA load flakes: `Export.presets.test.tsx`, `Compare.playall.test.tsx` (re-run alone).

- [ ] **Step 3: Commit docs; whole-branch review**

One fresh reviewer, told the report is unverified, with these claims to check: the hosted `scrub` branch never redirects to the source; `scrub_version` and the `scrub` route agree for every (trim, web, mirror) combination, including equal seconds and `last_modified=None`; the payload makes one `trimmed/` listing per request; local `web` reverting cannot reach a current caller that needed the pin (grep the SPA for `"web"` stream requests); each new test fails on `origin/main`.

- [ ] **Step 4: PR**

`Fixes #1209`. Note in the body that the hosted path is verified by moto tests and needs a staging check after the release (open a hosted-native Audit page, read the `<video>` src: `kind=scrub&v=...`, a 307 to `_web.mp4`).
