# Mobile Results Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only, mobile-first Results surface (match overview + per-stage clip playback with shot markers and synced splits), a mobile shell for MatchShell, and a desktop-only signpost for everything else.

**Architecture:** New pages mount inside the existing MatchShell route group and run entirely on existing GET endpoints (`getStageCoach`, outlet context, `videoStreamUrl`). Split taxonomy and the shot ruler move out of Coach into shared modules. A `useIsMobile` matchMedia hook drives the shell variant and a `DesktopGate` wrapper.

**Tech Stack:** React 19, React Router 7, Tailwind v4 tokens (styles/index.css), lucide-react, pnpm. SPA root: `src/splitsmith/ui_static`.

**Spec:** `docs/superpowers/specs/2026-07-04-mobile-results-viewer-design.md` - read it before starting any task.

## Global Constraints

- All paths below are relative to `src/splitsmith/ui_static` unless prefixed with `docs/` or `src/splitsmith/`.
- No new dependencies. No `package.json` changes. pnpm only - never npm.
- New user-facing copy and comments use plain ASCII and single dash "-" (never em dash, never "--" in copy).
- Results components must stay read-only: no POST/PATCH/DELETE calls, no localStorage writes, no auth assumptions (they must work in local mode - `useDeploymentMode()` defaults to "local").
- Every CSS token referenced must exist in `src/styles/index.css` - grep before using (`--color-beep`, `--color-led`, etc. are confirmed; anything else, verify).
- Custom classes that override utilities go in `@layer utilities` if any CSS is added (none is expected).
- Touch targets >= 44px (`min-h-11` / `size-11`) on new interactive mobile elements.
- Overlays: Portal to body (`components/ui/Portal.tsx`), z tokens (`z-drawer`, `z-modal`), `useDialogFocus` from `lib/dialogFocus.ts`. Never inline fixed overlays without Portal.
- There is NO test runner in this SPA. Per-task verification is: `pnpm typecheck` && `pnpm build` && `pnpm exec eslint <changed files>` from `src/splitsmith/ui_static`. All three must pass before commit.
- Commit messages end with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01G2Z121nV8dPjgu248huLMX`

---

### Task 1: Shared split taxonomy module + useIsMobile hook

**Files:**
- Create: `src/lib/splits.ts`
- Create: `src/lib/useIsMobile.ts`
- Modify: `src/pages/Coach.tsx:59-87` (delete moved constants, import instead)

**Interfaces:**
- Produces: `SPLIT_BUCKETS: SplitBucket[]`, `splitBucket(s: number): SplitBucket`, `INTERVAL_LABEL: Record<CoachIntervalClass, string>`, `INTERVAL_TONE: Record<CoachIntervalClass, string>`, `type SplitBucket = { max: number; label: string; color: string }` - all from `@/lib/splits`.
- Produces: `useIsMobile(): boolean` from `@/lib/useIsMobile` (true below 768px, live-updates on viewport change, synchronous initial value).

- [ ] **Step 1: Create `src/lib/splits.ts`**

Move these verbatim from `src/pages/Coach.tsx:59-87` (they are currently module-level constants there), adding the type and exports. The `CoachIntervalClass` type comes from `@/lib/api`:

```ts
/**
 * Split taxonomy - single source of truth for split-speed buckets and
 * interval-class presentation. Shared by Coach and the Results viewer.
 */
import type { CoachIntervalClass } from "@/lib/api";

export interface SplitBucket {
  max: number;
  label: string;
  color: string;
}

export const SPLIT_BUCKETS: SplitBucket[] = [
  { max: 0.25, label: "fast", color: "var(--color-done)" },
  { max: 0.45, label: "ok", color: "var(--color-ink-2)" },
  { max: 0.85, label: "slow", color: "var(--color-live)" },
  { max: Infinity, label: "vslow", color: "var(--color-led)" },
];

export function splitBucket(s: number): SplitBucket {
  for (const b of SPLIT_BUCKETS) if (s <= b.max) return b;
  return SPLIT_BUCKETS[SPLIT_BUCKETS.length - 1];
}

export const INTERVAL_LABEL: Record<CoachIntervalClass, string> = {
  first_shot: "Draw",
  split: "Fire",
  transition: "Transition",
  movement: "Movement",
  reload: "Reload",
  activation: "Activation",
};

export const INTERVAL_TONE: Record<CoachIntervalClass, string> = {
  first_shot: "text-led border-led-deep bg-led/10",
  split: "text-done border-done/40 bg-done/10",
  transition: "text-live border-live/40 bg-live/10",
  movement: "text-beep border-beep/40 bg-beep-tint",
  reload: "text-manual border-manual/40 bg-manual/10",
  activation: "text-ink-2 border-rule-strong bg-surface-3",
};
```

- [ ] **Step 2: Update Coach.tsx to import**

Delete the `INTERVAL_LABEL`, `INTERVAL_TONE`, `SPLIT_BUCKETS`, `splitBucket` definitions at `Coach.tsx:59-87` and add:

```ts
import { INTERVAL_LABEL, INTERVAL_TONE, SPLIT_BUCKETS, splitBucket } from "@/lib/splits";
```

Do not change anything else in Coach.tsx. If `CoachIntervalClass` is now an unused import there, remove it from the api import list only if typecheck says so.

- [ ] **Step 3: Create `src/lib/useIsMobile.ts`**

```ts
/**
 * useIsMobile - viewport gate for the mobile shell and desktop-only
 * signpost. Below Tailwind's md breakpoint (768px) = mobile. Uses
 * matchMedia with a change listener (not resize) and initializes
 * synchronously so phones never flash the desktop layout.
 */
