# Hardening Wave PR 2: Beep-Review Sync Follow-ups (#821 a-f) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the six unfixed #821 items: per-subdir media-key extensions (d), confirm-only merge writes no longer triggering re-trim (e), beep-queue storage listing scoped to beep_review (b), one honest `proxy_ready` for all endpoints plus honest SPA copy on mirrors (c), remote snippet GC on push (a), and the `origin` comment/typing fix (f).

**Architecture:** Backend in `src/splitsmith/sync/*` (pure-Python merge/plan/push), `src/splitsmith/ui/sync_api.py` (hosted mirror-write routes), `src/splitsmith/ui/server.py` (beep queue + get_project). GC follows the existing push architecture: `sync_state.items` records every uploaded remote key, so keys under `beep_review/` whose local file no longer exists are exactly the stale remote objects; a new mirror-scoped delete route + client method + push phase removes them. `Storage.delete()` already exists on both backends.

**Tech Stack:** Python/FastAPI/pytest, httpx client, React/vitest for the SPA copy changes.

## Global Constraints

- Branch: `fix/821-beep-sync-followups` off `origin/main` (stack on PR 1's branch only if it has not merged yet and conflicts arise; otherwise independent). Work in a worktree.
- New prose/comments use single ASCII dash `-`, never `--`, never em dash. ASCII punctuation only.
- SPA commits use `fix(ui):` / `refactor(ui):` prefixes.
- Scoped test runs per task; full `ruff check . && black --check . && pytest` + `pnpm typecheck && pnpm test` + scoped eslint once in the final task.
- `pytest -m docker` locally before merge - this PR touches sync/mirror routes (db-change smoke policy).
- Facts fixed by exploration (do not re-derive): `trimmed/` legitimately holds `.mp4` clips AND `.params.json` sidecars (`plan.py:197-210`); `beep_review/` holds `.m4a` and `.peaks.json` only (`plan.py:212-222`); already-fixed #821 items g (mirror banner copy, `16e4e53`) and h (updated_at tripwire, `c54beac`) need no work; #823 is done apart from two deliberate non-fixes.
- NOT in this PR: #821's "audible AAC-priming alignment" item - it needs a human ear on a phone; leave it open on the issue.

---

### Task 1: Per-subdir extensions in `_SYNC_MEDIA_KEY_RE` (#821 d)

**Files:**
- Modify: `src/splitsmith/ui/sync_api.py:57-60`
- Test: `tests/test_sync_media_api.py`

**Interfaces:**
- Produces: `_SYNC_MEDIA_KEY_RE` accepts `trimmed/*.mp4`, `trimmed/*.json` (params sidecar), `beep_review/*.m4a`, `beep_review/*.json` (peaks) and rejects the cross-product (`trimmed/*.m4a`, `beep_review/*.mp4`). The named group `match_id` is unchanged - `_validate_media_key` keeps working as-is.

- [ ] **Step 1: Write the failing tests**

In `tests/test_sync_media_api.py`, next to `test_beep_review_foreign_subdir_rejected` (follow that test's setup verbatim - same client/mirror fixture, POST to `CREATE_URL`):

```python
def test_trimmed_m4a_cross_product_rejected(hosted_media_client) -> None:
    """#821: the extension set is per-subdir. trimmed/ never holds audio
    snippets; admitting the cross-product widens the write surface."""
    client = hosted_media_client
    key = f"matches/{MATCH_ID}/shooters/{SLUG}/trimmed/stage1_cam_abc123.m4a"
    resp = client.post(CREATE_URL, json={"key": key})
    assert resp.status_code == 422


def test_beep_review_mp4_cross_product_rejected(hosted_media_client) -> None:
    """#821: beep_review/ holds .m4a snippets and .peaks.json only."""
    client = hosted_media_client
    key = f"matches/{MATCH_ID}/shooters/{SLUG}/beep_review/vid123.mp4"
    resp = client.post(CREATE_URL, json={"key": key})
    assert resp.status_code == 422
```

The file's existing tests build their client inline rather than via a `hosted_media_client` fixture - copy the exact setup of `test_beep_review_foreign_subdir_rejected` (whatever it does to get an adopted mirror + client) instead of inventing a fixture.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_sync_media_api.py -q`
Expected: the two new tests FAIL (both keys currently accepted, 200).

- [ ] **Step 3: Implement**

Replace the regex in `sync_api.py`:

```python
# Per-subdir extension sets (#821): trimmed/ holds .mp4 clips plus their
# .params.json sidecars; beep_review/ holds .m4a snippets plus their
# .peaks.json. The cross-product (trimmed/*.m4a, beep_review/*.mp4) is
# not a thing the desktop push ever writes, so the gate rejects it.
_SYNC_MEDIA_KEY_RE = re.compile(
    r"^matches/(?P<match_id>[A-Za-z0-9._-]+)/shooters/[A-Za-z0-9_-]+/"
    r"(?:trimmed/[A-Za-z0-9._-]+\.(?:mp4|json)"
    r"|beep_review/[A-Za-z0-9._-]+\.(?:m4a|json))$"
)
```

- [ ] **Step 4: Run the suite**

Run: `pytest tests/test_sync_media_api.py -q`
Expected: all PASS, including the pre-existing accepts (`trimmed/...mp4`, `beep_review/...m4a`, `beep_review/...peaks.json`) and rejects (`.wav`, traversal, foreign subdir).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/sync_api.py tests/test_sync_media_api.py
git commit -m "fix(sync): per-subdir extension sets in the media key gate (#821)"
```

---

### Task 2: Gate merge invalidation on beep_time change (#821 e)

**Files:**
- Modify: `src/splitsmith/sync/merge.py` (`merge_project_doc`, lines ~95-143)
- Test: `tests/test_sync_merge.py`

**Interfaces:**
- Consumes: `_beep_group`, `_resolve_unit`, `MergeResult` (exist, unchanged).
- Produces: a remote-wins beep-group change still replaces the whole group atomically (the `_BEEP_PREFIX` unit is untouched), but `processed["trim"]`/`processed["shot_detect"]` are reset and the video is queued in `reprocess_video_ids` ONLY when `beep_time` itself changed. A confirm-only phone write (`beep_reviewed` flip) merges without triggering desktop re-trim/re-detect.

- [ ] **Step 1: Write the failing test**

In `tests/test_sync_merge.py`, next to `test_remote_only_beep_change_wins_and_invalidates` (reuse its `_project`/`_video`/`T_OLD`/`T_NEW` helpers):

```python
def test_confirm_only_remote_change_wins_without_invalidating() -> None:
    """#821: a phone confirm flips beep_reviewed with beep_time unchanged.
    The group still moves atomically, but trim/shot_detect derive from
    beep_time alone - re-running them would burn ffmpeg minutes to
    produce identical output."""
    base = _project(_video(beep_time=2.5))
    local = _project(_video(beep_time=2.5))
    remote = _project(_video(beep_time=2.5, beep_reviewed=True))
    r = merge_project_doc(
        base, local, remote, doc_key="project/anna", local_ts=T_OLD, remote_ts=T_NEW
    )
    v = r.doc["stages"][0]["videos"][0]
    assert v["beep_reviewed"] is True
    assert v["processed"].get("trim") is not False
    assert r.reprocess_video_ids == []
    assert r.changed_vs_local is True
```

Match `_video()`'s actual signature/defaults (it exists at the top of the file); if `_video()` does not set `processed`, extend the local/base fixtures the same way the existing invalidation test observes `processed` afterwards.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_sync_merge.py -q`
Expected: the new test FAILS - `reprocess_video_ids == ["<id>"]` and `processed["trim"] is False` today.

- [ ] **Step 3: Implement**

In `merge_project_doc`, replace the remote-wins block:

```python
        if winner == "remote" and remote_u != local_u:
            # Only beep_time feeds the trim/shot-detect derivation chain.
            # A confirm-only change (beep_reviewed, beep_source, candidate
            # metadata) must merge without re-queueing work whose inputs
            # did not change (#821).
            derivation_changed = remote_u.get("beep_time") != local_u.get("beep_time")
            for k in list(merged_video):
                if k.startswith(_BEEP_PREFIX):
                    del merged_video[k]
            merged_video.update(copy.deepcopy(remote_u))
            processed = merged_video.setdefault("processed", {})
            processed["beep"] = remote_u.get("beep_time") is not None
            if derivation_changed:
                processed["trim"] = False
                if merged_video.get("role") == "primary":
                    processed["shot_detect"] = False
                result.reprocess_video_ids.append(video_id)
```

- [ ] **Step 4: Run the merge suite plus the pull/integration consumers**

Run: `pytest tests/test_sync_merge.py tests/test_sync_pull.py tests/test_sync_integration.py -q`
Expected: PASS. `test_remote_only_beep_change_wins_and_invalidates` (remote changes beep_time to 2.5) must still assert invalidation - it changes beep_time, so `derivation_changed` is true there.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/sync/merge.py tests/test_sync_merge.py
git commit -m "fix(sync): confirm-only beep writes merge without re-trim/re-detect (#821)"
```

---

### Task 3: Scope the beep-queue snippet listing to beep_review/ (#821 b)

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`get_beep_queue`, the `_snippet_keys` block ~13476-13481)
- Test: `tests/test_mirror_read_only.py`

**Interfaces:**
- Consumes: `state.storage.list(prefix)`, `match.shooters` (both already in scope in the function).
- Produces: `_snippet_keys` is built from one `storage.list` per shooter over `matches/<id>/shooters/<slug>/beep_review/` - trimmed-clip objects (the bulk of the prefix) are never enumerated. `_snippet_ready` is unchanged.

- [ ] **Step 1: Write the failing test**

In `tests/test_mirror_read_only.py`, next to `test_mirror_beep_queue_media_flags` (line ~592; reuse its app/storage/seeding setup). Observe list prefixes with a recording wrapper:

```python
def test_beep_queue_lists_only_beep_review_prefixes(...same fixtures as test_mirror_beep_queue_media_flags...) -> None:
    """#821: the snippet listing must not enumerate every trimmed clip.
    Pin the prefixes so a regression shows up as a wrong prefix, not as
    a silent hosted-list cost."""
    storage = <the storage object the sibling test uses>
    prefixes: list[str] = []
    original_list = storage.list

    def _recording_list(prefix: str):
        prefixes.append(prefix)
        return original_list(prefix)

    storage.list = _recording_list  # type: ignore[method-assign]
    resp = client.get("/api/match/beep-queue")
    assert resp.status_code == 200
    snippet_prefixes = [p for p in prefixes if p.startswith("matches/")]
    assert snippet_prefixes, "expected at least one snippet listing"
    assert all(p.endswith("/beep_review/") for p in snippet_prefixes), snippet_prefixes
```

Fill the fixture plumbing (client, storage handle, seeded mirror match) by copying `test_mirror_beep_queue_media_flags`'s body verbatim up to its GET call - only the recording wrapper and assertions are new. Note `raw_proxy/` listings do not start with `matches/`, so the filter isolates the snippet listings.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_mirror_read_only.py -k beep_queue -q`
Expected: new test FAILS - the recorded prefix is `matches/<id>/shooters/` (no `/beep_review/` suffix).

- [ ] **Step 3: Implement**

Replace the `_snippet_keys` block in `get_beep_queue`:

```python
        # Snippet artifacts pushed by desktop for unconfirmed videos
        # (slice 3). One list per shooter, scoped to beep_review/ - the
        # shooters/ prefix as a whole is dominated by trimmed clips that
        # this endpoint never reads (#821).
        _match_id = current_match_id.get()
        _snippet_keys: set[str] = set()
        if _storage is not None and _match_id:
            for _slug in match.shooters:
                _snippet_keys.update(
                    obj.path
                    for obj in _storage.list(
                        f"matches/{_match_id}/shooters/{_slug}/beep_review/"
                    )
                )
```

(`match` is already loaded above this point in the function; keep the loop variable underscored to avoid shadowing the later `for slug in match.shooters:` loop.)

- [ ] **Step 4: Run the mirror suite**

Run: `pytest tests/test_mirror_read_only.py -q`
Expected: PASS, including `test_mirror_beep_queue_media_flags` (behavior identical - same keys end up in the set).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_mirror_read_only.py
git commit -m "fix: scope beep-queue snippet listing to beep_review prefixes (#821)"
```

---

### Task 4: One honest proxy_ready + honest mirror copy (#821 c)

**Files:**
- Modify: `src/splitsmith/ui/server.py` (module-level helper; `get_project` ~6917-6967; `get_beep_queue` ~13456-13474)
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (project payload type gains `origin`)
- Modify: `src/splitsmith/ui_static/src/components/VideoPanel.tsx` (placeholder copy variant)
- Modify: `src/splitsmith/ui_static/src/pages/Audit.tsx`, `src/splitsmith/ui_static/src/pages/ingest/ClipDetail.tsx`, `src/splitsmith/ui_static/src/pages/BeepReview.tsx` (pass/branch on origin)
- Test: `tests/test_mirror_read_only.py`, `src/splitsmith/ui_static/src/components/VideoPanel.test.tsx` (or the component's existing test file)

**Interfaces:**
- Consumes: `current_match_origin` ContextVar (`server.py` ~1041, set per-request for hosted matches), `proxy_key_for` from `..proxy`.
- Produces: module-level `def _proxy_ready_for(storage, proxy_keys: set[str], path_str: str) -> bool` used by BOTH `get_project` and `get_beep_queue` (honest semantics: non-`raw/` path on hosted storage is never proxy-ready). `get_project`'s payload gains top-level `origin: "hosted" | "desktop" | "local"`. `VideoPanel` gains optional prop `mediaOnDesktop?: boolean` switching the placeholder from "Preview generating / Check back shortly" to "Video stays on the desktop install".

Background: `get_beep_queue`'s `_proxy_ready` honestly returns `False` for desktop-mirror videos (`server.py:13467-13474`); `get_project`'s twin returns `True` for the same case (`server.py:6939-6944`), so Audit/ClipDetail on a mirror mount a player the server answers with an error. Unifying on honest-`False` then makes the "Preview generating - check back shortly" copy a lie of its own on mirrors (nothing is generating; raw media never leaves desktop per the sync design) - hence the copy branch.

- [ ] **Step 1: Write the failing backend test**

In `tests/test_mirror_read_only.py`, following `test_mirror_beep_queue_media_flags`'s seeding (a desktop-pushed mirror match whose videos have non-`raw/` paths):

```python
def test_mirror_get_project_reports_proxy_not_ready(...same fixtures...) -> None:
    """#821: get_project's proxy_ready must agree with get_beep_queue's.
    A mirror video has no proxy object; reporting ready mounts a player
    the server can only answer with an error."""
    resp = client.get(f"/api/shooters/{SLUG}/project")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["origin"] == "desktop"
    videos = [v for s in payload["stages"] for v in s["videos"]]
    assert videos, "seeded mirror should have videos"
    assert all(v["proxy_ready"] is False for v in videos)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_mirror_read_only.py -k get_project -q`
Expected: FAIL twice over - no `origin` key in the payload, and `proxy_ready` is `True`.

- [ ] **Step 3: Implement the backend**

1. Add a module-level helper in `server.py` (near the other module helpers, outside `create_app`):

```python
def _proxy_ready_for(storage, proxy_keys: set[str], path_str: str) -> bool:
    """One honest answer for every endpoint (#821). Local mode streams
    the source directly (ready). Hosted: only ``raw/`` uploads ever get
    a proxy object; a desktop-pushed mirror path has nothing to stream,
    and saying otherwise mounts a player the server errors on."""
    from ..proxy import proxy_key_for

    if storage is None:
        return True
    if not path_str.startswith("raw/"):
        return False
    return proxy_key_for(path_str) in proxy_keys
```

(Adjust the import to the module's actual layout - both call sites currently do `from ..proxy import proxy_key_for` inside the route; hoisting that to module level is fine.)

2. In `get_beep_queue`: delete the local `_proxy_ready` closure; call `_proxy_ready_for(_storage, _proxy_keys, video.path.as_posix())` at the use site.

3. In `get_project`: delete the local `_proxy_ready` closure (the dishonest twin); call `_proxy_ready_for(_storage, proxy_keys, str(video_dict.get("path", "")))` at both use sites (stage videos and `unassigned_videos`). Add the origin to the payload next to the existing enrichment:

```python
        # Which media surface is honest for this match (#821): mirrors
        # have no proxies and never will - the SPA copy must not promise
        # one is coming.
        payload["origin"] = current_match_origin.get() or "local"
```

- [ ] **Step 4: Run backend suites**

Run: `pytest tests/test_mirror_read_only.py tests/test_ui_server.py -q`
Expected: PASS. `test_beep_queue_proxy_ready_local_mode_always_true` (`tests/test_ui_server.py:8868`) pins the local-mode branch of the shared helper.

- [ ] **Step 5: Write the failing SPA test**

In `VideoPanel`'s test file (create `src/components/VideoPanel.proxy.test.tsx` if none exists, rendering the component directly with minimal props as its existing tests do):

```tsx
it("says the video stays on desktop when the match is a mirror (#821)", () => {
  render(<VideoPanel {...minimalProps} proxyReady={false} mediaOnDesktop />);
  expect(screen.getByRole("status")).toHaveTextContent(/stays on the desktop install/i);
  expect(screen.queryByText(/check back shortly/i)).not.toBeInTheDocument();
});

it("keeps the generating copy when a proxy is actually coming", () => {
  render(<VideoPanel {...minimalProps} proxyReady={false} />);
  expect(screen.getByText(/preview generating/i)).toBeInTheDocument();
});
```

- [ ] **Step 6: Implement the SPA**

1. `api.ts`: add `origin: MatchOrigin;` to the project-response interface that `get_project` deserializes into (find the interface `getProject`/`getShooterProject` returns; `MatchOrigin` already exists at line ~1458).
2. `VideoPanel.tsx`: add `mediaOnDesktop?: boolean` to the props (documented next to `proxyReady`), and branch the placeholder (lines ~438-451):

```tsx
            {proxyReady === false ? (
              <div
                role="status"
                aria-label={mediaOnDesktop ? "Video available on desktop only" : "Preview still generating"}
                className={cn(
                  "flex flex-col items-center justify-center gap-2 bg-black p-4 text-center text-white/70",
                  showGrid ? "max-h-[40vh]" : "max-h-[60vh]",
                  "h-full min-h-[10rem] w-full",
                )}
              >
                <Clock className="size-5 opacity-60" aria-hidden />
                {mediaOnDesktop ? (
                  <>
                    <span className="text-sm">Video stays on the desktop install</span>
                    <span className="text-xs text-white/40">Raw footage is not synced to hosted</span>
                  </>
                ) : (
                  <>
                    <span className="text-sm">Preview generating</span>
                    <span className="text-xs text-white/40">Check back shortly</span>
                  </>
                )}
              </div>
            ) : (
```

3. `Audit.tsx`: pass `mediaOnDesktop={project?.origin === "desktop"}` where `proxyReady` is passed into `VideoPanel` (~line 2130).
4. `ClipDetail.tsx` (~line 340): branch the pill the same way:

```tsx
          {video.proxy_ready === false &&
            (project.origin === "desktop" ? (
              <StatusPill tone="neutral">Video on desktop</StatusPill>
            ) : (
              <StatusPill tone="in-progress">Proxy generating</StatusPill>
            ))}
```

(use whatever neutral tone value `StatusPill` actually supports - check its props; ClipDetail receives the project/origin via its existing data flow from the ingest page - thread the `origin` field through the same props/query the page already uses for `video`).
5. `BeepReview.tsx` `BeepVideoMini` (~1042-1128): same two-line copy branch keyed on the beep queue's existing `origin === "desktop"` (the response already carries it) instead of the unconditional "Preview generating / Check back shortly".

- [ ] **Step 7: Run SPA suites**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm vitest run src/components/VideoPanel.proxy.test.tsx src/pages/BeepReview.test.tsx && pnpm eslint src/components/VideoPanel.tsx src/pages/Audit.tsx src/pages/ingest/ClipDetail.tsx src/pages/BeepReview.tsx`
Expected: PASS (adjust the vitest file list to the test files that actually exist for those pages).

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_mirror_read_only.py src/splitsmith/ui_static/src
git commit -m "fix: one honest proxy_ready everywhere; mirror copy stops promising a proxy (#821)"
```

---

### Task 5: Remote snippet GC on push (#821 a)

**Files:**
- Modify: `src/splitsmith/ui/sync_api.py` (new route `POST /api/sync/matches/{match_id}/media/delete`)
- Modify: `src/splitsmith/sync/client.py` (`HostedSyncClient.delete_media`)
- Modify: `src/splitsmith/sync/push.py` (`run_push` gains a `gc` phase; `PushReport` gains `media_deleted`)
- Test: `tests/test_sync_media_api.py`, `tests/test_sync_push.py`

**Interfaces:**
- Consumes: `Storage.delete(path)` (exists on both backends, no-op on missing key); `_hosted_gate` / `_resolve_mirror` / `_validate_media_key` / `_require_storage` in `sync_api.py`; `sync_state.items: dict[str, SyncedItem]` in push.
- Produces: `POST /api/sync/matches/{match_id}/media/delete` with body `{"key": "<remote key>"}` - mirror-gated, key-validated, additionally restricted to `/beep_review/` keys (422 otherwise), idempotent, returns `{"deleted": true}`. `HostedSyncClient.delete_media(self, match_id: str, remote_key: str) -> None`. `run_push` deletes remote beep_review objects whose local file is gone and drops them from `sync_state.items`; failures are non-fatal (retried next push).

Why this closes the issue: confirmed videos delete local `beep_review/` files (`beep_snippets.py:154-162`) but the pushed R2 objects linger, so `_snippet_ready` stays true forever for reopened confirmed items. `sync_state.items` still holds those keys - the local-file-gone diff is exactly the stale set, including backlog from before this fix ships.

- [ ] **Step 1: Write the failing route tests**

In `tests/test_sync_media_api.py` (same setup pattern as the create-route tests; `DELETE_URL = f"/api/sync/matches/{MATCH_ID}/media/delete"` next to the other URL constants):

```python
def test_delete_media_removes_a_beep_review_object(...) -> None:
    key = f"matches/{MATCH_ID}/shooters/{SLUG}/beep_review/vid123.m4a"
    storage.write_bytes(key, b"snippet")
    resp = client.post(DELETE_URL, json={"key": key})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    assert not storage.exists(key)


def test_delete_media_is_idempotent(...) -> None:
    key = f"matches/{MATCH_ID}/shooters/{SLUG}/beep_review/vid123.m4a"
    resp = client.post(DELETE_URL, json={"key": key})
    assert resp.status_code == 200


def test_delete_media_rejects_trimmed_keys(...) -> None:
    """GC is beep_review-only: trimmed clips are what the mirror streams,
    and nothing on the desktop side ever needs to delete one remotely."""
    key = f"matches/{MATCH_ID}/shooters/{SLUG}/trimmed/stage1_cam_abc123_trimmed.mp4"
    resp = client.post(DELETE_URL, json={"key": key})
    assert resp.status_code == 422
```

Also extend the parametrized `test_key_containment_enforced_on_every_route` list with `DELETE_URL`, and add `DELETE_URL` coverage to the mirror-gate tests the file already runs per-route (native-hosted 409 `not_a_mirror`, unknown match 404, local mode 404, storage unwired 503) - follow the existing parametrization.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_sync_media_api.py -q`
Expected: new tests FAIL with 404 (route does not exist).

- [ ] **Step 3: Implement the route**

In `sync_api.py`, next to `abort_media_upload` (mirror its decorator/dependency shape exactly):

```python
class MediaDeleteRequest(BaseModel):
    key: str


@router.post("/matches/{match_id}/media/delete")
async def delete_media(
    match_id: str,
    body: MediaDeleteRequest,
    request: Request,
    user: Any = Depends(_current_user),
) -> dict:
    """Remove a pushed beep_review object (#821). Desktop calls this when
    a video's beep is confirmed: the local snippet files are deleted by
    the pre-push sweep, and leaving the remote copy makes snippet_ready
    lie forever for reopened items. beep_review-only on purpose - trimmed
    clips are what the mirror streams. Idempotent: deleting a missing key
    is success, so a crashed push can retry safely."""
    _hosted_gate()
    _resolve_mirror(request, match_id)
    _validate_media_key(body.key, match_id)
    if "/beep_review/" not in body.key:
        raise HTTPException(status_code=422, detail="delete is beep_review-only")
    storage = _require_storage(request)
    storage.delete(body.key)
    return {"deleted": True}
```

(Match the file's actual route/decorator idiom - if the existing media routes take `key` via a shared request model or extra fields, follow them.)

- [ ] **Step 4: Client method**

In `client.py`, next to `upload_media`:

```python
    def delete_media(self, match_id: str, remote_key: str) -> None:
        """Remove a pushed beep_review object (#821). Idempotent."""
        resp = self._http.post(
            f"/api/sync/matches/{match_id}/media/delete",
            json={"key": remote_key},
        )
        self._raise_for_status(resp)
```

(Use the same response/error handling helper the sibling methods use - if there is no `_raise_for_status`, copy `ensure_match`'s error pattern.)

- [ ] **Step 5: Write the failing push tests**

`tests/test_sync_push.py` drives a real `HostedSyncClient` over an `httpx.MockTransport` double (`_FakeHosted`, line ~117), and Case 12 (`test_run_push_regenerates_beep_snippets_before_planning`, ~line 681) already shows the snippet-spy pattern to copy. First teach the fake the new route - in `_FakeHosted.__init__` add `self.delete_status = 200`, and in `_http_handler` next to the other media branches:

```python
        if method == "POST" and path.endswith("/media/delete"):
            self.calls.append(f"media_delete:{body['key']}")
            if self.delete_status != 200:
                return httpx.Response(self.delete_status, json={"detail": "boom"})
            return httpx.Response(200, json={"deleted": True})
```

Then the tests (Case 13, after Case 12):

```python
def test_push_deletes_remote_snippets_for_reviewed_videos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#821: a confirmed video's local snippets are swept pre-push; the
    remote copies must follow, and the sync_state entries with them -
    otherwise snippet_ready lies forever for reopened items."""
    root, match_id = _build_match(tmp_path)
    reviewed = False

    def _spy(match_root: Path, **_kwargs: object) -> beep_snippets.BeepSnippetReport:
        out_dir = match_root / "shooters" / "alice" / "beep_review"
        if not reviewed:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "vid1.m4a").write_bytes(b"aac")
            (out_dir / "vid1.peaks.json").write_bytes(b"{}")
            return beep_snippets.BeepSnippetReport(generated=1)
        for name in ("vid1.m4a", "vid1.peaks.json"):
            stale = out_dir / name
            if stale.exists():
                stale.unlink()
        return beep_snippets.BeepSnippetReport(removed=1)

    monkeypatch.setattr(push, "generate_beep_snippets", _spy)
    fake = _FakeHosted()
    run_push(root, client=fake.clients())
    m4a_key = f"matches/{match_id}/shooters/alice/beep_review/vid1.m4a"
    peaks_key = f"matches/{match_id}/shooters/alice/beep_review/vid1.peaks.json"
    assert {m4a_key, peaks_key} <= set(load_sync_state(root).items)

    reviewed = True
    report = run_push(root, client=fake.clients())

    assert f"media_delete:{m4a_key}" in fake.calls
    assert f"media_delete:{peaks_key}" in fake.calls
    assert report.media_deleted == 2
    remaining = set(load_sync_state(root).items)
    assert m4a_key not in remaining and peaks_key not in remaining


def test_push_gc_failure_keeps_the_key_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GC must never fail a push that already moved the operator's data;
    the key stays in sync_state so the next push retries."""
    root, match_id = _build_match(tmp_path)
    reviewed = False

    def _spy(match_root: Path, **_kwargs: object) -> beep_snippets.BeepSnippetReport:
        out_dir = match_root / "shooters" / "alice" / "beep_review"
        if not reviewed:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "vid1.m4a").write_bytes(b"aac")
            return beep_snippets.BeepSnippetReport(generated=1)
        stale = out_dir / "vid1.m4a"
        if stale.exists():
            stale.unlink()
        return beep_snippets.BeepSnippetReport(removed=1)

    monkeypatch.setattr(push, "generate_beep_snippets", _spy)
    fake = _FakeHosted()
    run_push(root, client=fake.clients())
    m4a_key = f"matches/{match_id}/shooters/alice/beep_review/vid1.m4a"
    assert m4a_key in load_sync_state(root).items

    reviewed = True
    fake.delete_status = 500
    report = run_push(root, client=fake.clients())

    assert report.media_deleted == 0
    assert m4a_key in load_sync_state(root).items
```

(`_build_match`, `_FakeHosted`, `fake.clients()`, `load_sync_state`, and the `beep_snippets`/`push` imports all exist at the top of the file. `reviewed` is only read inside `_spy`, so no `nonlocal` is needed. If `fake.clients()` has a different name/signature, copy Case 12's exact call. Also extend Case 9's default-fields test with `assert PushReport(uploaded=0, skipped=0, docs=0).media_deleted == 0`.)

- [ ] **Step 6: Implement the push phase**

In `push.py`:
1. `PushReport` gains `media_deleted: int = 0` (follow the dataclass/model style of the existing fields).
2. After the `media` phase and before (or after) `docs`, add:

```python
        with _timed_phase(timings, timer, "gc"):
            # Remote snippet GC (#821): sync_state remembers every key we
            # ever uploaded. A beep_review key whose local file is gone
            # was swept by generate_beep_snippets because the video is
            # now reviewed - the remote copy must follow or snippet_ready
            # lies forever for reopened items. Failures keep the key in
            # sync_state so the next push retries; GC must never fail a
            # push that already moved the operator's data.
            stale = [
                key
                for key in list(sync_state.items)
                if "/beep_review/" in key
                and not _local_media_path(match_root, key).exists()
            ]
            for key in stale:
                try:
                    client.delete_media(plan.match_id, key)
                except SyncClientError:
                    continue
                del sync_state.items[key]
                report.media_deleted += 1
            if stale:
                save_sync_state(match_root, sync_state)
```

(`save_sync_state` is already imported at the top of `push.py` from `.state`; `plan.match_id` is the same identifier the docs phase passes to `client.put_doc`.)

3. Add the key-to-local-path helper near the top of `push.py`:

```python
_MEDIA_KEY_LOCAL_RE = re.compile(
    r"^matches/[^/]+/shooters/(?P<slug>[^/]+)/(?P<subdir>trimmed|beep_review)/(?P<name>[^/]+)$"
)


def _local_media_path(match_root: Path, remote_key: str) -> Path:
    m = _MEDIA_KEY_LOCAL_RE.match(remote_key)
    if m is None:
        # Unknown key shape: treat as still-present so GC never deletes
        # something it cannot map back to a local file.
        return match_root
    shooter_root = match_model.Match.shooter_root(match_root, m.group("slug"))
    return shooter_root / m.group("subdir") / m.group("name")
```

Adapt names to `push.py`'s actual imports and state-saving helper (it saves `sync_state` after each media item today - reuse that exact call; `plan.match_id` vs a `match_id` variable - use whatever `run_push` already has in scope for `ensure_match`).

- [ ] **Step 7: Run the sync suites**

Run: `pytest tests/test_sync_push.py tests/test_sync_media_api.py tests/test_sync_plan.py tests/test_sync_beep_snippets.py tests/test_sync_integration.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/ui/sync_api.py src/splitsmith/sync/client.py src/splitsmith/sync/push.py tests/test_sync_media_api.py tests/test_sync_push.py
git commit -m "feat(sync): remote snippet GC - push deletes beep_review objects for reviewed videos (#821)"
```

---

### Task 6: origin comment + typing in api.ts (#821 f)

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (lines ~1766-1775)

**Interfaces:** none - type/doc fix.

- [ ] **Step 1: Implement**

Change `BeepQueueResponse.origin` from bare `string` to the existing `MatchOrigin` union and fix the comment:

```ts
  /** "desktop" on a hosted mirror, "hosted" on a hosted-native match,
   *  "local" in local mode - lets the SPA pick the honest media surface
   *  (snippet vs proxy) without a second round trip. */
  origin: MatchOrigin;
```

- [ ] **Step 2: Verify**

Run: `pnpm typecheck && pnpm vitest run src/pages/MobileBeepReview.test.tsx`
Expected: clean (if any consumer compared `origin` against a non-union string, the typecheck failure is the point - fix the consumer). Adjust the vitest target to the beep-review test files that exist.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts
git commit -m "refactor(ui): BeepQueueResponse.origin is MatchOrigin; comment covers hosted (#821)"
```

---

### Task 7: Full gates, docker smoke, PR

- [ ] **Step 1: Full backend gate**

Run: `ruff check . && black --check . && pytest -q`
Expected: clean modulo the known ~21 env-dependent local failures (verify against a fresh origin/main run before attributing; never check-and-merge in one command).

- [ ] **Step 2: Docker smoke**

Run: `PATH="$HOME/.claude-tmp/bin:$PATH" pytest -m docker -q` (the PATH prepend is required or docker is silently absent and the smoke skips).
Expected: green - this PR adds a storage-touching route.

- [ ] **Step 3: Full SPA gate**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm test && pnpm eslint src`
Expected: clean.

- [ ] **Step 4: ASCII sweep of added lines**

Run: `git diff origin/main | grep '^+' | grep -nP '[\x{2010}-\x{2015}\x{2018}-\x{201F}\x{2026}\x{00A0}\x{200B}]' ; git diff origin/main | grep '^+' | grep -n ' -- '`
Expected: no output.

- [ ] **Step 5: Open the PR**

```bash
git push -u origin fix/821-beep-sync-followups
gh pr create --title "fix(sync): beep-review sync follow-ups - snippet GC, honest proxy_ready, merge gating (#821)" --body "$(cat <<'EOF'
Hardening wave PR 2 of 2 (post-v0.25.0 plan). Closes the six open items of #821 (g and h shipped earlier; the AAC-priming audible check stays open - needs a human ear on a phone).

- (d) media key gate: per-subdir extension sets; trimmed/*.m4a and beep_review/*.mp4 rejected.
- (e) merge: confirm-only phone writes (beep_reviewed flip, beep_time unchanged) no longer reset trim/shot_detect or queue reprocessing.
- (b) beep queue: snippet listing scoped to per-shooter beep_review/ prefixes instead of the whole shooters/ tree.
- (c) one honest proxy_ready shared by get_project and get_beep_queue; get_project payload carries origin; mirror surfaces say "video stays on the desktop install" instead of promising a proxy that never comes.
- (a) remote snippet GC: new mirror-scoped POST .../media/delete route + client method + push gc phase; reviewed videos' beep_review objects are deleted remotely and dropped from sync_state (backlog included), so snippet_ready stops lying for reopened items.
- (f) BeepQueueResponse.origin typed as MatchOrigin with a correct comment.

Ran locally: ruff, black, pytest, pytest -m docker, pnpm typecheck/test/eslint.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi
EOF
)"
```

After merge: comment on #821 listing what shipped and what remains (the audible alignment check), and close #823 if its two deliberate deferrals are already noted there.
