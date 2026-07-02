# Ingest Two-Pane Master-Detail Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Add Footage ingest review screen as a two-pane master-detail layout (clip list + one persistent player/assignment pane) with a collapsible right-edge stage-reference drawer.

**Architecture:** Replace the current single stacked card (`ReviewState` in `src/pages/Ingest.tsx`) with a three-column grid: a left clip list (work-queue ordering), a center detail pane holding the only `<video>` and the only stage picker, and a right stage-reference drawer that collapses to a handle. Selection is a single `selectedPath` string lifted into a new `ReviewLayout` container; there is only ever one player and one picker, which is what removes the dropdown-to-video ambiguity. No backend or API change; the same `api.moveAssignment` / `removeVideo` / `moveShooter` / `detectBeepForVideo` calls back the same actions.

**Tech Stack:** React 19 + TypeScript, Vite 6, Tailwind v4 (utilities + CSS-variable design tokens in `src/styles/index.css`), lucide-react icons, react-router-dom. Package manager is **pnpm** (the SPA is pnpm-only — never introduce npm/package-lock.json).

## Global Constraints

- Work only in `src/splitsmith/ui_static/`. All commands below run from that directory.
- No new runtime or dev dependencies. No test runner is added.
- Verification per task: `pnpm typecheck` (tsc), `pnpm lint` (eslint), `pnpm build` (tsc -b && vite build) must all pass. Interactive tasks additionally get a Playwright smoke driven via the Playwright MCP against the running dev app.
- Reuse existing design tokens (`bg-surface-*`, `border-rule*`, `text-ink*`, `text-led`, `text-muted`, `text-subtle`, `text-live`, `text-done`, `text-beep`, `--color-*`). Verify token names against `src/styles/index.css` before using them — bare `var(--foo)` refs fall back silently.
- No visual reskin: keep the LED-red "Shot Timer" aesthetic. This is a layout restructure.
- Imports use the `@/` alias (e.g. `@/lib/api`, `@/components/ui/button`).
- No auto-assignment or stage guessing. Surface data; the operator assigns.
- Commit after each task with a `feat(ingest):` / `refactor(ingest):` message.

## Prerequisites for Playwright smokes

The smokes need the app running locally against a match project that has **unassigned footage** (the state in the bug report). Before Task 5:

1. Start the backend API however the user normally does (the FastAPI server that serves `/api/...`).
2. From `src/splitsmith/ui_static/`, run `pnpm dev` (Vite dev server, default `http://localhost:5173`).
3. Navigate to an ingest URL that has footage: `/match/<matchId>/ingest/<shooterSlug>`. If the executor does not know a valid URL, open `http://localhost:5173`, click into a match with imported footage, then into a shooter's Add Footage page, and note the URL.

Drive the browser with the Playwright MCP tools: `mcp__plugin_playwright_playwright__browser_navigate`, `browser_snapshot`, `browser_click`, `browser_press_key`, `browser_take_screenshot`. Assert against roles/text from the snapshot (selectors are dynamic).

## File Structure

New / changed files:

- Create `src/pages/ingest/model.ts` — pure clip model: `CameraGroup`, `groupByCamera`, `pad2`, `ClipItem`, `StageGroup`, `ClipModel`, `buildClipModel`, and selection helpers (`selectDelta`, `nextUnassignedAfter`, `firstUnassignedPath`).
- Create `src/components/ingest/StageReferenceDrawer.tsx` — right-edge collapsible drawer + `useStageDrawerCollapsed` hook + click-to-assign. Replaces `StageReference.tsx`.
- Create `src/components/ingest/RoleToggles.tsx` — extracted from `Ingest.tsx`.
- Create `src/pages/ingest/ClipList.tsx` — left work-queue list.
- Create `src/pages/ingest/ClipDetail.tsx` — center player + assignment bar.
- Create `src/pages/ingest/ReviewLayout.tsx` — composes the three regions + toolbar + selection/keyboard state. Replaces `ReviewState`.
- Create `src/components/ingest/CameraCard.tsx` and `src/components/ingest/IngestMoveBanner.tsx` — moved verbatim out of `Ingest.tsx`.
- Modify `src/pages/Ingest.tsx` — delete `ReviewState`, `StageBlock`, `UnassignedBlock`, `VideoRow`, `RoleToggles`, `groupByCamera`, `CameraGroup`, `pad2`, `CameraCard`, `IngestMoveBanner`; render `<ReviewLayout>` at the old `<ReviewState>` call site.
- Delete `src/components/ingest/StageReference.tsx`.

---

### Task 1: Clip model + selection helpers

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/ingest/model.ts`
- Modify: `src/splitsmith/ui_static/src/pages/Ingest.tsx` (remove the now-duplicated `groupByCamera`, `CameraGroup`, `pad2`; import them from the new module)

**Model:** Sonnet 4.6 — mostly mechanical extraction plus one pure ordering function; no interactive wiring.

**Interfaces:**
- Consumes: `MatchProject`, `StageEntry`, `StageVideo`, `CameraMount`, `BulkCameraSetItem` from `@/lib/api`.
- Produces (later tasks depend on these exact names/types):
  - `pad2(n: number): string`
  - `interface CameraGroup { id: string; label: string; make: string | null; model: string | null; mount: CameraMount | null; videoCount: number; videoPaths: Set<string>; members: BulkCameraSetItem[] }`
  - `groupByCamera(assigned: { video: StageVideo; stage: StageEntry }[]): CameraGroup[]`
  - `interface ClipItem { video: StageVideo; stageNumber: number | null; camera?: CameraGroup }`
  - `interface StageGroup { stage: StageEntry; clips: ClipItem[] }`
  - `interface ClipModel { order: ClipItem[]; unassigned: ClipItem[]; stageGroups: StageGroup[]; cameras: CameraGroup[]; totalVideos: number; assignedCount: number; remaining: number; willProcess: number; ignoredCount: number }`
  - `buildClipModel(project: MatchProject): ClipModel`
  - `selectDelta(order: ClipItem[], selectedPath: string | null, delta: number): string | null`
  - `nextUnassignedAfter(model: ClipModel, path: string): string | null`
  - `firstUnassignedPath(model: ClipModel): string | null`

- [ ] **Step 1: Create the model module**

Create `src/pages/ingest/model.ts`:

```ts
import type {
  BulkCameraSetItem,
  CameraMount,
  MatchProject,
  StageEntry,
  StageVideo,
} from "@/lib/api";

export function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export interface CameraGroup {
  id: string;
  label: string;
  make: string | null;
  model: string | null;
  mount: CameraMount | null;
  videoCount: number;
  videoPaths: Set<string>;
  members: BulkCameraSetItem[];
}

/** Group assigned videos by make+model+mount and label them Camera A/B/C. */
export function groupByCamera(
  assigned: { video: StageVideo; stage: StageEntry }[],
): CameraGroup[] {
  const map = new Map<string, CameraGroup>();
  for (const { video, stage } of assigned) {
    const key = `${video.camera_make ?? ""}|${video.camera_model ?? ""}|${video.camera_mount ?? ""}`;
    let g = map.get(key);
    if (!g) {
      g = {
        id: key,
        label: "",
        make: video.camera_make,
        model: video.camera_model,
        mount: video.camera_mount,
        videoCount: 0,
        videoPaths: new Set(),
        members: [],
      };
      map.set(key, g);
    }
    g.videoCount += 1;
    g.videoPaths.add(video.path);
    g.members.push({ stage_number: stage.stage_number, video_id: video.video_id });
  }
  const groups = Array.from(map.values());
  groups.forEach((g, i) => {
    g.label = `Camera ${String.fromCharCode(65 + i)}`;
  });
  return groups;
}

