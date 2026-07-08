# Mobile Results Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shot-ticker HUD over the results video, container fullscreen with a faux fallback, and a sticky mobile player so splits stay synced with a visible video on phones.

**Architecture:** All changes live in the existing read-only results surface: one new overlay component (`ShotTicker`), a fullscreen rewrite inside `ResultsPlayer`, and layout changes in `ResultsStage` + `SplitsList`. No API or backend changes.

**Tech Stack:** React 19, Tailwind v4 (Shot Timer token system), TypeScript, vite. No test runner exists in `ui_static`; per-task gates are `pnpm typecheck` + `pnpm lint`, with an end-to-end Playwright task at the end.

## Global Constraints

- No new runtime dependencies (project rule: dep list is small on purpose).
- ASCII punctuation only in all copy and comments.
- Read-only surface: no mutations, no localStorage (`ResultsStage` contract).
- Color is never the sole cue (pair bucket colors with text labels).
- `prefers-reduced-motion` suppresses the ticker pulse.
- The share surface (`/share/:token`) sets no `--shell-header-h`; every use of that var in new code MUST fall back to `0px`, not `86px`.
- The `<video>` element must never be re-parented (portals reset media playback).
- All commands below run from `src/splitsmith/ui_static/` unless stated otherwise.

---

### Task 1: Shared current-shot helper

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/splits.ts`
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.tsx:117-129`

**Interfaces:**
- Produces: `currentShotIndex(shots: readonly { time_absolute: number }[], time: number): number` in `@/lib/splits` -- index of the last shot whose `time_absolute <= time + 0.02`, or `-1`. Task 2 consumes it.

- [ ] **Step 1: Add the helper to `lib/splits.ts`** (append at end):

```ts
/** Index of the shot currently "live" under the playhead: the last shot
 *  whose time_absolute has passed (+20ms grace so a seek exactly onto a
 *  shot counts it). No sort assumption - scans for the max qualifying
 *  time. Returns -1 before the first shot. Shared by ResultsStage
 *  (active row) and ShotTicker so the two can never drift. */
export function currentShotIndex(
  shots: readonly { time_absolute: number }[],
  time: number,
): number {
  let idx = -1;
  let bestT = -Infinity;
  for (let i = 0; i < shots.length; i++) {
    const t = shots[i].time_absolute;
    if (t <= time + 0.02 && t >= bestT) {
      bestT = t;
      idx = i;
    }
  }
  return idx;
}
```

- [ ] **Step 2: Use it in `ResultsStage.tsx`** -- replace the `activeShotNumber` memo body (lines 117-129):

```tsx
const activeShotNumber = useMemo(() => {
  const idx = currentShotIndex(shots, currentTime);
  return idx >= 0 ? shots[idx].shot_number : null;
}, [shots, currentTime]);
```

Add `currentShotIndex` to the existing `@/lib/splits` import if present, otherwise add the import.

- [ ] **Step 3: Gate** -- `pnpm typecheck && pnpm lint`. Expected: clean.

- [ ] **Step 4: Commit** -- `git commit -m "refactor(ui): extract shared currentShotIndex helper"`

---

### Task 2: ShotTicker overlay

**Files:**
- Create: `src/splitsmith/ui_static/src/components/results/ShotTicker.tsx`
- Modify: `src/splitsmith/ui_static/src/components/results/ResultsPlayer.tsx` (render inside the `relative` video wrapper, before the error overlay)

**Interfaces:**
- Consumes: `currentShotIndex` from Task 1; `INTERVAL_LABEL`, `splitBucket` from `@/lib/splits`; `CoachShot` from `@/lib/api`.
- Produces: `ShotTicker({ shots, beepTime, time }: { shots: CoachShot[]; beepTime: number; time: number })`.

- [ ] **Step 1: Create `ShotTicker.tsx`:**

