# Compare Cockpit Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Compare view (`/match/:matchId/compare/:stage` and `/share/:token/compare/:stage`) a viewport-locked "cockpit" layout: videos size to the space that remains, leaderboard rail on the right, fused transport + sync-timeline dock at the bottom - nothing below the fold on a 1080p desktop.

**Architecture:** `pages/Compare.tsx` keeps the page shell, sync engine, and video tiles; two new page-local components move to `src/pages/compare/` - `LeaderboardRail` (replaces the full-width `RankingTable`) and `TransportDock` (fuses the `Transport` bar and `SyncTimeline` SVG behind one playhead, with an HTML lane gutter and a measured-width SVG instead of the distorting `preserveAspectRatio="none"` stretch). The operator route bounds itself with the existing `h-[calc(100dvh-var(--shell-header-h,86px))]` idiom; the share route gets its bound from a reworked `ShareFrame` whose middle region becomes a `h-dvh`-locked flex parent.

**Tech Stack:** React 19 + TypeScript, Tailwind v4 (`@tailwindcss/vite`, tokens in `src/styles/index.css`), vitest v4 + @testing-library/react (jsdom), pnpm ONLY (never npm), Playwright Python (repo venv) for visual verification.

## Global Constraints

- All paths below are relative to the SPA root `src/splitsmith/ui_static/` unless prefixed with `repo:`.
- Package manager is **pnpm only** - never touch `package-lock.json` (repo rule since PR #506).
- Commit messages use conventional commits with a scope: `feat(ui):` / `refactor(ui):` - bare `ui:` commits are dropped from the changelog (repo rule).
- New copy/comments use plain ASCII and single `-` dashes - never em dashes, never `--` (user rule).
- No fallbacks / parallel legacy paths: the old below-the-fold layout, `RankingTable`, `Transport`, and `SyncTimeline` are DELETED in the same PR, along with tests that only exercised retired behavior (user rule).
- Share view (#700): every operator-only affordance stays behind `!shareView` exactly as today (Audit/Coach tabs, Export FCPXML, banner CTAs, empty-state CTAs). Do not add new operator affordances to the share surface.
- Accessibility: color is never the sole state carrier (lane identity = position + name label, not just track color); the range input stays as the keyboard-accessible scrub control; interactive elements keep aria labels/pressed states.
- z-index: page-local stacking stays below z-30; do not use the `z-chrome`/`z-takeover`/... tokens inside Compare (repo rule in `src/styles/index.css:259-269`).
- Never hard-code the operator header height - always `var(--shell-header-h,86px)` (see `src/lib/shellChrome.ts:1-13` for the bug history).
- Every commit ends with these trailers:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi
  ```

## Reference: current code layout (all in `src/pages/Compare.tsx`, 1236 lines)

- `Compare()` page: `:74-498`. Root wrapper `:318` is `flex flex-col gap-4 px-7 py-5` (grows, page scrolls).
- Row order today: header `:320-381`, toolbar `:384-435`, missing-footage banner `:442-451`, video grid `:454-474`, `Transport` `:477-483`, `SyncTimeline` `:486-492`, `RankingTable` `:495`.
- Exported symbols: `Compare`, `isShareView` (`:70`), `RankingTable` (`:1124`). Everything else is file-local.
- Tests today: `src/pages/Compare.isShareView.test.ts` (keep untouched), `src/pages/RankingTable.test.tsx` (retire; its split-stat assertions port to the new rail test).
- Height chain: MatchShell content wrapper (`src/components/match/MatchShell.tsx:620`) is `min-w-0 flex-1` with NO overflow/height - the document scrolls. ShareShell (`src/components/share/ShareShell.tsx:30-73`) is a `min-h-dvh` flex column; its `max-w-[1100px]` caps only the header/footer inner rows, not the Outlet.

---

### Task 1: Shared helpers + LeaderboardRail

**Files:**
- Create: `src/pages/compare/format.ts`
- Create: `src/pages/compare/LeaderboardRail.tsx`
- Test: `src/pages/compare/LeaderboardRail.test.tsx`

**Interfaces:**
- Consumes: `CompareShooterRecord` from `@/lib/api`, `splitsFromTimeline`/`statisticSplits` from `@/lib/splits`, `Avatar` from `@/components/ui`, `cn` from `@/lib/utils`.
- Produces: `initials(name: string): string` and `avg(arr: number[]): number` in `format.ts`; `LeaderboardRail({ shooters }: { shooters: CompareShooterRecord[] })` component (Task 4 imports both). `RankingTable` stays in place until Task 4 so the tree keeps compiling.

- [ ] **Step 1: Create the shared format helpers**

`src/pages/compare/format.ts` - move (copy for now; the originals are deleted in Task 4) the two helpers verbatim from `Compare.tsx:1223-1236`:

```ts
/** Page-local formatting helpers shared by Compare and its cockpit
 *  sub-components (rail, dock). */

export function avg(arr: number[]): number {
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 0 || !parts[0]) return "??";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
```

- [ ] **Step 2: Write the failing rail test**

`src/pages/compare/LeaderboardRail.test.tsx`. This ports the split-stat assertions from `src/pages/RankingTable.test.tsx` (read that file first and mirror its fixture shape exactly - it builds `CompareShooterRecord` objects with `shots` arrays) and adds rank/delta assertions:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CompareShooterRecord } from "@/lib/api";

import { LeaderboardRail } from "./LeaderboardRail";

function shooter(
  slug: string,
  name: string,
  stageTime: number | null,
  shotTimes: number[],
): CompareShooterRecord {
  return {
    slug,
    name,
    stage_time_seconds: stageTime,
    beep_offset_in_clip: 0,
    video_ref: `trimmed/${slug}.mp4`,
    shots: shotTimes.map((t, i) => ({
      shot_number: i + 1,
      time_after_beep: t,
      source: "detected",
      interval_class: null,
    })),
  } as CompareShooterRecord;
}

describe("LeaderboardRail", () => {
  it("ranks by stage time and shows the delta to the leader", () => {
    render(
      <LeaderboardRail
        shooters={[
          shooter("b", "Slow Shooter", 15.08, [1.31, 1.62, 15.08]),
          shooter("a", "Fast Shooter", 14.32, [1.18, 1.46, 14.32]),
        ]}
      />,
    );
    const names = screen
      .getAllByTestId("rail-name")
      .map((el) => el.textContent);
    expect(names).toEqual(["Fast Shooter", "Slow Shooter"]);
    expect(screen.getByText("+0.76s")).toBeInTheDocument();
  });

  it("computes draw, fastest and avg split from statistic splits", () => {
    // 0.28 and 0.31 are shot splits; the 9.0 -> 14.32 gap is movement and
    // must be excluded by statisticSplits (behavior under test, #774).
    render(
      <LeaderboardRail
        shooters={[
          shooter("a", "Fast Shooter", 14.32, [1.18, 1.46, 1.77, 9.0, 14.32]),
        ]}
      />,
    );
    expect(screen.getByTestId("rail-draw")).toHaveTextContent("1.18");
    expect(screen.getByTestId("rail-fast")).toHaveTextContent("0.280");
  });

  it("renders dashes for a shooter with no shots", () => {
    render(
      <LeaderboardRail shooters={[shooter("a", "Empty Shooter", null, [])]} />,
    );
    expect(screen.getByTestId("rail-draw")).toHaveTextContent("-");
    expect(screen.getByTestId("rail-fast")).toHaveTextContent("-");
    expect(screen.getByTestId("rail-avg")).toHaveTextContent("-");
  });
});
```

Before finalizing the fixture, open `src/pages/RankingTable.test.tsx` and `src/lib/splits.ts`: if `statisticSplits` uses a different threshold/shape than the 9.0s movement gap assumes, copy the exact fixture values the old test used for "excludes non-split intervals" so the ported assertion tests the same behavior. Adjust the expected `0.280` string to the old test's expected fastest-split value if it differs.

- [ ] **Step 3: Run the test to verify it fails**

Run (from `src/splitsmith/ui_static/`): `pnpm vitest run src/pages/compare/LeaderboardRail.test.tsx`
Expected: FAIL - cannot resolve `./LeaderboardRail`.

- [ ] **Step 4: Implement LeaderboardRail**

`src/pages/compare/LeaderboardRail.tsx`:

```tsx
/** Cockpit right rail: the RankingTable's data at a third of the height.
 *  One card per shooter - rank, name, stage time, delta to leader, and
 *  the draw / fastest / avg-split microstats (#774 semantics via
 *  statisticSplits, same as the retired RankingTable). */

import { Avatar } from "@/components/ui";
import { type CompareShooterRecord } from "@/lib/api";
import { splitsFromTimeline, statisticSplits } from "@/lib/splits";
import { cn } from "@/lib/utils";

import { avg, initials } from "./format";

export function LeaderboardRail({
  shooters,
}: {
  shooters: CompareShooterRecord[];
}) {
  const rows = shooters
    .map((s) => {
      const pairs = splitsFromTimeline(s.shots);
      const splits = statisticSplits(pairs);
      return {
        shooter: s,
        time: s.stage_time_seconds ?? Infinity,
        draw: pairs.length > 0 ? pairs[0].split : null,
        fastestSplit: splits.length === 0 ? null : Math.min(...splits),
        avgSplit: splits.length === 0 ? null : avg(splits),
        shotCount: s.shots.length,
      };
    })
    .sort((a, b) => a.time - b.time)
    .map((row, i) => ({ ...row, rank: i + 1 }));
  const leaderTime = rows.length > 0 ? rows[0].time : Infinity;

  return (
    <aside
      data-testid="leaderboard-rail"
      aria-label="Leaderboard"
      className="flex w-[360px] flex-none flex-col overflow-hidden rounded-2xl border border-rule-strong bg-surface shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]"
    >
      <div className="flex items-baseline justify-between border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-4 py-2.5">
        <span className="font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
          Leaderboard
        </span>
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          stage time
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.map((row) => (
          <div
            key={row.shooter.slug}
            className="grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-x-2.5 gap-y-1.5 border-b border-rule px-4 py-3 last:border-b-0"
          >
            <span
              className={cn(
                "font-display text-xl font-bold tabular-nums",
                row.rank === 1
                  ? "text-led drop-shadow-[0_0_10px_var(--color-led-glow)]"
                  : "text-whisper",
              )}
            >
              {row.rank}
            </span>
            <span className="inline-flex min-w-0 items-center gap-2">
              <Avatar
                size="xs"
                initials={initials(row.shooter.name)}
                tone={undefined}
                seed={row.shooter.slug}
              />
              <span
                data-testid="rail-name"
                className="truncate font-display text-[0.8125rem] font-bold uppercase tracking-[0.04em] text-ink"
              >
                {row.shooter.name}
              </span>
            </span>
            <span
              className={cn(
                "text-right font-mono text-lg font-bold leading-none tabular-nums",
                row.rank === 1
                  ? "text-led drop-shadow-[0_0_8px_var(--color-led-glow)]"
                  : "text-ink",
              )}
            >
              {Number.isFinite(row.time) ? `${row.time.toFixed(2)}s` : "-"}
            </span>
            <span aria-hidden="true" />
            <div className="col-span-2 col-start-2 flex items-center gap-3 font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted tabular-nums">
              <span data-testid="rail-draw">
                draw{" "}
                <b className="font-bold text-ink-2">
                  {row.draw != null ? row.draw.toFixed(2) : "-"}
                </b>
              </span>
              <span data-testid="rail-fast">
                fast{" "}
                <b className="font-bold text-ink-2">
                  {row.fastestSplit != null ? row.fastestSplit.toFixed(3) : "-"}
                </b>
              </span>
              <span data-testid="rail-avg">
                avg{" "}
                <b className="font-bold text-ink-2">
                  {row.avgSplit != null ? row.avgSplit.toFixed(3) : "-"}
                </b>
              </span>
              <span className="ml-auto text-subtle">
                {row.rank === 1 || !Number.isFinite(row.time)
                  ? ""
                  : `+${(row.time - leaderTime).toFixed(2)}s`}
              </span>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pnpm vitest run src/pages/compare/LeaderboardRail.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/compare-cockpit-layout   # first task only; later tasks commit on this branch
git add src/pages/compare/format.ts src/pages/compare/LeaderboardRail.tsx src/pages/compare/LeaderboardRail.test.tsx
git commit -m "feat(ui): add compare leaderboard rail for cockpit layout"
```

---

### Task 2: TransportDock (fused transport + sync timeline)

**Files:**
- Create: `src/pages/compare/TransportDock.tsx`
- Test: `src/pages/compare/TransportDock.test.tsx`

**Interfaces:**
- Consumes: `CompareShooterRecord` from `@/lib/api`, `initials` from `./format`, lucide icons `MoveLeft`, `MoveRight`, `Pause`, `Play`, `Volume2`, `cn` from `@/lib/utils`.
- Produces:
  ```ts
  export function timeFromTrackX(px: number, width: number, maxTime: number): number;
  export function TransportDock(props: {
    shooters: CompareShooterRecord[];
    maxTime: number;
    timeSinceBeep: number;
    audioSlug: string | null;
    isPlaying: boolean;
    onTogglePlay: () => void;
    onScrub: (tsb: number) => void;
    onPickAudio: (slug: string) => void;
  }): JSX.Element;
  ```
  Task 4 imports both. Root element carries `data-testid="transport-dock"`.

Design notes locked in from the approved mockup:
- One panel: header row = jump-to-beep / play / jump-to-end buttons + t-beep/span readouts + the range slider (kept: it is the keyboard-accessible scrub control); body = lane gutter (HTML) + track SVG.
- Lane gutter is HTML, not SVG text (the old `preserveAspectRatio="none"` stretch distorts glyphs): one button per shooter - color dot, name, stage time - click picks that shooter as audio source (`aria-pressed`).
- SVG is rendered at measured pixel width (ResizeObserver, same idiom as `src/pages/Audit.tsx:289-303`), `preserveAspectRatio` untouched (default), so nothing distorts.
- Shot markers: fired (`time_after_beep <= timeSinceBeep`) = solid fill; upcoming = hollow (surface fill + colored stroke). Manual shots keep `--color-manual` and the larger radius. Track shows a brighter progress segment up to the playhead.
- Scrubbing: pointer-down + drag anywhere on the SVG (pointer capture), plus the slider.

- [ ] **Step 1: Write the failing test**

`src/pages/compare/TransportDock.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CompareShooterRecord } from "@/lib/api";

import { timeFromTrackX, TransportDock } from "./TransportDock";

function shooter(
  slug: string,
  name: string,
  stageTime: number,
  shotTimes: number[],
): CompareShooterRecord {
  return {
    slug,
    name,
    stage_time_seconds: stageTime,
    beep_offset_in_clip: 0,
    video_ref: `trimmed/${slug}.mp4`,
    shots: shotTimes.map((t, i) => ({
      shot_number: i + 1,
      time_after_beep: t,
      source: i === 0 ? "manual" : "detected",
      interval_class: null,
    })),
  } as CompareShooterRecord;
}

const baseProps = {
  maxTime: 10,
  audioSlug: "a",
  isPlaying: false,
  onTogglePlay: () => {},
  onScrub: () => {},
  onPickAudio: () => {},
};

describe("timeFromTrackX", () => {
  it("maps pixels linearly and clamps to [0, maxTime]", () => {
    expect(timeFromTrackX(0, 1000, 20)).toBe(0);
    expect(timeFromTrackX(500, 1000, 20)).toBe(10);
    expect(timeFromTrackX(2000, 1000, 20)).toBe(20);
    expect(timeFromTrackX(-50, 1000, 20)).toBe(0);
    expect(timeFromTrackX(500, 0, 20)).toBe(0);
  });
});

describe("TransportDock", () => {
  it("renders fired shots solid and upcoming shots hollow", () => {
    render(
      <TransportDock
        {...baseProps}
        shooters={[shooter("a", "Fast Shooter", 9.5, [1.0, 2.0, 8.0])]}
        timeSinceBeep={5.0}
      />,
    );
    expect(screen.getByTestId("shot-a-1")).toHaveAttribute(
      "data-fired",
      "true",
    );
    expect(screen.getByTestId("shot-a-2")).toHaveAttribute(
      "data-fired",
      "true",
    );
    expect(screen.getByTestId("shot-a-3")).toHaveAttribute(
      "data-fired",
      "false",
    );
  });

  it("picks audio when a lane gutter button is clicked", () => {
    const onPickAudio = vi.fn();
    render(
      <TransportDock
        {...baseProps}
        onPickAudio={onPickAudio}
        shooters={[
          shooter("a", "Fast Shooter", 9.5, [1.0]),
          shooter("b", "Slow Shooter", 9.9, [1.2]),
        ]}
        timeSinceBeep={0}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Slow Shooter/ }),
    );
    expect(onPickAudio).toHaveBeenCalledWith("b");
    expect(
      screen.getByRole("button", { name: /Fast Shooter/ }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("scrubs via the range slider", () => {
    const onScrub = vi.fn();
    render(
      <TransportDock
        {...baseProps}
        onScrub={onScrub}
        shooters={[shooter("a", "Fast Shooter", 9.5, [1.0])]}
        timeSinceBeep={0}
      />,
    );
    fireEvent.change(screen.getByRole("slider"), { target: { value: "4.5" } });
    expect(onScrub).toHaveBeenCalledWith(4.5);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm vitest run src/pages/compare/TransportDock.test.tsx`
Expected: FAIL - cannot resolve `./TransportDock`.

- [ ] **Step 3: Implement TransportDock**

`src/pages/compare/TransportDock.tsx`:

```tsx
/** Cockpit bottom dock: the Transport bar and the SyncTimeline fused
 *  into one panel behind one playhead. The lane gutter is HTML (real
 *  buttons, no SVG-text distortion); the track SVG renders at measured
 *  pixel width (ResizeObserver, Audit.tsx idiom) so nothing stretches.
 *  Scrub by dragging anywhere on the tracks or via the range slider
 *  (the keyboard-accessible control). */

import { MoveLeft, MoveRight, Pause, Play, Volume2 } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { type CompareShooterRecord } from "@/lib/api";
import { cn } from "@/lib/utils";

import { initials } from "./format";

const TRACK_PALETTE: string[] = [
  "var(--color-led)",
  "var(--color-shooter-jl)",
  "var(--color-shooter-pe)",
  "var(--color-shooter-rj)",
  "var(--color-manual)",
];

const GUTTER_W = 224;
const TRACK_H = 38;
const RULER_H = 24;
const PAD_BOTTOM = 6;
const PAD_RIGHT = 56; // room for the end-of-run time label

export function timeFromTrackX(
  px: number,
  width: number,
  maxTime: number,
): number {
  if (width <= 0) return 0;
  return Math.max(0, Math.min((px / width) * maxTime, maxTime));
}

export function TransportDock({
  shooters,
  maxTime,
  timeSinceBeep,
  audioSlug,
  isPlaying,
  onTogglePlay,
  onScrub,
  onPickAudio,
}: {
  shooters: CompareShooterRecord[];
  maxTime: number;
  timeSinceBeep: number;
  audioSlug: string | null;
  isPlaying: boolean;
  onTogglePlay: () => void;
  onScrub: (tsb: number) => void;
  onPickAudio: (slug: string) => void;
}) {
  const [trackW, setTrackW] = useState(960);
  const observerRef = useRef<ResizeObserver | null>(null);
  const svgRef = useCallback((el: SVGSVGElement | null) => {
    observerRef.current?.disconnect();
    observerRef.current = null;
    if (!el) return;
    const write = () =>
      setTrackW(Math.max(240, el.getBoundingClientRect().width));
    write();
    const ro = new ResizeObserver(write);
    ro.observe(el);
    observerRef.current = ro;
  }, []);

  const svgH = RULER_H + shooters.length * TRACK_H + PAD_BOTTOM;
  const plotW = trackW - PAD_RIGHT;
  const xOf = (tsb: number) => (tsb / maxTime) * plotW;
  const clampedT = Math.max(0, Math.min(timeSinceBeep, maxTime));

  const scrubFromPointer = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    onScrub(
      timeFromTrackX(
        e.clientX - rect.left,
        rect.width - PAD_RIGHT,
        maxTime,
      ),
    );
  };

  // Time ruler ticks every second; labels every second up to 20s span,
  // every 5s beyond that so long stages stay legible.
  const labelEvery = maxTime > 20 ? 5 : 1;
  const ticks: number[] = [];
  for (let t = 0; t <= maxTime + 0.001; t += 1) ticks.push(t);

  return (
    <div
      data-testid="transport-dock"
      className="flex-none rounded-2xl border border-rule-strong bg-bg-glow px-4 pb-2 pt-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]"
    >
      {/* Transport row */}
      <div className="flex flex-wrap items-center gap-3 pb-2">
        <button
          type="button"
          onClick={() => onScrub(0)}
          aria-label="Jump to beep"
          title="Jump to beep"
          className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-3 text-muted transition-colors hover:bg-surface-4 hover:text-ink"
        >
          <MoveLeft className="size-4" />
        </button>
        <button
          type="button"
          onClick={onTogglePlay}
          aria-label={isPlaying ? "Pause" : "Play"}
          className="inline-flex size-11 items-center justify-center rounded-full bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] transition-colors hover:bg-led-soft"
        >
          {isPlaying ? <Pause className="size-5" /> : <Play className="size-5" />}
        </button>
        <button
          type="button"
          onClick={() => onScrub(maxTime)}
          aria-label="Jump to end"
          title="Jump to end"
          className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-3 text-muted transition-colors hover:bg-surface-4 hover:text-ink"
        >
          <MoveRight className="size-4" />
        </button>
        <div className="ml-2 flex items-center gap-4 font-mono tabular-nums">
          <span className="flex flex-col items-start gap-0.5">
            <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
              t-beep
            </span>
            <span className="font-mono text-base font-bold leading-none text-led-text [text-shadow:0_0_10px_var(--color-led-glow)]">
              {timeSinceBeep.toFixed(3)}s
            </span>
          </span>
          <span className="flex flex-col items-start gap-0.5">
            <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
              span
            </span>
            <span className="font-mono text-base font-bold leading-none text-ink">
              {maxTime.toFixed(2)}s
            </span>
          </span>
        </div>
        <input
          type="range"
          aria-label="Scrub time since beep"
          className="min-w-[160px] flex-1 accent-led"
          min={0}
          max={maxTime}
          step={0.01}
          value={clampedT}
          onChange={(e) => onScrub(parseFloat(e.target.value))}
        />
        <span className="hidden font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle lg:inline">
          drag the tracks to scrub - click a lane for audio
        </span>
      </div>

      {/* Lane gutter + track SVG */}
      <div className="flex items-stretch">
        <div
          className="flex flex-none flex-col"
          style={{ width: GUTTER_W, paddingTop: RULER_H }}
        >
          {shooters.map((s, i) => {
            const isAudio = audioSlug === s.slug;
            const color = TRACK_PALETTE[i % TRACK_PALETTE.length];
            return (
              <button
                key={s.slug}
                type="button"
                onClick={() => onPickAudio(s.slug)}
                aria-pressed={isAudio}
                aria-label={`${s.name} - use as audio source`}
                title={`${s.name} - use as audio source`}
                className={cn(
                  "flex items-center gap-2 rounded-md pr-3 text-left transition-colors hover:bg-surface-2",
                  isAudio ? "text-ink" : "text-ink-2",
                )}
                style={{ height: TRACK_H }}
              >
                <span
                  aria-hidden="true"
                  className="size-2.5 flex-none rounded-full"
                  style={{ background: color }}
                />
                <span className="min-w-0 truncate font-display text-[0.75rem] font-bold uppercase tracking-[0.05em]">
                  {s.name}
                </span>
                {isAudio ? (
                  <Volume2 className="size-3 flex-none text-led" />
                ) : null}
                <span className="ml-auto font-mono text-[0.75rem] font-bold tabular-nums text-muted">
                  {s.stage_time_seconds != null
                    ? s.stage_time_seconds.toFixed(2)
                    : "-"}
                </span>
              </button>
            );
          })}
        </div>
        <svg
          ref={svgRef}
          role="presentation"
          height={svgH}
          className="min-w-0 flex-1 cursor-crosshair touch-none select-none"
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            scrubFromPointer(e);
          }}
          onPointerMove={(e) => {
            if (e.buttons & 1) scrubFromPointer(e);
          }}
        >
          {/* Time ruler */}
          {ticks.map((t) => (
            <g key={`tick-${t}`}>
              <line
                x1={xOf(t)}
                x2={xOf(t)}
                y1={RULER_H - (t % 5 === 0 ? 9 : 5)}
                y2={RULER_H}
                stroke="var(--color-rule)"
                strokeWidth={1}
              />
              {t % labelEvery === 0 ? (
                <text
                  x={xOf(t)}
                  y={RULER_H - 12}
                  textAnchor="middle"
                  fill="var(--color-subtle)"
                  fontFamily="JetBrains Mono, monospace"
                  fontSize={9}
                >
                  {t}s
                </text>
              ) : null}
            </g>
          ))}
          {/* Per-shooter tracks */}
          {shooters.map((s, i) => {
            const yMid = RULER_H + i * TRACK_H + TRACK_H / 2;
            const color = TRACK_PALETTE[i % TRACK_PALETTE.length];
            const endT = s.stage_time_seconds ?? maxTime;
            return (
              <g key={s.slug}>
                <line
                  x1={xOf(0)}
                  x2={xOf(endT)}
                  y1={yMid}
                  y2={yMid}
                  stroke={color}
                  strokeWidth={2.5}
                  strokeOpacity={0.3}
                  strokeLinecap="round"
                />
                {/* Progress segment up to the playhead */}
                <line
                  x1={xOf(0)}
                  x2={xOf(Math.min(clampedT, endT))}
                  y1={yMid}
                  y2={yMid}
                  stroke={color}
                  strokeWidth={2.5}
                  strokeOpacity={0.7}
                  strokeLinecap="round"
                />
                {/* End-of-run cap + total */}
                <line
                  x1={xOf(endT)}
                  x2={xOf(endT)}
                  y1={yMid - 9}
                  y2={yMid + 9}
                  stroke={color}
                  strokeWidth={2.5}
                  strokeLinecap="round"
                />
                <text
                  x={xOf(endT) + 7}
                  y={yMid + 3.5}
                  textAnchor="start"
                  fill="var(--color-ink)"
                  fontFamily="JetBrains Mono, monospace"
                  fontSize={10}
                  fontWeight={700}
                >
                  {(s.stage_time_seconds ?? 0).toFixed(2)}s
                </text>
                {/* Shot markers: fired solid, upcoming hollow */}
                {s.shots.map((shot) => {
                  const fired = shot.time_after_beep <= clampedT + 0.0005;
                  const isManual = shot.source === "manual";
                  const markerColor = isManual
                    ? "var(--color-manual)"
                    : color;
                  return (
                    <circle
                      key={`${s.slug}-${shot.shot_number}`}
                      data-testid={`shot-${s.slug}-${shot.shot_number}`}
                      data-fired={fired ? "true" : "false"}
                      cx={xOf(shot.time_after_beep)}
                      cy={yMid}
                      r={isManual ? 5 : 4}
                      fill={fired ? markerColor : "var(--color-surface)"}
                      stroke={fired ? "var(--color-bg)" : markerColor}
                      strokeWidth={fired ? 1.5 : 1.5}
                      strokeOpacity={fired ? 1 : 0.6}
                    />
                  );
                })}
              </g>
            );
          })}
          {/* Beep marker at t=0 */}
          <line
            x1={xOf(0)}
            x2={xOf(0)}
            y1={RULER_H - 2}
            y2={svgH - PAD_BOTTOM + 2}
            stroke="var(--color-beep)"
            strokeWidth={1.5}
            strokeDasharray="4 4"
            strokeOpacity={0.8}
          />
          {/* Playhead */}
          <line
            x1={xOf(clampedT)}
            x2={xOf(clampedT)}
            y1={RULER_H - 6}
            y2={svgH - PAD_BOTTOM}
            stroke="var(--color-led)"
            strokeWidth={2}
            style={{ filter: "drop-shadow(0 0 4px var(--color-led-glow))" }}
          />
        </svg>
      </div>
    </div>
  );
}
```

Note on jsdom: the test's ResizeObserver is a no-op stub (`src/testSetup.ts`), so `trackW` keeps its 960 default and `getBoundingClientRect` returns zeros - the marker/gutter/slider tests do not depend on real geometry, which is why the pointer-scrub path is covered by `timeFromTrackX` unit tests instead of a synthetic pointer event.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm vitest run src/pages/compare/TransportDock.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/pages/compare/TransportDock.tsx src/pages/compare/TransportDock.test.tsx
git commit -m "feat(ui): add fused transport + timeline dock for compare cockpit"
```

---

### Task 3: ShareShell viewport bound

**Files:**
- Modify: `src/components/share/ShareShell.tsx:30-73` (the `ShareFrame` component)

**Interfaces:**
- Consumes: nothing new.
- Produces: the middle region (`ShareFrame`'s children wrapper) becomes a bounded flex parent with its own scroll, so a child rendering `min-h-0 flex-1` (Compare in Task 4) is viewport-locked. Other share pages (Results viewer etc.) keep scrolling - the scroll just moves from the document to the middle region, pinning the branded header/footer.

- [ ] **Step 1: Rework ShareFrame's height chain**

In `src/components/share/ShareShell.tsx`, change the frame from a growing column (document scrolls, header/footer scroll away) to a locked column (middle region scrolls):

Current (`:30-73` shape):

```tsx
<div className="flex min-h-dvh flex-col bg-bg">
  <header className="border-b border-rule bg-surface">...</header>
  <div className="flex flex-1 flex-col">{children}</div>
  <footer className="border-t border-rule">...</footer>
</div>
```

New:

```tsx
<div className="flex h-dvh flex-col bg-bg">
  <header className="flex-none border-b border-rule bg-surface">...</header>
  <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</div>
  <footer className="flex-none border-t border-rule">...</footer>
</div>
```

Only the three wrapper classNames change; header/footer inner content (the `max-w-[1100px]` rows) is untouched. Update the comment block at `ShareShell.tsx:25-29` to document the new contract in the file's own comment style, e.g.: "The frame locks to the viewport (h-dvh); the middle region owns scrolling, so the branded header/footer stay pinned and a child that renders min-h-0 flex-1 (Compare's cockpit layout) is viewport-bounded without needing --shell-header-h."

- [ ] **Step 2: Run the full SPA test suite to verify nothing regressed**

Run: `pnpm test`
Expected: PASS - same counts as on main. If any share-surface test asserts on the old wrapper classes, update it to the new classes (that is a retired-behavior assertion, not a regression).

- [ ] **Step 3: Commit**

```bash
git add src/components/share/ShareShell.tsx
git commit -m "refactor(ui): pin share frame to viewport with scrolling middle region"
```

---

### Task 4: Compare page assembly (viewport lock + cockpit wiring)

**Files:**
- Modify: `src/pages/Compare.tsx` (root layout `:318`, header `:320-381`, toolbar `:384-435`, grid `:454-474`, dock wiring `:477-495`, `ShooterChip` `:675-730`, `layoutClass` `:736-740`, `VideoTile` `:772-852`; DELETE `Transport` `:858-914`, `Readout` `:916-927`, `TRACK_PALETTE` `:933-939`, `SyncTimeline` `:941-1118`, `RankingTable` `:1124-1198`, `RankPill` `:1200-1217`, local `avg`/`initials` `:1223-1236`)
- Delete: `src/pages/RankingTable.test.tsx`
- Test: `src/pages/Compare.test.tsx` (new)

**Interfaces:**
- Consumes: `LeaderboardRail` from `./compare/LeaderboardRail`, `TransportDock` from `./compare/TransportDock`, `initials` from `./compare/format` (for `ShooterChip`/`VideoTile`), everything else already imported.
- Produces: `Compare` and `isShareView` exports unchanged (`Compare.isShareView.test.ts` must keep passing). `RankingTable` export is REMOVED - grep the repo for `RankingTable` imports first; the only consumer is its own test file, which is deleted here.

- [ ] **Step 1: Write the failing page test**

`src/pages/Compare.test.tsx`. Follow the provider/mocking idiom of `src/components/match/MatchShell.test.tsx` (read it first). Compare needs router context only (`useParams`/`useLocation`/`useNavigate` + `useMatchHref`):

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { CompareStageResponse } from "@/lib/api";

import { Compare } from "./Compare";

const bundle: CompareStageResponse = {
  stage_number: 2,
  stage_name: "Standards",
  shooters: [
    {
      slug: "a",
      name: "Fast Shooter",
      stage_time_seconds: 14.32,
      beep_offset_in_clip: 1.0,
      video_ref: "trimmed/a.mp4",
      shots: [
        {
          shot_number: 1,
          time_after_beep: 1.18,
          source: "detected",
          interval_class: null,
        },
      ],
    },
    {
      slug: "b",
      name: "Slow Shooter",
      stage_time_seconds: 15.08,
      beep_offset_in_clip: 1.2,
      video_ref: "trimmed/b.mp4",
      shots: [
        {
          shot_number: 1,
          time_after_beep: 1.31,
          source: "detected",
          interval_class: null,
        },
      ],
    },
  ],
} as CompareStageResponse;

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn().mockResolvedValue({ shooters: [] }),
      getProject: vi.fn(),
      getStageCompare: vi.fn().mockResolvedValue(bundle),
      shooterVideoStreamUrl: (slug: string, ref: string) =>
        `/stream/${slug}/${ref}`,
    },
  };
});

function renderAt(path: string, routePattern: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={routePattern} element={<Compare />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Compare cockpit layout", () => {
  it("renders the leaderboard rail and transport dock on the operator route", async () => {
    renderAt("/match/m1/compare/2", "match/:matchId/compare/:stage");
    await waitFor(() =>
      expect(screen.getByTestId("leaderboard-rail")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("transport-dock")).toBeInTheDocument();
    // The old full-width ranking table is gone.
    expect(screen.queryByText("Ranking")).not.toBeInTheDocument();
    // Operator affordances present.
    expect(screen.getByRole("button", { name: "Audit" })).toBeInTheDocument();
  });

  it("hides operator affordances on the share route", async () => {
    renderAt("/share/tok123/compare/2", "share/:token/compare/:stage");
    await waitFor(() =>
      expect(screen.getByTestId("leaderboard-rail")).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: "Audit" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Export FCPXML/)).not.toBeInTheDocument();
  });
});
```

If `useMatchHref` or the api mock needs an extra provider to render, mirror exactly what `MatchShell.test.tsx` wraps with - add the minimum providers that make the render mount, not the full stack.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm vitest run src/pages/Compare.test.tsx`
Expected: FAIL - `getByTestId("leaderboard-rail")` not found (page still renders RankingTable).

- [ ] **Step 3: Restructure Compare.tsx**

3a. Imports: add exactly

```tsx
import { initials } from "./compare/format";
import { LeaderboardRail } from "./compare/LeaderboardRail";
import { TransportDock } from "./compare/TransportDock";
```

Remove now-unused lucide imports after the deletions (`Crosshair`, `MoveLeft`, `MoveRight`, `Pause`, `Play` move out with Transport/SyncTimeline; keep `Volume2`/`VolumeX` for `ShooterChip`, keep `ArrowDownToLine`, `ArrowLeft`, `ArrowRight`, `Loader2`) and remove the `splitsFromTimeline`/`statisticSplits` import (now only the rail uses them). Let `pnpm typecheck` be the arbiter of the final import list.

3b. Root wrapper (`:318`) - the viewport lock:

```tsx
<div
  data-testid="compare-page"
  className={cn(
    "flex min-h-0 flex-col gap-3 overflow-hidden px-7 py-4",
    isShareView(location.pathname)
      ? "min-h-0 flex-1"
      : "h-[calc(100dvh-var(--shell-header-h,86px))] min-h-[560px]",
  )}
>
```

(Reuse the existing `shareView` const instead of calling `isShareView` twice.) Operator route: hard bound to viewport minus measured header, with a 560px floor as the small-window safety valve (below it the document scrolls, matching MatchShell's grow behavior). Share route: `flex-1 min-h-0` against Task 3's bounded ShareFrame middle.

3c. Merge header + toolbar into ONE flex-none row (replaces both `:320-381` and `:384-435`): keep every existing element and handler, just re-parent them -

```tsx
<div className="flex flex-none flex-wrap items-center gap-x-4 gap-y-2 border-b border-rule pb-3">
  {/* stage nav buttons - unchanged markup */}
  <div className="flex items-center gap-1.5">...</div>
  {/* title - drop from text-3xl to text-2xl to slim the row */}
  <h1 className="font-display text-2xl font-bold uppercase leading-none tracking-tight text-ink">
    ...unchanged children...
  </h1>
  {/* tab strip - unchanged, but remove ml-auto (the right cluster owns it now) */}
  <nav aria-label="Stage views" className="inline-flex overflow-hidden rounded-lg border border-rule bg-surface-2 p-0.5">
    ...unchanged children...
  </nav>
  <div className="ml-auto flex flex-wrap items-center gap-3">
    <div className="flex flex-wrap items-center gap-2">
      {orderedShooters.map((shooter) => (
        <ShooterChip ... unchanged props ... />
      ))}
    </div>
    {/* layout pills - unchanged markup, minus its old ml-auto */}
    <div className="inline-flex overflow-hidden rounded-lg border border-rule bg-surface-2 p-0.5">...</div>
    {/* Export FCPXML button - unchanged, still behind !shareView */}
  </div>
</div>
```

3d. Compact `ShooterChip` (`:675-730`): the name button renders `initials(shooter.name)` instead of `shooter.name`, with the full name preserved for assistive tech and hover:

```tsx
<button
  type="button"
  onClick={onToggleVisibility}
  className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-ink-2 hover:text-ink"
  title={`${shooter.name} - ${visible ? "hide" : "show"}`}
  aria-label={`${shooter.name} - ${visible ? "hide" : "show"}`}
  aria-pressed={visible}
>
  {initials(shooter.name)}
</button>
```

(The full name still appears on the tile header, the dock gutter, and the rail.) The audio toggle button gains `aria-label={\`${shooter.name} - audio source\`}` so the two chip buttons stay distinguishable to screen readers.

3e. Video zone + rail (replaces the grid block `:454-474`): the zone and rail share a bounded row; the dock sits below it.

```tsx
<div className="flex min-h-0 flex-1 gap-4">
  {visibleShooters.length === 0 ? (
    <CompareEmptyState ... unchanged props ... />
  ) : (
    <>
      <div
        className={cn(
          "min-h-0 min-w-0 flex-1",
          layout === "stack"
            ? "flex flex-col gap-3 overflow-y-auto"
            : "grid gap-3",
        )}
        style={
          layout === "stack"
            ? undefined
            : layout === "grid"
              ? {
                  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                  gridTemplateRows: `repeat(${Math.max(1, Math.ceil(visibleShooters.length / 2))}, minmax(0, 1fr))`,
                }
              : {
                  gridTemplateColumns: `repeat(${visibleShooters.length}, minmax(0, 1fr))`,
                  gridTemplateRows: "minmax(0, 1fr)",
                }
        }
      >
        {visibleShooters.map((shooter) => (
          <VideoTile
            key={shooter.slug}
            shooter={shooter}
            isAudio={audioSlug === shooter.slug}
            fit={layout === "stack" ? "aspect" : "fill"}
            onPickAudio={() => setAudioSlug(shooter.slug)}
            onMount={(el) => setVideoRef(shooter.slug, el)}
          />
        ))}
      </div>
      <LeaderboardRail shooters={playableShooters} />
    </>
  )}
</div>
{visibleShooters.length > 0 ? (
  <TransportDock
    shooters={playableShooters}
    maxTime={maxStageTime}
    timeSinceBeep={timeSinceBeep}
    audioSlug={audioSlug}
    isPlaying={isPlaying}
    onTogglePlay={togglePlay}
    onScrub={scrubTo}
    onPickAudio={(slug) => setAudioSlug(slug)}
  />
) : null}
```

Delete the `layoutClass` function (`:736-740`) - the inline grid templates above replace it. The `LayoutPill` labels stay "2x2" / "1x4" / "Stack". The missing-footage banner block (`:442-451`) stays where it is, unchanged (it is flex-none by default).

3f. `VideoTile` fill mode (`:772-852`): add a `fit: "fill" | "aspect"` prop. Root div gains `flex min-h-0 flex-col` when filling; the media wrapper and video change; the footer stat strip (`:835-849`) is DELETED (the rail and dock gutter carry time + shot count now):

```tsx
function VideoTile({
  shooter,
  isAudio,
  fit,
  onPickAudio,
  onMount,
}: {
  shooter: CompareShooterRecord;
  isAudio: boolean;
  fit: "fill" | "aspect";
  onPickAudio: () => void;
  onMount: (el: HTMLVideoElement | null) => void;
}) {
  const url = shooter.video_ref
    ? api.shooterVideoStreamUrl(shooter.slug, shooter.video_ref)
    : null;
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border bg-bg-glow",
        fit === "fill" && "flex min-h-0 flex-col",
        isAudio
          ? "border-led shadow-[0_0_0_1px_var(--color-led-deep),0_0_16px_var(--color-led-glow)]"
          : "border-rule-strong",
      )}
    >
      <div className="flex flex-none items-center gap-2 border-b border-rule bg-surface-2 px-3 py-1.5">
        {/* header children unchanged from today (Avatar, name, Audio badge) */}
      </div>
      <div className={cn("relative", fit === "fill" && "min-h-0 flex-1 bg-black")}>
        {url ? (
          <video
            ref={onMount}
            src={url}
            preload="metadata"
            playsInline
            controls={false}
            className={cn(
              fit === "fill"
                ? "h-full w-full object-contain"
                : "aspect-video w-full bg-black",
            )}
            onClick={(e) => {
              if (!isAudio) {
                onPickAudio();
                e.preventDefault();
              }
            }}
          />
        ) : (
          <div
            className={cn(
              "flex items-center justify-center bg-surface-3 text-sm text-muted",
              fit === "fill" ? "h-full" : "aspect-video",
            )}
          >
            No trim yet
          </div>
        )}
      </div>
    </div>
  );
}
```

3g. Delete the retired blocks: `Transport` + `Readout` (`:858-927`), `TRACK_PALETTE` + `SyncTimeline` (`:933-1118`), `RankingTable` + `RankPill` (`:1124-1217`), local `avg`/`initials` (`:1223-1236`, now imported from `./compare/format` - `pad2` stays). Update the file docstring (`:1-24`) to describe the cockpit layout: viewport-locked page, merged header row, fill-sizing video zone, leaderboard rail, fused transport dock.

3h. Delete `src/pages/RankingTable.test.tsx` (its behavior assertions were ported to `LeaderboardRail.test.tsx` in Task 1; the component is gone).

- [ ] **Step 4: Run the tests**

Run: `pnpm vitest run src/pages`
Expected: PASS - `Compare.test.tsx` (2), `Compare.isShareView.test.ts` (unchanged), `compare/LeaderboardRail.test.tsx` (3), `compare/TransportDock.test.tsx` (4), plus the other page tests untouched. Then `pnpm typecheck` - expected clean; fix any unused-import fallout from the deletions.

- [ ] **Step 5: Commit**

```bash
git add src/pages/Compare.tsx src/pages/Compare.test.tsx
git rm src/pages/RankingTable.test.tsx
git commit -m "feat(ui): viewport-locked cockpit layout for compare view"
```

---

### Task 5: Gates + visual verification

**Files:**
- No source changes expected (fixes only if gates/screenshots surface problems).
- Create (scratch, not committed): `repo:~/.claude-tmp/compare_cockpit_shots.py`

- [ ] **Step 1: Run the full local CI gates**

From `src/splitsmith/ui_static/`:

```bash
pnpm typecheck && pnpm test && pnpm exec eslint src --max-warnings 0
```

From the repo root (python gates are required before any push even for UI-only diffs - repo rule):

```bash
uv run ruff check . && uv run black --check . && uv run pytest -q
```

Expected: all clean. Fix anything red - "pre-existing" is not an excuse in this repo; if a failure is unrelated and large, surface it to the user instead of dismissing it.

- [ ] **Step 2: Build the SPA bundle**

```bash
pnpm build
```

Expected: clean build into `dist/` (the local server serves the BUILT bundle, so screenshots without this step show stale UI).

- [ ] **Step 3: Launch the app against real match data and screenshot**

Test data: `~/matches/blacksmith-handgun-open-2026` (multi-shooter; read `match_id` from its `match.json`). Serve with stdout to a FILE (never an undrained pipe - it wedges uvicorn):

```bash
uv run splitsmith ui --project ~/matches/blacksmith-handgun-open-2026 > ~/.claude-tmp/compare-ui-server.log 2>&1 &
```

Screenshot script `~/.claude-tmp/compare_cockpit_shots.py` - Playwright Python from the repo venv, bundled Chromium, STRICTLY headless (`p.chromium.launch(headless=True)`, never `channel="chrome"` - a visible window on the user's desktop is a known past mistake), `wait_until="domcontentloaded"` + fixed timeout (the SPA long-polls; networkidle never settles):

```python
import faulthandler
import json
import pathlib

faulthandler.dump_traceback_later(240, exit=True)
from playwright.sync_api import sync_playwright

match_json = pathlib.Path.home() / "matches/blacksmith-handgun-open-2026/match.json"
match_id = json.loads(match_json.read_text())["match_id"]
out = pathlib.Path.home() / ".claude-tmp"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for name, w, h in [("1080p", 1920, 1080), ("laptop", 1366, 768)]:
        page = browser.new_page(viewport={"width": w, "height": h})
        page.goto(
            f"http://127.0.0.1:5174/match/{match_id}/compare/1",
            wait_until="domcontentloaded",
        )
        page.wait_for_timeout(2500)
        page.screenshot(path=str(out / f"compare-cockpit-{name}.png"))
        # Layout toggles: 1x4 and Stack
        for label in ["1x4", "Stack"]:
            page.get_by_role("button", name=label).click()
            page.wait_for_timeout(400)
            page.screenshot(
                path=str(out / f"compare-cockpit-{name}-{label.lower()}.png"),
            )
        page.close()
    browser.close()
print("DONE", flush=True)
```

Run: `uv run python -u ~/.claude-tmp/compare_cockpit_shots.py` (from the repo root).
Expected: `DONE` and 6 PNGs in `~/.claude-tmp/`.

Note: the match route is `/match/:matchId` SINGULAR. If video sources live on the unplugged external drive, streams 424 and tiles show poster-less black - the LAYOUT is still fully verifiable (tile boxes, rail, dock all render); do not chase the 424s.

- [ ] **Step 4: Inspect the screenshots**

Read each PNG and verify against the approved mockup (direction A):
- 1080p 2x2: header one row; four tiles fully visible; rail on the right with ranks/times/microstats; dock at the bottom with gutter names + tracks + playhead; NO page scrollbar (compare-page bottom edge within viewport).
- 1080p 1x4 and Stack: 1x4 fits without scroll; Stack scrolls INSIDE the video zone only (dock and rail stay pinned).
- laptop 1366x768: everything still lands; if the 560px min-height floor engages, the page may scroll - acceptable, note it.
- Share route spot check: append a share-token URL check only if a share token exists locally; otherwise note that share-route visual verify happens on staging.

Kill the server afterwards (`kill %1` or pkill on the uvicorn pid) and send the 1080p screenshots to the user for sign-off.

- [ ] **Step 5: Commit any fixes**

If screenshot inspection forced source fixes, re-run Step 1 + Step 2 and commit:

```bash
git add -u   # only files you actually modified - never glob-add untracked siblings
git commit -m "fix(ui): compare cockpit layout polish from visual verification"
```

---

## Self-review notes (already applied)

- Spec coverage: viewport lock (Tasks 3+4), videos size to remaining space (Task 4 / 3e-3f), leaderboard rail (Task 1), fused dock (Task 2), share route parity (Tasks 3+4 test), delete-legacy rule (Task 4 / 3g-3h), visual verify at target resolution (Task 5).
- Type consistency: `LeaderboardRail({ shooters })` and `TransportDock` prop names in Task 4's wiring match the Task 1/2 definitions; `timeFromTrackX` only consumed inside TransportDock + its test; `initials` import path `./compare/format` used in Tasks 1, 2, 4.
- Known judgment calls an implementer must NOT "fix": keeping the range slider (a11y), keeping banner/empty-state markup, keeping the `isShareView` export and its test, initials-only chips (full name stays available via title/aria + three other surfaces).
