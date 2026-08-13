# Share Camera Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let viewers of shared (and owner) match surfaces pick which camera to watch when a stage run has more than one.

**Architecture:** Frontend-only. The coach payload already carries every camera (`videos: CoachVideoEntry[]`, each with `path`, `role`, `beep_in_clip`) and the share whitelist already admits every camera's stream. A new read-only `CamPicker` strip goes on the Results stage page; Compare tiles get a native-select camera switcher fed by per-shooter coach fetches; `lib/moment.ts` gains a `v` param so copied moment links carry the camera choice. All shot times / `beep_time` are in the PRIMARY clip's coordinates, so non-primary cameras shift times by `entry.beep_in_clip - coach.beep_time`.

**Tech Stack:** React 18 + TypeScript, vitest + @testing-library/react, Tailwind. All frontend code under `src/splitsmith/ui_static/`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-13-share-camera-selection-design.md`.
- No backend changes. No new dependencies.
- ASCII punctuation only in new copy/comments; single `-` dash, never `--` or em dash.
- Accessibility: controls are real buttons/selects, visible focus rings (`focus-visible:ring-led` idiom), active state never color-only (`aria-pressed` / select value + border), labels like "Camera 2 of 3".
- Cameras with `beep_in_clip == null` are unsyncable: render them disabled.
- Camera identity = index into `coach.videos` (0 = primary). Invalid/stale index falls back silently to 0.
- Run each task's test file only (`pnpm --dir src/splitsmith/ui_static exec vitest run <file>`); the full gate (typecheck + test + lint) runs once in the final task.
- Commit message trailer required:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi`

**Branch setup (once, before Task 1):** current worktree branch `fix/863-railway-deploy-status-poll` already equals `origin/main` + the spec commit. Run:

```bash
git checkout -b feat/share-camera-selection
```

---

### Task 1: `v` param in lib/moment.ts

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/moment.ts`
- Test: `src/splitsmith/ui_static/src/lib/moment.test.ts`

**Interfaces:**
- Consumes: existing `Moment`, `momentToSearch`, `parseMoment`.
- Produces: `Moment.v?: number | Record<string, number>` (exported type `MomentCam`), serialized as `v=<int>` (Results form) or `v=slug:idx[,slug:idx...]` (Compare form). Index 0 is never serialized (primary = absence). Cap: index 1..32 (`V_INDEX_LIMIT = 32`, exported), record capped at `WHO_MAX` entries.

- [ ] **Step 1: Write the failing tests** - append to `moment.test.ts`:

```ts
describe("camera pick (v=)", () => {
  it("round-trips a results-form camera index", () => {
    const m = { t: 1.5, v: 2 };
    expect(momentToSearch(m).get("v")).toBe("2");
    expect(parseMoment(momentToSearch(m))).toEqual(m);
  });

  it("round-trips a compare-form per-shooter map", () => {
    const m = { t: 1.5, v: { alice: 1, bob: 2 } };
    expect(momentToSearch(m).get("v")).toBe("alice:1,bob:2");
    expect(parseMoment(momentToSearch(m))).toEqual(m);
  });

  it("never serializes index 0 (primary = absence)", () => {
    expect(momentToSearch({ t: 1, v: 0 }).get("v")).toBeNull();
    expect(momentToSearch({ t: 1, v: { alice: 0 } }).get("v")).toBeNull();
  });

  it("drops junk v tokens and keeps the valid ones", () => {
    expect(parseMoment(new URLSearchParams("t=1&v=abc"))).toEqual({ t: 1 });
    expect(parseMoment(new URLSearchParams("t=1&v=-1"))).toEqual({ t: 1 });
    expect(parseMoment(new URLSearchParams("t=1&v=999"))).toEqual({ t: 1 });
    expect(parseMoment(new URLSearchParams("t=1&v=alice:1,ghost:,:2,bob:999"))).toEqual({
      t: 1,
      v: { alice: 1 },
    });
  });

  it("caps the record form at WHO_MAX entries", () => {
    const v = Object.fromEntries(
      Array.from({ length: 15 }, (_, i) => [`s${i}`, 1]),
    );
    const parsed = parseMoment(momentToSearch({ t: 1, v }));
    expect(Object.keys((parsed?.v ?? {}) as Record<string, number>)).toHaveLength(WHO_MAX);
  });
});
```

Also import `V_INDEX_LIMIT` is not needed in tests; only `WHO_MAX` (already imported).

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/lib/moment.test.ts`
Expected: FAIL (v round-trip tests; `v` is not part of `Moment`).