```tsx
/**
 * ShotTicker - chronograph HUD overlaid bottom-left on the Results
 * video. Elapsed-from-beep clock + shot counter on top; the current
 * shot's interval label, split value (bucket-colored), and bucket text
 * below - color is never the sole cue. aria-hidden: the transport row
 * already carries the accessible clock, and a live region firing per
 * shot would be noise. Read-only by contract (share-link surface).
 */
import { useEffect, useRef, useState } from "react";

import type { CoachShot } from "@/lib/api";
import { INTERVAL_LABEL, currentShotIndex, splitBucket } from "@/lib/splits";
import { cn } from "@/lib/utils";

interface ShotTickerProps {
  shots: CoachShot[];
  beepTime: number;
  time: number;
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

export function ShotTicker({ shots, beepTime, time }: ShotTickerProps) {
  const idx = currentShotIndex(shots, time);
  const shot = idx >= 0 ? shots[idx] : null;
  const elapsed = Math.max(0, time - beepTime);

  // One motion moment: a short tint pulse on the split row when the
  // live shot changes. Skipped entirely under prefers-reduced-motion.
  const [pulse, setPulse] = useState(false);
  const prevShotRef = useRef<number | null>(null);
  useEffect(() => {
    const n = shot?.shot_number ?? null;
    if (prevShotRef.current === n) return;
    prevShotRef.current = n;
    if (n == null) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    setPulse(true);
    const t = window.setTimeout(() => setPulse(false), 150);
    return () => window.clearTimeout(t);
  }, [shot]);

  const bucket = shot ? splitBucket(shot.split) : null;

  return (
    <div
      aria-hidden
      className="pointer-events-none absolute bottom-2 left-2 rounded-lg bg-black/60 px-3 py-2 backdrop-blur-sm"
    >
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-2xl font-semibold leading-none tabular-nums text-ink">
          {elapsed.toFixed(2)}
        </span>
        <span className="font-mono text-xs tabular-nums text-muted">
          {pad2(shot?.shot_number ?? 0)}/{pad2(shots.length)}
        </span>
      </div>
      {shot && bucket ? (
        <div
          className={cn(
            "-mx-1 mt-1 flex items-baseline gap-2 rounded px-1 transition-colors",
            pulse && "bg-led-tint",
          )}
        >
          <span className="font-display text-[0.625rem] font-bold uppercase tracking-[0.08em] text-muted">
            {shot.interval_class ? INTERVAL_LABEL[shot.interval_class] : "Split"}
          </span>
          <span
            className="font-mono text-sm font-bold tabular-nums"
            style={{ color: bucket.color }}
          >
            {shot.split.toFixed(2)}
          </span>
          <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            {bucket.label}
          </span>
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 2: Mount it in `ResultsPlayer.tsx`** -- inside the `relative` video wrapper, after `<video ...>` and BEFORE the `videoError` overlay (so the opaque error surface paints over it):

```tsx
<ShotTicker shots={shots} beepTime={beepTime} time={time} />
```

Import: `import { ShotTicker } from "@/components/results/ShotTicker";`

- [ ] **Step 3: Gate** -- `pnpm typecheck && pnpm lint`. Expected: clean.

- [ ] **Step 4: Commit** -- `git commit -m "feat(ui): shot-ticker HUD over results video"`

---

### Task 3: Container fullscreen with faux fallback

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/results/ResultsPlayer.tsx` (delete `enterFullscreen` incl. the `webkitEnterFullscreen` path; add mode state, wrapper ref, classes)

**Interfaces:**
- Produces: `export type FullscreenMode = "off" | "native" | "faux"` from `ResultsPlayer.tsx`; new optional prop `onFullscreenChange?: (mode: FullscreenMode) => void`. Task 4 consumes both.

- [ ] **Step 1: State + handlers** (replace `enterFullscreen`):

```tsx
export type FullscreenMode = "off" | "native" | "faux";
```

Inside the component:

```tsx
const wrapperRef = useRef<HTMLDivElement | null>(null);
const [fullscreen, setFullscreen] = useState<FullscreenMode>("off");

const setFullscreenMode = useCallback(
  (m: FullscreenMode) => {
    setFullscreen(m);
    onFullscreenChange?.(m);
  },
  [onFullscreenChange],
);

// Container fullscreen keeps the ticker + transport + scrub alive.
// Native Fullscreen API where it exists (Android, desktop, iOS 16.4+);
// otherwise a "faux" fixed-inset takeover so older iPhones get the
// same experience. The DOM is restyled in place in both modes - the
// video element is never re-parented (that resets playback).
const toggleFullscreen = useCallback(() => {
  const el = wrapperRef.current;
  if (!el) return;
  if (fullscreen === "native") {
    void document.exitFullscreen().catch(() => {});
    return;
  }
  if (fullscreen === "faux") {
    setFullscreenMode("off");
    return;
  }
  if (typeof el.requestFullscreen === "function") {
    el.requestFullscreen()
      .then(() => setFullscreenMode("native"))
      .catch(() => setFullscreenMode("faux"));
  } else {
    setFullscreenMode("faux");
  }
}, [fullscreen, setFullscreenMode]);

// Sync native exits (Esc, swipe, system UI).
useEffect(() => {
  const onChange = () => {
    if (document.fullscreenElement === wrapperRef.current) {
      setFullscreenMode("native");
    } else if (fullscreen === "native") {
      setFullscreenMode("off");
    }
  };
  document.addEventListener("fullscreenchange", onChange);
  return () => document.removeEventListener("fullscreenchange", onChange);
}, [fullscreen, setFullscreenMode]);

// Faux mode: Escape exits, and the page behind must not scroll.
useEffect(() => {
  if (fullscreen !== "faux") return;
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") setFullscreenMode("off");
  };
  document.addEventListener("keydown", onKey);
  const prev = document.documentElement.style.overflow;
  document.documentElement.style.overflow = "hidden";
  return () => {
    document.removeEventListener("keydown", onKey);
    document.documentElement.style.overflow = prev;
  };
}, [fullscreen, setFullscreenMode]);
```

New prop in `ResultsPlayerProps`: `onFullscreenChange?: (mode: FullscreenMode) => void;`

- [ ] **Step 2: Markup changes.** `const isFs = fullscreen !== "off";`

Outer card div gains the ref and conditional classes:

```tsx
<div
  ref={wrapperRef}
  className={cn(
    "overflow-hidden bg-surface",
    isFs
      ? "fixed inset-0 z-takeover flex flex-col bg-black p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-[max(0.75rem,env(safe-area-inset-top))]"
      : "rounded-2xl border border-rule-strong p-3",
  )}
>
```

Video wrapper: `className={cn("relative", isFs && "min-h-0 flex-1")}`.
Video element: `className={cn("w-full bg-black", isFs ? "h-full object-contain" : "aspect-video")}`.

Fullscreen button: `onClick={toggleFullscreen}`, `aria-label={isFs ? "Exit fullscreen" : "Fullscreen"}`, icon `{isFs ? <Minimize className="size-4" /> : <Maximize className="size-4" />}` (import `Minimize`).

`cn` needs importing from `@/lib/utils` if not present.

- [ ] **Step 3: Gate** -- `pnpm typecheck && pnpm lint`. Expected: clean.

- [ ] **Step 4: Commit** -- `git commit -m "feat(ui): container fullscreen with faux fallback for results player"`

---

### Task 4: Sticky mobile player + scroll margins

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.tsx` (wrap `ResultsPlayer`, measure height, hold fullscreen mode)
- Modify: `src/splitsmith/ui_static/src/components/results/SplitsList.tsx` (row scroll margin)

**Interfaces:**
- Consumes: `FullscreenMode`, `onFullscreenChange` from Task 3.

- [ ] **Step 1: `ResultsStage.tsx`** -- add state + measurement (callback-ref pattern; the player only mounts once coach data is in):

```tsx
const [fsMode, setFsMode] = useState<FullscreenMode>("off");
const [playerBox, setPlayerBox] = useState<HTMLDivElement | null>(null);
const [playerH, setPlayerH] = useState(0);

// The pinned player's height varies with viewport width, so no constant
// is safe (same rationale as useShellHeaderHeight). Measured into a CSS
// var the splits rows use as scroll-margin-top.
useEffect(() => {
  if (!playerBox) return;
  const ro = new ResizeObserver(() => setPlayerH(playerBox.offsetHeight));
  ro.observe(playerBox);
  return () => ro.disconnect();
}, [playerBox]);
```

Root container div gets `style={{ "--results-player-h": `${playerH}px` } as React.CSSProperties}`.

Wrap `<ResultsPlayer ...>` in:

```tsx
<div
  ref={setPlayerBox}
  className={cn(
    "max-lg:z-20 max-lg:-mx-4 max-lg:bg-bg max-lg:px-4 max-lg:pb-2",
    "max-lg:[@media(min-height:501px)]:sticky max-lg:[@media(min-height:501px)]:top-[var(--shell-header-h,0px)]",
    fsMode === "faux" && "z-takeover",
  )}
>
  <ResultsPlayer ... onFullscreenChange={setFsMode} />
