# Ingest Stage Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface each stage's round/target metadata and put unassigned videos in capture-time order on the Ingest page, so assigning videos no longer requires reading the SSI stage list by hand.

**Architecture:** Frontend-only. The project API already sends `stage_rounds` per stage; the frontend type just discards it. Declare the field, add a sticky collapsible `StageReference` panel to the Ingest review view, and sort the unassigned video list by capture time. Reuse the existing `StageDot` status primitive and `deriveStageStatus`.

**Tech Stack:** React + TypeScript, Vite, Tailwind, lucide-react icons. Package manager: pnpm.

## Global Constraints

- Frontend only: touch `src/splitsmith/ui_static/src/` only. No backend, no import-path, no new dependencies (runtime or dev).
- No new JS test framework -- `ui_static` has none (scripts: `dev`, `build`, `typecheck`, `lint`). Verification is `pnpm typecheck` + `pnpm lint` + `pnpm build` + browser check.
- Instrument-panel aesthetic: dark surfaces, mono/`font-display`, existing `--color-*` tokens. Verify token names exist in `styles/index.css` before use.
- Accessibility WCAG 2.2 AA: color never the sole state carrier (`StageDot` already pairs color with `aria-label`); collapse control is a real `<button>` with `aria-expanded` and a visible focus ring; `rem` sizing; `tabular-nums` on counts; motion via `motion-safe:`/global reduced-motion block only.
- Line length and style per existing eslint config.

---

### Task 1: Declare `stage_rounds` on the frontend stage type

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (near the `StageEntry` interface, ~line 178)

**Interfaces:**
- Produces: `interface StageRounds { expected: number | null; paper_targets: number | null; steel_targets: number | null }` and `StageEntry.stage_rounds: StageRounds | null`.

- [ ] **Step 1: Add the `StageRounds` interface and field**

Immediately before `export interface StageEntry {`, add:

```ts
/** Round count + target breakdown for a stage, mirrored from the backend
 *  ``config.StageRounds``. Sourced from SSI Scoreboard on import; any field
 *  may be null for manually-created stages or older imports. */
export interface StageRounds {
  expected: number | null;
  paper_targets: number | null;
  steel_targets: number | null;
}
```

Then add this line inside `StageEntry` (after `status?: StageStatus;`):

```ts
  /** Round/target metadata already sent by the project API; surfaced in the
   *  Ingest stage reference panel. Null when the match carries no round data. */
  stage_rounds: StageRounds | null;
```

- [ ] **Step 2: Confirm the payload actually carries the field**

Run the app (or inspect a saved project JSON) and confirm one `GET /api/match/{slug}` response has `stages[].stage_rounds`. If it is absent, STOP -- the assumption in the spec is wrong and the plan needs a backend task. Expected: present (the backend returns `ui/project.py::StageEntry`, which declares `stage_rounds`).

- [ ] **Step 3: Typecheck**

Run: `cd src/splitsmith/ui_static && pnpm typecheck`
Expected: clean (no errors). The new field is required, so any code constructing a bare `StageEntry` literal in tests/mocks will now error -- fix those by adding `stage_rounds: null`. Search: `rg "stage_name:" src/splitsmith/ui_static/src` for object literals that need the field.

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts
git commit -m "feat(ui): declare stage_rounds on the frontend StageEntry type"
```

---

### Task 2: `StageReference` panel component

**Files:**
- Create: `src/splitsmith/ui_static/src/components/ingest/StageReference.tsx`

**Interfaces:**
- Consumes: `StageEntry` / `StageRounds` (Task 1), `StageDot` from `@/components/ui/StageDot`, `deriveStageStatus` from `@/lib/stageStatus`.
- Produces: `export function StageReference({ stages }: { stages: StageEntry[] }): JSX.Element | null`.

- [ ] **Step 1: Create the component**

Create `src/splitsmith/ui_static/src/components/ingest/StageReference.tsx` with exactly:

```tsx
import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

import { StageDot } from "@/components/ui/StageDot";
import type { StageEntry, StageStatus } from "@/lib/api";
import { deriveStageStatus } from "@/lib/stageStatus";

const COLLAPSE_KEY = "splitsmith:ingest:stage-reference:collapsed";

function readCollapsed(defaultCollapsed: boolean): boolean {
  if (typeof window === "undefined") return defaultCollapsed;
  const raw = window.localStorage.getItem(COLLAPSE_KEY);
  if (raw === "1") return true;
  if (raw === "0") return false;
  return defaultCollapsed;
}

