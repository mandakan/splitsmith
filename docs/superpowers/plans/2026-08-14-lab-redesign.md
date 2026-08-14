# Lab Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the legacy Algorithm Lab's four remaining capabilities (fixture labeling, tuning, promotion, sweeps) into the dev-mode workflow pages, then delete `/dev/legacy/lab` and `Lab.tsx`.

**Architecture:** Approach C from the spec -- fresh page structure/layout on the dev-mode design language; the proven interaction primitives (labeling stepper, waveform diff, snippet player) extracted from `Lab.tsx` into `src/components/lab/` unchanged in behavior; new thin data hook (`useLabRun`) and routes; one backend change (slug-scoped eval merges into a same-config cached run).

**Tech Stack:** React 19 + react-router 7 + vitest/testing-library (SPA, in `src/splitsmith/ui_static/`); FastAPI + pytest (server). `uv` for Python, never pip. Black line length 110.

**Spec:** `docs/superpowers/specs/2026-08-14-lab-redesign-design.md`

## Global Constraints

- Delivery is five PRs, in task order below; each merged on green CI (`gh pr checks --watch`, then squash-merge) before the next lands. Branch names `feat/lab-redesign-<n>-<slug>`, all cut from fresh `main`.
- TDD for every behavior change: write the failing test, watch it fail, implement, watch it pass. Mechanical extractions are covered by the existing suite staying green + `tsc -b --noEmit`.
- All SPA commands run from `src/splitsmith/ui_static/` (`npx vitest run`, `npx tsc -b --noEmit`, `npx eslint src`). Python: `uv run pytest tests/<file> -n0 -q`.
- Never commit `uv.lock` or `package-lock.json` (pre-existing local changes; stage files explicitly, no `git add -A` / `commit -am`).
- Every internal dev-mode link preserves the `?match=` search param (existing `withMatch` pattern in `DeveloperShell.tsx`).
- Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Browser verification uses Playwright's bundled headless Chromium driven from Bash (never the user's Chrome), server on port 5199.
- When a task says "frontend-design pass", load the `frontend-design:frontend-design` skill before writing the JSX and follow the dev-mode (cyan accent, `data-mode="developer"`) token language used by `DevCorpus.tsx` / `DevValidate.tsx`.

---

## PR 1 -- Extract the lab primitives (no behavior change)

### Task 1: Extract audio + visual primitives from Lab.tsx

**Files:**
- Create: `src/splitsmith/ui_static/src/components/lab/labAudio.ts`
- Create: `src/splitsmith/ui_static/src/components/lab/labPalette.ts`
- Create: `src/splitsmith/ui_static/src/components/lab/SnippetPlayer.tsx`
- Create: `src/splitsmith/ui_static/src/components/lab/Pin.tsx`
- Create: `src/splitsmith/ui_static/src/components/lab/ZoomedWaveform.tsx`
- Create: `src/splitsmith/ui_static/src/components/lab/KeyboardLegend.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/Lab.tsx` (delete moved code, import from the new modules)

**Interfaces:**
- Consumes: nothing new.
- Produces (later tasks import these exact names):
  - `labAudio.ts`: `getAudioCtx(): AudioContext`, `loadAudioBuffer(url: string): Promise<AudioBuffer>`, `disposeLabAudio(): void`, `useAudioBuffer(url: string)`, `CONTEXT_HALF_MS`, `AUDIO_CACHE_MAX`
  - `labPalette.ts`: `LAB_PALETTE`, `candidateLineColor(c)`, `otherCandidateColor(c)`, `outcomeLabel(c)`, `outcomeColor(c)`, `fmtPct(x: number): string`
  - `SnippetPlayer.tsx`: `SnippetPlayer` (same props as today)
  - `Pin.tsx`: `Pin`
  - `ZoomedWaveform.tsx`: `ZoomedWaveform`
  - `KeyboardLegend.tsx`: `KeyboardLegend`

