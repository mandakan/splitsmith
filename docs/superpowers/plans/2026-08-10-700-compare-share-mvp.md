# Compare Share MVP (#700) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Anonymous share-link holders watch the compare grid at `/share/{token}/compare/{stage}`; hosted Compare resolves trims through object storage; the share surface exposes the decided minimal fields.

**Architecture:** Backend first: storage-aware trim resolution returning logical relative refs (`exports/<name>` | `trimmed/<name>`), a relative-only hosted-aware stream fallback, two allowlist shapes, coach-note stripping. Then SPA: share route + DesktopGate + share-mode gating in Compare.tsx + drift logging.

**Tech Stack:** FastAPI/Pydantic (ui/server.py), object storage helpers (audio.py, export_storage.py), pytest + hosted fixtures (tests/hosted_helpers.py); React/TS, vitest + @testing-library/react.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-10-700-compare-share-mvp-design.md` (committed on this branch) - binding.
- Share reads must NEVER write: share requests impersonate the owner tenant; `current_share_request` (server.py:1000) is the only write defense.
- `_share_alias` (server.py:6322-6368) is the single implementation of token resolution and impersonation - never duplicate it.
- The stream fallback must not accept absolute paths or traversal - only `^(exports|trimmed)/[^/]+\.mp4$`.
- New text uses "-" only, never "--" or em dash.
- Field rename is `video_path` -> `video_ref` on `CompareShooterRecord` (server.py:3823-3836) and its TS mirror (api.ts) - no alias/back-compat field (pre-production, clean-no-fallbacks).
- Python gates: `ruff check .`, `black --check .`, pytest (venv at `.venv/bin`, prepend `/opt/homebrew/opt/ffmpeg-full/bin` to PATH). SPA gates from `src/splitsmith/ui_static`: `pnpm typecheck`, `pnpm test`, scoped eslint.
- Commit messages end with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi`

---

### Task 1: Storage-aware trim resolution; `video_ref` on the compare payload

**Files:**
- Modify: `src/splitsmith/ui/server.py` - `CompareShooterRecord` (:3823-3836), `get_stage_compare` (:12586-12696)
- Test: Modify `tests/test_compare_stage_endpoint.py` (rename fallout + new resolution tests)

**Interfaces:**
- Consumes: `audio_helpers` trim-key convention (`audio.py:685-696`: `f"{project._storage_scope}/trimmed/{name}"`), `export_storage._storage_export_key` (`{scope}/exports/{name}`), `storage.exists`, `match_project.StageVideo.video_id`.
- Produces: `CompareShooterRecord.video_ref: str | None` - a logical ref, `exports/<base>_trimmed.mp4` or `trimmed/stage{n}_cam_{video_id}_trimmed.mp4`. Also a module-level helper in server.py: `_resolve_compare_trim(legacy, shooter_root, stage_number, stage_name, primary, storage) -> str | None` returning that ref. Tasks 2 and 4 depend on exactly these shapes.

- [ ] **Step 1: Write the failing tests**

In `tests/test_compare_stage_endpoint.py`:

a. Mechanical fallout: the existing 4 tests assert on `shooter["shots"]`; add to `test_compare_heals_legacy_doc_in_memory_only` an assertion that the payload key is `video_ref` (value `None` in that fixture - no trim files exist) and that no key named `video_path` remains in the shooter record.

