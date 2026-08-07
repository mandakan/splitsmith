# Share View Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public share surface self-explanatory: tappable-looking stage rows with a play affordance, a route back from stage playback to the overview, an uncluttered shooter switcher, and a timer that freezes on the last shot instead of running to video end.

**Architecture:** All changes are in the React SPA (`src/splitsmith/ui_static/src`). The Results overview and ResultsStage playback pages already serve both the owner route (`/match/:matchId/results...`) and the anonymous share route (`/share/:token/results...`); share mode is detected via the `token` URL param. No backend changes, no new dependencies. Spec: `docs/superpowers/specs/2026-08-07-share-view-polish-design.md`.

**Tech Stack:** React 18 + TypeScript, react-router-dom, Tailwind (design tokens), lucide-react icons, vitest + Testing Library (jsdom).

## Global Constraints

- Read-only by contract: Results/ResultsStage/components/results/ must not gain mutations, localStorage, or operator-only assumptions.
- Color is never the sole state carrier (WCAG 2.2 AA project rule); every icon-only affordance pairs with visible text or an sr-only label.
- New copy and comments use a single ASCII dash "-", never "--" or an em dash.
- No new dependencies.
- All commands below run from `src/splitsmith/ui_static/` unless prefixed otherwise. The test runner is `pnpm test` (vitest run); single file: `pnpm vitest run <path>`.
- Commit trailer for every commit:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi`

---

### Task 1: Freeze the beep-relative clock at stage end

The ShotTicker HUD and the transport row both render `time - beepTime`
unclamped, so they keep counting until the video file ends. Clamp both to
the last shot's `time_from_beep` via one shared helper.

**Files:**
- Modify: `src/lib/splits.ts` (add `beepElapsed` helper)
- Modify: `src/components/results/ShotTicker.tsx:33-36`
- Modify: `src/components/results/ResultsPlayer.tsx:202-204,345-353`
- Test: `src/components/results/ShotTicker.test.tsx` (new)

**Interfaces:**
- Consumes: `CoachShot` from `@/lib/api` (fields `shot_number`, `time_from_beep`, `time_absolute`, `split`, ...).
- Produces: `beepElapsed(time: number, beepTime: number, stageTime: number | null): number` exported from `@/lib/splits`. Later tasks do not depend on it, but both call sites in this task do.

- [ ] **Step 1: Write the failing test**

Create `src/components/results/ShotTicker.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

import { ShotTicker } from "@/components/results/ShotTicker";
import type { CoachShot } from "@/lib/api";