- [ ] **Step 1:** In `Lab.tsx`, locate by symbol name (line numbers have drifted): `CONTEXT_HALF_MS`, `getAudioCtx`, `AUDIO_CACHE_MAX`, `loadAudioBuffer`, `disposeLabAudio`, `useAudioBuffer` -> move verbatim to `labAudio.ts` with `export` added to each. Keep the module-level cache map inside `labAudio.ts` (it is deliberately module-global; the extraction preserves that).
- [ ] **Step 2:** Move `LAB_PALETTE`, `candidateLineColor`, `otherCandidateColor`, `outcomeLabel`, `outcomeColor`, `fmtPct` verbatim to `labPalette.ts`, exported.
- [ ] **Step 3:** Move `SnippetPlayer`, `Pin`, `ZoomedWaveform`, `KeyboardLegend` to their own files, exported, each importing what it needs from `labAudio.ts` / `labPalette.ts` / existing `@/lib/*`. Copy the file-header doc comments along.
- [ ] **Step 4:** In `Lab.tsx`, add imports for every moved symbol (`import { Pin } from "@/components/lab/Pin";` etc.) and delete the moved definitions. `fmtPct` and `LAB_PALETTE` are used across many remaining Lab components -- import once at top.
- [ ] **Step 5:** Verify unchanged behavior:
  Run: `npx vitest run && npx tsc -b --noEmit && npx eslint src/components/lab src/pages/Lab.tsx`
  Expected: full suite green (604 tests), tsc clean, 0 lint errors.
- [ ] **Step 6:** Commit:
  ```bash
  git add src/splitsmith/ui_static/src/components/lab src/splitsmith/ui_static/src/pages/Lab.tsx
  git commit -m "refactor(lab): extract audio + visual primitives to components/lab"
  ```

### Task 2: Extract the labeling components

**Files:**
- Create: `src/splitsmith/ui_static/src/components/lab/labels.ts`
- Create: `src/splitsmith/ui_static/src/components/lab/LabelDropdown.tsx`
- Create: `src/splitsmith/ui_static/src/components/lab/LabelBreakdown.tsx`
- Create: `src/splitsmith/ui_static/src/components/lab/LabelProgress.tsx`
- Create: `src/splitsmith/ui_static/src/components/lab/StepThroughPanel.tsx`
- Create: `src/splitsmith/ui_static/src/components/lab/CandidateTable.tsx`
- Create: `src/splitsmith/ui_static/src/components/lab/DiffList.tsx`
- Create: `src/splitsmith/ui_static/src/components/lab/VoterChips.tsx`
- Create: `src/splitsmith/ui_static/src/components/lab/VoterRecallTable.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/Lab.tsx`

**Interfaces:**
- Consumes: Task 1 modules.
- Produces: components exported under their current names with their current props -- `StepThroughPanel`, `CandidateTable`, `DiffList`, `LabelDropdown`, `LabelBreakdown`, `LabelProgress`, `VoterChips`, `VoterRecallTable`; `labels.ts` exports `REASON_SHORTCUTS`, `SUBCLASS_SHORTCUTS`.

- [ ] **Step 1:** Move `REASON_SHORTCUTS` and `SUBCLASS_SHORTCUTS` to `labels.ts`, exported.
- [ ] **Step 2:** Move each listed component verbatim to its own file, exported, importing from Task 1 modules, `labels.ts`, and `@/lib/api` (`LAB_REASONS`, `LAB_SUBCLASSES` stay in `api.ts` -- they mirror server enums).
- [ ] **Step 3:** Update `Lab.tsx` imports; delete moved definitions.
- [ ] **Step 4:** Run: `npx vitest run && npx tsc -b --noEmit && npx eslint src/components/lab src/pages/Lab.tsx`
  Expected: suite green, tsc clean, 0 errors.
- [ ] **Step 5:** Commit:
  ```bash
  git add src/splitsmith/ui_static/src/components/lab src/splitsmith/ui_static/src/pages/Lab.tsx
  git commit -m "refactor(lab): extract labeling components to components/lab"
  ```

### Task 3: PR 1 ship

- [ ] **Step 1:** Push branch `feat/lab-redesign-1-extract`, open PR titled `refactor(lab): extract lab primitives to components/lab` whose body notes zero behavior change and that the legacy page now renders the extracted components.
- [ ] **Step 2:** `gh pr checks <n> --watch --fail-fast`, then `gh pr merge <n> --squash --delete-branch`. Pull `main`.

---

## PR 2 -- Backend: slug-scoped eval merges into a same-config cached run

### Task 4: Merge semantics for the eval cache

**Files:**
- Modify: `src/splitsmith/ui/server.py` (the `_run` closure inside `POST /api/lab/eval`, currently writing `_lab_universe_cache["last_run"] = run` unconditionally)
- Test: `tests/test_lab_eval_merge.py` (new)

