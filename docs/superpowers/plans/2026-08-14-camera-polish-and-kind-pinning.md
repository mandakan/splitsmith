# Camera Polish + Stream-Kind Pinning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close issue #870: three SPA polish items from the camera-selection review (PR A), then pin each camera's stream `kind` to the clip its `beep_in_clip` was measured against (PR B, backend + SPA).

**Architecture:** PR A is frontend-only refactor/test work on `Compare.tsx` / `ResultsStage.tsx`. PR B threads one new field (`kind: "trim" | "source"`) from `_video_beep_in_clip`'s existing trim resolution through `_coach_video_entries` into the SPA's `CoachVideoEntry`, and the camera pickers pass it to `videoStreamUrl` instead of `auto` - so a trim job completing mid-session can never shift served bytes under a stale beep anchor.

**Tech Stack:** React 18 + TypeScript + vitest (src/splitsmith/ui_static/), FastAPI + pytest (src/splitsmith/ui/server.py, tests/).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-14-camera-polish-and-kind-pinning-design.md`.
- Two branches/PRs: PR A on `chore/870-camera-polish` (exists, holds the spec commit), PR B on `feat/870-kind-pinning` cut AFTER PR A merges, from updated `origin/main`.
- No new dependencies. No DB/queue changes (no docker smoke needed).
- ASCII punctuation only in new copy/comments; single `-` dash, never `--` or em dash.
- PR A is behavior-preserving: no existing test may be modified except to import moved helpers; assertions stay identical.
- SPA test runs per task: `pnpm --dir src/splitsmith/ui_static exec vitest run <files>`; backend: `uv run pytest tests/test_coach_api.py -q`.
- Python gate for PR B: `uv run ruff check . && uv run black --check .` plus the scoped pytest.
- Commit message trailer required:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi`

---