// jsdom has no matchMedia; ShotTicker probes prefers-reduced-motion.
// matches: true also disables the pulse animation path in tests.
beforeAll(() => {
  window.matchMedia = ((query: string) => ({
    matches: true,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

const BEEP = 5;

function makeShot(n: number, timeFromBeep: number): CoachShot {
  return {
    shot_number: n,
    ms_after_beep: timeFromBeep * 1000,
    time_from_beep: timeFromBeep,
    time_absolute: BEEP + timeFromBeep,
    split: n === 1 ? timeFromBeep : 0.25,
    interval_class: null,
    interval_class_source: null,
    improvement_flag: false,
    coaching_note: null,
    stale: false,
  };
}

const SHOTS = [makeShot(1, 1.2), makeShot(2, 1.45)];

describe("ShotTicker elapsed clock", () => {
  it("tracks time between beep and last shot", () => {
    render(<ShotTicker shots={SHOTS} beepTime={BEEP} time={BEEP + 1.3} baselines={null} />);
    expect(screen.getByText("1.30")).toBeInTheDocument();
  });

  it("freezes at the stage time once past the last shot", () => {
    render(<ShotTicker shots={SHOTS} beepTime={BEEP} time={BEEP + 30} baselines={null} />);
    expect(screen.getByText("1.45")).toBeInTheDocument();
    expect(screen.queryByText("30.00")).not.toBeInTheDocument();
  });

  it("keeps counting when there are no shots to freeze on", () => {
    render(<ShotTicker shots={[]} beepTime={BEEP} time={BEEP + 3} baselines={null} />);
    expect(screen.getByText("3.00")).toBeInTheDocument();
  });
});
```

Note on the freeze assertion: with the bug present the HUD shows "30.00";
the split row shows shot 2's split "0.25", so "1.45" appears nowhere and
the test fails against pre-change code.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm vitest run src/components/results/ShotTicker.test.tsx`
Expected: FAIL - "freezes at the stage time once past the last shot"
(unable to find element with text "1.45"). The other two cases pass.

- [ ] **Step 3: Add the helper and wire both call sites**

In `src/lib/splits.ts`, add (near `currentShotIndex`):

```ts
/** Beep-relative elapsed seconds for the playback readouts, clamped to
 *  the stage time (the last shot's time_from_beep) so the clock stops
 *  on the final shot the way a shot timer does - not at video end.
 *  ``stageTime`` null (no shots) leaves the upper bound open. */
export function beepElapsed(
  time: number,
  beepTime: number,
  stageTime: number | null,
): number {
  const raw = Math.max(0, time - beepTime);
  return stageTime != null ? Math.min(raw, stageTime) : raw;
}
```

In `src/components/results/ShotTicker.tsx`:

```tsx
import {
  INTERVAL_LABEL,
  TIER_NEUTRAL_COLOR,
  type TierBaselines,
  beepElapsed,
  currentShotIndex,
  gapTier,
} from "@/lib/splits";
```

and replace the `elapsed` line inside the component:

```tsx
export function ShotTicker({ shots, beepTime, time, baselines }: ShotTickerProps) {
  const idx = currentShotIndex(shots, time);
  const shot = idx >= 0 ? shots[idx] : null;
  const stageTime = shots.length > 0 ? shots[shots.length - 1].time_from_beep : null;
  const elapsed = beepElapsed(time, beepTime, stageTime);
```

In `src/components/results/ResultsPlayer.tsx`, import the helper:

```tsx
import { TIER_NEUTRAL_COLOR, type TierBaselines, beepElapsed, gapTier } from "@/lib/splits";
```

and change the transport-row readout (the `stageTime` const at line 204
already exists just above; keep it):

```tsx
        <span className="font-mono text-sm tabular-nums text-ink-2">
          {clock(beepElapsed(time, beepTime, stageTime))}
          <span className="text-muted">
            {" / "}
            {stageTime != null ? clock(stageTime) : "-"}
          </span>
        </span>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm vitest run src/components/results/ShotTicker.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/splits.ts \
  src/splitsmith/ui_static/src/components/results/ShotTicker.tsx \
  src/splitsmith/ui_static/src/components/results/ResultsPlayer.tsx \
  src/splitsmith/ui_static/src/components/results/ShotTicker.test.tsx
git commit -m "fix(ui): freeze the results playback clock at the last shot"
```

(Repo root paths shown for git; the trailer from Global Constraints goes
on every commit.)

---

### Task 2: Stage list rows - play affordance + share-view wording

Audited rows lose the AUDITED chip and gain a circled play icon (owner
and share). On share only, non-audited rows collapse to "No video" and
the header counter says "videos" instead of "audited".

**Files:**
- Modify: `src/pages/Results.tsx`
- Test: `src/pages/Results.test.tsx` (new)

**Interfaces:**
- Consumes: `buildStageMatrix` cells (`cell.status`, `cell.tone`, `cell.shooter`), `shareToken` already derived at `Results.tsx:103`.
- Produces: no new exports. A `PlayAffordance` local component and an `isShare` boolean inside `Results.tsx`.

- [ ] **Step 1: Write the failing test**

Create `src/pages/Results.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import type { MatchProject, ShooterListEntry, StageStatus } from "@/lib/api";

import { Results } from "@/pages/Results";

// Hosted-only chrome (Share button) is out of scope here; pin local mode.
vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: () => "local" as const };
});

// Multi-shooter Results fetches every shooter's project for stage times.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn().mockImplementation(() => new Promise(() => {})),
    },
  };
});

function makeShooter(
  slug: string,
  name: string,
  statuses: [number, StageStatus][],
): ShooterListEntry {
  return {
    slug,
    name,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: statuses.filter(([, s]) => s === "audited").length,
    stages_total: statuses.length,
    video_count: 0,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: statuses.map(([stage_number, status]) => ({ stage_number, status })),
  };
}

function makeProject(): MatchProject {
  return {
    schema_version: 1,
    name: "bromma-2026",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    competitor_name: null,
    scoreboard_match_id: null,
    scoreboard_content_type: null,
    selected_shooter_id: null,
    selected_competitor_id: null,
    shooter_token: null,
    match_date: null,
    stages: [
      {
        stage_number: 1,
        stage_name: "Steel Rush",
        time_seconds: 20,
        scorecard_updated_at: null,
        videos: [],
        skipped: false,
        placeholder: false,
        time_seconds_manual: false,
        stage_rounds: null,
        scorecard: null,
      },
    ],
    unassigned_videos: [],
    last_scanned_dir: null,
    raw_dir: null,
    audio_dir: null,
    trimmed_dir: null,
    exports_dir: null,
    probes_dir: null,
    thumbs_dir: null,
    trim_pre_buffer_seconds: 5,
    trim_post_buffer_seconds: 5,
    automation: {},
    nudges_dismissed_stages: [],
    compare_camera: null,
    raw_videos: [],
  };
}

const SHOOTERS = [
  makeShooter("anna", "Anna", [[1, "audited"]]),
  makeShooter("bjorn", "Bjorn", [[1, "ready"]]),
  makeShooter("cleo", "Cleo", [[1, "skipped"]]),
];

function Shell({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function renderResults(path: string) {
  const ctx: MatchShellOutletContext = {
    project: makeProject(),
    health: null,
    shooters: SHOOTERS,
    refresh: vi.fn(),
    origin: null,
  };
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Shell ctx={ctx} />}>
          <Route path="/match/:matchId/results" element={<Results />} />
          <Route path="/share/:token/results" element={<Results />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Results rows - owner surface", () => {
  it("gives audited rows a watch affordance instead of the audited chip", () => {
    renderResults("/match/m1/results");
    expect(screen.getAllByText(", watch run").length).toBeGreaterThan(0);
    expect(screen.queryByText("Audited")).not.toBeInTheDocument();
  });

  it("keeps operator status chips on non-audited rows", () => {
    renderResults("/match/m1/results");
    expect(screen.getAllByText("Ready").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Skipped").length).toBeGreaterThan(0);
  });

  it("keeps the audited wording in the header counter", () => {
    renderResults("/match/m1/results");
    expect(screen.getByText(/audited/)).toBeInTheDocument();
  });
});

describe("Results rows - share surface", () => {
  it("gives audited rows the watch affordance", () => {
    renderResults("/share/tok123/results");
    expect(screen.getAllByText(", watch run").length).toBeGreaterThan(0);
  });

  it("collapses non-audited and skipped rows to a No video label", () => {
    renderResults("/share/tok123/results");
    expect(screen.queryByText("Ready")).not.toBeInTheDocument();
    expect(screen.queryByText("Skipped")).not.toBeInTheDocument();
    expect(screen.queryByText("Not audited")).not.toBeInTheDocument();
    expect(screen.getAllByText("No video").length).toBeGreaterThan(0);
  });

  it("counts videos, not audits, in the header", () => {
    renderResults("/share/tok123/results");
    expect(screen.getByText(/videos/)).toBeInTheDocument();
    expect(screen.queryByText(/audited/)).not.toBeInTheDocument();
  });
});
```

Layout note: at jsdom's default viewport both the mobile cards and the
desktop matrix render (Tailwind `lg:` classes are media-query CSS that
jsdom does not apply), which is why the assertions use `getAllByText`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm vitest run src/pages/Results.test.tsx`
Expected: FAIL - no ", watch run" elements, "Audited" chip present,
share cases show "Ready"/"Skipped".

- [ ] **Step 3: Implement the row changes in `src/pages/Results.tsx`**

Add `Play` to the lucide import:

```tsx
import { Loader2, Play, RefreshCw, Share2 } from "lucide-react";
```

Below the `StatusChip` component, add:

```tsx
/** Row-trailing play affordance for audited (watchable) rows. Muted at
 *  rest, LED on the row's hover/focus (the row Link carries `group`).
 *  aria-hidden - the row's sr-only ", watch run" suffix names the action
 *  so the icon is never the sole cue. */
function PlayAffordance() {
  return (
    <span
      aria-hidden
      className="inline-flex size-7 shrink-0 items-center justify-center rounded-full border border-rule text-muted transition-colors group-hover:border-led group-hover:text-led group-focus-visible:border-led group-focus-visible:text-led"
    >
      <Play className="size-3.5 fill-current" />
    </span>
  );
}
```

Inside `Results()`, right after the `shareToken` line (103), add:

```tsx
  const isShare = Boolean(shareToken);
```

Header counter (lines 320-325) - share wording:

```tsx
          <span>
            <span className="font-bold text-ink-2">{totals.auditedShooterStages}</span>
            {" / "}
            <span>{totals.totalShooterStages}</span>
            {isShare ? " videos" : " audited"}
          </span>
```

Mobile audited row (the `Link` at lines 362-389): add `group` to the
className and swap the chip for the affordance + sr-only text. Full
replacement of the `return` for the audited branch:

```tsx
                    return (
                      <Link
                        key={cell.shooter.slug}
                        to={href("results", cell.shooter.slug, String(row.stageNumber))}
                        className="group flex min-h-11 items-center gap-3 px-4 py-2 hover:bg-surface-3 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led focus-visible:ring-inset"
                      >
                        {!isSingleShooter && (
                          <span className="flex-1 truncate font-display text-sm font-semibold uppercase tracking-tight text-ink">
                            {cell.shooter.name}
                          </span>
                        )}
                        <span
                          className={cn(
                            "flex flex-col leading-tight",
                            isSingleShooter ? "flex-1 items-start" : "items-end",
                          )}
                        >
                          <span className="font-mono text-sm tabular-nums text-ink-2">
                            {time != null ? formatTime(time) : "-"}
                          </span>
                          {hitFactor != null ? (
                            <span className="font-mono text-[0.6875rem] tabular-nums text-muted">
                              {formatHitFactor(hitFactor)}
                            </span>
                          ) : null}
                        </span>
                        <span className="sr-only">, watch run</span>
                        <PlayAffordance />
                      </Link>
                    );
```

Mobile non-audited row (lines 391-419): keep the owner branch as-is and
add a share branch before it. Full replacement of the code from the
`// Skipped rows carry their state...` comment to the closing of that
`return`:

```tsx
                  // Share viewers get no workflow states - any row
                  // without a watchable run reads "No video", including
                  // skipped (the skip decision is operator context).
                  if (isShare) {
                    return (
                      <div
                        key={cell.shooter.slug}
                        className="flex min-h-11 items-center gap-3 px-4 py-2"
                      >
                        {!isSingleShooter && (
                          <span className="flex-1 truncate font-display text-sm font-semibold uppercase tracking-tight text-subtle">
                            {cell.shooter.name}
                          </span>
                        )}
                        <span
                          className={cn(
                            "font-mono text-xs uppercase tracking-[0.08em] text-subtle",
                            isSingleShooter && "flex-1",
                          )}
                        >
                          No video
                        </span>
                      </div>
                    );
                  }
                  // Skipped rows carry their state in the chip alone - a
                  // "Not audited" label next to a "Skipped" chip contradicts
                  // itself (skipping was a decision, not missing work).
                  const skipped = cell.status === "skipped";
                  return (
                    <div
                      key={cell.shooter.slug}
                      className="flex min-h-11 items-center gap-3 px-4 py-2"
                    >
                      {!isSingleShooter && (
                        <span className="flex-1 truncate font-display text-sm font-semibold uppercase tracking-tight text-subtle">
                          {cell.shooter.name}
                        </span>
                      )}
                      {skipped ? (
                        isSingleShooter && <span aria-hidden className="flex-1" />
                      ) : (
                        <span
                          className={cn(
                            "font-mono text-xs uppercase tracking-[0.08em] text-subtle",
                            isSingleShooter && "flex-1",
                          )}
                        >
                          Not audited
                        </span>
                      )}
                      <StatusChip tone={cell.tone} status={cell.status} />
                    </div>
                  );
```

Desktop matrix audited cell (the `Link` at lines 481-499): same swap -
add `group`, replace `<StatusChip ... />` with:

```tsx
                        <span className="sr-only">, watch run</span>
                        <PlayAffordance />
```

(className becomes
`"group flex min-h-11 items-center justify-between gap-2 bg-surface-2 px-3 py-2 hover:bg-surface-3 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led focus-visible:ring-inset"`.)

Desktop matrix non-audited cell (lines 501-519): add the share branch
before the existing return, mirroring the mobile one:

```tsx
                  if (isShare) {
                    return (
                      <div
                        key={cell.shooter.slug}
                        className="flex min-h-11 items-center justify-end gap-2 bg-surface-2 px-3 py-2"
                      >
                        <span className="font-mono text-xs uppercase tracking-[0.08em] text-subtle">
                          No video
                        </span>
                      </div>
                    );
                  }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm vitest run src/pages/Results.tsx src/pages/Results.test.tsx`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Results.tsx \
  src/splitsmith/ui_static/src/pages/Results.test.tsx
git commit -m "feat(ui): play affordance + share-view wording on the results stage list"
```

---

### Task 3: Stage playback - back link + shooter switcher

ResultsStage gets a kicker-styled "All stages" link above the title
(both surfaces; on share it is the only way back), and on multi-shooter
matches the static shooter-name line becomes a minimal native select.

**Files:**
- Modify: `src/pages/ResultsStage.tsx`
- Test: `src/pages/ResultsStage.test.tsx` (new)

**Interfaces:**
- Consumes: `useMatchHref()` (round-trips `/share/:token`), `shooters` from outlet context with `stage_statuses: { stage_number, status }[]`.
- Produces: no new exports.

- [ ] **Step 1: Write the failing test**

Create `src/pages/ResultsStage.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import type { CoachStageResponse, ShooterListEntry, StageStatus } from "@/lib/api";

import { ResultsStage } from "@/pages/ResultsStage";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getStageCoach: vi.fn(),
      getProject: vi.fn().mockRejectedValue(new Error("no project")),
      getMatchCoachDistributions: vi.fn().mockRejectedValue(new Error("no dist")),
      videoStreamUrl: () => "http://localhost/video.mp4",
    },
  };
});

import { api } from "@/lib/api";

beforeAll(() => {
  // jsdom lacks both; ResultsStage measures the player box and the
  // ShotTicker probes prefers-reduced-motion.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  window.matchMedia = ((query: string) => ({
    matches: true,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

function makeCoach(): CoachStageResponse {
  return {
    stage_number: 2,
    stage_name: "Steel Rush",
    beep_time: 5,
    videos: [{ path: "trimmed/stage2.mp4", role: "primary", beep_in_clip: 5 }],
    shots: [],
  };
}

function makeShooter(
  slug: string,
  name: string,
  statuses: [number, StageStatus][],
): ShooterListEntry {
  return {
    slug,
    name,
    selected_shooter_id: null,
    selected_competitor_id: null,
    stages_audited: statuses.filter(([, s]) => s === "audited").length,
    stages_total: statuses.length,
    video_count: 0,
    cameras: [],
    stages_missing_trim: 0,
    stage_statuses: statuses.map(([stage_number, status]) => ({ stage_number, status })),
  };
}

function Shell({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function renderStage(path: string, shooters: ShooterListEntry[]) {
  vi.mocked(api.getStageCoach).mockResolvedValue(makeCoach());
  const ctx: MatchShellOutletContext = {
    project: null,
    health: null,
    shooters,
    refresh: vi.fn(),
    origin: null,
  };
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Shell ctx={ctx} />}>
          <Route path="/match/:matchId/results/:slug/:stage" element={<ResultsStage />} />
          <Route path="/share/:token/results/:slug/:stage" element={<ResultsStage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

const MULTI = [
  makeShooter("anna", "Anna", [[1, "audited"], [2, "audited"]]),
  makeShooter("bjorn", "Bjorn", [[1, "audited"], [2, "ready"]]),
];
const SOLO = [makeShooter("anna", "Anna", [[2, "audited"]])];

describe("ResultsStage back link", () => {
  it("links back to the share overview from a share stage URL", async () => {
    renderStage("/share/tok123/results/anna/2", MULTI);
    const back = await screen.findByRole("link", { name: /all stages/i });
    expect(back).toHaveAttribute("href", "/share/tok123/results");
  });

  it("links back to the match overview on the owner surface", async () => {
    renderStage("/match/m1/results/anna/2", MULTI);
    const back = await screen.findByRole("link", { name: /all stages/i });
    expect(back).toHaveAttribute("href", "/match/m1/results");
  });
});

describe("ResultsStage shooter switcher", () => {
  it("renders a select on multi-shooter matches, disabling shooters without an audited take", async () => {
    renderStage("/share/tok123/results/anna/2", MULTI);
    const select = await screen.findByRole("combobox", { name: /shooter/i });
    expect(select).toHaveValue("anna");
    const bjorn = screen.getByRole("option", { name: "Bjorn" }) as HTMLOptionElement;
    expect(bjorn.disabled).toBe(true);
    const anna = screen.getByRole("option", { name: "Anna" }) as HTMLOptionElement;
    expect(anna.disabled).toBe(false);
  });

  it("renders plain text, no select, for a single shooter", async () => {
    renderStage("/match/m1/results/anna/2", SOLO);
    await waitFor(() => {
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Anna")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm vitest run src/pages/ResultsStage.test.tsx`
Expected: FAIL - no link named "all stages", no combobox.

- [ ] **Step 3: Implement in `src/pages/ResultsStage.tsx`**

Imports: extend the lucide line and add `useNavigate`:

```tsx
import { ArrowLeft, ArrowRight, ChevronDown, ChevronLeft, Loader2 } from "lucide-react";
import { Link, useNavigate, useOutletContext, useParams } from "react-router-dom";
```

Inside `ResultsStageInner`, after `const href = useMatchHref();`:

```tsx
  const navigate = useNavigate();
```

Replace the `header` const (lines 219-271) with:

```tsx
  const header = (
    <div className="flex items-center gap-3">
      <div className="min-w-0 flex-1">
        <Link
          to={href("results")}
          className="mb-1 inline-flex items-center gap-0.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.14em] text-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
        >
          <ChevronLeft className="size-3.5" aria-hidden />
          All stages
        </Link>
        <h1 className="truncate font-display text-xl font-bold uppercase leading-tight tracking-tight text-ink md:text-2xl">
          <span className="text-led">Stage {pad2(stage)}</span>
          {coach?.stage_name ? <span className="text-ink"> - {coach.stage_name}</span> : null}
        </h1>
        {shooter ? (
          shooters.length > 1 ? (
            <span className="relative inline-flex max-w-full items-center">
              <select
                value={slug}
                onChange={(e) => navigate(href("results", e.target.value, String(stage)))}
                aria-label="Shooter"
                className="cursor-pointer appearance-none truncate bg-transparent pr-4 font-mono text-xs uppercase tracking-[0.08em] text-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
              >
                {shooters.map((s) => (
                  <option
                    key={s.slug}
                    value={s.slug}
                    disabled={
                      !s.stage_statuses.some(
                        (e) => e.stage_number === stage && e.status === "audited",
                      )
                    }
                  >
                    {s.name}
                  </option>
                ))}
              </select>
              <ChevronDown
                aria-hidden
                className="pointer-events-none absolute right-0 size-3 text-subtle"
              />
            </span>
          ) : (
            <p className="truncate font-mono text-xs uppercase tracking-[0.08em] text-muted">
              {shooter.name}
            </p>
          )
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        {prevStage != null ? (
          <Link
            to={href("results", slug, String(prevStage))}
            aria-label="Previous stage"
            className={navButton}
          >
            <ArrowLeft className="size-4" />
          </Link>
        ) : (
          <button
            type="button"
            disabled
            aria-label="Previous stage"
            className={cn(navButton, "opacity-40")}
          >
            <ArrowLeft className="size-4" />
          </button>
        )}
        {nextStage != null ? (
          <Link
            to={href("results", slug, String(nextStage))}
            aria-label="Next stage"
            className={navButton}
          >
            <ArrowRight className="size-4" />
          </Link>
        ) : (
          <button
            type="button"
            disabled
            aria-label="Next stage"
            className={cn(navButton, "opacity-40")}
          >
            <ArrowRight className="size-4" />
          </button>
        )}
      </div>
    </div>
  );
```

Note the one behavioral nuance: `coach?.stage_name` (was `coach.stage_name`)
- the header is only rendered on paths where `coach` is non-null today, so
this is defensive only, keeping TypeScript happy if the header ever moves.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm vitest run src/pages/ResultsStage.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/ResultsStage.tsx \
  src/splitsmith/ui_static/src/pages/ResultsStage.test.tsx
git commit -m "feat(ui): back link + shooter switcher on the results stage page"
```

---

### Task 4: Full gates + visual pass

**Files:**
- No source changes expected; fixes only if gates fail.

- [ ] **Step 1: SPA gates**

From `src/splitsmith/ui_static/`:

Run: `pnpm typecheck && pnpm test && pnpm exec eslint src/pages/Results.tsx src/pages/Results.test.tsx src/pages/ResultsStage.tsx src/pages/ResultsStage.test.tsx src/components/results/ShotTicker.tsx src/components/results/ShotTicker.test.tsx src/components/results/ResultsPlayer.tsx src/lib/splits.ts`
Expected: all pass, no eslint findings.

- [ ] **Step 2: Python gates (repo hygiene - no Python touched, cheap to confirm)**

From repo root: `uv run ruff check . && uv run black --check .`
Expected: clean.

- [ ] **Step 3: Dash check on added lines**

From repo root:
Run: `git diff main -- src/splitsmith/ui_static | grep '^+' | grep -nE '—|--' | grep -v '^+++' || echo clean`
Expected: `clean` (comment markdown `-` list bullets are fine; the rule
targets prose em dashes and double dashes in new copy/comments).

- [ ] **Step 4: Visual pass at phone width**

Serve the SPA (`pnpm dev`) with the local backend running, then use the
bounded headless screenshot recipe (UI verification memory: Playwright
`navigate` hangs on live SSE - use `domcontentloaded` with a timeout) at
390x844 against a local match's `/match/<id>/results` and a stage page.
Check: play affordances visible on audited rows, back link present, the
select caret does not collide with a long shooter name, timer freezes at
stage time when playing past the last shot.

- [ ] **Step 5: Commit any gate fixes**

```bash
git add -u src/splitsmith/ui_static
git commit -m "chore(ui): gate fixes for share view polish"
```

(Skip if nothing changed.)

---

## Self-review notes

- Spec coverage: section 1 -> Task 2; section 2 + 3 -> Task 3; section 4
  -> Task 1; spec testing section -> per-task tests + Task 4 gates.
- The spec's "x / y videos" counter is implemented with the existing
  audited count as numerator - on share those are the same thing (a row
  is watchable iff audited), so no new counting rule is introduced.
- Type names cross-checked against `lib/api.ts` (`ShooterListEntry`,
  `StageStatusEntry`, `CoachStageResponse`, `CoachShot`) and
  `lib/stageMatrix.ts` (`cell.status`/`cell.tone`/`cell.shooter`).