- [ ] **Step 3: Implement** in `moment.ts`:

```ts
/** Camera pick: plain index on Results stage links, per-shooter map on
 *  Compare links. Index into the coach payload's videos[] (0 = primary,
 *  never serialized - primary is the absence of v). */
export type MomentCam = number | Record<string, number>;

export const V_INDEX_LIMIT = 32;
```

Extend the type: `v?: MomentCam;` on `Moment`.

In `momentToSearch`, after the `who` line:

```ts
if (m.v != null) {
  if (typeof m.v === "number") {
    if (Number.isInteger(m.v) && m.v > 0 && m.v <= V_INDEX_LIMIT) {
      params.set("v", String(m.v));
    }
  } else {
    const entries = Object.entries(m.v)
      .filter(([slug, idx]) => slug && Number.isInteger(idx) && idx > 0 && idx <= V_INDEX_LIMIT)
      .slice(0, WHO_MAX)
      .map(([slug, idx]) => `${slug}:${idx}`);
    if (entries.length > 0) params.set("v", entries.join(","));
  }
}
```

In `parseMoment`, after the `who` block:

```ts
const v = params.get("v");
if (v) {
  if (/^\d+$/.test(v)) {
    const idx = Number(v);
    if (idx > 0 && idx <= V_INDEX_LIMIT) moment.v = idx;
  } else {
    const map: Record<string, number> = {};
    for (const token of v.split(",").slice(0, WHO_MAX)) {
      const sep = token.indexOf(":");
      if (sep <= 0) continue;
      const slug = token.slice(0, sep).trim();
      const rawIdx = token.slice(sep + 1);
      if (!slug || !/^\d+$/.test(rawIdx)) continue;
      const idx = Number(rawIdx);
      if (idx > 0 && idx <= V_INDEX_LIMIT) map[slug] = idx;
    }
    if (Object.keys(map).length > 0) moment.v = map;
  }
}
```

Note the slice-then-filter order in serialize vs the tests: the cap test uses 15 valid entries so `.filter().slice(0, WHO_MAX)` and `.slice().filter()` agree; keep `.filter()` first as written.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/lib/moment.test.ts`
Expected: PASS (all, including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/moment.ts src/splitsmith/ui_static/src/lib/moment.test.ts
git commit -m "feat(ui): camera pick param v= in moment links"
```

---

### Task 2: CamPicker component

**Files:**
- Create: `src/splitsmith/ui_static/src/components/results/CamPicker.tsx`
- Test: `src/splitsmith/ui_static/src/components/results/CamPicker.test.tsx`

**Interfaces:**
- Consumes: `CoachVideoEntry` from `@/lib/api` (`{ path, role, beep_in_clip }`).
- Produces: `<CamPicker entries activeIndex onSelect srcFor />` - presentational strip, renders `null` when `entries.length < 2`. `srcFor(entry) => string` lets the page own URL building (owner vs share scoping). Camera labels: index 0 = "Primary", else "Cam N" (N = index + 1).

- [ ] **Step 1: Write the failing tests**:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CoachVideoEntry } from "@/lib/api";
import { CamPicker } from "@/components/results/CamPicker";

function entry(path: string, role: "primary" | "secondary", beep: number | null): CoachVideoEntry {
  return { path, role, beep_in_clip: beep };
}

const srcFor = (e: CoachVideoEntry) => `http://localhost/${e.path}`;