### Task 1: Share-mount ?v= test (PR A)

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.cameras.test.tsx` (append one test)

**Interfaces:**
- Consumes: the file's existing harness - `renderStage(path, shooters, {videos})`, `TWO_CAMS`, `mainVideoSrcs()`, and the already-declared `/share/:token/results/:slug/:stage` route in its `renderStage` router.
- Produces: nothing new.

- [ ] **Step 1: Append the test** (adapt helper names to the file's actual harness if they differ - assertions must stay as written):

```tsx
it("share mount: a ?v= moment link opens on the named camera", async () => {
  renderStage(
    "/share/tok123/results/anna/2?t=1.00&v=1",
    [makeShooter("anna", "Anna", [[2, "audited"]])],
    { videos: TWO_CAMS },
  );
  await screen.findByText(/steel rush/i);
  expect(mainVideoSrcs()).toEqual(["http://localhost/cam-b.mp4"]);
  expect(screen.getByRole("group", { name: /cameras/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run - this is a pinning test, it should PASS immediately**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/ResultsStage.cameras.test.tsx`
Expected: PASS (all). If it FAILS, stop and report - that is a real share-mount bug, not a test problem.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/ResultsStage.cameras.test.tsx
git commit -m "test(ui): pin the share-mount ?v= camera contract"
```

---

### Task 2: Compare resync listener hygiene (PR A)

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Compare.tsx` (the resync effect, currently deps `[camIndexBySlug, camsBySlug, orderedShooters, effectiveBeep, timeSinceBeep, isPlaying]`)

**Interfaces:**
- Consumes: existing `videoRefs`, `effectiveBeep`, `orderedShooters`, `timeSinceBeep`, `isPlaying`.
- Produces: same behavior; no API change.

- [ ] **Step 1: Add mirror refs** (near the other refs at the top of `Compare()`):

```tsx
// Read by the resync effect without re-arming it per 120ms sync tick -
// the effect must fire on camera/list changes only, but its target math
// still needs the current clock and play state.
const timeSinceBeepRef = useRef(0);
const isPlayingRef = useRef(false);
useEffect(() => {
  timeSinceBeepRef.current = timeSinceBeep;
}, [timeSinceBeep]);
useEffect(() => {
  isPlayingRef.current = isPlaying;
}, [isPlaying]);
// At most one pending metadata listener per tile (keyed by slug), so a
// slow-loading swap cannot stack stale-target listeners.
const pendingResyncRef = useRef<Map<string, { el: HTMLVideoElement; fn: () => void }>>(
  new Map(),
);
```

- [ ] **Step 2: Rewrite the resync effect** (replace the whole existing effect, keeping its comment):

```tsx
// A tile whose src just swapped reloads at clip time 0; put it back on
// the shared clock once its metadata is in. The drift guard keeps this
// from fighting the sync engine or the user's scrubbing.
useEffect(() => {
  const pending = pendingResyncRef.current;
  videoRefs.current.forEach((el, slug) => {
    const shooter = orderedShooters.find((s) => s.slug === slug);
    if (!shooter) return;
    const beep = effectiveBeep(shooter);
    if (beep == null) return;
    const target = Math.max(0, beep + timeSinceBeepRef.current);
    if (Math.abs(el.currentTime - target) < 0.3) return;
    const apply = () => {
      pending.delete(slug);
      el.currentTime = Math.max(0, beep + timeSinceBeepRef.current);
      if (isPlayingRef.current) void el.play().catch(() => {});
    };
    if (el.readyState >= 1) {
      apply();
      return;
    }
    const prev = pending.get(slug);
    if (prev) prev.el.removeEventListener("loadedmetadata", prev.fn);
    pending.set(slug, { el, fn: apply });
    el.addEventListener("loadedmetadata", apply, { once: true });
  });
  return () => {
    pending.forEach(({ el, fn }) => el.removeEventListener("loadedmetadata", fn));
    pending.clear();
  };
}, [camIndexBySlug, camsBySlug, orderedShooters, effectiveBeep]);
```

- [ ] **Step 3: Run covering tests**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/Compare.cameras.test.tsx src/pages/Compare.test.tsx src/pages/Compare.isShareView.test.ts`
Expected: PASS, unmodified. Also: `pnpm --dir src/splitsmith/ui_static exec eslint src/pages/Compare.tsx` - 0 errors, 0 warnings (the narrowed dep array must not trigger exhaustive-deps; refs are exempt).

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Compare.tsx
git commit -m "refactor(ui): resync effect fires per swap, not per sync tick"
```

---

### Task 3: ResultsStage cam-index DRY (PR A)

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.tsx`

**Interfaces:**
- Produces (module-level, above `ResultsStage()`, not exported):

```tsx
// Camera identity is the payload index (primary first); a stale index
// (coach reloaded with fewer cameras) resolves to 0 rather than erroring.
function resolveCamIndex(coach: CoachStageResponse, raw: number): number {
  return coach.videos[raw] ? raw : 0;
}

// The beep anchor of the clip the SPA plays for this camera; falls back
// to the primary anchor when the entry is missing or beepless.
function camBeep(coach: CoachStageResponse, index: number): number {
  return coach.videos[index]?.beep_in_clip ?? coach.beep_time;
}
```

- [ ] **Step 1: Add the two helpers** as above.

- [ ] **Step 2: Replace the three duplicated expressions** (current lines ~156-160 `momentTime`, ~372-375 `camDeltaForShots`, and the post-early-return `camIndex`/`activeBeep`/`camDelta` block):

```tsx
const momentTime =
  moment != null && coach != null
    ? camBeep(coach, resolveCamIndex(coach, activeCamIndex)) + moment.t
    : null;
```

```tsx
const camDeltaForShots = coach
  ? camBeep(coach, resolveCamIndex(coach, activeCamIndex)) - coach.beep_time
  : 0;
```

```tsx
const camIndex = resolveCamIndex(coach, activeCamIndex);
const activeVideo = coach.videos[camIndex];
const activeBeep = camBeep(coach, camIndex);
const camDelta = activeBeep - coach.beep_time;
```

- [ ] **Step 3: Close the stale-index edge in `handleSelectCam`** - replace the `prevBeep` line:

```tsx
const prevBeep = camBeep(coach, resolveCamIndex(coach, prev));
```

- [ ] **Step 4: Run covering tests** (must pass unmodified - pure refactor):

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/ResultsStage.cameras.test.tsx src/pages/ResultsStage.test.tsx src/pages/ResultsStage.trimstale.test.tsx`
Expected: PASS. Then `pnpm --dir src/splitsmith/ui_static typecheck` - clean.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/ResultsStage.tsx
git commit -m "refactor(ui): DRY camera index/beep resolution on ResultsStage"
```

---

### Task 4: PR A gate + PR (PR A)

- [ ] **Step 1: Full gate**

```bash
pnpm --dir src/splitsmith/ui_static typecheck
pnpm --dir src/splitsmith/ui_static test
pnpm --dir src/splitsmith/ui_static exec eslint src/pages/Compare.tsx src/pages/ResultsStage.tsx src/pages/ResultsStage.cameras.test.tsx
git diff origin/main -- src/splitsmith/ui_static | grep '^+' | grep -vE '^\+\+\+' | LC_ALL=C grep -nE -- '--|—' || echo clean
```

Expected: all green, 0 eslint problems, `clean` (CSS-var hits like `--color-*` are allowed; prose/comments must be clean).

- [ ] **Step 2: Push and open PR A** (title `refactor(ui): camera polish follow-ups from #868 review`, body references issue #870 items 1/2/4 and the standard footer). Merge per the session's merge-on-green flow before starting Task 5.

---

### Task 5: Backend kind field (PR B)

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`_video_beep_in_clip` ~11461, `_coach_video_entries` ~11509)
- Test: `tests/test_coach_api.py` (append)

**Interfaces:**
- Produces: `_video_clip_anchor(slug, project, stage_number, video) -> tuple[float | None, str]` returning `(beep_in_clip, kind)` with `kind in {"trim", "source"}`; `_video_beep_in_clip` becomes a thin `[0]` wrapper (its other caller, the `prim` line ~11555, stays untouched). Coach entries gain `"kind"`.

- [ ] **Step 1: Write the failing tests** - append to `tests/test_coach_api.py`:

```python
def test_get_coach_entry_kind_source_without_trim(tmp_path: Path) -> None:
    """No trim on disk: the entry pins kind=source, matching the bytes
    stream_video would serve for kind=auto."""
    client, _audit, base = _bootstrap(tmp_path)
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200, resp.text
    assert resp.json()["videos"][0]["kind"] == "source"


def test_get_coach_entry_kind_trim_with_trim_on_disk(tmp_path: Path) -> None:
    """Trim + params sidecar on disk: kind=trim rides with the trim-based
    beep_in_clip, so the SPA can pin the exact clip the anchor was
    measured against."""
    client, base = _bootstrap_legacy_trim(tmp_path, stage_numbers=(1,))
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["videos"][0]["kind"] == "trim"
    assert body["videos"][0]["beep_in_clip"] == pytest.approx(3.0)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_coach_api.py -q -k kind`
Expected: FAIL with `KeyError: 'kind'`.

- [ ] **Step 3: Implement.** Rename the body of `_video_beep_in_clip` to `_video_clip_anchor` returning tuples, keeping the docstring (append one line: `Returns (anchor, kind) where kind names the clip measured: "trim" or "source".`); each `return` becomes:

- `if video.beep_time is None:` branch: resolve first, then return `(None, kind)` - move the beep check AFTER trim resolution so beepless cameras still report an accurate kind:

```python
    def _video_clip_anchor(
        slug: str,
        project: MatchProject,
        stage_number: int,
        video: StageVideo,
    ) -> tuple[float | None, str]:
        # (docstring as described above)
        resolved = audio_helpers.resolve_trim_for_read(
            state.shooter_root(slug), stage_number, video, project=project
        )
        if resolved is not None:
            if video.beep_time is None:
                return (None, "trim")
            pre_buffer = audio_helpers.trim_pre_buffer_seconds_for(
                resolved, default=project.trim_pre_buffer_seconds
            )
            return (min(video.beep_time, pre_buffer), "trim")
        trimmed = audio_helpers.trimmed_video_path(
            state.shooter_root(slug), stage_number, video, project=project
        )
        if audio_helpers.trim_available(project, trimmed):
            if video.beep_time is None:
                return (None, "trim")
            return (min(video.beep_time, project.trim_pre_buffer_seconds), "trim")
        return (video.beep_time, "source")
```

(Keep the two existing explanatory comments - hosted storage-only cache, legacy-key degradation - attached to their branches.) Then:

```python
    def _video_beep_in_clip(
        slug: str,
        project: MatchProject,
        stage_number: int,
        video: StageVideo,
    ) -> float | None:
        return _video_clip_anchor(slug, project, stage_number, video)[0]
```

And in `_coach_video_entries`, replace the per-video dict build:

```python
        for v in ordered_videos:
            anchor, kind = _video_clip_anchor(slug, project, stg.stage_number, v)
            out.append(
                {
                    "path": str(v.path),
                    "role": v.role,
                    "beep_in_clip": anchor,
                    "kind": kind,
                }
            )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_coach_api.py -q`
Expected: PASS (all, including pre-existing anchor tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_coach_api.py
git commit -m "feat(coach): report which clip kind each camera's beep anchor measured"
```

---

### Task 6: SPA kind pinning (PR B)

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`CoachVideoEntry`), `src/splitsmith/ui_static/src/pages/ResultsStage.tsx` (player src + `srcFor`), `src/splitsmith/ui_static/src/pages/Compare.tsx` (`tileSrc`)
- Test: `src/splitsmith/ui_static/src/pages/ResultsStage.cameras.test.tsx`, `src/splitsmith/ui_static/src/pages/Compare.cameras.test.tsx`

**Interfaces:**
- Consumes: Task 5's `kind` field; existing `videoStreamUrl(slug, path, kind)` whose kind union already includes `"trim" | "source"`.

- [ ] **Step 1: Write the failing tests.** In both cameras test files, extend the mocked `videoStreamUrl` to encode kind, update fixtures, and assert pinning. ResultsStage file: change the mock to `videoStreamUrl: (_slug: string, path: string, kind = "auto") => \`http://localhost/${kind}/${path}\``, add `kind: "trim" as const` to both `TWO_CAMS` entries (and `kind: "source" as const` to the no-primary fixture's entry), then update every URL assertion from `http://localhost/<file>` to `http://localhost/trim/<file>` (or `/source/` for the no-primary test) - the existing tests become the pinning assertions. Compare file: same mock change, add `kind: "trim" as const` to anna's two entries and bob's entry, and update the swap test's expectation to `"http://localhost/coach/..."` equivalent with kind, e.g. mock `videoStreamUrl: (_s: string, path: string, kind = "auto") => \`http://localhost/coach/${kind}/${path}\`` and expect `http://localhost/coach/trim/anna-b.mp4`.

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/ResultsStage.cameras.test.tsx src/pages/Compare.cameras.test.tsx`
Expected: FAIL - TypeScript object-literal error on `kind` until api.ts changes, then URL mismatches (`auto` vs `trim`) until the pages pin.

- [ ] **Step 3: Implement.**

`api.ts`, `CoachVideoEntry` gains:

```ts
  /** Which clip ``beep_in_clip`` was measured against - pin it as the
   *  stream kind so a trim job completing mid-session cannot shift the
   *  served bytes under a stale anchor (same hazard the audit screen
   *  avoids with explicit trim/proxy kinds). */
  kind: "trim" | "source";
```

`ResultsStage.tsx` render:

```tsx
src={api.videoStreamUrl(slug, activeVideo.path, activeVideo.kind)}
```

```tsx
srcFor={(e) => api.videoStreamUrl(slug, e.path, e.kind)}
```

`Compare.tsx` `tileSrc`:

```tsx
if (idx > 0) {
  const cam = camsBySlug[s.slug][idx];
  return api.videoStreamUrl(s.slug, cam.path, cam.kind);
}
```

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/ResultsStage.cameras.test.tsx src/pages/Compare.cameras.test.tsx src/pages/ResultsStage.test.tsx src/pages/Compare.test.tsx`
Expected: PASS. Then `pnpm --dir src/splitsmith/ui_static typecheck` - clean (this also catches any other CoachVideoEntry literal fixtures needing the new field; add `kind: "source"` to those, e.g. ResultsStage.test.tsx's `makeCoach`).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/pages/ResultsStage.tsx src/splitsmith/ui_static/src/pages/Compare.tsx src/splitsmith/ui_static/src/pages/ResultsStage.cameras.test.tsx src/splitsmith/ui_static/src/pages/Compare.cameras.test.tsx src/splitsmith/ui_static/src/pages/ResultsStage.test.tsx
git commit -m "feat(ui): pin camera streams to the clip kind the beep anchor measured"
```

(Include only files actually changed; enumerate, never glob.)

---

### Task 7: PR B gate + PR (PR B)

- [ ] **Step 1: Full gate**

```bash
pnpm --dir src/splitsmith/ui_static typecheck
pnpm --dir src/splitsmith/ui_static test
pnpm --dir src/splitsmith/ui_static exec eslint src/lib/api.ts src/pages/ResultsStage.tsx src/pages/Compare.tsx
uv run ruff check . && uv run black --check .
uv run pytest tests/test_coach_api.py -q
git diff origin/main | grep '^+' | grep -vE '^\+\+\+' | LC_ALL=C grep -nE -- '--|—' || echo clean
```

Expected: all green (dash sweep: CSS-var/CLI-flag hits allowed, prose clean). Note: ~21 env-dependent local failures exist in the FULL pytest suite - do not run it; the scoped file is the gate, CI runs the rest.

- [ ] **Step 2: Push and open PR B** (title `feat: pin camera streams to the measured clip kind`, body: closes the remaining #870 item, references the race; standard footer). Merge per the session's merge-on-green flow. After both PRs merge, close #870 with a comment summarizing what shipped and the corrected trim-pipeline finding.