**Interfaces:**
- Consumes: `lab_module.run_eval(runtime, slugs=..., config=..., progress=...)`, `lab_module.rescore_universe(universe: EvalUniverse, config: EvalConfig) -> EvalRun` (`src/splitsmith/lab/core.py:835`).
- Produces: `GET /api/lab/last-run` returns the union of fixtures after a scoped eval with an unchanged config; replacement behavior when config differs.

- [ ] **Step 1: Write the failing test.** Build the test around a fake runtime/monkeypatched `run_eval` so no CLAP/PANN models load. Pattern on `tests/test_lab_promote.py`'s use of `create_app` + `TestClient` + `tests.conftest.bound_match_id`. Monkeypatch `server`'s `lab_module.run_eval` to return canned `EvalRun`s built via `lab_core` model constructors:

  ```python
  """Slug-scoped eval must merge into a same-config cached run.

  A full Validate run is expensive (~10 min); labeling one fixture
  triggers a scoped eval and must not clobber it. Same config_hash ->
  merge (scoped fixtures replace/extend the cached universe); different
  config -> replace, as before.
  """
  # Arrange: seed _lab_universe_cache via one fake full eval (fixtures A, B),
  # then run a scoped eval for fixture B' (same config) and assert
  # /api/lab/last-run contains {A, B'} with B's fixture object replaced.
  # Then run a scoped eval with a different config and assert last-run
  # contains only the scoped fixture.
  ```

  Concretely: drive `POST /api/matches/{mid}/lab/eval` twice with monkeypatched `run_eval` returning (1) a two-fixture run, (2) a one-fixture run with the same `config_hash`, poll the job endpoints to completion (jobs run inline under TestClient's threadpool -- poll `GET /api/me/jobs/{id}` until terminal), then `GET /api/lab/last-run` and assert both slugs present and the overlapping slug carries the second run's data. Third call with a config override (e.g. `{"consensus": 3}`) must replace.
- [ ] **Step 2:** Run: `uv run pytest tests/test_lab_eval_merge.py -n0 -q` -- Expected: FAIL (last-run holds only the scoped fixture after step-2 eval).
- [ ] **Step 3: Implement.** In the `_run` closure, replace the two cache-write lines with:

  ```python
  cached = _lab_universe_cache.get("last_run")
  if (
      wanted_slugs is not None
      and cached is not None
      and cached.config_hash == run.config_hash
  ):
      # Scoped re-eval of a subset: fold it into the cached run so a
      # full Validate universe survives per-fixture labeling evals.
      fresh = {f.slug for f in run.universe.fixtures}
      keep = [f for f in cached.universe.fixtures if f.slug not in fresh]
      merged = cached.universe.model_copy(
          update={"fixtures": keep + list(run.universe.fixtures)}
      )
      run = lab_module.rescore_universe(merged, cfg)
  if persist:
      try:
          lab_module.save_run(run)
      except OSError as exc:
          logger.warning("lab: save_run failed: %s", exc)
  _lab_universe_cache["universe"] = run.universe
  _lab_universe_cache["last_run"] = run
  ```

  Note `save_run` moves BELOW the merge so the persisted run is the merged one; delete the old pre-merge `save_run` block.
- [ ] **Step 4:** Run: `uv run pytest tests/test_lab_eval_merge.py tests/test_lab_promote.py -n0 -q` and `uv run pytest tests/ -n0 -q -k "lab"` -- Expected: all green.
- [ ] **Step 5:** `uv run black --check src/splitsmith/ui/server.py tests/test_lab_eval_merge.py && uv run ruff check` the same files.
- [ ] **Step 6:** Commit, push `feat/lab-redesign-2-eval-merge`, PR `feat(lab): scoped eval merges into a same-config cached run`, merge on green.

---

## PR 3 -- Fixture detail page

### Task 5: `useLabRun` data hook

**Files:**
- Create: `src/splitsmith/ui_static/src/components/lab/useLabRun.ts`
- Test: `src/splitsmith/ui_static/src/components/lab/useLabRun.test.tsx`

**Interfaces:**
- Consumes: `api.getLastLabRun`, `api.runLabEval({slugs?, config, persist})`, `api.pollJob`, `api.rescoreLabUniverse` (existing), `DEFAULT_CONFIG` (move it here from `Lab.tsx`, exported).
- Produces:
  ```ts
  export function useLabRun(opts?: { autoRescore?: boolean }): {
    run: LabEvalRun | null;
    setRun: (r: LabEvalRun | null) => void;
    config: LabEvalConfig;
    setConfig: (c: Partial<LabEvalConfig>) => void;
    resetConfig: () => void;
    runEval: (slugs?: string[]) => Promise<void>;
    evalLoading: boolean;
    rescoreLoading: boolean;
    error: string | null;
  };
  export const DEFAULT_CONFIG: LabEvalConfig;
  ```

- [ ] **Step 1: Write the failing test** (harness: render a probe component inside `MemoryRouter`, mock `@/lib/api` with the `importOriginal` spread idiom used across the suite):
  ```tsx
  it("hydrates from the server's last-run cache on mount", async () => {
    // getLastLabRun resolves RUN -> hook exposes it and adopts its config
  });
  it("runEval(slugs) posts the scoped eval and refreshes the run", async () => {
    // click probe button calling runEval(["s1"]) ->
    // runLabEval called with {slugs:["s1"], config, persist:true},
    // pollJob resolves succeeded, getLastLabRun called again
  });
  it("config changes rescore the cached universe when autoRescore is on", async () => {
    // with a run present, setConfig -> after the 120ms debounce
    // rescoreLabRun called; run replaced by its result
  });
  ```
  Use `vi.useFakeTimers()` for the debounce case (mirror the existing debounce test idioms, e.g. `Snackbar.test.tsx`).
- [ ] **Step 2:** Run: `npx vitest run src/components/lab/useLabRun.test.tsx` -- Expected: FAIL (module missing).
- [ ] **Step 3: Implement** by lifting the exact logic already in `Lab.tsx` (`runEval` with `inFlightEvalRef` + the `Array.isArray` slug guard, the 120 ms rescore debounce effect, `getLastLabRun` mount hydration) into the hook. `autoRescore` defaults true; the detail page passes `{ autoRescore: false }`.
- [ ] **Step 4:** Run the hook test -- Expected: PASS. Then `npx vitest run && npx tsc -b --noEmit`.
- [ ] **Step 5:** Commit: `feat(lab): useLabRun data hook`.

### Task 6: `/dev/corpus/:slug` fixture detail page

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/dev/DevFixtureDetail.tsx`
- Modify: `src/splitsmith/ui_static/src/App.tsx` (route under `DeveloperShell`: `<Route path="dev/corpus/:slug" element={<DesktopGate screen="Developer tools" links={false}><DevFixtureDetail /></DesktopGate>} />`)
- Modify: `src/splitsmith/ui_static/src/pages/dev/DevCorpus.tsx` (row `onOpen` target: `` navigate(`/dev/corpus/${fx.slug}${search}`) `` where `search` preserves `?match=`)
- Test: `src/splitsmith/ui_static/src/pages/dev/DevFixtureDetail.test.tsx`

**Interfaces:**
- Consumes: `useLabRun`, Task 1-2 components, `api.listLabFixtures`, `api.getFixturePeaks`, `api.getFixtureAudit`.
- Produces: the labeling page later tasks/queue link to at `/dev/corpus/:slug`.

- [ ] **Step 1: Write the failing tests:**
  ```tsx
  it("auto-runs a scoped eval when the fixture is missing from the cached run", async () => {
    // getLastLabRun rejects -> expect runLabEval called with {slugs:[SLUG]}
  });
  it("renders the labeling working area once the run contains the fixture", async () => {
    // getLastLabRun resolves a run containing SLUG ->
    // CandidateTable + StepThroughPanel content visible, no auto-eval fired
  });
  it("walks the corpus with prev/next preserving ?match=", async () => {
    // catalog of three slugs, mounted at the middle one with ?match=m-1 ->
    // "Next fixture" link href = /dev/corpus/<third>?match=m-1
  });
  ```
  Reuse the mock shapes from `Lab.fixtureLite.test.tsx` (catalog record) and `Lab.promoteMatch.test.tsx` (run/config); mock `@/components/Waveform` and the lab `ZoomedWaveform`/`SnippetPlayer` (audio APIs don't exist in jsdom).
- [ ] **Step 2:** Run them -- Expected: FAIL (page missing).
- [ ] **Step 3: Implement with the frontend-design pass** (load the skill first). Page structure -- port the *content* of legacy `FixtureDetail`/`FixtureDetailLite` onto this skeleton, dev-mode tokens throughout:
  ```tsx
  export function DevFixtureDetail() {
    const { slug } = useParams();  // + useSearchParams for ?match=
    const navigate = useNavigate();
    const { run, setRun, config, runEval, evalLoading, error } = useLabRun({ autoRescore: false });
    // catalog for header identity + prev/next ordering
    // focused = run?.universe.fixtures.find(f => f.slug === slug) ?? null
    // auto-eval effect: if (catalogLoaded && !focused && !evalLoading) void runEval([slug])
    return (
      <div className="mx-auto max-w-[1500px] space-y-4 px-7 py-5">
        <header>{/* slug, event id, GT count; actions: Edit markers -> /review,
                    delete, re-review promotion; Prev / Next (withMatch) */}</header>
        <EvalStatusStrip {...{ focused, evalLoading, error, retry: () => runEval([slug]) }} />
        {/* full-width ZoomedWaveform diff when focused */}
        <div className="grid grid-cols-[minmax(0,1fr)_400px] gap-4">
          <CandidateTable ... />
          <div className="sticky top-[var(--shell-header-h,86px)] self-start">
            <StepThroughPanel ... onLabelChanged={(updated) => updated ? setRun(updated) : runEval([slug])} />
          </div>
        </div>
        <footer>{/* metrics row: P/R/F1 + VoterRecallTable, compact */}</footer>
      </div>
    );
  }
  ```
  `EvalStatusStrip` is a small local component: `cached` chip when `focused`, spinner + job message while `evalLoading`, error + retry button on failure. Delete/re-review actions reuse the exact handlers from legacy `FixtureRow` (confirm dialog + `api.deleteFixture`, `/promote-review` link when `anchor_slug`).
- [ ] **Step 4:** Run: `npx vitest run src/pages/dev/DevFixtureDetail.test.tsx` then the full suite + tsc + eslint. Expected: green.
- [ ] **Step 5:** Commit: `feat(dev): full-page fixture detail with labeling at /dev/corpus/:slug`.

### Task 7: PR 3 ship + live drive

- [ ] **Step 1:** `npm run build`; start `uv run splitsmith ui --lab --no-browser --port 5199`; Playwright drive (bundled Chromium): corpus row click -> `/dev/corpus/:slug`, auto-eval chip appears, labeling panel renders within ~30 s, prev/next navigates, `?match=` survives. Screenshot to `~/.claude-tmp/`.
- [ ] **Step 2:** Push `feat/lab-redesign-3-detail-page`, PR, merge on green.

---

## PR 4 -- Promote to Corpus, tuning + sweeps to Validate

### Task 8: Promote panels on the Corpus page

**Files:**
- Create: `src/splitsmith/ui_static/src/components/lab/PromoteStagesPanel.tsx` (from `PromoteAllStagesButton` -- keep every behavior pinned by `Lab.promoteMatch.test.tsx`: match selector defaulting `?match=` -> recents[0], shooter checkboxes all-on, per-(shooter, stage) rows, `promoteFixtureIn` with `shooter_slug`)
- Create: `src/splitsmith/ui_static/src/components/lab/PromoteFromAnchorPanel.tsx` (from `PromoteFromAnchorButton`)
- Modify: `src/splitsmith/ui_static/src/pages/dev/DevCorpus.tsx` (header gains the two entry points; the panel renders as a full-width expandable section above the table, not a popover -- frontend-design pass)
- Test: `src/splitsmith/ui_static/src/pages/dev/DevCorpus.promote.test.tsx` (port ALL cases from `Lab.promoteMatch.test.tsx`, remounted on DevCorpus; watch them fail against DevCorpus before wiring)
- Modify: `src/splitsmith/ui_static/src/pages/Lab.tsx` (renders the moved panels from their new home so legacy stays working until PR 5)

- [ ] **Step 1:** Port the test file; run: expected FAIL (no panel on DevCorpus).
- [ ] **Step 2:** Move the components; mount in DevCorpus; legacy Lab imports the moved components.
- [ ] **Step 3:** Full suite + tsc + eslint green; commit `feat(dev): promotion moves to the Corpus page`.

### Task 9: Tuning + sweeps on Validate, ship PR 4

**Files:**
- Create: `src/splitsmith/ui_static/src/components/lab/TuningPanel.tsx` (from `TuningCard` + `SaveYamlButton`; consumes `useLabRun` values via props: `config`, `onChange`, `onReset`, `run`, `rescoreLoading`)
- Modify: `src/splitsmith/ui_static/src/pages/dev/DevValidate.tsx` (mount `TuningPanel` + `SweepsCard` side by side under the metrics; wire its existing run state through `useLabRun({ autoRescore: true })`)
- Test: `src/splitsmith/ui_static/src/pages/dev/DevValidate.tuning.test.tsx`

- [ ] **Step 1: Failing test:** slider change on Validate calls `rescoreLabUniverse` after the debounce and the headline metrics re-render from the response (fake timers; mock api).
- [ ] **Step 2:** Implement; DevValidate swaps its ad-hoc eval state for `useLabRun` (keep its run-config bar semantics identical -- read its current implementation first and preserve its request shapes).
- [ ] **Step 3:** Full suite + tsc + eslint; live drive: Validate sliders rescore < 1 s against a cached run; sweeps render.
- [ ] **Step 4:** Push `feat/lab-redesign-4-tuning-promote`, PR, merge on green.

---

## PR 5 -- Delete the legacy Lab

### Task 10: Deletion, redirects, reference sweep

**Files:**
- Delete: `src/splitsmith/ui_static/src/pages/Lab.tsx`, `Lab.promoteMatch.test.tsx`, `Lab.fixtureLite.test.tsx` (their behaviors now covered by `DevCorpus.promote.test.tsx` + `DevFixtureDetail.test.tsx` -- verify coverage parity before deleting, case by case)
- Modify: `src/splitsmith/ui_static/src/App.tsx`:
  ```tsx
  <Route path="dev/legacy/lab" element={<Navigate to="/dev/corpus" replace />} />
  <Route path="dev/legacy/lab/:slug" element={<RedirectLegacyLabSlug />} />
  // RedirectLegacyLabSlug: useParams -> <Navigate to={`/dev/corpus/${slug}`} replace />
  // and re-point the old /lab(/:slug) redirects at the same targets.
  ```
- Modify: `src/splitsmith/ui_static/src/components/developer/DeveloperShell.tsx` (remove the "Lab playground LEGACY" SubLink)
- Modify: `src/splitsmith/ui_static/src/pages/Pick.tsx` (`postBindTarget` -> `/dev/corpus?match=...`), `src/splitsmith/ui_static/src/pages/Pick.devMode.test.tsx` (expectation follows)
- Modify: stale comments naming legacy routes: `DevReviewQueue.tsx` header, `DevCorpus.tsx` line ~180 comment, `src/splitsmith/cli.py` `--lab` help text ("Algorithm Lab page" -> "developer Lab surfaces")

- [ ] **Step 1: Failing tests first:** update `Pick.devMode.test.tsx` expectation to `/dev/corpus?match=m-hfo`; add a redirect test (mount App routes at `/dev/legacy/lab/<slug>?match=m` -> lands on `/dev/corpus/<slug>` -- note the redirect must carry the search string via `<Navigate to={{pathname, search}}>`).
- [ ] **Step 2:** Apply deletions + redirects; `grep -rn "legacy/lab\|Lab.tsx\|from \"@/pages/Lab\"" src/` must come back empty (routes file's own redirect strings excepted).
- [ ] **Step 3:** Full suite + tsc + eslint; `npm run build`.
- [ ] **Step 4: Full workflow live drive** (bundled Chromium, fresh server): pick match from dev mode -> `/dev/corpus?match=` -> promote section opens with shooters all-on -> row click -> detail auto-eval -> label one candidate via keyboard shortcut -> Validate: slider rescore + sweeps -> legacy URL `/dev/legacy/lab/<slug>` redirects to the new detail. Screenshots to `~/.claude-tmp/`.
- [ ] **Step 5:** Push `feat/lab-redesign-5-delete-legacy`, PR (body includes the drive evidence), merge on green.
- [ ] **Step 6:** Update `CLAUDE.md`'s "Detection pipeline" note if it references the legacy lab route (it references the module + CLI only -- verify, change nothing if accurate).