/** A single clip in the ingest list. stageNumber === null means unassigned. */
export interface ClipItem {
  video: StageVideo;
  stageNumber: number | null;
  camera?: CameraGroup;
}

export interface StageGroup {
  stage: StageEntry;
  clips: ClipItem[];
}

export interface ClipModel {
  /** Flat keyboard-nav order: unassigned first, then per-stage in stage order. */
  order: ClipItem[];
  unassigned: ClipItem[];
  stageGroups: StageGroup[];
  cameras: CameraGroup[];
  totalVideos: number;
  assignedCount: number;
  remaining: number;
  willProcess: number;
  ignoredCount: number;
}

/**
 * Derive the whole ingest view model from a project. Unassigned videos sort
 * by capture timestamp (timestamp-less sink below, stable); assigned videos
 * keep their per-stage order. Cameras are grouped from assigned videos only,
 * matching the prior behavior.
 */
export function buildClipModel(project: MatchProject): ClipModel {
  const assigned: { video: StageVideo; stage: StageEntry }[] = project.stages.flatMap(
    (s) => (s.videos ?? []).map((video) => ({ video, stage: s })),
  );
  const cameras = groupByCamera(assigned);
  const cameraFor = (path: string): CameraGroup | undefined =>
    cameras.find((c) => c.videoPaths.has(path));

  const unassignedSorted = (project.unassigned_videos ?? [])
    .map((v, i) => ({ v, i }))
    .sort((a, b) => {
      const ta = a.v.match_timestamp;
      const tb = b.v.match_timestamp;
      if (ta && tb) {
        const cmp = ta.localeCompare(tb);
        return cmp !== 0 ? cmp : a.i - b.i;
      }
      if (ta) return -1;
      if (tb) return 1;
      return a.i - b.i;
    })
    .map((x) => x.v);

  const unassigned: ClipItem[] = unassignedSorted.map((v) => ({
    video: v,
    stageNumber: null,
    camera: cameraFor(v.path),
  }));

  const stageGroups: StageGroup[] = project.stages
    .map((stage) => ({
      stage,
      clips: (stage.videos ?? []).map((v) => ({
        video: v,
        stageNumber: stage.stage_number,
        camera: cameraFor(v.path),
      })),
    }))
    .filter((g) => g.clips.length > 0);

  const order: ClipItem[] = [
    ...unassigned,
    ...stageGroups.flatMap((g) => g.clips),
  ];

  const assignedCount = assigned.length;
  const totalVideos = assignedCount + unassigned.length;
  const willProcess = assigned.filter((a) => a.video.role !== "ignored").length;
  const ignoredCount =
    assigned.filter((a) => a.video.role === "ignored").length +
    unassignedSorted.filter((v) => v.role === "ignored").length;

  return {
    order,
    unassigned,
    stageGroups,
    cameras,
    totalVideos,
    assignedCount,
    remaining: unassigned.length,
    willProcess,
    ignoredCount,
  };
}

/** Move selection by delta within the flat order, clamped to the ends. */
export function selectDelta(
  order: ClipItem[],
  selectedPath: string | null,
  delta: number,
): string | null {
  if (order.length === 0) return null;
  const idx = order.findIndex((c) => c.video.path === selectedPath);
  if (idx === -1) return order[0].video.path;
  const next = Math.min(order.length - 1, Math.max(0, idx + delta));
  return order[next].video.path;
}

/** First unassigned clip's path, or null if the queue is empty. */
export function firstUnassignedPath(model: ClipModel): string | null {
  return model.unassigned[0]?.video.path ?? null;
}

/**
 * The unassigned clip to select after assigning `path`: the next one after it
 * in the queue, else the previous, else null. Used for auto-advance so the
 * operator keeps clearing the pile without reaching for the mouse.
 */
export function nextUnassignedAfter(model: ClipModel, path: string): string | null {
  const idx = model.unassigned.findIndex((c) => c.video.path === path);
  if (idx === -1) return firstUnassignedPath(model);
  const after = model.unassigned[idx + 1];
  if (after) return after.video.path;
  const before = model.unassigned[idx - 1];
  return before ? before.video.path : null;
}
```

- [ ] **Step 2: Point Ingest.tsx at the shared helpers**

In `src/pages/Ingest.tsx`: delete the local `groupByCamera` function, the `interface CameraGroup { ... }`, and the `pad2` helper (search for `function pad2`). Add to the import block near the other `@/` imports:

```ts
import {
  buildClipModel,
  groupByCamera,
  pad2,
  type CameraGroup,
} from "@/pages/ingest/model";
```

Leave every existing call site (`groupByCamera(assignedVideos)`, `pad2(...)`, `CameraGroup` type refs) untouched — they now resolve to the imported versions. `buildClipModel` is imported for use in later tasks; if eslint flags it as unused at this point, remove it from this import and add it back in Task 5.

- [ ] **Step 3: Verify typecheck, lint, build**

Run from `src/splitsmith/ui_static/`:

```bash
pnpm typecheck && pnpm lint && pnpm build
```

Expected: all three pass. If lint reports `buildClipModel` unused, drop it from the Task-1 import (re-added in Task 5) and re-run.

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/ingest/model.ts src/splitsmith/ui_static/src/pages/Ingest.tsx
git commit -m "refactor(ingest): extract clip model + camera grouping into model.ts"
```

---

### Task 2: Stage-reference drawer

**Files:**
- Create: `src/splitsmith/ui_static/src/components/ingest/StageReferenceDrawer.tsx`
- (The old `src/components/ingest/StageReference.tsx` is deleted in Task 5, when its last consumer goes away.)

**Model:** Sonnet 4.6 — self-contained presentational component with a small localStorage hook.

**Interfaces:**
- Consumes: `StageEntry`, `StageStatus` from `@/lib/api`; `StageDot` from `@/components/ui/StageDot`; `deriveStageStatus` from `@/lib/stageStatus`; `pad2` from `@/pages/ingest/model`.
- Produces:
  - `useStageDrawerCollapsed(defaultCollapsed: boolean): [boolean, () => void]`
  - `function StageReferenceDrawer(props: { stages: StageEntry[]; collapsed: boolean; onToggle: () => void; canAssign: boolean; onAssignStage: (stageNumber: number) => void }): JSX.Element | null`

- [ ] **Step 1: Create the drawer component**