function roundsLabel(stage: StageEntry): string {
  const n = stage.stage_rounds?.expected;
  return n == null ? "--" : `${n}rd`;
}

function targetsLabel(stage: StageEntry): string {
  const paper = stage.stage_rounds?.paper_targets ?? null;
  const steel = stage.stage_rounds?.steel_targets ?? null;
  const parts: string[] = [];
  if (paper != null) parts.push(`${paper}P`);
  if (steel != null) parts.push(`${steel}S`);
  return parts.length ? parts.join(" ") : "--";
}

const STATUS_LABEL: Record<StageStatus, string> = {
  todo: "todo",
  partial: "partial",
  ready: "ready",
  in_progress: "detecting",
  audited: "audited",
  skipped: "skipped",
};

/**
 * StageReference -- the in-app copy of the SSI stage list, shown while
 * assigning videos so the operator never leaves the Ingest page. Read-only:
 * round count, paper/steel targets, assigned-video count, and lifecycle
 * status per stage. Sticky + collapsible; collapse state persists.
 */
export function StageReference({ stages }: { stages: StageEntry[] }) {
  const withFootage = stages.filter((s) => (s.videos?.length ?? 0) > 0).length;
  const remaining = stages.length - withFootage;
  const hasAnyRounds = stages.some((s) => s.stage_rounds?.expected != null);

  const [collapsed, setCollapsed] = useState(() =>
    readCollapsed(remaining === 0),
  );

  useEffect(() => {
    window.localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  const toggle = useCallback(() => setCollapsed((c) => !c), []);

  if (stages.length === 0) return null;

  return (
    <div className="sticky top-0 z-10 border-b border-rule-strong bg-surface-2/95 backdrop-blur">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={!collapsed}
        className="flex w-full items-center gap-3 px-5 py-2.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-led"
      >
        <span className="font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
          Stage reference
        </span>
        <span className="font-mono text-[0.6875rem] tabular-nums text-muted">
          {withFootage} / {stages.length} have footage
          {remaining > 0 && <> &middot; {remaining} remaining</>}
        </span>
        <span aria-hidden className="ml-auto text-muted">
          {collapsed ? (
            <ChevronDown className="size-4" />
          ) : (
            <ChevronUp className="size-4" />
          )}
        </span>
      </button>

      {!collapsed && (
        <div className="max-h-[40vh] overflow-y-auto border-t border-rule">
          <table className="w-full border-collapse font-mono text-[0.6875rem]">
            <thead className="sticky top-0 bg-surface-2 text-muted">
              <tr className="text-left uppercase tracking-[0.08em]">
                <th className="px-5 py-1.5 font-medium">Stage</th>
                <th className="py-1.5 font-medium">Name</th>
                <th className="py-1.5 pr-4 text-right font-medium">Rounds</th>
                <th className="py-1.5 pr-4 font-medium">Targets</th>
                <th className="py-1.5 pr-4 text-right font-medium">Videos</th>
                <th className="px-5 py-1.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {stages.map((s) => {
                const status = s.status ?? deriveStageStatus(s);
                const count = s.videos?.length ?? 0;
                return (
                  <tr
                    key={s.stage_number}
                    className="border-t border-rule/60 text-ink-2 hover:bg-surface-3"
                  >
                    <td className="px-5 py-1.5 tabular-nums text-ink">
                      {String(s.stage_number).padStart(2, "0")}
                    </td>
                    <td className="py-1.5 pr-4 text-ink">{s.stage_name}</td>
                    <td className="py-1.5 pr-4 text-right tabular-nums">
                      {roundsLabel(s)}
                    </td>
                    <td className="py-1.5 pr-4 tabular-nums">
                      {targetsLabel(s)}
                    </td>
                    <td className="py-1.5 pr-4 text-right tabular-nums">
                      {count === 0 ? (
                        <span className="text-muted">0</span>
                      ) : (
                        count
                      )}
                    </td>
                    <td className="px-5 py-1.5">
                      <span className="inline-flex items-center gap-1.5">
                        <StageDot status={status} />
                        <span className="uppercase tracking-[0.06em] text-muted">
                          {STATUS_LABEL[status]}
                        </span>
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!hasAnyRounds && (
            <div className="border-t border-rule px-5 py-2 font-mono text-[0.625rem] text-muted">
              Round and target data unavailable -- re-sync this match from the
              scoreboard to populate it.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify `deriveStageStatus` export exists and its signature**

Run: `rg "export function deriveStageStatus|export const deriveStageStatus" src/splitsmith/ui_static/src/lib/stageStatus.ts`
Expected: found, taking a stage-like arg and returning `StageStatus`. If the arg name/type differs, the call `deriveStageStatus(s)` still type-checks as long as it accepts a `StageEntry`. If it does not, adapt the call to pass the fields it needs.

- [ ] **Step 3: Typecheck + lint**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm lint`
Expected: clean. Common fixes: `--color-status-*` and `focus-visible:ring-led` must resolve -- if `ring-led` is not a configured Tailwind color, use the existing focus pattern from `Ingest.tsx` (`focus:border-led focus:shadow-[0_0_0_2px_var(--color-led-tint)]`) on the button instead.

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/components/ingest/StageReference.tsx
git commit -m "feat(ui): add StageReference panel for the ingest page"
```

---

### Task 3: Wire the panel into ReviewState and sort unassigned videos by capture time

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Ingest.tsx` (`ReviewState`, ~lines 503-680)

**Interfaces:**
- Consumes: `StageReference` (Task 2).

- [ ] **Step 1: Import the panel**

Add to the imports at the top of `Ingest.tsx` (with the other `@/components` imports):

```ts
import { StageReference } from "@/components/ingest/StageReference";
```

- [ ] **Step 2: Sort the unassigned list by capture time**

Replace the line (~537):

```ts
  const unassignedVideos = project.unassigned_videos ?? [];
```

with a stable capture-time sort (ISO `match_timestamp` ascending, timestamp-less last):

```ts
  // Capture-time order so videos line up with shooting/stage sequence next
  // to the stage reference. Stable: timestamp-less videos keep their order
  // and sink below timestamped ones. (#ingest-stage-reference)
  const unassignedVideos = useMemo(() => {
    const list = (project.unassigned_videos ?? []).map((v, i) => ({ v, i }));
    list.sort((a, b) => {
      const ta = a.v.match_timestamp;
      const tb = b.v.match_timestamp;
      if (ta && tb) {
        const cmp = ta.localeCompare(tb);
        return cmp !== 0 ? cmp : a.i - b.i;
      }
      if (ta) return -1;
      if (tb) return 1;
      return a.i - b.i;
    });
    return list.map((x) => x.v);
  }, [project]);
```

(`useMemo` is already imported and used in this file.)

- [ ] **Step 3: Render the panel above the review card**

Change the `return (` in `ReviewState` (~line 552) so the panel is the first child of the fragment, above the card `<div>`:

```tsx
  return (
    <>
      <StageReference stages={project.stages} />
      <div className="overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
```

(Leave the rest of the card unchanged. The closing `</>` already exists.)

- [ ] **Step 4: Typecheck + lint + build**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm lint && pnpm build`
Expected: all clean, build succeeds.

- [ ] **Step 5: Browser verification**

Start the app, open a match created from SSI Scoreboard that has unassigned videos, go to the Ingest/videos page, and confirm:
- The sticky "Stage reference" bar appears above the review card and stays visible while scrolling the video lists (adjust the sticky `top-0` offset if a fixed app header overlaps it).
- Each stage row shows round count (`Nrd`), targets (`nP nS`), assigned-video count, and a `StageDot` + status label; null round/target fields render `--`.
- The unassigned videos are in capture-time order (earliest first; any without a timestamp at the bottom).
- Collapse/expand toggles and the state survives a page reload.
- On a match with no round data, the "Round and target data unavailable" hint shows.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Ingest.tsx
git commit -m "feat(ui): show stage reference + capture-time order on ingest"
```

---

## Self-Review

- **Spec coverage:** Type plumbing (Task 1), stage reference panel with round/target/videos/status + empty-state hint (Task 2), capture-time ordering + picker left unchanged (Task 3), a11y constraints (Global Constraints + Task 2 code), verification without a JS test runner (each task + Task 3 Step 5). All spec sections mapped.
- **Placeholder scan:** No TBDs; every code step carries complete code.
- **Type consistency:** `StageRounds`/`StageEntry.stage_rounds` defined in Task 1 and consumed in Task 2; `StageReference({ stages })` produced in Task 2 and consumed in Task 3; `StageStatus` keys in `STATUS_LABEL` match the `StageStatus` union in `lib/api.ts`.