import { useSyncExternalStore } from "react";

const QUERY = "(max-width: 767px)";

function subscribe(onChange: () => void): () => void {
  const mql = window.matchMedia(QUERY);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

function getSnapshot(): boolean {
  return window.matchMedia(QUERY).matches;
}

export function useIsMobile(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
```

- [ ] **Step 4: Verify**

Run from `src/splitsmith/ui_static`:
`pnpm typecheck && pnpm build && pnpm exec eslint src/lib/splits.ts src/lib/useIsMobile.ts src/pages/Coach.tsx`
Expected: all pass, no output from eslint.

- [ ] **Step 5: Commit**

`git add src/splitsmith/ui_static/src/lib/splits.ts src/splitsmith/ui_static/src/lib/useIsMobile.ts src/splitsmith/ui_static/src/pages/Coach.tsx` then commit: `refactor(ui): extract split taxonomy to lib/splits, add useIsMobile hook`

---

### Task 2: Shared ShotRuler component

**Files:**
- Create: `src/components/results/ShotRuler.tsx`
- Modify: `src/pages/Coach.tsx:1243-1279` (replace inline ruler with component)

**Interfaces:**
- Consumes: `splitBucket`, `INTERVAL_LABEL` from `@/lib/splits` (Task 1); `CoachShot` from `@/lib/api`.
- Produces: `<ShotRuler shots={CoachShot[]} minAbs={number} span={number} activeShotNumber={number | null} onSeek={(shot: CoachShot) => void} />` from `@/components/results/ShotRuler`.

- [ ] **Step 1: Create the component**

Lift the JSX from `Coach.tsx:1244-1279` verbatim into a standalone component. The outer card div (line 1244) moves too. Props replace the closure variables `coach.shots` -> `shots`, `minAbs`, `span`, `activeShotNumber`, `seekToShot` -> `onSeek`:

```tsx
/**
 * ShotRuler - horizontal shot-dot timeline, colored by split bucket,
 * click/tap seeks. Read-only; shared by Coach per-stage and Results.
 */
import type { CoachShot } from "@/lib/api";
import { INTERVAL_LABEL, splitBucket } from "@/lib/splits";
import { cn } from "@/lib/utils";

interface ShotRulerProps {
  shots: CoachShot[];
  minAbs: number;
  span: number;
  activeShotNumber: number | null;
  onSeek: (shot: CoachShot) => void;
}

export function ShotRuler({ shots, minAbs, span, activeShotNumber, onSeek }: ShotRulerProps) {
  return (
    <div className="overflow-hidden rounded-xl border border-rule-strong bg-surface px-6 py-5">
      <div className="relative h-5">
        <span
          aria-hidden
          className="absolute inset-y-1/2 left-0 right-0 h-px -translate-y-1/2 bg-rule"
        />
        {shots.map((shot) => {
          const x = ((shot.time_absolute - minAbs) / span) * 100;
          const b = splitBucket(shot.split);
          const active = activeShotNumber === shot.shot_number;
          return (
            <button
              key={shot.shot_number}
              type="button"
              onClick={() => onSeek(shot)}
              title={`Shot ${shot.shot_number} - ${shot.split.toFixed(3)}s${
                shot.interval_class ? ` - ${INTERVAL_LABEL[shot.interval_class]}` : ""
              }`}
              aria-label={`Shot ${shot.shot_number}`}
              className={cn(
                "absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full transition-all",
                active
                  ? "size-4 ring-2 ring-led ring-offset-2 ring-offset-surface shadow-[0_0_8px_var(--color-led-glow)]"
                  : "size-3 hover:size-3.5",
              )}
              style={{ left: `${x}%`, backgroundColor: b.color }}
            />
          );
        })}
      </div>
    </div>
  );
}
```

Note: the original title used a middle-dot glyph; the shared version uses "-" per the copy rule. Keep the ring/size classes exactly as shown - they are the existing Coach visual.

- [ ] **Step 2: Replace the inline ruler in Coach**

At `Coach.tsx:1243-1279`, replace the `{/* Shot ruler */}` block with:

```tsx
<ShotRuler
  shots={coach.shots}
  minAbs={minAbs}
  span={span}
  activeShotNumber={activeShotNumber}
  onSeek={seekToShot}
/>
```

and add `import { ShotRuler } from "@/components/results/ShotRuler";` to Coach's imports. `minAbs` / `span` / `activeShotNumber` / `seekToShot` already exist in `CoachStageInner` scope - do not redefine them.

- [ ] **Step 3: Verify** - same three commands as Task 1 Step 4 (eslint on the two changed files). All pass.

- [ ] **Step 4: Commit** - `refactor(ui): extract ShotRuler from Coach for reuse in Results`

---

### Task 3: Shared nav item list

**Files:**
- Create: `src/components/match/navItems.tsx`
- Modify: `src/components/match/MatchSidebar.tsx:197-257` (render from the list)

**Interfaces:**
- Consumes: nothing new.
- Produces: `matchNavItems(args: { base: string; shooterSlug?: string; hasFootage: boolean; shooterCount?: number; beepReviewPendingCount: number; footageHint?: string }): MatchNavItem[]` where `MatchNavItem = { key: string; to: string; icon: ReactNode; label: string; end?: boolean; disabled?: boolean; disabledHint?: string; count?: number; badgeKind?: "count" | "pending" }`.

- [ ] **Step 1: Create `src/components/match/navItems.tsx`**

Transcribe the seven `SidebarLink` declarations at `MatchSidebar.tsx:197-257` into data, inserting a new "Results" item right after Overview. Icons: reuse the exact lucide imports MatchSidebar uses today (`LayoutGrid`, `Crosshair`, `ClipboardCheck`, `Users`, `Film`, `Volume2`, `ArrowDownToLine`) plus `MonitorPlay` for Results, all at `className="size-[15px]"`:

```tsx
/**
 * matchNavItems - single source of truth for match-scoped navigation.
 * Rendered by MatchSidebar (desktop) and MobileNav (drawer). Keep the
 * destination logic identical to the pre-extraction SidebarLink rows.
 */
import type { ReactNode } from "react";
import {
  ArrowDownToLine, ClipboardCheck, Crosshair, Film, LayoutGrid,
  MonitorPlay, Users, Volume2,
} from "lucide-react";

export interface MatchNavItem {
  key: string;
  to: string;
  icon: ReactNode;
  label: string;
  end?: boolean;
  disabled?: boolean;
  disabledHint?: string;
  count?: number;
  badgeKind?: "count" | "pending";
}

export function matchNavItems(args: {
  base: string;
  shooterSlug?: string;
  hasFootage: boolean;
  shooterCount?: number;
  beepReviewPendingCount: number;
  footageHint?: string;
}): MatchNavItem[] {
  const { base, shooterSlug, hasFootage, shooterCount, beepReviewPendingCount, footageHint } = args;
  return [
    { key: "overview", to: `${base}/`, icon: <LayoutGrid className="size-[15px]" />, label: "Overview", end: true },
    { key: "results", to: `${base}/results`, icon: <MonitorPlay className="size-[15px]" />, label: "Results" },
    { key: "audit", to: shooterSlug ? `${base}/audit/${shooterSlug}` : `${base}/shooters?pick=audit`, icon: <Crosshair className="size-[15px]" />, label: "Audit", disabled: !hasFootage, disabledHint: footageHint },
    { key: "coach", to: shooterSlug ? `${base}/coach/${shooterSlug}` : `${base}/shooters?pick=coach`, icon: <ClipboardCheck className="size-[15px]" />, label: "Coach", disabled: !hasFootage, disabledHint: footageHint },
    { key: "shooters", to: `${base}/shooters`, icon: <Users className="size-[15px]" />, label: "Shooters", count: shooterCount, badgeKind: "count" },
    { key: "videos", to: shooterSlug ? `${base}/ingest/${shooterSlug}` : `${base}/shooters?pick=videos`, icon: <Film className="size-[15px]" />, label: "Videos" },
    { key: "beep-review", to: `${base}/beep-review`, icon: <Volume2 className="size-[15px]" />, label: "Beep review", count: beepReviewPendingCount, badgeKind: "pending" },
    { key: "export", to: shooterSlug ? `${base}/export/${shooterSlug}` : `${base}/shooters?pick=export`, icon: <ArrowDownToLine className="size-[15px]" />, label: "Export", disabled: !hasFootage, disabledHint: footageHint },
  ];
}
```

Check MatchSidebar's actual `count` prop handling before finalizing: `Volume2` row passes `count={beepReviewPendingCount}` - mirror whatever undefined/zero handling `SidebarLink` does today (read `SidebarLink`'s props in the same file; badge is hidden when count is 0/undefined - preserve that by passing the raw value through).

- [ ] **Step 2: Render MatchSidebar rows from the list**

Replace `MatchSidebar.tsx:197-257` (the seven `SidebarLink` elements) with:

```tsx
{matchNavItems({
  base,
  shooterSlug,
  hasFootage,
  shooterCount,
  beepReviewPendingCount,
  footageHint,
}).map((item) => (
  <SidebarLink
    key={item.key}
    to={item.to}
    icon={item.icon}
    end={item.end}
    collapsed={collapsed}
    disabled={item.disabled}
    disabledHint={item.disabledHint}
    count={item.count}
    badgeKind={item.badgeKind}
  >
    {item.label}
  </SidebarLink>
))}
```

The variables (`base`, `shooterSlug`, `hasFootage`, `shooterCount`, `beepReviewPendingCount`, `footageHint`) all exist in scope - confirm names against the file (e.g. the footage hint constant) and adjust the call to the real local names. Sidebar now shows the new Results row - that is intended.

- [ ] **Step 3: Verify** - typecheck, build, eslint on both files. Pass.

- [ ] **Step 4: Commit** - `refactor(ui): lift match nav destinations into shared navItems list; add Results entry`

---

### Task 4: Results overview page + routes

**Files:**
- Create: `src/pages/Results.tsx`
- Modify: `src/App.tsx:146` (add routes inside the MatchShell group)

**Interfaces:**
- Consumes: `MatchShellOutletContext` via `useOutletContext` (shape at `MatchShell.tsx:48-53`: `{ project, health, shooters, refresh }`); `buildStageMatrix`, `matchTotals` from `@/lib/stageMatrix` (read that file first - Home.tsx shows usage); `useMatchHref` from `@/lib/matchHref`; `StageStatus` chips - reuse the existing status chip component Home/stage tiles use (find it via Home.tsx imports; render status as text label chip, never color alone).
- Produces: route `/match/:matchId/results`; page component `Results` (default-less named export, matching sibling pages).

- [ ] **Step 1: Read `src/lib/stageMatrix.ts` and `src/pages/Home.tsx` stage-grid section** to learn the exact `StageMatrixRow` shape and the status chip idiom. Do not guess field names.

- [ ] **Step 2: Create `src/pages/Results.tsx`**

Structure (mobile-first, single column; desktop gets a matrix at `lg:`):

```tsx
/**
 * Results - read-only match results overview. One card per stage; each
 * row inside is one shooter's run: time + status, tap -> stage playback.
 * Desktop (lg+) renders the same rows as a stages-x-shooters matrix.
 * Read-only by contract: this surface (and everything under
 * components/results/) is the future share-view; no mutations, no
 * operator-only assumptions. See the 2026-07-04 spec.
 */
```

Implementation requirements (write real code for all of these):

- `const { project, shooters } = useOutletContext<MatchShellOutletContext>();` - import the type from `@/components/match/MatchShell`.
- Loading state while `project` is null: reuse the page-level loading idiom from Home.tsx (mono uppercase "Standby..." style).
- Header block: match name (`project.name`), `project.match_date` when set, total match time from `matchTotals(...)` rendered mono/tnum.
- Body: `buildStageMatrix(project.stages, shooters)` rows -> one `<section>` card per stage: kicker line `Stage NN` + stage name; inside, one row per shooter cell.
- Each audited cell renders as a `<Link>` (React Router) row, `min-h-11`, `to={href("results", cell.slug, String(stage_number))}` using `useMatchHref`; content: shooter name (truncate), stage time right-aligned mono `tnum`, status chip with text label.
- Unaudited cells render a non-link row, `text-subtle`, label "Not audited".
- Single-shooter matches: skip the shooter name (keep time + status), rows are still per-stage links.
- `lg:` layout: CSS grid table - first column stage, one column per shooter (`gridTemplateColumns` built from `shooters.length`), same cell components. Cap visual width `max-w-[1100px] mx-auto` like sibling pages.
- Page padding: follow Home.tsx (`px-` scale) but use `px-4 md:px-7` so 390px gets breathing room.

- [ ] **Step 3: Add routes in `src/App.tsx`**

Inside the `<Route element={<MatchShell />}>` group after the `index` route (`App.tsx:146`):

```tsx
<Route path="results" element={<Results />} />
<Route
  path="results/:slug/:stage"
  element={<ShooterScopedRoute element={<ResultsStage />} />}
/>
```

Import both pages. `ResultsStage` does not exist until Task 5 - to keep this task shippable, add only the `results` route here and let Task 5 add the second line. (If executing tasks together, add both in Task 5.)

- [ ] **Step 4: Verify** - typecheck, build, eslint on `src/pages/Results.tsx src/App.tsx`. Pass. Then visual: `pnpm dev` against a bound local match if available; otherwise defer to the plan-level screenshot pass.

- [ ] **Step 5: Commit** - `feat(ui): read-only Results overview page`

---

### Task 5: Results stage playback page (player, scrub bar, splits list, stats)

This is the hardest task - session-model territory.

**Files:**
- Create: `src/components/results/ResultsPlayer.tsx`
- Create: `src/components/results/SplitsList.tsx`
- Create: `src/components/results/StageStats.tsx`
- Create: `src/pages/ResultsStage.tsx`
- Modify: `src/App.tsx` (add the `results/:slug/:stage` route per Task 4 Step 3)

**Interfaces:**
- Consumes: `api.getStageCoach(slug, stage)` -> `CoachStageResponse` (`api.ts:951-959`; `shots[].time_absolute` is already in served-clip coordinates; `beep_time` same axis); `api.videoStreamUrl(slug, path)` (`kind=auto` default behavior at `api.ts:2784`); `splitBucket`, `SPLIT_BUCKETS`, `INTERVAL_LABEL`, `INTERVAL_TONE` from `@/lib/splits`; `ShotRuler` optional on desktop; `useSpacePlayPause` from `@/lib/keyboard` (see Coach.tsx usage); `useMatchHref`.
- Produces:
  - `<ResultsPlayer src={string} beepTime={number} shots={CoachShot[]} videoRef={RefObject<HTMLVideoElement | null>} onTimeChange={(t: number) => void} />`
  - `<SplitsList shots={CoachShot[]} activeShotNumber={number | null} onSeek={(shot: CoachShot) => void} />`
  - `<StageStats stageTime={number | null} shotCount={number} fastestSplit={number | null} avgSplit={number | null} />`
  - page `ResultsStage` at route `results/:slug/:stage`.

- [ ] **Step 1: `StageStats.tsx`** - presentational strip, 2x2 grid on mobile (`grid-cols-2 md:grid-cols-4`), each cell: mono kicker label + `tnum` value (seconds to 2 decimals, splits to 3). Null renders "-". Follow the `HeroStat` visual idiom from Home.tsx (border, `bg-surface-3`, rounded) but grid, not inline-flex.

- [ ] **Step 2: `SplitsList.tsx`**

One row per shot, `min-h-11`, full-width `<button type="button">` rows (buttons, not divs - keyboard operable by default):

- Columns (flex row, no fixed-track grid): shot number (mono, w-8), time from beep (`time_from_beep.toFixed(2)`, mono tnum), split (`split.toFixed(3)`, mono tnum, bold), bucket chip, interval chip when `interval_class` set.
- Bucket chip: text label from `splitBucket(shot.split).label` + a `size-2 rounded-full` dot with the bucket color - label AND color, never color alone.
- Interval chip: `INTERVAL_LABEL[ic]` styled with `INTERVAL_TONE[ic]` (border + text classes, `rounded px-1.5 py-0.5 font-mono text-[0.625rem] uppercase`).
- `improvement_flag` -> `Flag` lucide icon (`size-3.5 text-led`) with `aria-label="Flagged for improvement"`; `coaching_note` -> render the note text under the row in `text-xs text-muted` (read-only).
- Active row (`activeShotNumber === shot.shot_number`): `bg-surface-2` + left LED bar (the 3px `bg-led` absolute span idiom from Pick.tsx:809-817).
- Auto-scroll: `useEffect` on `activeShotNumber` -> `rowRef.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion ? "auto" : "smooth" })`, only while playing (pass `isPlaying` prop or gate in parent). Read reduced motion via `window.matchMedia("(prefers-reduced-motion: reduce)").matches` at call time.
- Row `onClick` -> `onSeek(shot)`.

- [ ] **Step 3: `ResultsPlayer.tsx`**

Owns the `<video>` markup but NOT the element ref (parent passes `videoRef` so the page and SplitsList share one source of truth):

- `<video ref={videoRef} src={src} controls={false} preload="metadata" playsInline onTimeUpdate onPlay onPause onLoadedMetadata onError className="aspect-video w-full bg-black" />` inside the rounded-2xl bordered card idiom (Coach.tsx:1284-1298).
- Video error state: replace the box content with a visible message ("Video failed to load - retry") and a retry button that re-sets `video.load()`.
- Transport row under the video, all controls `size-11`: play/pause button (lucide `Play`/`Pause`), elapsed readout `mm:ss.s` relative to display-window start (mono tnum), fullscreen button (`Maximize` icon, `videoRef.current?.requestFullscreen()`).
- Display window: `const winStart = Math.max(0, beepTime - 3); const winEnd = Math.min(duration || Infinity, lastShotAbs + 3);` where `lastShotAbs = shots.length ? shots[shots.length - 1].time_absolute : beepTime + 5` and `duration` comes from `loadedmetadata`. Until metadata arrives use `lastShotAbs + 3` as `winEnd`. Guard `winEnd > winStart` (fall back to full duration window if not).
- Scrub bar: a `relative h-11 touch-none cursor-pointer` track div (generous hit area; the visual track is a centered `h-1.5 rounded-full bg-surface-3` span inside). Positioned children, `pct = (t - winStart) / (winEnd - winStart)`:
  - beep marker at `beepTime`: 2px vertical line `bg-beep` full track height + tiny "beep" mono label above (or `aria-label` + title when space is tight);
  - shot dots at each `time_absolute`: `size-2.5 rounded-full`, `backgroundColor: splitBucket(shot.split).color`, `pointer-events-none` (the track is the hit area);
  - playhead: 2px `bg-ink` line, driven by rAF while playing / `timeupdate` while paused, clamped to window.
- Scrubbing: `onPointerDown` -> `setPointerCapture`, compute `t = winStart + (x / rect.width) * (winEnd - winStart)`, set `video.currentTime = clamp(t, winStart, winEnd)`; same math `onPointerMove` while captured. Also `role="slider"`, `aria-label="Seek"`, `aria-valuemin/max/now` (seconds relative to window), and ArrowLeft/ArrowRight keydown seeking +/-0.5s so the bar itself is keyboard operable.
- On mount / `loadedmetadata`: if `video.currentTime < winStart`, set it to `winStart` so playback starts at the window, not file zero.
- Wire `useSpacePlayPause(videoRef)` - check its real signature in `@/lib/keyboard` (Coach.tsx:55 imports it) and call it the same way Coach does.

- [ ] **Step 4: `ResultsStage.tsx`**

- Params via `useParams<{ slug?: string; stage?: string }>()`; numeric-validate stage like Coach.tsx:97-101 ("Bad stage." fallback).
- Fetch on mount: `api.getStageCoach(slug, stageNumber)` with the alive-flag effect idiom (copy the pattern from `CoachStageInner`'s fetch; handle `ApiError`). Null response -> "Stage not audited yet" panel + link back to `href("results")`.
- Primary video: `coach.videos.find(v => v.role === "primary")`; missing -> message panel naming the problem ("No primary video for this stage").
- `streamUrl = api.videoStreamUrl(slug, primary.path)` (same call as Coach.tsx:1142).
- State: `currentTime` (from player callback), `isPlaying`; derived `activeShotNumber` = last shot with `time_absolute <= currentTime + 0.02` else null; `stageTime` = last shot's `time_from_beep`; `fastest/avg` computed from `shots.map(s => s.split)` excluding shot 1 (draw is not a split - exclude `shot_number === 1` from fastest/avg; this matches how splits are discussed in the app).
- `seekToShot` = `videoRef.current.currentTime = shot.time_absolute; videoRef.current.play()`.
- Title row: `Stage NN - name`, shooter display name if available from outlet context shooters list, prev/next stage `<Link>` buttons - prev/next = adjacent entries in the ordered list of THIS shooter's audited stages (build from outlet `shooters` entry `stage_statuses` where status indicates an audit exists; verify exact status values against `lib/api.ts` `StageStatus` and reuse any existing "is audited" helper - grep for one before writing your own).
- Layout: `flex flex-col gap-4 px-4 py-4 md:px-7` mobile; `lg:grid lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]` - left: player; right: StageStats + SplitsList (scrollable, `lg:max-h-[calc(100dvh-var(--shell-header-h,86px)-2rem)] lg:overflow-y-auto`).
- Mobile order: title, ResultsPlayer (video + transport + scrub), StageStats, SplitsList.

- [ ] **Step 5: Add the `results/:slug/:stage` route** (Task 4 Step 3 second line) with imports.

- [ ] **Step 6: Verify** - typecheck, build, eslint on all new files. Pass.

- [ ] **Step 7: Commit** - `feat(ui): Results stage playback - marker scrub bar + synced splits list`

---

### Task 6: DesktopGate signpost

**Files:**
- Create: `src/components/DesktopOnlyNotice.tsx` (includes `DesktopGate`)
- Modify: `src/App.tsx` (wrap desktop-only route elements)

**Interfaces:**
- Consumes: `useIsMobile` (Task 1), `useMatchHref`.
- Produces: `<DesktopGate screen="Audit"><Audit /></DesktopGate>` - renders children at >= md, the notice below md. Children DO NOT MOUNT below md (no fetches behind the gate).

- [ ] **Step 1: Create the component**

```tsx
/**
 * DesktopGate - phones get a signpost instead of a broken desktop
 * layout. Pass-through above md; below md the wrapped page never
 * mounts. Rotating a tablet re-renders the real screen (matchMedia
 * listener), so no redirect and no URL change.
 */