b. New local-resolution test: extend `_bootstrap` (or add a variant) that also creates `shooter_root/"trimmed"/f"stage1_cam_{video_id}_trimmed.mp4"` (empty bytes are fine; read `video_id` from the loaded `MatchProject` stage's primary video, `StageVideo.video_id`). Assert `video_ref == f"trimmed/stage1_cam_{video_id}_trimmed.mp4"`. Then also create `shooter_root/"exports"/f"{base}_trimmed.mp4"` where `base = export_naming.stage_file_base(1, "K-vallen")` (import the same helper server.py uses - grep `stage_file_base` in server.py for its import source) and assert the ref flips to the `exports/...` form (lossless preferred).

c. New hosted-resolution test in `tests/test_share_routes.py` style is Task 3's job; here add a unit-level test for the helper against a fake storage object:

```python
class _FakeStorage:
    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.supports_presigned_get = True

    def exists(self, key: str) -> bool:
        return key in self.existing
```

Call `_resolve_compare_trim` directly with `storage=_FakeStorage({f"{scope}/trimmed/stage1_cam_{vid}_trimmed.mp4"})` and a project whose `_storage_scope` is set (see how `tests/test_media_presign_serving.py` builds hosted-shaped projects; mimic minimally). Assert the trimmed ref; assert the exports key wins when both exist; assert `None` when neither exists. Match the real call signature you implement - if `_resolve_compare_trim` takes the project and derives scope itself, build the fixture accordingly.

- [ ] **Step 2: Run to verify failure**

`PATH=/opt/homebrew/opt/ffmpeg-full/bin:$PWD/.venv/bin:$PATH pytest tests/test_compare_stage_endpoint.py -v` - new tests FAIL (`video_ref` missing / helper undefined); old tests still pass.

- [ ] **Step 3: Implement**

In server.py:

a. `CompareShooterRecord`: rename field to `video_ref: str | None`, comment stating it is a logical ref relative to the shooter's exports/trimmed dirs, resolvable by the stream route in both modes.

b. Extract the current resolution block of `get_stage_compare` (:12630-12646, the exports/trimmed dir derivation + lossless/audit-cache checks) into module-level `_resolve_compare_trim(...)` placed just above `get_stage_compare`. Behavior:

```python
def _resolve_compare_trim(
    legacy: MatchProject,
    shooter_root: Path,
    stage_number: int,
    stage_name: str,
    primary: StageVideo,
    storage: MediaStorage | None,
) -> str | None:
    """Logical ref for the stage's playable trim: ``exports/<name>`` or
    ``trimmed/<name>``, lossless export preferred. Local mode checks the
    actual dirs on disk; hosted checks object storage under the
    established key conventions. Returns None when nothing is playable."""
    base = stage_file_base(stage_number, stage_name)
    lossless_name = f"{base}_trimmed.mp4"
    cache_name = f"stage{stage_number}_cam_{primary.video_id}_trimmed.mp4"
    hosted = storage is not None and storage.supports_presigned_get
    if hosted:
        scope = legacy._storage_scope
        if scope:
            if storage.exists(f"{scope}/exports/{lossless_name}"):
                return f"exports/{lossless_name}"
            if storage.exists(f"{scope}/trimmed/{cache_name}"):
                return f"trimmed/{cache_name}"
            return None
    exports = ...  # the existing exports_dir derivation from :12630-12639, verbatim
    trimmed = ...  # ditto
    if (exports / lossless_name).exists():
        return f"exports/{lossless_name}"
    if (trimmed / cache_name).exists():
        return f"trimmed/{cache_name}"
    return None
```

Adjust names/annotations to what server.py actually imports (`MediaStorage` may be named differently - grep how `stream_video` at :10763 types `storage`; if `_storage_scope` is private-but-established, use it the way `audio_helpers._storage_trim_key` does, or call that helper if it fits). The hosted branch must not fall through to disk checks when a scope exists - hosted resolution is storage-only.

c. `get_stage_compare` calls the helper; sets `video_ref=ref`; `beep_offset` is set when `ref is not None` (same condition shape as today at :12645-12646). Where does `storage` come from in the handler - mirror how `stream_video` obtains it (grep `storage =` near :10760).

- [ ] **Step 4: Run to verify pass**

Same command as Step 2 - all pass. Also `pytest tests/test_compare_grid_endpoint.py tests/test_trims_to_compare_e2e.py -q` (neighbors that touch compare shapes; if they assert on `video_path`, update them to `video_ref` - that fallout is in scope for this task).

- [ ] **Step 5: Lint + commit**

ruff/black on touched files, then commit `feat(compare): resolve trims through storage, expose logical video_ref`.

---

### Task 2: Stream fallback - relative refs only, hosted-aware

**Files:**
- Modify: `src/splitsmith/ui/server.py` - `stream_shooter_video` non-registered fallback (:12863-12893) and the route docstring (:12786-12791)
- Test: Modify `tests/test_stream_proxy.py` or create `tests/test_compare_stream_ref.py` (implementer's call - follow where the existing fallback tests live; grep `stream_shooter_video` in tests/)

**Interfaces:**
- Consumes: Task 1's ref grammar `^(exports|trimmed)/[^/]+\.mp4$`; `serve_media` (as used at :10788-10801); storage key conventions as Task 1.
- Produces: fallback behavior - relative ref in `?path=`, 404 for absolute/traversal/non-mp4/unknown; hosted 307 presign. The registered-video branch (:12813-12861) byte-identical.

- [ ] **Step 1: Failing tests**

Rejection matrix (local fixture): `path=/abs/anything.mp4`, `path=trimmed/../secrets.mp4`, `path=trimmed/clip.mov`, `path=exports/nope.mp4` (well-formed, file absent) - all 404 with no file served. Happy path local: create `trimmed/stage1_cam_{vid}_trimmed.mp4` with real bytes, request `?path=trimmed/...&kind=auto`, assert 200 + bytes (or 206 with Range - mirror whatever the existing fallback test asserts today). Hosted: under the `tests/test_media_presign_serving.py` fixture style, put the trim key in fake storage and assert 307 with a presigned Location; absent key -> 404.

- [ ] **Step 2: Verify failure** (absolute-path test will PASS against current code only if the file doesn't exist - make the fixture create a real file at an absolute path inside exports_dir and assert 404 anyway, proving the absolute form is rejected, not just missing).

- [ ] **Step 3: Implement**

Replace the fallback body: parse `path` against `re.fullmatch(r"(exports|trimmed)/([^/]+\.mp4)", path)` - no match -> 404 immediately (before any dir derivation). Local: resolve dir kind against the actual `exports_dir`/`trimmed_dir` (existing derivation code), keep the containment check as defense-in-depth, serve as today. Hosted (`storage is not None and storage.supports_presigned_get` and scope set): build `{scope}/{dirkind}/{name}`, `storage.exists` -> `serve_media` 307, else 404. Update the route docstring - it currently promises "non-registered paths are always served from local disk"; that promise is retired.

- [ ] **Step 4: Verify pass** + run `tests/test_media_presign_serving.py` (registered branch untouched proof).

- [ ] **Step 5: Grep-verify no other caller sends absolute paths** to this route: `rg "shooterVideoStreamUrl|match/shooters/.*videos/stream" src/splitsmith/ui_static/src tests/` - the only SPA caller is Compare.tsx (updated in Task 4 to pass `video_ref`, which is already relative). Note findings in the report.

- [ ] **Step 6: Lint + commit** `feat(compare): stream fallback takes logical refs, serves hosted via presign`.

---

### Task 3: Share allowlist + boundary probes + coach-note stripping

**Files:**
- Modify: `src/splitsmith/ui/server.py` - `_SHARE_PATH_RE` (:936-946), `_build_coach_response` (:10382-10383)
- Test: Modify `tests/test_share_routes.py`

**Interfaces:**
- Consumes: Tasks 1-2 (the share compare happy path streams a ref end-to-end).
- Produces: two new allowlist shapes; share coach responses with `coaching_note=None`, `improvement_flag=False`.

- [ ] **Step 1: Failing tests** (hosted fixtures: `hosted_env`/`hosted_app`, `_setup_shared_match`, `_share_url` - all in tests/test_share_routes.py:229-255)

- Regex-lock: add `match/stage/1/compare` and `match/shooters/some-slug/videos/stream` to `test_share_path_re_accepts` (:597+); add near-miss rejects (`match/stage/x/compare`, `match/shooters/videos/stream`, `match/stage/1/compare/extra`) to `test_share_path_re_rejects`.
- Happy path: `client.get(_share_url(token, "match/stage/1/compare"))` -> 200, shooters present, payload keys are exactly the minimal surface (assert `video_ref` present-or-null and `video_path` absent; assert shot keys are `shot_number`/`time_after_beep`/`source`/`interval_class`).
- Stream containment: share-prefixed stream request with a well-formed but absent ref -> 404; malformed ref -> 404 (the middleware's uniform-404 seam, server.py:6359-6364, means both look identical - assert body equality with an unknown-token 404 like #786's probes do).
- Revoked/non-GET: extend the path lists in `test_share_revoked_token_404_on_every_path` (:319-343) and `test_share_whitelisted_non_get_404` (:377+) with the two new shapes.
- Coach stripping: extend `test_share_coach_read_classifies_in_memory_without_persisting` (:456-480): seed a shot with `coaching_note="private!"` and `improvement_flag=True`; share read returns `coaching_note is None` / `improvement_flag is False`; then assert an OWNER read of the same stage still returns the real values (the strip is share-scoped, not global).

- [ ] **Step 2: Verify failure** (`pytest -m docker` may be required for the hosted fixtures - check the marker on this file and run the same way its existing tests run).

- [ ] **Step 3: Implement**

`_SHARE_PATH_RE` gains, after the existing stream alternative:

```python
    r"|match/stage/\d+/compare"
    r"|match/shooters/[^/]+/videos/stream"
```

`_build_coach_response`: where `coaching_note`/`improvement_flag` are serialized (:10382-10383), gate on `current_share_request.get()`:

```python
                    "improvement_flag": (
                        False if current_share_request.get()
                        else bool(s.get("improvement_flag", False))
                    ),
                    "coaching_note": (
                        None if current_share_request.get() else s.get("coaching_note")
                    ),
```

- [ ] **Step 4: Verify pass**, run the whole `tests/test_share_routes.py` + `tests/test_share_og_routes.py` (og shells do sub-requests into the share API - prove no regression).

- [ ] **Step 5: Lint + commit** `feat(share): allowlist stage compare + ref streaming, strip coach notes for viewers`.

---

### Task 4: SPA - share route, DesktopGate, share-mode Compare, ref rename

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`CompareShooterRecord` :1641-1650, `shooterVideoStreamUrl` :2920-2924)
- Modify: `src/splitsmith/ui_static/src/App.tsx` (share route block :202-206)
- Modify: `src/splitsmith/ui_static/src/pages/Compare.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.tsx` (Compare affordance)
- Modify: `src/splitsmith/ui_static/src/pages/RankingTable.test.tsx` (fixture rename)
- Test: Modify `src/splitsmith/ui_static/src/components/share/ShareShell.test.tsx` or `App.routes.test.tsx` (route mount), extend as needed

**Interfaces:**
- Consumes: `video_ref` payload field (Task 1); allowlisted routes (Task 3); `useMatchHref`/share-token detection (`matchHref.ts:24-42`); `DesktopGate` (`components/DesktopOnlyNotice.tsx:13-25`).
- Produces: route `share/:token/compare/:stage`; Compare.tsx share mode; `api.shooterVideoStreamUrl(slug, ref)` unchanged signature (ref goes into the same `path` query param).

- [ ] **Step 1: Failing tests**

- Route test (mirror the style of the existing share-route assertions in App.routes tests or ShareShell.test.tsx): rendering the app at `/share/tok123/compare/2` on a desktop-width environment mounts the Compare page (assert on a stable Compare landmark, e.g. the "Ranking" header once data loads is too deep - assert the DesktopGate wrapper + Compare's top-level test id; add a `data-testid="compare-page"` to Compare's root if none exists); on mobile width it renders `DesktopOnlyNotice` with a link whose href is `/share/tok123/results`.
- Share-mode gating: a focused component test is impractical for the whole page; instead extract the affordance predicate as a tiny exported helper in Compare.tsx (`export function isShareView(pathname: string): boolean` matching `/^\/share\//`) and unit-test it, then assert gating via the route test where feasible (e.g. the Audit tab button absent when mounted under `/share/...`). Mock `api.*` fetches the way existing App.routes tests do (check their pattern first; if they stub fetch/`request`, mirror it).
- RankingTable.test.tsx: fixture field `video_path` -> `video_ref` (type-driven).

- [ ] **Step 2: Verify failure** (`pnpm test -- run` the touched files; typecheck will also fail until the rename lands - that is expected mid-task).

- [ ] **Step 3: Implement**

a. api.ts: rename the interface field; `shooterVideoStreamUrl` keeps its `(slug, path, kind?)` signature - callers now pass refs.

b. App.tsx share block gains:

```tsx
<Route
  path="compare/:stage"
  element={
    <DesktopGate screen="Compare">
      <Compare />
    </DesktopGate>
  }
/>
```

(import Compare + DesktopGate as the owner block at :248 does).

c. Compare.tsx:
- `const shareView = isShareView(location.pathname)` (via `useLocation`; the exported helper from Step 1).
- Gate the affordances behind `!shareView`: Audit tab (:318-331), Coach tab (:335-344), "Open in audit" (:540-547), "Build trim cache" (:526-538), empty-state "Audit {name}" buttons (:585-596).
- Empty-state copy: share view renders "The match owner hasn't prepared comparison video for this stage yet." in place of audit instructions (same component, ternary on `shareView`).
- All `video_path` reads become `video_ref` (truthiness sites at :118, :132, :403-416, :473, :569, :716-717 - typecheck finds them all).

d. ResultsStage.tsx: a "Compare shooters" link (styled like existing secondary affordances on that page - copy an existing Link's classes) to `href("compare/{stage}")` via `useMatchHref` so it resolves owner- and share-side; wrap in the page's existing desktop-only visibility pattern if one exists, else `hidden md:inline-flex`.

- [ ] **Step 4: Verify pass**: `pnpm typecheck && pnpm test -- run` (all), scoped eslint on touched files.

- [ ] **Step 5: Commit** `feat(ui): compare view behind share links - desktop-only, read-only`.

---

### Task 5: Drift instrumentation

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Compare.tsx` (resync loop :149-171 region)
- Test: extend an existing Compare-adjacent test file only if a pure helper falls out; otherwise this task ships without new tests (logging only).

**Interfaces:**
- Consumes: the resync loop's computed `el.currentTime - target`.
- Produces: `console.info("[compare-sync] stage %s max drift %sms over %ss", ...)` emitted on pause and on unmount, then reset. A `maxDriftRef = useRef(0)` updated inside the loop (before the threshold branch), plus a session-seconds counter.

- [ ] **Step 1: Implement directly** (no TDD - observable is a console line): update `maxDriftRef.current = Math.max(maxDriftRef.current, Math.abs(el.currentTime - target))` in the slave loop; in the effect cleanup and in the pause handler, if `maxDriftRef.current > 0`, emit the info line with stage number, `Math.round(maxDriftRef.current * 1000)`, and elapsed playing time, then zero it.
- [ ] **Step 2: Verify**: `pnpm typecheck`, `pnpm test -- run` (no regressions), scoped eslint (console.info must not trip a no-console rule - if it does, check how the SPA's eslint config treats console and use the allowed level or an inline disable with justification).
- [ ] **Step 3: Commit** `feat(compare): log max observed sync drift per playback session`.

---

### Task 6: Full gates, final review, PR

**Files:** fixes only if gates fail.

- [ ] **Step 1:** `ruff check . && black --check .` and full pytest (venv + ffmpeg-full PATH). Baseline: 3109+/16 skipped grows by the new tests.
- [ ] **Step 2:** `pytest -m docker` (hosted share/storage fixtures - memory says CI skips these; docker PATH workaround: symlink at `~/.claude-tmp/bin` if docker missing from non-interactive PATH).
- [ ] **Step 3:** SPA: `pnpm typecheck && pnpm test`, scoped eslint, dash sweep of branch-added lines.
- [ ] **Step 4:** Final whole-branch review (controller dispatches; session model), fix wave if findings.
- [ ] **Step 5:** Push, open PR titled `feat(share): compare view behind share links (#700 MVP)` - body: what/why incl. the hosted-Compare fix, the minimal-surface decision link to #700 comment, verification evidence, staging-verify checklist as follow-up. Reference `Closes #700`? NO - the epic stays open (burned-in parity etc. remain); reference `Part of #700`. Do not merge - owner reviews.