describe("CamPicker", () => {
  it("renders nothing for a single camera", () => {
    const { container } = render(
      <CamPicker entries={[entry("a.mp4", "primary", 5)]} activeIndex={0} onSelect={() => {}} srcFor={srcFor} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders one labelled button per camera and marks the active one", () => {
    render(
      <CamPicker
        entries={[entry("a.mp4", "primary", 5), entry("b.mp4", "secondary", 3)]}
        activeIndex={1}
        onSelect={() => {}}
        srcFor={srcFor}
      />,
    );
    const primary = screen.getByRole("button", { name: /camera 1 of 2/i });
    const cam2 = screen.getByRole("button", { name: /camera 2 of 2/i });
    expect(primary).toHaveAttribute("aria-pressed", "false");
    expect(cam2).toHaveAttribute("aria-pressed", "true");
  });

  it("selects on click and disables beepless cameras", () => {
    const onSelect = vi.fn();
    render(
      <CamPicker
        entries={[entry("a.mp4", "primary", 5), entry("b.mp4", "secondary", 3), entry("c.mp4", "secondary", null)]}
        activeIndex={0}
        onSelect={onSelect}
        srcFor={srcFor}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /camera 2 of 3/i }));
    expect(onSelect).toHaveBeenCalledWith(1);
    expect(screen.getByRole("button", { name: /camera 3 of 3/i })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/components/results/CamPicker.test.tsx`
Expected: FAIL ("Cannot find module .../CamPicker").

- [ ] **Step 3: Implement `CamPicker.tsx`**:

```tsx
/**
 * CamPicker - read-only camera strip for the Results stage surface
 * (owner and share mounts alike). Click-to-focus tiles; only the page's
 * main player ever plays (multicam tiles are pickers, PR #803). The
 * page owns stream-URL building via srcFor so owner/share scoping stays
 * in lib/api. Hidden entirely for single-camera runs.
 */
import type { CoachVideoEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

interface CamPickerProps {
  entries: CoachVideoEntry[];
  activeIndex: number;
  onSelect: (index: number) => void;
  srcFor: (entry: CoachVideoEntry) => string;
}

function camLabel(index: number): string {
  return index === 0 ? "Primary" : `Cam ${index + 1}`;
}

export function CamPicker({ entries, activeIndex, onSelect, srcFor }: CamPickerProps) {
  if (entries.length < 2) return null;
  return (
    <div role="group" aria-label="Cameras" className="mt-2 flex gap-2 overflow-x-auto pb-1">
      {entries.map((e, i) => {
        const active = i === activeIndex;
        // No beep on this camera yet: unsyncable, so unpickable - the
        // shot timeline could not be mapped onto its clock.
        const disabled = e.beep_in_clip == null;
        return (
          <button
            key={e.path}
            type="button"
            onClick={() => onSelect(i)}
            disabled={disabled}
            aria-pressed={active}
            aria-label={`Camera ${i + 1} of ${entries.length}: ${camLabel(i)}${disabled ? " (no beep sync)" : ""}`}
            className={cn(
              "flex w-24 shrink-0 flex-col overflow-hidden rounded-md border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
              active ? "border-led" : "border-rule-strong hover:border-rule",
              disabled && "opacity-40",
            )}
          >
            <video
              src={srcFor(e)}
              preload="metadata"
              muted
              playsInline
              tabIndex={-1}
              aria-hidden
              className="aspect-video w-full bg-black object-cover"
              onLoadedMetadata={(ev) => {
                // Park the thumb on the beep frame so tiles show the
                // run, not a pre-stage lull.
                if (e.beep_in_clip != null) ev.currentTarget.currentTime = e.beep_in_clip;
              }}
            />
            <span
              className={cn(
                "px-1.5 py-0.5 text-left font-mono text-[0.5625rem] font-bold uppercase tracking-[0.1em]",
                active ? "text-led" : "text-muted",
              )}
            >
              {camLabel(i)}
            </span>
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/components/results/CamPicker.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/results/CamPicker.tsx src/splitsmith/ui_static/src/components/results/CamPicker.test.tsx
git commit -m "feat(ui): CamPicker read-only camera strip"
```

---

### Task 3: Camera selection on ResultsStage

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.tsx`
- Test: create `src/splitsmith/ui_static/src/pages/ResultsStage.cameras.test.tsx`

**Interfaces:**
- Consumes: `CamPicker` (Task 2), `Moment.v` number form (Task 1), `api.videoStreamUrl(slug, path)`.
- Produces: user-visible behavior only; no new exports.

Key facts for the implementer:
- `coach.beep_time` and every `shots[i].time_absolute` are in the PRIMARY clip's coordinates. For camera index i, `delta = coach.videos[i].beep_in_clip - coach.beep_time`; pass `beepTime = coach.beep_time + delta` and shots with `time_absolute + delta` into `ResultsPlayer` and `SplitsList`. `time_from_beep` / `split` fields are camera-independent.
- Remount `ResultsPlayer` on camera switch via `key={camIndex}`; the page-level pending seek is applied in an effect (parent effects run after the child's, so the preserved position lands after ResultsPlayer's own `seekToWindowStart` and moment seek in both the listener path and the readyState>=1 path).
- The existing `const primary = coach.videos.find((v) => v.role === "primary")` and its `if (!primary)` dead end are replaced by index-based selection with fallback to entry 0.

- [ ] **Step 1: Write the failing tests** - new file `ResultsStage.cameras.test.tsx`, modeled on the harness in `ResultsStage.test.tsx` (copy its `vi.mock("@/lib/api", ...)`, `beforeAll` stubs, `makeShot`, `makeShooter`, `Shell`, `renderStage` helpers) with these deltas: mock `videoStreamUrl: (_slug: string, path: string) => \`http://localhost/${path}\`` and a `makeCoach` that takes a videos array:

```tsx
function makeCoach(videos: CoachVideoEntry[], shots: CoachShot[] = []): CoachStageResponse {
  return { stage_number: 2, stage_name: "Steel Rush", beep_time: 5, version: 4, videos, shots };
}

const TWO_CAMS: CoachVideoEntry[] = [
  { path: "cam-primary.mp4", role: "primary", beep_in_clip: 5 },
  { path: "cam-b.mp4", role: "secondary", beep_in_clip: 12 },
];

function mainVideoSrcs(): string[] {
  // CamPicker thumbs are aria-hidden; the main player's video is not.
  return Array.from(document.querySelectorAll("video:not([aria-hidden])")).map(
    (v) => (v as HTMLVideoElement).src,
  );
}

describe("ResultsStage camera selection", () => {
  it("renders no picker for a single-camera run", async () => {
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: [TWO_CAMS[0]],
    });
    await screen.findByText(/steel rush/i);
    expect(screen.queryByRole("group", { name: /cameras/i })).toBeNull();
  });

  it("renders the picker and swaps the player to the chosen camera", async () => {
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: TWO_CAMS,
    });
    await screen.findByText(/steel rush/i);
    expect(mainVideoSrcs()).toEqual(["http://localhost/cam-primary.mp4"]);
    fireEvent.click(screen.getByRole("button", { name: /camera 2 of 2/i }));
    expect(mainVideoSrcs()).toEqual(["http://localhost/cam-b.mp4"]);
  });

  it("opens on the camera a moment link names via ?v=", async () => {
    renderStage("/match/m1/results/anna/2?t=1.00&v=1", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: TWO_CAMS,
    });
    await screen.findByText(/steel rush/i);
    expect(mainVideoSrcs()).toEqual(["http://localhost/cam-b.mp4"]);
  });

  it("falls back to the first camera when no primary exists", async () => {
    renderStage("/match/m1/results/anna/2", [makeShooter("anna", "Anna", [[2, "audited"]])], {
      videos: [{ path: "cam-b.mp4", role: "secondary", beep_in_clip: 12 }],
    });
    await screen.findByText(/steel rush/i);
    expect(mainVideoSrcs()).toEqual(["http://localhost/cam-b.mp4"]);
  });
});
```

(`renderStage` gains a third options argument `{ videos, shots? }` feeding `makeCoach`; adapt the copied helper accordingly.)

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/ResultsStage.cameras.test.tsx`
Expected: FAIL (no picker group; single-cam assertions may pass - the swap and ?v= tests must fail).

- [ ] **Step 3: Implement in `ResultsStage.tsx`** (all inside `ResultsStageInner`):

State + derivations (near the other `useState` calls; the derived block goes after the early returns where `coach` is non-null):

```tsx
const [activeCamIndex, setActiveCamIndex] = useState(0);
// Position to restore after a camera switch remounts the player.
const pendingSeekRef = useRef<{ t: number; play: boolean } | null>(null);
// One-shot: apply a moment link's ?v= once per mount (the page remounts
// per slug-stage via the key in ResultsStage).
const appliedMomentCamRef = useRef(false);
```

Moment `?v=` application (an effect next to the coach-loading effect):

```tsx
useEffect(() => {
  if (!coach || appliedMomentCamRef.current) return;
  appliedMomentCamRef.current = true;
  const v = moment?.v;
  if (
    typeof v === "number" &&
    v < coach.videos.length &&
    coach.videos[v]?.beep_in_clip != null
  ) {
    setActiveCamIndex(v);
  }
}, [coach, moment]);
```

Pending-seek restore (runs after ResultsPlayer's own metadata handlers - parent effects run after child effects, and listeners fire in attach order):

```tsx
useEffect(() => {
  const pending = pendingSeekRef.current;
  const el = videoRef.current;
  if (!pending || !el) return;
  pendingSeekRef.current = null;
  const apply = () => {
    el.currentTime = Math.max(0, pending.t);
    if (pending.play) void el.play().catch(() => {});
  };
  if (el.readyState >= 1) apply();
  else el.addEventListener("loadedmetadata", apply, { once: true });
}, [activeCamIndex]);
```

Selection handler:

```tsx
const handleSelectCam = useCallback(
  (index: number) => {
    setActiveCamIndex((prev) => {
      if (index === prev || !coach) return prev;
      const prevBeep = coach.videos[prev]?.beep_in_clip ?? coach.beep_time;
      const nextBeep = coach.videos[index]?.beep_in_clip;
      if (nextBeep == null) return prev;
      const el = videoRef.current;
      if (el) {
        // Same run moment on the new camera's clock.
        pendingSeekRef.current = { t: el.currentTime - prevBeep + nextBeep, play: !el.paused };
      }
      return index;
    });
  },
  [coach],
);
```

Replace the `const primary = ...` line and the `if (!primary)` block (after the existing `!coach` early return) with:

```tsx
// Camera identity is the payload index (primary first). A stale index
// (coach reloaded with fewer cameras) silently falls back to entry 0;
// entry 0 also covers the no-primary edge instead of dead-ending.
const camIndex = coach.videos[activeCamIndex] ? activeCamIndex : 0;
const activeVideo = coach.videos[camIndex];
const activeBeep = activeVideo?.beep_in_clip ?? coach.beep_time;
const camDelta = activeBeep - coach.beep_time;
```

and change the dead end condition to `if (!activeVideo)` with copy "No video for this stage."

Shifted shots (replace the existing `shots` memo usage carefully - keep the raw `shots` memo, add below it, ABOVE the early returns since it is a hook):

```tsx
// Shot times arrive in the primary clip's coordinates; replaying them
// on another camera shifts them onto that clip's clock via the beep.
const camDeltaForShots = coach
  ? (coach.videos[coach.videos[activeCamIndex] ? activeCamIndex : 0]?.beep_in_clip ??
      coach.beep_time) - coach.beep_time
  : 0;
const displayShots = useMemo(
  () =>
    camDeltaForShots === 0
      ? shots
      : shots.map((s) => ({ ...s, time_absolute: s.time_absolute + camDeltaForShots })),
  [shots, camDeltaForShots],
);
```

Then swap consumers: `activeShotNumber`'s `currentShotIndex(displayShots, currentTime)`, `seekToShot` unchanged (it receives whatever SplitsList hands back), `<ResultsPlayer shots={displayShots} beepTime={coach.beep_time + camDelta} ...>`, `<SplitsList shots={displayShots} ...>`. Aggregates (`stageTime`, `splits`, `draw`) keep using the raw `shots` - they read camera-independent fields.

`momentTime` becomes beep-shift aware (it feeds the player's one-shot seek and marker; hooks order: it is a plain expression, keep it where it is but it reads state only):

```tsx
const momentTime =
  moment != null && coach != null
    ? (coach.videos[coach.videos[activeCamIndex] ? activeCamIndex : 0]?.beep_in_clip ??
        coach.beep_time) + moment.t
    : null;
```

Player render: add `key={camIndex}` and the new src, then the picker below it inside the sticky wrapper:

```tsx
<ResultsPlayer
  key={camIndex}
  src={api.videoStreamUrl(slug, activeVideo.path)}
  beepTime={coach.beep_time + camDelta}
  shots={displayShots}
  ...
/>
<CamPicker
  entries={coach.videos}
  activeIndex={camIndex}
  onSelect={handleSelectCam}
  srcFor={(e) => api.videoStreamUrl(slug, e.path)}
/>
```

Copy-moment carries the camera (in `handleCopyMoment`, both branches build from one object):

```tsx
const m = { t, ...(camIndexRef.current > 0 ? { v: camIndexRef.current } : {}) };
```

Add `const camIndexRef = useRef(0);` and set `camIndexRef.current = camIndex;` right after `camIndex` is derived (the callback is memoized on other deps; a ref avoids re-memoizing on every switch). Import `CamPicker` and `CoachVideoEntry` type as needed.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/ResultsStage.cameras.test.tsx`
Expected: PASS. Also run the neighbors to catch regressions:
`pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/ResultsStage.test.tsx src/pages/ResultsStage.trimstale.test.tsx src/components/results/ResultsPlayer.moment.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/ResultsStage.tsx src/splitsmith/ui_static/src/pages/ResultsStage.cameras.test.tsx
git commit -m "feat(ui): camera selection on the results stage viewer"
```

---

### Task 4: Per-shooter camera choice on Compare

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Compare.tsx`
- Test: create `src/splitsmith/ui_static/src/pages/Compare.cameras.test.tsx`

**Interfaces:**
- Consumes: `api.getStageCoach(slug, stage)` (per-shooter camera lists), `api.videoStreamUrl(slug, path)`, `CoachVideoEntry`.
- Produces: `camIndexBySlug` state + `camIndexFor(slug): number`, `effectiveBeep(shooter): number | null`, `tileSrc(shooter): string | null` helpers (component-internal; Task 5 reuses them). VideoTile gains props `src: string | null`, `cams: CoachVideoEntry[] | null`, `camIndex: number`, `onPickCam: (index: number) => void`.

Design note (spec updated to match): the tile camera control is a native `<select>` in the tile header, following the shooter-switcher precedent in ResultsStage's header - the tile has `overflow-hidden`, which would clip a custom popover, and the OS picker is the accessible overlay.

- [ ] **Step 1: Write the failing tests** - new file `Compare.cameras.test.tsx`, modeled on the harness in `Compare.test.tsx` (reuse its api mock/render scaffolding; keep its stubs for `ResizeObserver`/`matchMedia` if present). Mock additions: `getStageCoach: vi.fn()`, `videoStreamUrl: (_s: string, path: string) => \`http://localhost/coach/${path}\``, `shooterVideoStreamUrl: (_s: string, ref: string) => \`http://localhost/trim/${ref}\``. Bundle: two shooters `anna` (2 cameras) and `bob` (1 camera), both with `video_ref` and `beep_offset_in_clip`. `getStageCoach` resolves per slug:

```tsx
vi.mocked(api.getStageCoach).mockImplementation(async (slug: string) =>
  makeCoachFor(slug, slug === "anna"
    ? [
        { path: "anna-primary.mp4", role: "primary", beep_in_clip: 5 },
        { path: "anna-b.mp4", role: "secondary", beep_in_clip: 9 },
      ]
    : [{ path: "bob-primary.mp4", role: "primary", beep_in_clip: 4 }]),
);
```

Tests:

```tsx
it("shows a camera select only on multi-camera tiles", async () => {
  renderCompare("/match/m1/compare/2");
  await screen.findByTestId("compare-page");
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: /anna - camera/i })).toBeInTheDocument(),
  );
  expect(screen.queryByRole("combobox", { name: /bob - camera/i })).toBeNull();
});

it("swaps the tile video to the chosen camera and back", async () => {
  renderCompare("/match/m1/compare/2");
  await screen.findByTestId("compare-page");
  const select = await screen.findByRole("combobox", { name: /anna - camera/i });
  const annaVideo = () =>
    Array.from(document.querySelectorAll("video")).find((v) =>
      (v as HTMLVideoElement).src.includes("anna"),
    ) as HTMLVideoElement;
  expect(annaVideo().src).toContain("/trim/");
  fireEvent.change(select, { target: { value: "1" } });
  expect(annaVideo().src).toBe("http://localhost/coach/anna-b.mp4");
  fireEvent.change(select, { target: { value: "0" } });
  expect(annaVideo().src).toContain("/trim/");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/Compare.cameras.test.tsx`
Expected: FAIL (no combobox named "anna - camera").

- [ ] **Step 3: Implement in `Compare.tsx`**:

State + fetch (after the bundle-loading effect):

```tsx
// Camera alternatives per shooter, from each shooter's coach payload
// (share-whitelisted; Compare's own bundle carries only the primary
// trim). A failed fetch just means no switcher for that shooter.
const [camsBySlug, setCamsBySlug] = useState<Record<string, CoachVideoEntry[]>>({});
const [camIndexBySlug, setCamIndexBySlug] = useState<Record<string, number>>({});

useEffect(() => {
  if (!bundle) return;
  let alive = true;
  setCamsBySlug({});
  setCamIndexBySlug({});
  (async () => {
    const results = await Promise.allSettled(
      bundle.shooters.map(
        async (s) => [s.slug, (await api.getStageCoach(s.slug, stageNumber)).videos] as const,
      ),
    );
    if (!alive) return;
    const map: Record<string, CoachVideoEntry[]> = {};
    for (const r of results) if (r.status === "fulfilled") map[r.value[0]] = r.value[1];
    setCamsBySlug(map);
  })();
  return () => {
    alive = false;
  };
}, [bundle, stageNumber]);
```

Resolution helpers (all three memoized together; every consumer effect adds them to its deps):

```tsx
// Index into camsBySlug[slug]; 0 = the bundle's own primary trim.
// Invalid or unsyncable picks resolve to 0 - graceful drift, never an
// error (moment links may name cameras that no longer exist).
const camIndexFor = useCallback(
  (slug: string): number => {
    const idx = camIndexBySlug[slug] ?? 0;
    return idx > 0 && camsBySlug[slug]?.[idx]?.beep_in_clip != null ? idx : 0;
  },
  [camIndexBySlug, camsBySlug],
);
const effectiveBeep = useCallback(
  (s: CompareShooterRecord): number | null => {
    const idx = camIndexFor(s.slug);
    return idx > 0 ? camsBySlug[s.slug][idx].beep_in_clip : s.beep_offset_in_clip;
  },
  [camIndexFor, camsBySlug],
);
const tileSrc = useCallback(
  (s: CompareShooterRecord): string | null => {
    const idx = camIndexFor(s.slug);
    if (idx > 0) return api.videoStreamUrl(s.slug, camsBySlug[s.slug][idx].path);
    return s.video_ref ? api.shooterVideoStreamUrl(s.slug, s.video_ref) : null;
  },
  [camIndexFor, camsBySlug],
);
```

Beep-offset consumers switch to `effectiveBeep` (add it to each effect's deps):
- Sync engine: `const masterBeep = effectiveBeep(audioShooter) ?? 0;` and in the slave loop `const beep = effectiveBeep(shooter); if (beep == null) return; const target = beep + tsb;`.
- `scrubTo`: `const beep = effectiveBeep(shooter); if (beep == null) return; el.currentTime = Math.max(0, beep + tsb);`.
- Moment-apply effect's `loadedmetadata` fallback: `const offset = effectiveBeep(shooter); if (offset == null) return;` then `offset + moment.t`.

Resync after a swap (new effect; the drift guard makes the extra dep-triggered runs no-ops):

```tsx
// A tile whose src just swapped reloads at clip time 0; put it back on
// the shared clock once its metadata is in. The drift guard keeps this
// from fighting the sync engine or the user's scrubbing.
useEffect(() => {
  videoRefs.current.forEach((el, slug) => {
    const shooter = orderedShooters.find((s) => s.slug === slug);
    if (!shooter) return;
    const beep = effectiveBeep(shooter);
    if (beep == null) return;
    const target = Math.max(0, beep + timeSinceBeep);
    if (Math.abs(el.currentTime - target) < 0.3) return;
    const apply = () => {
      el.currentTime = target;
      if (isPlaying) void el.play().catch(() => {});
    };
    if (el.readyState >= 1) apply();
    else el.addEventListener("loadedmetadata", apply, { once: true });
  });
}, [camIndexBySlug, camsBySlug, orderedShooters, effectiveBeep, timeSinceBeep, isPlaying]);
```

VideoTile call site:

```tsx
<VideoTile
  key={shooter.slug}
  shooter={shooter}
  src={tileSrc(shooter)}
  cams={camsBySlug[shooter.slug] ?? null}
  camIndex={camIndexFor(shooter.slug)}
  onPickCam={(index) =>
    setCamIndexBySlug((prev) => ({ ...prev, [shooter.slug]: index }))
  }
  isAudio={audioSlug === shooter.slug}
  fit={layout === "stack" ? "aspect" : "fill"}
  onPickAudio={() => setAudioSlug(shooter.slug)}
  onMount={(el) => setVideoRef(shooter.slug, el)}
/>
```

VideoTile changes: props gain `src: string | null; cams: CoachVideoEntry[] | null; camIndex: number; onPickCam: (index: number) => void;`; delete the internal `url` computation (use `src`); the header's right side becomes one cluster so the audio badge and the select share the `ml-auto`:

```tsx
<span className="ml-auto flex items-center gap-2">
  {cams && cams.length > 1 ? (
    <span className="relative inline-flex items-center">
      <select
        value={camIndex}
        onChange={(e) => onPickCam(Number(e.target.value))}
        aria-label={`${shooter.name} - camera`}
        className="cursor-pointer appearance-none bg-transparent pr-4 font-mono text-[0.625rem] font-bold uppercase tracking-[0.1em] text-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
      >
        {cams.map((c, i) => (
          <option key={c.path} value={i} disabled={c.beep_in_clip == null}>
            {i === 0 ? "Primary" : `Cam ${i + 1}`}
          </option>
        ))}
      </select>
      <ChevronDown aria-hidden className="pointer-events-none absolute right-0 size-3 text-subtle" />
    </span>
  ) : null}
  {isAudio && (
    /* existing Audio badge JSX, minus its ml-auto */
  )}
</span>
```

Imports to add: `ChevronDown` from lucide-react, `CoachVideoEntry` type from `@/lib/api`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/Compare.cameras.test.tsx src/pages/Compare.test.tsx src/pages/Compare.isShareView.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Compare.tsx src/splitsmith/ui_static/src/pages/Compare.cameras.test.tsx
git commit -m "feat(ui): per-shooter camera choice on compare tiles"
```

---

### Task 5: Compare moment links carry camera picks

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Compare.tsx`
- Test: extend `src/splitsmith/ui_static/src/pages/Compare.cameras.test.tsx`

**Interfaces:**
- Consumes: `Moment.v` record form (Task 1), `camIndexBySlug` / `camIndexFor` (Task 4).
- Produces: user-visible behavior only.

- [ ] **Step 1: Write the failing tests** - append to `Compare.cameras.test.tsx`:

```tsx
it("applies a moment link's per-shooter camera picks", async () => {
  renderCompare("/match/m1/compare/2?t=1.00&v=anna:1");
  await screen.findByTestId("compare-page");
  const select = await screen.findByRole("combobox", { name: /anna - camera/i });
  await waitFor(() => expect((select as HTMLSelectElement).value).toBe("1"));
});

it("copies moment links with the current camera picks", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
  renderCompare("/match/m1/compare/2");
  await screen.findByTestId("compare-page");
  const select = await screen.findByRole("combobox", { name: /anna - camera/i });
  fireEvent.change(select, { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: /copy link/i }));
  await waitFor(() => expect(writeText).toHaveBeenCalled());
  expect(String(writeText.mock.calls[0][0])).toContain("v=anna%3A1");
});
```

(Check `TransportDock` for the copy button's accessible name and adjust the `getByRole` query to the real label; `TransportDock.moment.test.tsx` shows it.)

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/Compare.cameras.test.tsx`
Expected: the two new tests FAIL.

- [ ] **Step 3: Implement in `Compare.tsx`**:

Apply in the moment effect (after the `if (view.cam) setAudioSlug(view.cam);` line):

```tsx
if (moment.v && typeof moment.v === "object") {
  const roster = new Set(bundle.shooters.map((s) => s.slug));
  const picks: Record<string, number> = {};
  for (const [slug, idx] of Object.entries(moment.v)) {
    if (roster.has(slug)) picks[slug] = idx;
  }
  // Validity against the camera lists is enforced lazily by camIndexFor
  // - the lists may still be loading when the moment applies.
  if (Object.keys(picks).length > 0) setCamIndexBySlug((prev) => ({ ...prev, ...picks }));
}
```

Timing note: the moment-apply effect and the resync effect (Task 4) together cover the load races - whichever of {camera lists, moment} lands last, the resync effect runs on the state change and re-seats the swapped tile at `effectiveBeep + timeSinceBeep`.

Copy in `handleCopyMoment` (build `v` next to `who`; add `camIndexFor` to the callback's deps):

```tsx
const v: Record<string, number> = {};
for (const s of playableShooters) {
  const idx = camIndexFor(s.slug);
  if (idx > 0) v[s.slug] = idx;
}
const moment = {
  t,
  cam: audioSlug ?? undefined,
  who,
  ...(Object.keys(v).length > 0 ? { v } : {}),
};
```

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/Compare.cameras.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Compare.tsx src/splitsmith/ui_static/src/pages/Compare.cameras.test.tsx
git commit -m "feat(ui): compare moment links carry camera picks"
```

---

### Task 6: End-of-branch gate

**Files:** none new.

- [ ] **Step 1: Full SPA gate**

```bash
pnpm --dir src/splitsmith/ui_static typecheck
pnpm --dir src/splitsmith/ui_static test
pnpm --dir src/splitsmith/ui_static exec eslint src/lib/moment.ts src/components/results/CamPicker.tsx src/components/results/CamPicker.test.tsx src/pages/ResultsStage.tsx src/pages/ResultsStage.cameras.test.tsx src/pages/Compare.tsx src/pages/Compare.cameras.test.tsx src/lib/moment.test.ts
```

Expected: all pass, zero eslint errors.

- [ ] **Step 2: Dash sweep** - no `--` or em dashes in added lines:

```bash
git diff origin/main -- src/splitsmith/ui_static | grep '^+' | grep -nE '(--|\x{2014})' || echo clean
```

Expected: `clean` (ignore CLI-flag hits inside commands/urls if any; prose/comments must be clean).

- [ ] **Step 3: Visual spot-check** (owner surface, bounded headless screenshot per the UI-verification recipe - Playwright MCP navigate hangs on live SSE; use domcontentloaded): load a `/match/:matchId/results/:slug/:stage` page for a stage with 2+ cameras if a local match is available; otherwise note it for the user to eyeball on staging.

- [ ] **Step 4: Commit any fixes, then hand off** to superpowers:finishing-a-development-branch (PR to main).