import type { ReactNode } from "react";
import { MonitorSmartphone } from "lucide-react";
import { Link } from "react-router-dom";
import { useMatchHref } from "@/lib/matchHref";
import { useIsMobile } from "@/lib/useIsMobile";

export function DesktopGate({ screen, children }: { screen: string; children: ReactNode }) {
  const isMobile = useIsMobile();
  if (!isMobile) return <>{children}</>;
  return <DesktopOnlyNotice screen={screen} />;
}

export function DesktopOnlyNotice({ screen }: { screen: string }) {
  const href = useMatchHref();
  return (
    <div className="grid min-h-[60dvh] place-items-center px-6 py-10">
      <div className="flex max-w-sm flex-col items-center gap-4 text-center">
        <MonitorSmartphone className="size-8 text-subtle" aria-hidden />
        <div className="font-display text-xl font-bold uppercase tracking-tight text-ink">
          This screen needs a desktop
        </div>
        <p className="text-sm text-muted">
          {screen} works with waveforms and dense controls that do not fit a
          phone. Results and the match overview work great here.
        </p>
        <div className="flex flex-wrap items-center justify-center gap-3">
          <Link to={href("results")} className="btn-led-fill min-h-11 px-5">
            Results
          </Link>
          <Link to={href("")} className="btn-led-outline min-h-11 px-5">
            Match overview
          </Link>
        </div>
      </div>
    </div>
  );
}
```

Verify `btn-led-fill` / `btn-led-outline` exact utility names in `src/styles/index.css` (the tokens memory says grep first) and match their expected element shape (some are for `<button>`; if they set display/height already, drop the min-h/px overrides). Note `useMatchHref` requires a matchId param; for non-match routes (Pick sub-pages, dev, Review) `href("")` degrades to "/" which is acceptable - but simpler: give `DesktopOnlyNotice` an optional `links?: boolean` and pass `links={false}` for non-match screens (then it shows only the message). Implement that flag.

- [ ] **Step 2: Wrap route elements in App.tsx**

Wrap these elements (keep `ShooterScopedRoute` outermost where present - the gate goes around the page element itself, e.g. `element={<ShooterScopedRoute element={<DesktopGate screen="Audit"><Audit /></DesktopGate>} />}`):
- Audit (both routes, screen="Audit"), Compare ("Compare"), Coach both ("Coach"), Shooters ("Shooter management"), BeepReview ("Beep review"), TakeOverview ("Take review"), Export both ("Export"), Ingest ("Ingest") - Ingest sits outside MatchShell but still under match/:matchId, gate works the same.
- Non-match screens with `links={false}` inside the notice: CreateMatch ("Match creation"), MergeMatches ("Match merge"), Review ("Fixture editor"), PromoteReview ("Promote review"), all `/dev/*` pages ("Developer tools") - for the dev group, wrap once around each page element, not the shell.
- Do NOT gate: Login, Pick, Home, Results, ResultsStage.

- [ ] **Step 3: Verify** - typecheck, build, eslint on changed files. Pass. Quick manual check in dev tools responsive mode if a dev server is running: Audit at 390px shows the notice; at 1280px unchanged.

- [ ] **Step 4: Commit** - `feat(ui): desktop-only signpost gate for non-mobile screens`

---

### Task 7: Mobile shell - compact header + nav drawer + jobs sheet position

**Files:**
- Create: `src/components/match/MobileNav.tsx`
- Modify: `src/components/match/MatchShell.tsx` (header + sidebar render at < md; `--shell-sidebar-w` 0 on mobile)
- Modify: `src/components/Jobs.tsx:391` area (full-width sheet position on mobile)

**Interfaces:**
- Consumes: `useIsMobile`, `matchNavItems` (Task 3), `Portal` (`components/ui/Portal.tsx`), `useDialogFocus` (`lib/dialogFocus.ts`), z tokens (`z-drawer`), `AccountChip`, `Brand`.
- Produces: `<MobileNav open onClose items={MatchNavItem[]} header={{ matchName: string }} extras={ReactNode} />` - drawer with nav rows + a slot where MatchShell passes account/help/settings and the Jobs trigger.

- [ ] **Step 1: `MobileNav.tsx`**

- Render null when `!open`. When open: `<Portal>` -> backdrop `fixed inset-0 z-drawer bg-background/70` (verify the token name Coach/ConfirmDialog uses - ConfirmDialog.tsx:67 uses `bg-background/70`; if `--color-background` does not exist in index.css use the same class ConfirmDialog uses verbatim) with `onClick={onClose}`, plus panel `fixed inset-y-0 left-0 z-drawer w-[280px] max-w-[85vw] overflow-y-auto border-r border-rule bg-surface p-4`.
- Slide-in via `transition-transform` only when `!prefers-reduced-motion` (gate the transition class on the same matchMedia check as SplitsList; static render is fine under reduced motion).
- `useDialogFocus(open, panelRef, onClose)` - default trap + Escape.
- Header row inside: match name (font-display bold uppercase, truncate) + close `X` button `size-11`.
- Nav rows from `items`: `NavLink` (React Router) rows `min-h-11 flex items-center gap-3 rounded-md px-3 font-display text-sm font-bold uppercase tracking-wide`, active style `bg-surface-2 text-led` via NavLink className callback, disabled items render as `span` with `text-subtle` + `title={disabledHint}`, badges: reuse `badge-count` / `badge-pending` utility classes as MatchSidebar does (check SidebarLink's markup and copy the badge span idiom). Every row `onClick={onClose}` (navigate then close).
- `extras` ReactNode renders below a `border-t border-rule` divider.

- [ ] **Step 2: MatchShell mobile variant**

In `MatchShell.tsx`:
- `const isMobile = useIsMobile();` + `const [navOpen, setNavOpen] = useState(false);`
- `shellStyle`: sidebar width var becomes `isMobile ? "0px" : (collapsed ? 56 : 240) + "px"` (`MatchShell.tsx:122-127`).
- Header (`MatchShell.tsx:352-426`): when `isMobile`, render instead: hamburger button (`Menu` lucide, `size-11`, `aria-label="Open navigation"`, `onClick={() => setNavOpen(true)}`), `Brand variant="compact"`, truncated match name (`health?.project_name`), `flex-1` spacer - nothing else (breadcrumb, chip strip, account, help, settings, switch-project all move to the drawer extras). Keep the desktop branch byte-identical.
- Sidebar render (`MatchShell.tsx:430-456`): `{isMobile ? null : <MatchSidebar ... />}`.
- Mount `<MobileNav open={navOpen} onClose={() => setNavOpen(false)} items={matchNavItems({...same args the sidebar uses...})} header={{ matchName: project?.name ?? health?.project_name ?? "..." }} extras={...} />` after the header. Extras: `AccountChip`, the switch-project button (reuse the existing handler `switchProject`), and the Jobs trigger from Step 3. Build `base` for navItems the way MatchSidebar does (check how it derives `base` - likely from matchId prop).
- Note: MatchSidebar renders JobsSurface today; with the sidebar unmounted on mobile, JobsSurface must still exist - see Step 3.

- [ ] **Step 3: Jobs on mobile**

Read `components/Jobs.tsx` around line 391 (`fixed bottom-4 z-drawer`, `left: sidebar width + 8`). Add a `mobile?: boolean` prop to `JobsSurface`: when true, the trigger renders as a full-width drawer row (`min-h-11`, "Jobs" label + status dot) and the sheet positions `left-4 right-4 bottom-4` (no sidebar offset). Mount `<JobsSurface mobile ... />` inside MobileNav's extras from MatchShell. Keep the desktop rendering path untouched (MatchSidebar continues to own it at >= md).

- [ ] **Step 4: Verify** - typecheck, build, eslint. Then responsive-mode sanity: at 390px the shell shows hamburger header, no sidebar; drawer opens, focus lands inside, Escape closes; at >= 768px identical to main.

- [ ] **Step 5: Commit** - `feat(ui): mobile shell - compact header, nav drawer, mobile jobs sheet`

---

### Task 8: Pick + Home responsive fixes

**Files:**
- Modify: `src/pages/Pick.tsx:787-960` (MatchRow)
- Modify: `src/pages/Home.tsx:343-360` (hero stat strip)

**Interfaces:** none new.

- [ ] **Step 1: MatchRow two-line card below md**

The row currently hard-codes `gridTemplateColumns: "56px minmax(0,1fr) 180px 220px 160px 152px"` (`Pick.tsx:796-798`). Change to: keep the inline style only at `md+` by moving it to a wrapper decision - simplest correct approach: replace the `style` prop with a `md:`-scoped arbitrary property class:

```
className={cn(
  "... existing classes ...",
  "flex flex-col gap-3",
  "md:grid md:items-center md:gap-6 md:[grid-template-columns:56px_minmax(0,1fr)_180px_220px_160px_152px]",
)}
```

and delete the `style` prop. Then audit the six cell divs: at `< md` the index cell ("No. 01") renders inline with the title - wrap index + primary in a `flex items-start gap-4 md:contents` container so mobile shows "01 + name/date" on one line and the remaining cells (shooters, ticks, status, actions) flow below with `flex flex-wrap gap-x-4 gap-y-2 md:contents` on a second wrapper. Read the full MatchRow (through ~line 960) before editing; keep every cell's inner markup unchanged - only grouping wrappers and the grid class change. Keyboard/click behavior unchanged.

- [ ] **Step 2: Home hero stats wrap**

`Home.tsx:343`: `inline-flex overflow-hidden rounded-[10px] border border-rule bg-surface-3` -> add `flex-wrap` and let cells keep their padding (`inline-flex flex-wrap ...`). Check visually that wrapped cells do not double the border radius awkwardly; if they do, switch to `grid grid-cols-2 md:flex` with the same container classes.

- [ ] **Step 3: Verify** - typecheck, build, eslint; screenshot Pick + Home at 390px (or defer to Task 9's pass if no server handy).

- [ ] **Step 4: Commit** - `fix(ui): Pick match rows + Home hero stats survive 390px`

---

### Task 9: Verification pass + screenshots

**Files:** none (evidence only; fix regressions found).

- [ ] **Step 1: Full gates** from `src/splitsmith/ui_static`: `pnpm typecheck && pnpm build`, plus `pnpm exec eslint` over every file the branch touched (scoped - whole-repo eslint is known-red on 4 pre-existing files).
- [ ] **Step 2: Backend gates** from repo root: `uv run ruff check . && uv run black --check . && uv run pytest -x -q` (backend untouched; cheap insurance).
- [ ] **Step 3: Screenshots** - start the local server (see scripts/ or `uv run splitsmith ui` - check README/SPEC for the run command) with a real bound match. Headless Chromium, bounded navigation with `domcontentloaded` (never networkidle - live polling), viewports 390x844 and 1280x800: Pick, Home, /results, /results/:slug/:stage, /audit at 390 (signpost), drawer open. Save under `~/.claude-tmp/mobile-results-screens/`.
- [ ] **Step 4: Fix what the screenshots show broken**, re-run gates, commit fixes.

Plan complete. PR follows (outside plan scope): push branch, `gh pr create` to main with spec + plan + screenshots attached.