</div>
```

(The faux-fullscreen card is `fixed` inside this wrapper's stacking context; without the `z-takeover` raise it would pin under the shell header -- the trapped-z trap the design system warns about. Native fullscreen uses the top layer and needs nothing.)

Fallback `0px` on `--shell-header-h`, NOT `86px`: the share surface never sets the var.

- [ ] **Step 2: `SplitsList.tsx`** -- add to the row button `className` list:

```
"max-lg:scroll-mt-[calc(var(--shell-header-h,0px)+var(--results-player-h,0px)+8px)]"
```

- [ ] **Step 3: Gate** -- `pnpm typecheck && pnpm lint && pnpm build`. Expected: clean.

- [ ] **Step 4: Commit** -- `git commit -m "feat(ui): sticky mobile player so splits sync never loses the video"`

---

### Task 5: End-to-end verification (mock share API + Playwright)

**Files:**
- Create (scratch, NOT committed): `~/.claude-tmp/splitsmith-e2e/mock_share_api.py`, `~/.claude-tmp/splitsmith-e2e/stage.mp4`

**Interfaces:**
- Consumes: the built feature on the `/share/:token` route (the real mobile surface); vite dev proxy (`/api` -> `127.0.0.1:5174`).

- [ ] **Step 1: Fixture video** (repo root, ffmpeg available per project):

```bash
mkdir -p ~/.claude-tmp/splitsmith-e2e
ffmpeg -y -f lavfi -i "testsrc=duration=20:size=640x360:rate=30" \
  -f lavfi -i "sine=frequency=440:duration=20" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest \
  ~/.claude-tmp/splitsmith-e2e/stage.mp4
```

- [ ] **Step 2: Mock server** on 127.0.0.1:5174 (python stdlib, Range support for the mp4). Routes (token `t1`, slug `alice`):
  - `GET /api/share/t1/match/shooters` -> `{"shooters": [{"slug": "alice", "name": "Alice Test", "selected_shooter_id": null, "selected_competitor_id": null, "stages_audited": 1, "stages_total": 1, "video_count": 1, "cameras": [], "stages_missing_trim": 0, "stage_statuses": [{"stage_number": 1, "status": "audited"}]}]}`
  - `GET /api/share/t1/shooters/alice/project` -> `{"stages": [{"stage_number": 1, "scorecard": null, "scorecard_updated_at": null}]}`
  - `GET /api/share/t1/shooters/alice/stages/1/coach` -> beep_time 2.0, 8 shots: draw at 3.2 (split 1.2, first_shot), then splits 0.22, 0.31, 0.55, 0.24, 0.95 (reload), 0.28, 0.33 -- `time_absolute = beep_time + time_from_beep`, `ms_after_beep = round(time_from_beep*1000)`, all with `stale: false, reload_hint: false, improvement_flag: false, coaching_note: null, interval_class_source: "auto"`, videos `[{"path": "stage.mp4", "role": "primary", "beep_in_clip": 2.0}]`.
  - `GET /api/share/t1/shooters/alice/videos/stream?...` -> serve `stage.mp4` with `Accept-Ranges`/206 handling.

- [ ] **Step 3: Drive with Playwright** (dev server `pnpm dev`, browser at `http://localhost:5173/share/t1/results/alice/1`), viewport 390x844:
  - Ticker visible over video; play; ticker clock advances; after a shot passes, split row shows interval label + value + bucket text.
  - Scroll down: player pins below top (share surface: top 0), list scrolls beneath, no ghosting in gutters.
  - While playing, active row stays visible below the pinned player (scroll-margin works).
  - Tap fullscreen: card fills viewport (native or faux), transport + scrub + ticker present; Escape (or button) exits.
  - Viewport 844x390 (landscape): player NOT sticky.
  - Viewport 1440x900: desktop two-column layout unchanged; ticker present.
  - Screenshot each state.

**Model note:** dispatch the browser-driving to a subagent (sonnet-class) so the heavy snapshot/screenshot output stays out of the main context; the main agent reviews the screenshots + reported findings.

- [ ] **Step 4: Fix anything found, re-gate, commit fixes.**

---

### Task 6: Review, PR, merge

- [ ] **Step 1:** Run `/code-review` on the branch diff; apply confirmed findings; commit.
- [ ] **Step 2:** Push, `gh pr create` (title `feat(ui): mobile results viewer -- shot ticker, fullscreen, sticky player`), body summarizing spec.
- [ ] **Step 3:** Monitor CI; merge when green (repo merges PRs normally, no branch protection). Staging deploys automatically via `deploy-app.yml` on merge to main.