Create `src/components/ingest/StageReferenceDrawer.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { StageDot } from "@/components/ui/StageDot";
import type { StageEntry, StageStatus } from "@/lib/api";
import { deriveStageStatus } from "@/lib/stageStatus";
import { pad2 } from "@/pages/ingest/model";

const COLLAPSE_KEY = "splitsmith:ingest:stage-reference:collapsed";

function readCollapsed(defaultCollapsed: boolean): boolean {
  if (typeof window === "undefined") return defaultCollapsed;
  const raw = window.localStorage.getItem(COLLAPSE_KEY);
  if (raw === "1") return true;
  if (raw === "0") return false;
  return defaultCollapsed;
}

/** Collapse state for the stage drawer, persisted across reloads. */
export function useStageDrawerCollapsed(
  defaultCollapsed: boolean,
): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(() => readCollapsed(defaultCollapsed));
  useEffect(() => {
    window.localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);
  const toggle = useCallback(() => setCollapsed((c) => !c), []);
  return [collapsed, toggle];
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
 * StageReferenceDrawer -- the in-app SSI stage list, as a right-edge column
 * that collapses to a thin handle. It sits BESIDE the player (never over it)
 * so the operator can read stage names while scrubbing. When a clip is
 * selected, each stage row becomes a one-click assign target.
 */
export function StageReferenceDrawer({
  stages,
  collapsed,
  onToggle,
  canAssign,
  onAssignStage,
}: {
  stages: StageEntry[];
  collapsed: boolean;
  onToggle: () => void;
  canAssign: boolean;
  onAssignStage: (stageNumber: number) => void;
}) {
  if (stages.length === 0) return null;

  const withFootage = stages.filter((s) => (s.videos?.length ?? 0) > 0).length;
  const remaining = stages.length - withFootage;
  const hasAnyRounds = stages.some((s) => s.stage_rounds?.expected != null);

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={false}
        aria-label="Show stage reference"
        title="Show stage reference"
        className="flex h-full w-7 flex-col items-center gap-2 rounded-lg border border-rule-strong bg-surface-2 py-3 text-muted transition-colors hover:border-ink-2 hover:text-ink"
      >
        <ChevronLeft className="size-4" />
        <span
          className="font-display text-[0.625rem] font-bold uppercase tracking-[0.14em]"
          style={{ writingMode: "vertical-rl" }}
        >
          Stages
        </span>
      </button>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-rule-strong bg-surface-2">
      <div className="flex items-center gap-2 border-b border-rule px-3 py-2.5">
        <span className="font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
          Stage reference
        </span>
        <span className="font-mono text-[0.625rem] tabular-nums text-muted">
          {withFootage}/{stages.length}
          {remaining > 0 && <> &middot; {remaining} left</>}
        </span>
        <button
          type="button"
          onClick={onToggle}
          aria-label="Hide stage reference"
          title="Hide stage reference"
          className="ml-auto rounded p-0.5 text-muted transition-colors hover:text-ink"
        >
          <ChevronRight className="size-4" />
        </button>
      </div>

      {canAssign && (
        <div className="border-b border-rule bg-led/[0.06] px-3 py-1.5 font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-led">
          Click a stage to assign the selected clip
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        <ul className="divide-y divide-rule/60">
          {stages.map((s) => {
            const status = s.status ?? deriveStageStatus(s);
            const count = s.videos?.length ?? 0;
            const rowClass =
              "flex w-full items-center gap-2 px-3 py-2 text-left font-mono text-[0.6875rem] text-ink-2";
            const inner = (
              <>
                <span className="w-6 shrink-0 tabular-nums text-ink">
                  {pad2(s.stage_number)}
                </span>
                <span className="min-w-0 flex-1 truncate text-ink">
                  {s.stage_name}
                </span>
                <span className="shrink-0 tabular-nums text-muted">
                  {roundsLabel(s)} {targetsLabel(s)}
                </span>
                <span className="w-6 shrink-0 text-right tabular-nums text-muted">
                  {count === 0 ? "0" : count}
                </span>
                <StageDot status={status} />
                <span className="sr-only">{STATUS_LABEL[status]}</span>
              </>
            );
            return (
              <li key={s.stage_number}>
                {canAssign ? (
                  <button
                    type="button"
                    onClick={() => onAssignStage(s.stage_number)}
                    className={`${rowClass} transition-colors hover:bg-led-tint hover:text-led`}
                  >
                    {inner}
                  </button>
                ) : (
                  <div className={rowClass}>{inner}</div>
                )}
              </li>
            );
          })}
        </ul>
        {!hasAnyRounds && (
          <div className="border-t border-rule px-3 py-2 font-mono text-[0.625rem] text-muted">
            Round and target data unavailable -- re-sync this match from the
            scoreboard to populate it.
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify typecheck, lint, build**

```bash
pnpm typecheck && pnpm lint && pnpm build
```

Expected: pass. (`StageReferenceDrawer` is exported-but-unrendered; that is fine — Task 5 mounts it.) If lint's `no-unused-vars` is configured to flag unused exports it will not, since exports are considered used. If it does flag anything, it will be a real typo — fix it.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/components/ingest/StageReferenceDrawer.tsx
git commit -m "feat(ingest): add collapsible stage-reference drawer"
```

---

### Task 3: Clip list (left column)

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/ingest/ClipList.tsx`

**Model:** Sonnet 4.6 — presentational list with selection styling.

**Interfaces:**
- Consumes: `ClipModel`, `ClipItem`, `pad2` from `@/pages/ingest/model`; `cn` from `@/lib/utils`.
- Produces:
  - `function ClipList(props: { model: ClipModel; selectedPath: string | null; onSelect: (path: string) => void }): JSX.Element`

- [ ] **Step 1: Create the ClipList component**

Create `src/pages/ingest/ClipList.tsx`:

```tsx
import type { ClipItem, ClipModel } from "@/pages/ingest/model";
import { pad2 } from "@/pages/ingest/model";
import { cn } from "@/lib/utils";

const ROLE_BADGE: Record<string, string> = {
  primary: "P",
  secondary: "S",
  ignored: "ign",
};

function ClipRow({
  clip,
  selected,
  onSelect,
}: {
  clip: ClipItem;
  selected: boolean;
  onSelect: (path: string) => void;
}) {
  const filename = clip.video.path.split("/").pop() ?? clip.video.path;
  const recordedAt =
    clip.video.match_timestamp &&
    new Date(clip.video.match_timestamp).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  const role = clip.video.role;
  const hasBeep = clip.video.beep_time != null;
  return (
    <button
      type="button"
      onClick={() => onSelect(clip.video.path)}
      aria-current={selected}
      className={cn(
        "flex w-full items-center gap-2.5 border-l-2 px-3 py-2 text-left transition-colors",
        selected
          ? "border-led bg-led/10"
          : "border-transparent hover:bg-surface-2",
      )}
    >
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            "truncate font-mono text-[0.75rem] font-semibold",
            selected ? "text-led" : "text-ink",
          )}
        >
          {filename}
        </div>
        <div className="mt-0.5 truncate font-mono text-[0.5625rem] uppercase tracking-[0.06em] text-muted">
          {recordedAt ?? "no timestamp"}
          {clip.camera && <> &middot; {clip.camera.label}</>}
        </div>
      </div>
      {clip.stageNumber != null && (
        <span
          className={cn(
            "shrink-0 rounded border px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.08em]",
            role === "ignored"
              ? "border-rule bg-surface-3 text-muted line-through"
              : "border-led-deep bg-led/10 text-led",
          )}
        >
          {ROLE_BADGE[role] ?? role}
        </span>
      )}
      {hasBeep && (
        <span
          aria-label="beep detected"
          title="beep detected"
          className="size-1.5 shrink-0 rounded-full bg-beep shadow-[0_0_5px_var(--color-beep-glow)]"
        />
      )}
    </button>
  );
}

function SectionHeader({ label, count }: { label: string; count: number }) {
  return (
    <div className="flex items-center gap-2 border-b border-rule bg-surface-2/60 px-3 py-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-subtle">
      <span className="truncate">{label}</span>
      <span className="ml-auto tabular-nums text-muted">{count}</span>
    </div>
  );
}

/**
 * ClipList -- the left master column. Unassigned clips float to the top as
 * the work queue; assigned clips follow, grouped by stage. Exactly one row
 * is selected at a time, highlighted with the LED accent.
 */
export function ClipList({
  model,
  selectedPath,
  onSelect,
}: {
  model: ClipModel;
  selectedPath: string | null;
  onSelect: (path: string) => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-rule-strong bg-surface">
      <div className="flex items-center gap-2 border-b border-rule-strong px-3 py-2.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] tabular-nums text-muted">
        <span className="font-display font-bold text-ink">
          {model.assignedCount}
        </span>
        assigned
        <span className="text-whisper">/</span>
        <span className="font-display font-bold text-live">{model.remaining}</span>
        left
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {model.remaining > 0 && (
          <div>
            <SectionHeader label="To assign" count={model.remaining} />
            {model.unassigned.map((clip) => (
              <ClipRow
                key={clip.video.video_id}
                clip={clip}
                selected={clip.video.path === selectedPath}
                onSelect={onSelect}
              />
            ))}
          </div>
        )}

        {model.stageGroups.map((group) => (
          <div key={group.stage.stage_number}>
            <SectionHeader
              label={`Stage ${pad2(group.stage.stage_number)} ${group.stage.stage_name}`}
              count={group.clips.length}
            />
            {group.clips.map((clip) => (
              <ClipRow
                key={clip.video.video_id}
                clip={clip}
                selected={clip.video.path === selectedPath}
                onSelect={onSelect}
              />
            ))}
          </div>
        ))}

        {model.order.length === 0 && (
          <div className="px-3 py-6 text-center font-mono text-[0.6875rem] text-muted">
            No footage yet.
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify typecheck, lint, build**

```bash
pnpm typecheck && pnpm lint && pnpm build
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/ingest/ClipList.tsx
git commit -m "feat(ingest): add clip list work-queue column"
```

---

### Task 4: Detail pane (player + assignment bar) and RoleToggles

**Files:**
- Create: `src/splitsmith/ui_static/src/components/ingest/RoleToggles.tsx`
- Create: `src/splitsmith/ui_static/src/pages/ingest/ClipDetail.tsx`

**Model:** Sonnet 4.6 — reuses the existing `useSpacePlayPause` hook; no novel algorithm.

**Interfaces:**
- Consumes: `ClipItem`, `pad2` from `@/pages/ingest/model`; `StageEntry`, `VideoRole`, `ShooterListEntry`, `api`, `ApiError` from `@/lib/api`; `useSpacePlayPause` from `@/lib/keyboard`; `ShooterPickerPopover` from `@/components/ingest/ShooterPickerPopover`; `cn` from `@/lib/utils`.
- Produces:
  - `RoleToggles` in its own module: `function RoleToggles(props: { value: VideoRole; onChange: (r: VideoRole) => void; disabled?: boolean }): JSX.Element`
  - `function ClipDetail(props: { slug: string; clip: ClipItem | null; allStages: StageEntry[]; shooters: ShooterListEntry[]; busy: boolean; onMove: (videoPath: string, toStage: number | null, role: VideoRole) => Promise<void>; onRemove: (videoPath: string) => Promise<void>; onMoveShooter: (targetSlug: string, videoPaths: string[]) => Promise<void>; onError: (msg: string | null) => void }): JSX.Element`

- [ ] **Step 1: Extract RoleToggles into its own module**

Create `src/components/ingest/RoleToggles.tsx`:

```tsx
import type { VideoRole } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Primary / Secondary / Ignore segmented control for a video's role. */
export function RoleToggles({
  value,
  onChange,
  disabled,
}: {
  value: VideoRole;
  onChange: (r: VideoRole) => void;
  disabled?: boolean;
}) {
  const opts: { v: VideoRole; label: string }[] = [
    { v: "primary", label: "Primary" },
    { v: "secondary", label: "Secondary" },
    { v: "ignored", label: "Ignore" },
  ];
  return (
    <div className="inline-flex gap-0.5 rounded-md border border-rule bg-surface-2 p-0.5">
      {opts.map((o) => {
        const on = value === o.v;
        return (
          <button
            key={o.v}
            type="button"
            onClick={() => onChange(o.v)}
            disabled={disabled}
            className={cn(
              "rounded px-2.5 py-1 font-display text-[0.625rem] font-semibold uppercase tracking-[0.06em] transition-all",
              on && o.v === "primary" && "border border-led-deep bg-led/10 text-led",
              on && o.v === "secondary" && "bg-surface-4 text-ink",
              on && o.v === "ignored" && "bg-surface-4 text-muted line-through",
              !on && "text-muted hover:text-ink",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Create the ClipDetail component**

Create `src/pages/ingest/ClipDetail.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, MoreVertical, XCircle } from "lucide-react";

import { RoleToggles } from "@/components/ingest/RoleToggles";
import { ShooterPickerPopover } from "@/components/ingest/ShooterPickerPopover";
import {
  ApiError,
  api,
  type ShooterListEntry,
  type StageEntry,
  type VideoRole,
} from "@/lib/api";
import { useSpacePlayPause } from "@/lib/keyboard";
import type { ClipItem } from "@/pages/ingest/model";
import { pad2 } from "@/pages/ingest/model";

/**
 * ClipDetail -- the center master-detail pane. Renders the ONLY <video> on the
 * page (keyed on the clip path so switching clips loads fresh) plus the ONLY
 * stage picker, docked directly beneath the player so watch-and-assign is one
 * motion with no scrolling. Space toggles playback via the shared hook.
 */
export function ClipDetail({
  slug,
  clip,
  allStages,
  shooters,
  busy,
  onMove,
  onRemove,
  onMoveShooter,
  onError,
}: {
  slug: string;
  clip: ClipItem | null;
  allStages: StageEntry[];
  shooters: ShooterListEntry[];
  busy: boolean;
  onMove: (videoPath: string, toStage: number | null, role: VideoRole) => Promise<void>;
  onRemove: (videoPath: string) => Promise<void>;
  onMoveShooter: (targetSlug: string, videoPaths: string[]) => Promise<void>;
  onError: (msg: string | null) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [rowBusy, setRowBusy] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [kebabOpen, setKebabOpen] = useState(false);
  const kebabRef = useRef<HTMLDivElement>(null);
  const hasOtherShooters = shooters.length > 1;

  const togglePlay = useCallback(() => {
    const el = videoRef.current;
    if (!el) return;
    if (el.paused) void el.play();
    else el.pause();
  }, []);
  useSpacePlayPause(togglePlay, clip != null);

  useEffect(() => {
    if (!kebabOpen) return;
    function onOutside(e: MouseEvent) {
      if (kebabRef.current && !kebabRef.current.contains(e.target as Node)) {
        setKebabOpen(false);
      }
    }
    document.addEventListener("mousedown", onOutside);
    return () => document.removeEventListener("mousedown", onOutside);
  }, [kebabOpen]);

  if (!clip) {
    return (
      <div className="flex h-full min-h-0 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-rule-strong bg-surface/50 px-6 text-center">
        <div className="font-display text-sm font-bold uppercase tracking-[0.08em] text-muted">
          Select a clip to preview and assign
        </div>
        <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
          Space plays / pauses &middot; Up / Down moves between clips
        </div>
      </div>
    );
  }

  const video = clip.video;
  const currentStage = clip.stageNumber;
  const filename = video.path.split("/").pop() ?? video.path;
  const cameraDetail = [clip.camera?.model ?? null, clip.camera?.mount ?? null]
    .filter(Boolean)
    .join(" · ");
  const needsBeep =
    video.role !== "ignored" && video.beep_time == null && currentStage != null;

  async function changeStage(next: string) {
    setRowBusy(true);
    try {
      if (next === "unassigned") await onMove(video.path, null, video.role);
      else {
        const n = Number(next);
        if (!Number.isNaN(n)) await onMove(video.path, n, video.role);
      }
    } finally {
      setRowBusy(false);
    }
  }

  async function setRole(next: VideoRole) {
    setRowBusy(true);
    try {
      await onMove(video.path, currentStage, next);
    } finally {
      setRowBusy(false);
    }
  }

  async function detectBeep() {
    if (currentStage == null) return;
    setDetecting(true);
    onError(null);
    try {
      await api.detectBeepForVideo(slug, currentStage, video.video_id);
    } catch (e) {
      onError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setDetecting(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-rule-strong bg-surface">
      {/* Header: filename + camera */}
      <div className="flex items-center gap-3 border-b border-rule px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[0.8125rem] font-semibold text-ink">
            {filename}
          </div>
          {(clip.camera || cameraDetail) && (
            <div className="mt-0.5 truncate font-mono text-[0.5625rem] uppercase tracking-[0.06em] text-muted">
              {clip.camera?.label}
              {cameraDetail && <> &middot; {cameraDetail}</>}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={() => void onRemove(video.path)}
          disabled={busy}
          title="Remove video"
          aria-label="Remove video"
          className="inline-flex size-8 items-center justify-center rounded-md text-subtle transition-colors hover:bg-led/10 hover:text-led disabled:opacity-50"
        >
          <XCircle className="size-4" />
        </button>
        {hasOtherShooters && (
          <div ref={kebabRef} className="relative">
            <button
              type="button"
              onClick={() => setKebabOpen((o) => !o)}
              disabled={busy || rowBusy}
              title="More actions"
              aria-label="More actions"
              aria-expanded={kebabOpen}
              className="inline-flex size-8 items-center justify-center rounded-md text-subtle transition-colors hover:bg-surface-2 hover:text-ink-2 disabled:opacity-50"
            >
              <MoreVertical className="size-4" />
            </button>
            {kebabOpen && (
              <div className="absolute right-0 top-full z-20 mt-1 w-48 overflow-hidden rounded-lg border border-rule-strong bg-surface shadow-[0_8px_24px_-4px_rgba(0,0,0,0.5)]">
                <div className="border-b border-rule px-3 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-subtle">
                  Move to shooter
                </div>
                <div className="p-2">
                  <ShooterPickerPopover
                    shooters={shooters}
                    excludeSlug={slug}
                    busy={busy || rowBusy}
                    onPick={async (targetSlug) => {
                      setKebabOpen(false);
                      setRowBusy(true);
                      onError(null);
                      try {
                        await onMoveShooter(targetSlug, [video.path]);
                      } finally {
                        setRowBusy(false);
                      }
                    }}
                  />
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Player */}
      <div className="min-h-0 flex-1 overflow-hidden bg-black">
        <video
          key={video.path}
          ref={videoRef}
          controls
          preload="metadata"
          src={api.shooterVideoStreamUrl(slug, video.path)}
          className="h-full w-full object-contain"
        />
      </div>

      {/* Assignment bar -- docked directly under the player, no scroll gap */}
      <div className="border-t border-rule bg-surface-2 px-4 py-3">
        <div className="mb-2 font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-subtle">
          Streaming source &middot; scrub to identify the stage
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative">
            <select
              value={currentStage === null ? "unassigned" : String(currentStage)}
              onChange={(e) => void changeStage(e.target.value)}
              disabled={busy}
              className="min-h-9 rounded-md border border-rule bg-surface-3 px-3 py-1.5 pr-8 font-mono text-[0.6875rem] text-ink outline-none focus:border-led focus:shadow-[0_0_0_2px_var(--color-led-tint)]"
            >
              <option value="unassigned">-- Unassigned --</option>
              {allStages.map((s) => (
                <option key={s.stage_number} value={s.stage_number}>
                  Stage {pad2(s.stage_number)} -- {s.stage_name}
                </option>
              ))}
            </select>
            {rowBusy && (
              <Loader2
                aria-label="Saving assignment"
                className="pointer-events-none absolute right-2 top-1/2 size-3.5 -translate-y-1/2 animate-spin text-led"
              />
            )}
          </div>

          {currentStage === null ? (
            <span
              className="font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-muted"
              title="A video needs a stage before it can have a role. The first video assigned to a stage becomes its primary automatically."
            >
              pick a stage &rarr; auto-primary
            </span>
          ) : (
            <RoleToggles
              value={video.role}
              onChange={(r) => void setRole(r)}
              disabled={busy || rowBusy}
            />
          )}

          <div className="ml-auto flex items-center gap-2">
            {video.beep_time != null ? (
              <span className="inline-flex items-center gap-1.5 rounded border border-beep/40 bg-beep-tint px-2 py-0.5 font-mono text-[0.625rem] font-bold tabular-nums text-beep">
                beep {video.beep_time.toFixed(2)}s
              </span>
            ) : video.role === "ignored" ? (
              <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                ignored
              </span>
            ) : null}
            {needsBeep && (
              <button
                type="button"
                onClick={() => void detectBeep()}
                disabled={busy || detecting}
                title="Detect beep on this video"
                className="inline-flex items-center gap-1.5 rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 font-display text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-ink-2 transition-colors hover:border-led-deep hover:bg-led-tint hover:text-led disabled:opacity-50"
              >
                {detecting ? "Queuing..." : "Detect beep"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verify typecheck, lint, build**

```bash
pnpm typecheck && pnpm lint && pnpm build
```

Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/components/ingest/RoleToggles.tsx src/splitsmith/ui_static/src/pages/ingest/ClipDetail.tsx
git commit -m "feat(ingest): add detail pane with player + docked assignment bar"
```

---

### Task 5: Compose ReviewLayout, swap it in, delete old sub-components

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/ingest/ReviewLayout.tsx`
- Create: `src/splitsmith/ui_static/src/components/ingest/CameraCard.tsx` (moved verbatim from `Ingest.tsx`)
- Create: `src/splitsmith/ui_static/src/components/ingest/IngestMoveBanner.tsx` (moved verbatim from `Ingest.tsx`)
- Modify: `src/splitsmith/ui_static/src/pages/Ingest.tsx` (render `<ReviewLayout>`; delete `ReviewState`, `StageBlock`, `UnassignedBlock`, `VideoRow`, `RoleToggles`, `CameraCard`, `IngestMoveBanner`)
- Delete: `src/splitsmith/ui_static/src/components/ingest/StageReference.tsx`

**Model:** Opus 4.8 — highest-risk task: prop-threading, deletions across a 1600-line file, grid layout, and keeping every existing behavior wired. Careful cross-file surgery.

**Interfaces:**
- Consumes: `buildClipModel`, `firstUnassignedPath` from `@/pages/ingest/model`; `ClipList`; `ClipDetail`; `StageReferenceDrawer`, `useStageDrawerCollapsed`; `CameraCard`; `IngestMoveBanner`.
- Produces: `function ReviewLayout(props): JSX.Element` with the **same prop interface `ReviewState` had** (so the `Ingest.tsx` call site changes only the component name).

- [ ] **Step 1: Move CameraCard into its own file**

Cut the entire `function CameraCard({ camera, slug, onSaved }: {...}) { ... }` block from `Ingest.tsx` (starts at the `function CameraCard(` around line 1386) into a new file `src/components/ingest/CameraCard.tsx`. Add `export` before `function`. At the top of the new file add the imports CameraCard uses. Determine them from the cut code — it uses React hooks (`useState`, `useEffect`, `useRef` as present), `lucide-react` icons it references, `api` + the types `CalibratedCameraModel`, `CameraGroup` (now from `@/pages/ingest/model`), `CAMERA_MOUNTS`, `CameraMount`, `BulkCameraSetItem`, and `cn`. Concretely, start the file with:

```tsx
import { useEffect, useRef, useState } from "react";
// Add the exact lucide-react icons CameraCard references (e.g. Camera, Check, ChevronDown, Loader2) — copy them from the icon usages in the cut block.
import {
  CAMERA_MOUNTS,
  api,
  type BulkCameraSetItem,
  type CalibratedCameraModel,
  type CameraMount,
} from "@/lib/api";
import type { CameraGroup } from "@/pages/ingest/model";
import { cn } from "@/lib/utils";
```

Then paste the `export function CameraCard(...) { ... }` body. Run `pnpm typecheck` and let the compiler name any missing/unused imports; add or remove until clean.

- [ ] **Step 2: Move IngestMoveBanner into its own file**

Cut the `function IngestMoveBanner(...) { ... }` block from `Ingest.tsx` into `src/components/ingest/IngestMoveBanner.tsx`, add `export`, and add its imports (it uses `ShooterPickerPopover`, `ShooterListEntry`, `MoveShooterBlocked`, lucide icons, `cn`). Use `pnpm typecheck` to converge the import list.

- [ ] **Step 3: Create ReviewLayout**

Create `src/pages/ingest/ReviewLayout.tsx`. This mirrors `ReviewState`'s prop interface, keeps the toolbar/banners/cameras/confirm chrome, and renders the three-column grid. Selection defaults to the first unassigned clip.

```tsx
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Package, Plus, Video, X } from "lucide-react";
import { Link } from "react-router-dom";

import { CameraCard } from "@/components/ingest/CameraCard";
import { IngestMoveBanner } from "@/components/ingest/IngestMoveBanner";
import {
  StageReferenceDrawer,
  useStageDrawerCollapsed,
} from "@/components/ingest/StageReferenceDrawer";
import { Button } from "@/components/ui/button";
import type {
  MatchProject,
  MoveShooterBlocked,
  ShooterListEntry,
  VideoRole,
} from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";
import { cn } from "@/lib/utils";
import { ClipDetail } from "@/pages/ingest/ClipDetail";
import { ClipList } from "@/pages/ingest/ClipList";
import { buildClipModel, firstUnassignedPath } from "@/pages/ingest/model";

export function ReviewLayout({
  slug,
  project,
  shooters,
  lastImportedPaths,
  moveBlocked,
  onDismissBanner,
  onMoveShooter,
  onAddMore,
  onMoveAssignment,
  onRemoveVideo,
  onConfirm,
  onSaved,
  busy,
  lastScannedDir,
  onError,
  beepPending,
}: {
  slug: string;
  project: MatchProject;
  shooters: ShooterListEntry[];
  lastImportedPaths: string[] | null;
  moveBlocked: MoveShooterBlocked[];
  onDismissBanner: () => void;
  onMoveShooter: (targetSlug: string, videoPaths: string[]) => Promise<void>;
  onAddMore: () => void;
  onMoveAssignment: (
    videoPath: string,
    toStage: number | null,
    role: VideoRole,
  ) => Promise<void>;
  onRemoveVideo: (videoPath: string) => Promise<void>;
  onConfirm: () => void;
  onSaved: () => Promise<void>;
  busy: boolean;
  lastScannedDir: string | null;
  onError: (msg: string | null) => void;
  beepPending: number;
}) {
  const href = useMatchHref();
  const model = useMemo(() => buildClipModel(project), [project]);

  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  // Default selection follows the work queue: first unassigned clip, else the
  // first clip overall. Re-resolves if the current selection disappears (moved
  // to another shooter, removed) after a reload.
  useEffect(() => {
    const stillExists =
      selectedPath != null &&
      model.order.some((c) => c.video.path === selectedPath);
    if (!stillExists) {
      setSelectedPath(firstUnassignedPath(model) ?? model.order[0]?.video.path ?? null);
    }
  }, [model, selectedPath]);

  const selectedClip =
    model.order.find((c) => c.video.path === selectedPath) ?? null;

  const [drawerCollapsed, toggleDrawer] = useStageDrawerCollapsed(
    model.remaining === 0,
  );

  const activeShooterName = shooters.find((s) => s.slug === slug)?.name ?? slug;
  const showBanner =
    lastImportedPaths != null &&
    lastImportedPaths.length > 0 &&
    shooters.length > 1;

  const assignStage = (stageNumber: number) => {
    if (selectedClip) void onMoveAssignment(selectedClip.video.path, stageNumber, selectedClip.video.role);
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar: drop summary + add more */}
      <div className="relative flex items-center gap-4 overflow-hidden rounded-xl border border-rule-strong bg-gradient-to-r from-led/10 to-transparent px-5 py-3.5">
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-0.5 bg-led shadow-[0_0_12px_var(--color-led-glow)]"
        />
        <span className="inline-flex size-10 items-center justify-center rounded-[10px] bg-led-fill text-ink shadow-[0_0_16px_var(--color-led-glow)]">
          <Package className="size-5" strokeWidth={2.2} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="font-display text-[0.9375rem] font-bold uppercase tracking-[0.04em] text-ink tabular-nums">
            <b className="text-led">{model.totalVideos}</b>{" "}
            {model.totalVideos === 1 ? "video" : "videos"} detected &middot;{" "}
            <b className="text-led">{model.cameras.length}</b>{" "}
            {model.cameras.length === 1 ? "camera" : "cameras"} inferred
          </div>
          {lastScannedDir && (
            <div className="mt-0.5 truncate font-mono text-[0.6875rem] tracking-[0.04em] text-muted">
              {lastScannedDir}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={onAddMore}
          className="inline-flex items-center gap-1.5 rounded-md border border-rule-strong bg-surface-2 px-3.5 py-2 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-ink transition-colors hover:border-ink-2 hover:bg-surface-3"
        >
          <Plus className="size-3" /> Add more
        </button>
      </div>

      {/* Beep review CTA */}
      {beepPending > 0 && (
        <Link
          to={href("beep-review")}
          className="flex items-center gap-3.5 rounded-xl border border-rule bg-gradient-to-r from-beep/10 to-transparent px-5 py-3 font-mono text-[0.75rem] uppercase tracking-[0.06em] text-ink-2 transition-colors hover:bg-beep/15"
        >
          <span className="inline-flex size-7 items-center justify-center rounded-full border border-beep/40 bg-beep-tint text-beep shadow-[0_0_10px_var(--color-beep-glow)]">
            <Video className="size-3.5" />
          </span>
          <span className="flex-1">
            <b className="font-bold text-beep">{beepPending}</b> beep
            {beepPending === 1 ? "" : "s"} need{beepPending === 1 ? "s" : ""}{" "}
            confirmation &middot;{" "}
            <span className="text-muted">detect found candidates but isn't sure</span>
          </span>
          <span className="inline-flex items-center gap-1.5 font-display text-[0.6875rem] font-bold uppercase tracking-[0.1em] text-beep">
            Review beeps <ArrowRight className="size-3" />
          </span>
        </Link>
      )}

      {/* Post-import batch move banner */}
      {showBanner && (
        <IngestMoveBanner
          shooterName={activeShooterName}
          videoPaths={lastImportedPaths}
          shooters={shooters}
          excludeSlug={slug}
          blocked={moveBlocked}
          busy={busy}
          onMove={onMoveShooter}
          onDismiss={onDismissBanner}
        />
      )}
      {moveBlocked.length > 0 && !showBanner && (
        <div className="flex items-start gap-3 rounded-xl border border-live/40 bg-live/10 px-4 py-3 text-[0.8125rem]">
          <span className="mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-live font-mono text-xs font-bold text-bg">
            !
          </span>
          <div className="flex-1 font-mono text-[0.6875rem] leading-relaxed text-ink-2">
            <b className="font-display font-bold uppercase tracking-[0.06em] text-live">
              {moveBlocked.length} stage{moveBlocked.length === 1 ? "" : "s"} not moved
            </b>{" "}
            -- the destination already had reviewed footage. Resolve manually.
          </div>
          <button
            type="button"
            onClick={() => onDismissBanner()}
            aria-label="Dismiss"
            className="rounded p-0.5 text-subtle hover:text-ink"
          >
            <X className="size-4" />
          </button>
        </div>
      )}

      {/* Cameras */}
      {model.cameras.length > 0 && (
        <div className="grid grid-cols-1 gap-3.5 md:grid-cols-2">
          {model.cameras.map((cam) => (
            <CameraCard key={cam.id} camera={cam} slug={slug} onSaved={onSaved} />
          ))}
        </div>
      )}

      {/* Three-region workspace */}
      <div
        className={cn(
          "grid min-h-[70vh] grid-cols-1 gap-4",
          drawerCollapsed
            ? "lg:grid-cols-[300px_minmax(0,1fr)_28px]"
            : "lg:grid-cols-[300px_minmax(0,1fr)_300px]",
        )}
      >
        <ClipList model={model} selectedPath={selectedPath} onSelect={setSelectedPath} />
        <ClipDetail
          slug={slug}
          clip={selectedClip}
          allStages={project.stages}
          shooters={shooters}
          busy={busy}
          onMove={onMoveAssignment}
          onRemove={onRemoveVideo}
          onMoveShooter={onMoveShooter}
          onError={onError}
        />
        <StageReferenceDrawer
          stages={project.stages}
          collapsed={drawerCollapsed}
          onToggle={toggleDrawer}
          canAssign={selectedClip != null}
          onAssignStage={assignStage}
        />
      </div>

      {/* Footer */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rule-strong bg-surface px-6 py-4">
        <div className="font-mono text-[0.75rem] uppercase tracking-[0.06em] text-muted tabular-nums">
          <b className="font-bold text-ink">{model.totalVideos}</b> videos &middot;{" "}
          <b className="font-bold text-ink">{model.willProcess}</b> will process{" "}
          {model.ignoredCount > 0 && (
            <>
              &middot; <b className="font-bold text-ink">{model.ignoredCount}</b> ignored
            </>
          )}
        </div>
        <Button
          type="button"
          onClick={onConfirm}
          disabled={busy || model.willProcess === 0}
          className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink"
        >
          <span className="font-display uppercase tracking-[0.08em]">
            Confirm &amp; start processing
          </span>
          <ArrowRight className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Swap the call site and delete dead code in Ingest.tsx**

In `src/pages/Ingest.tsx`:

1. Add the import: `import { ReviewLayout } from "@/pages/ingest/ReviewLayout";`
2. Change the JSX `<ReviewState ...props />` (around line 335) to `<ReviewLayout ...props />` — the props are identical, so only the tag name changes.
3. Delete the now-dead top-level functions: `ReviewState`, `StageBlock`, `UnassignedBlock`, `VideoRow`, and the local `RoleToggles` (now imported into ClipDetail's module — it is no longer used in Ingest.tsx). `CameraCard` and `IngestMoveBanner` were already moved in Steps 1-2.
4. Remove the import of the old `StageReference`: delete the line `import { StageReference } from "@/components/ingest/StageReference";`
5. Remove any imports that are now unused in `Ingest.tsx` (e.g. `ChevronUp`, `Loader2`, `MoreVertical`, `Play`, `XCircle`, `ShooterPickerPopover`, `StageVideo`, `BulkCameraSetItem`, `CalibratedCameraModel`, `CAMERA_MOUNTS`) — let `pnpm typecheck` and `pnpm lint` tell you exactly which, and delete only those the tools flag.

- [ ] **Step 5: Delete the old StageReference file**

```bash
git rm src/splitsmith/ui_static/src/components/ingest/StageReference.tsx
```

- [ ] **Step 6: Verify typecheck, lint, build**

```bash
pnpm typecheck && pnpm lint && pnpm build
```

Expected: all pass. Fix any unused-import / missing-import fallout from the moves until clean.

- [ ] **Step 7: Playwright smoke — layout, selection, drawer**

Start the app (see "Prerequisites") and open an ingest page with unassigned footage. Then, via the Playwright MCP:

1. `browser_navigate` to the ingest URL. `browser_snapshot`.
2. Assert the three regions exist: a "To assign" section with clip rows (left), a player region with a `<video>` (center), and a "Stage reference" panel (right).
3. `browser_click` a clip row in the left list. `browser_snapshot`. Assert: that row shows the selected (LED) state (`aria-current`), and the center pane's filename header matches the clicked clip's filename.
4. Assert the center pane shows the stage `<select>` reading `-- Unassigned --` for an unassigned clip.
5. Click the drawer's hide control (the `ChevronRight` "Hide stage reference" button). `browser_snapshot`. Assert the drawer collapsed to the vertical "Stages" handle.
6. `browser_navigate` to reload the same URL. `browser_snapshot`. Assert the drawer is still collapsed (localStorage persisted).
7. Click the "Stages" handle to expand it again; assert the stage rows return.
8. `browser_take_screenshot` for the record.

If any assertion fails, this is a real defect — fix before committing.

- [ ] **Step 8: Commit**

```bash
git add -A src/splitsmith/ui_static/src
git commit -m "feat(ingest): two-pane master-detail layout replaces stacked card"
```

---

### Task 6: Keyboard navigation, auto-advance, and drawer click-to-assign

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/ingest/ReviewLayout.tsx`

**Model:** Opus 4.8 — interaction wiring where the reload/selection timing is subtle (auto-advance must target the right next clip across an async project reload).

**Interfaces:**
- Consumes: `selectDelta`, `nextUnassignedAfter` from `@/pages/ingest/model` (add to the existing model import).

- [ ] **Step 1: Add arrow-key navigation**

In `ReviewLayout.tsx`, extend the model import:

```ts
import {
  buildClipModel,
  firstUnassignedPath,
  nextUnassignedAfter,
  selectDelta,
} from "@/pages/ingest/model";
```

Add this effect after the `selectedClip` is computed (it reads `model.order` and `selectedPath` from the closure; re-binds when they change):

```ts
  // Up/Down (and j/k) move the selection through the flat clip order. Skip
  // when focus is in a form control so typing / native <select> keys still
  // work. preventDefault stops the page from scrolling under the list.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target;
      if (t instanceof HTMLElement) {
        if (t.isContentEditable) return;
        if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT") {
          return;
        }
      }
      let delta = 0;
      if (e.key === "ArrowDown" || e.key === "j") delta = 1;
      else if (e.key === "ArrowUp" || e.key === "k") delta = -1;
      else return;
      e.preventDefault();
      setSelectedPath((cur) => selectDelta(model.order, cur, delta));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [model.order]);
```

- [ ] **Step 2: Add auto-advance on assignment**

Replace the direct `onMove={onMoveAssignment}` handoff to `ClipDetail` and the `assignStage` helper with a wrapper that, when a clip is moved out of the unassigned queue into a stage, advances the selection to the next unassigned clip. Add above the `return`:

```ts
  // After assigning a queued clip to a stage, jump to the next unassigned one
  // so the operator keeps clearing the pile without the mouse. Compute the
  // target from the CURRENT model (before the move); its path is unchanged by
  // the reassignment, so selecting it by path survives the project reload.
  async function handleMove(
    videoPath: string,
    toStage: number | null,
    role: VideoRole,
  ) {
    const wasUnassigned =
      model.order.find((c) => c.video.path === videoPath)?.stageNumber == null;
    const nextPath =
      wasUnassigned && toStage != null
        ? nextUnassignedAfter(model, videoPath)
        : null;
    await onMoveAssignment(videoPath, toStage, role);
    if (nextPath != null) setSelectedPath(nextPath);
  }
```

Then update the two consumers to use `handleMove`:

- In the `assignStage` helper, call `handleMove(...)` instead of `onMoveAssignment(...)`:

```ts
  const assignStage = (stageNumber: number) => {
    if (selectedClip) void handleMove(selectedClip.video.path, stageNumber, selectedClip.video.role);
  };
```

- In the `<ClipDetail>` element, change `onMove={onMoveAssignment}` to `onMove={handleMove}`.

(`onMoveAssignment` is still used inside `handleMove`; `ClipDetail` and the drawer now both route through the auto-advance wrapper.)

- [ ] **Step 3: Verify typecheck, lint, build**

```bash
pnpm typecheck && pnpm lint && pnpm build
```

Expected: pass.

- [ ] **Step 4: Playwright smoke — keyboard, space, auto-advance, click-to-assign**

With the app running on an ingest page that has at least two unassigned clips:

1. `browser_navigate` to the ingest URL. `browser_snapshot`. Note the first two clip filenames in "To assign".
2. Click the first clip. `browser_press_key` `ArrowDown`. `browser_snapshot`. Assert selection moved to the second clip (center header filename changed; second row `aria-current`).
3. `browser_press_key` `ArrowUp`. Assert selection is back on the first clip.
4. With a clip selected and its `<video>` present, `browser_press_key` `Space`. Use `browser_evaluate` on the `<video>` element to read `.paused` and assert it is now `false` (playing). Press `Space` again; assert `.paused` is `true`.
5. Auto-advance: with the first "To assign" clip selected, use the center stage `<select>` (`browser_select_option`) to pick a stage. `browser_snapshot`. Assert the selection advanced to what was the next unassigned clip, and the just-assigned clip now appears under its stage group.
6. Click-to-assign: select a remaining unassigned clip, then in the right drawer `browser_click` a stage row. `browser_snapshot`. Assert the clip moved into that stage group.
7. `browser_take_screenshot` for the record.

Fix any failing assertion before committing.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/ingest/ReviewLayout.tsx
git commit -m "feat(ingest): keyboard nav, space-to-play, auto-advance, click-to-assign"
```

---

## Notes on responsive behavior

The workspace grid is `grid-cols-1` by default and switches to the three-column `lg:` template at Tailwind's `lg` breakpoint (1024px). Below that, the list, detail pane, and drawer stack vertically — an acceptable fallback for a desktop-first personal tool. A polished small-screen treatment (drawer as an overlay sheet) is a possible follow-up but is out of scope for this plan.

## Self-Review

**Spec coverage:**
- Two-pane master-detail layout → Tasks 3, 4, 5.
- Collapsible stage-reference drawer with hide-handle → Task 2 + wired in Task 5.
- Player + stage picker co-located, no scroll gap → Task 4 (assignment bar docked under player).
- Spacebar play/pause via `useSpacePlayPause` → Task 4 (hook) + Task 6 (smoke).
- One player / one picker, unambiguous mapping → Task 5 (single `selectedClip`) + Task 3 (LED highlight).
- Work-queue list ordering (unassigned first, then stage groups) → Task 1 (`buildClipModel`) + Task 3.
- `12 assigned / N left` progress line → Task 3.
- Drawer click-to-assign → Task 2 (prop) + Task 6 (wiring + smoke).
- Auto-advance + arrow-key nav → Task 6.
- Toolbar chrome (drop summary, beep CTA, cameras, confirm) relocated → Task 5.
- No API change, selection is client state, drawer collapse in localStorage → Tasks 1, 2, 5.
- `Ingest.tsx` split into focused files → Tasks 1-5.
- Responsive fallback → noted, grid-cols-1 default (Task 5).

**Placeholder scan:** No TBD/TODO/"handle edge cases". The two verbatim moves (CameraCard, IngestMoveBanner) are existing code relocations, not new authored logic, and are described with exact import-convergence steps rather than re-pasted.

**Type consistency:** `ClipItem`, `ClipModel`, `CameraGroup`, `groupByCamera`, `pad2`, `buildClipModel`, `selectDelta`, `nextUnassignedAfter`, `firstUnassignedPath` are defined in Task 1 and consumed with the same signatures in Tasks 3-6. `ReviewLayout` reuses `ReviewState`'s exact prop shape so the Task-5 call-site swap is name-only. `onMove` signature `(videoPath, toStage, role) => Promise<void>` is consistent across ClipDetail, ReviewLayout, and the page.
