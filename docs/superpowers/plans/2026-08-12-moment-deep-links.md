# Moment Deep Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Share a link to an exact stage-relative timestamp on the video (ResultsStage) and comparison (Compare) pages, with moment-aware OG unfurls, per `docs/superpowers/specs/2026-08-12-moment-deep-links-design.md`.

**Architecture:** A frontend `Moment` module serializes `{t, cam, who}` to query params; both pages apply a parsed moment once on load (seek paused + view state) and gain a "Copy link at moment" action. The backend extends the existing share-card pipeline (`share_card.py` -> `share_card_html.py` -> `share_card_render.py` -> `ui/share_og.py`): `StageCard` gains a moment badge, a new `CompareCard` and compare shell close the compare-OG gap, and moment-variant PNGs render on demand without object-storage writes.

**Tech Stack:** React 19 + react-router 7 + vitest (ui_static, pnpm); FastAPI + pydantic v2 + pytest (backend, uv).

## Global Constraints

- Work on branch `feat/moment-deep-links` (created from `spec/moment-deep-links`, which sits on main's tip a563345).
- `t` is seconds after the start beep; may be negative. Frontend parse rejects non-finite or |t| > 3600. Server clamps to [-60, 3600]. Both round to 2 decimals.
- Moment-variant card PNGs are NEVER written to object storage (unbounded-cardinality abuse vector). Moment-free cards keep the existing storage-backed path.
- New compare routes MUST be registered before the `{slug}`-parameterized routes in `share_og.py` (FastAPI matches in registration order; `{slug}` would swallow the literal `compare`).
- Share-surface failure philosophy (existing): "no rich preview" is acceptable, "no page" is not; dead and unknown tokens indistinguishable; malformed input degrades, never errors.
- All new user-visible copy and comments: ASCII only, single `-` dash, no em dashes.
- Moment marker on scrub bars: distinct shape + accessible label, never color-only.
- No new dependencies on either side.
- Backend commands: `uv run pytest -n0 <file> -q` (serial for a single file). Frontend: `pnpm vitest run <file>` from `src/splitsmith/ui_static/`.
- Scoped tests per task; full gates (ruff + black + full scoped pytest, pnpm typecheck + test + scoped eslint) once at end of branch (Task 10).

---

### Task 1: `Moment` module (frontend)

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/moment.ts`
- Test: `src/splitsmith/ui_static/src/lib/moment.test.ts`

**Interfaces:**
- Consumes: nothing (pure module).
- Produces: `type Moment = { t: number; cam?: string; who?: string[] }`; `momentToSearch(m: Moment): URLSearchParams`; `parseMoment(params: URLSearchParams): Moment | null`; `momentHref(pathname: string, m: Moment): string`; `resolveMomentView(moment: Moment, slugs: ReadonlySet<string>): { cam: string | null; who: string[] | null }`. Tasks 2-5 import these.

- [ ] **Step 1: Write the failing tests**

```ts
// src/splitsmith/ui_static/src/lib/moment.test.ts
import { describe, expect, it } from "vitest";
import { momentHref, momentToSearch, parseMoment, resolveMomentView } from "@/lib/moment";

describe("momentToSearch / parseMoment", () => {
  it("round-trips a full compare moment", () => {
    const m = { t: 4.32, cam: "alice", who: ["alice", "bob"] };
    expect(parseMoment(momentToSearch(m))).toEqual(m);
  });

  it("round-trips a bare results moment and formats t to 2 decimals", () => {
    const params = momentToSearch({ t: 1.005 });
    expect(params.toString()).toBe("t=1.00");
    expect(parseMoment(params)).toEqual({ t: 1 });
  });

  it("keeps negative pre-beep times", () => {
    expect(parseMoment(new URLSearchParams("t=-1.5"))).toEqual({ t: -1.5 });
  });

  it("returns null without t, or with junk / non-finite / out-of-range t", () => {
    expect(parseMoment(new URLSearchParams(""))).toBeNull();
    expect(parseMoment(new URLSearchParams("t=abc"))).toBeNull();
    expect(parseMoment(new URLSearchParams("t=Infinity"))).toBeNull();
    expect(parseMoment(new URLSearchParams("t=3600.01"))).toBeNull();
  });

  it("ignores unknown params and drops empty who entries", () => {
    const m = parseMoment(new URLSearchParams("t=2&foo=bar&who=alice,,"));
    expect(m).toEqual({ t: 2, who: ["alice"] });
  });

  it("momentHref builds pathname?query", () => {
    expect(momentHref("/share/tok/compare/3", { t: 4.32, cam: "alice" })).toBe(
      "/share/tok/compare/3?t=4.32&cam=alice",
    );
  });
});

describe("resolveMomentView", () => {
  const roster = new Set(["alice", "bob"]);

  it("keeps only slugs present in the roster", () => {
    expect(resolveMomentView({ t: 1, cam: "alice", who: ["alice", "ghost"] }, roster)).toEqual({
      cam: "alice",
      who: ["alice"],
    });
  });

  it("returns nulls when nothing valid remains", () => {
    expect(resolveMomentView({ t: 1, cam: "ghost", who: ["ghost"] }, roster)).toEqual({
      cam: null,
      who: null,
    });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/moment.test.ts`
Expected: FAIL - cannot resolve `@/lib/moment`.

- [ ] **Step 3: Implement**

```ts
// src/splitsmith/ui_static/src/lib/moment.ts
// A Moment is the shareable "what I am looking at" unit: seconds after the
// start beep plus, on Compare, the focused camera and visible shooters.
// This module is the single serializer/parser for its URL form - the future
// bookmark feature stores this same object and navigates via momentToSearch.

export type Moment = {
  t: number;
  cam?: string;
  who?: string[];
};

const T_LIMIT = 3600;

export function momentToSearch(m: Moment): URLSearchParams {
  const params = new URLSearchParams();
  params.set("t", m.t.toFixed(2));
  if (m.cam) params.set("cam", m.cam);
  if (m.who && m.who.length > 0) params.set("who", m.who.join(","));
  return params;
}

export function parseMoment(params: URLSearchParams): Moment | null {
  const raw = params.get("t");
  if (raw == null || raw.trim() === "") return null;
  const t = Number(raw);
  if (!Number.isFinite(t) || Math.abs(t) > T_LIMIT) return null;
  const moment: Moment = { t: Math.round(t * 100) / 100 };
  const cam = params.get("cam");
  if (cam) moment.cam = cam;
  const who = params.get("who");
  if (who) {
    const slugs = who
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (slugs.length > 0) moment.who = slugs;
  }
  return moment;
}

export function momentHref(pathname: string, m: Moment): string {
  return `${pathname}?${momentToSearch(m).toString()}`;
}

export function resolveMomentView(
  moment: Moment,
  slugs: ReadonlySet<string>,
): { cam: string | null; who: string[] | null } {
  const who = moment.who?.filter((s) => slugs.has(s)) ?? [];
  return {
    cam: moment.cam && slugs.has(moment.cam) ? moment.cam : null,
    who: who.length > 0 ? who : null,
  };
}
```

Note: `toFixed(2)` on `1.005` yields `"1.00"` (binary float rounds down) - the test above pins that so nobody "fixes" it into a mismatch with `parseMoment`'s `Math.round`.

- [ ] **Step 4: Run to verify pass**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/moment.test.ts`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/moment.ts src/splitsmith/ui_static/src/lib/moment.test.ts
git commit -m "feat(ui): Moment type with URL serializer for timestamp deep links"
```

---

### Task 2: ResultsPlayer moment support (marker, initial paused seek, copy button)

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/results/ResultsPlayer.tsx` (props interface L39-54; scrub-bar markers around L400-427; transport row L335-362)
- Test: `src/splitsmith/ui_static/src/components/results/ResultsPlayer.moment.test.tsx` (new file; check for an existing `ResultsPlayer.test.tsx` first and extend it instead if present)

**Interfaces:**
- Consumes: existing `ResultsPlayerProps`, internal `pct(t)` helper (L98), window clamp `winStart`/`winEnd` (L88-96).
- Produces: two new optional props consumed by Task 3:
  - `momentTime?: number | null` - moment position in ABSOLUTE clip seconds (page converts from seconds-after-beep). Renders a scrub-bar marker and performs a one-shot paused seek once video metadata is available.
  - `onCopyMoment?: () => void` - when set, renders a "Copy link at moment" button in the transport row.

- [ ] **Step 1: Write the failing test**

```tsx
// src/splitsmith/ui_static/src/components/results/ResultsPlayer.moment.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { ResultsPlayer } from "@/components/results/ResultsPlayer";

function renderPlayer(extra: Partial<React.ComponentProps<typeof ResultsPlayer>> = {}) {
  const videoRef = createRef<HTMLVideoElement>();
  const utils = render(
    <ResultsPlayer
      src="blob:test"
      beepTime={3}
      shots={[]}
      videoRef={videoRef}
      onTimeChange={() => {}}
      baselines={null}
      {...extra}
    />,
  );
  return { videoRef, ...utils };
}

describe("ResultsPlayer moment support", () => {
  it("renders a labelled moment marker when momentTime is set", () => {
    renderPlayer({ momentTime: 7.32 });
    expect(screen.getByLabelText(/moment at 4\.32s/i)).toBeTruthy();
  });

  it("renders no marker and no copy button without moment props", () => {
    renderPlayer();
    expect(screen.queryByLabelText(/moment at/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /copy link at moment/i })).toBeNull();
  });

  it("seeks paused to momentTime once metadata loads, exactly once", () => {
    const { videoRef } = renderPlayer({ momentTime: 7.32 });
    const video = videoRef.current!;
    Object.defineProperty(video, "duration", { value: 20, configurable: true });
    fireEvent(video, new Event("loadedmetadata"));
    expect(video.currentTime).toBeCloseTo(7.32, 2);
    expect(video.paused).toBe(true);
    video.currentTime = 1;
    fireEvent(video, new Event("loadedmetadata"));
    expect(video.currentTime).toBe(1);
  });

  it("clamps an out-of-range momentTime to the clip", () => {
    const { videoRef } = renderPlayer({ momentTime: 999 });
    const video = videoRef.current!;
    Object.defineProperty(video, "duration", { value: 20, configurable: true });
    fireEvent(video, new Event("loadedmetadata"));
    expect(video.currentTime).toBe(20);
  });

  it("fires onCopyMoment from the transport-row button", () => {
    const onCopyMoment = vi.fn();
    renderPlayer({ onCopyMoment });
    fireEvent.click(screen.getByRole("button", { name: /copy link at moment/i }));
    expect(onCopyMoment).toHaveBeenCalledTimes(1);
  });
});
```

Marker label math: `momentTime` is absolute clip seconds; the visible/announced label is seconds after beep, i.e. `momentTime - beepTime` = 7.32 - 3 = 4.32.

- [ ] **Step 2: Run to verify failure**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/components/results/ResultsPlayer.moment.test.tsx`
Expected: FAIL - unknown props / missing elements.

- [ ] **Step 3: Implement in ResultsPlayer.tsx**

Add to `ResultsPlayerProps` (L39-54):

```ts
  momentTime?: number | null;
  onCopyMoment?: () => void;
```

Add the one-shot seek effect near the other video-event effects (destructure `momentTime` with the other props):

```ts
const momentAppliedRef = useRef(false);
useEffect(() => {
  const v = videoRef.current;
  if (!v || momentTime == null || momentAppliedRef.current) return;
  const apply = () => {
    if (momentAppliedRef.current) return;
    momentAppliedRef.current = true;
    const end = Number.isFinite(v.duration) ? v.duration : momentTime;
    v.currentTime = Math.min(Math.max(momentTime, 0), end);
  };
  if (v.readyState >= 1) {
    apply();
    return;
  }
  v.addEventListener("loadedmetadata", apply, { once: true });
  return () => v.removeEventListener("loadedmetadata", apply);
}, [momentTime, videoRef]);
```

Add the marker in the scrub bar, next to the beep marker (L400-405), reusing the beep marker's positioning idiom but a distinct shape (diamond via rotated square, plus label - never color-only):

```tsx
{momentTime != null && (
  <div
    className="moment-marker"
    role="img"
    aria-label={`Moment at ${(momentTime - beepTime).toFixed(2)}s`}
    style={{ left: `${pct(momentTime)}%` }}
  />
)}
```

Style `.moment-marker` alongside the existing scrub-bar marker styles (same file or its co-located styles - mirror wherever the beep marker's styles live): absolutely positioned like the beep marker, `width/height` matching, `transform: translateX(-50%) rotate(45deg)` for the diamond, an outline so it reads in any theme.

Add the copy button in the transport row (L335-362), after the fullscreen button (L354). Mirror the fullscreen button's exact element structure and `className` (read it at L354 before writing this - the classes must match its siblings):

```tsx
{onCopyMoment && (
  <button
    type="button"
    aria-label="Copy link at moment"
    title="Copy link at moment"
    onClick={onCopyMoment}
  >
    {/* link icon: reuse the icon idiom of the sibling buttons (inline SVG) */}
  </button>
)}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/components/results/ResultsPlayer.moment.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/results/ResultsPlayer.tsx src/splitsmith/ui_static/src/components/results/ResultsPlayer.moment.test.tsx
git commit -m "feat(ui): moment marker, one-shot paused seek and copy hook in ResultsPlayer"
```

---

### Task 3: ResultsStage wiring (parse URL, convert to clip time, copy handler)

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/ResultsStage.tsx` (router imports L18, params L69, player render L451-473, Snackbar already at L496)

**Interfaces:**
- Consumes: `parseMoment`, `momentHref` (Task 1); `momentTime` / `onCopyMoment` props (Task 2); existing `coach.beep_time: number`, `videoRef` (L93), `snack`/`setSnack` state, `useLocation` (already imported).
- Produces: user-facing behavior only; no new exports.

- [ ] **Step 1: Implement (no new test file - behavior is covered by Task 1 unit tests + Task 2 component tests; the page adds only glue)**

Add imports: `useSearchParams` to the react-router import (L18); `momentHref, parseMoment` from `@/lib/moment`.

Inside the component:

```ts
const [searchParams] = useSearchParams();
const moment = useMemo(() => parseMoment(searchParams), [searchParams]);
const momentTime = moment != null && coach != null ? coach.beep_time + moment.t : null;

const handleCopyMoment = useCallback(async () => {
  const v = videoRef.current;
  if (!v || !coach) return;
  const t = Math.round((v.currentTime - coach.beep_time) * 100) / 100;
  const href = `${window.location.origin}${momentHref(location.pathname, { t })}`;
  try {
    await navigator.clipboard.writeText(href);
    setSnack({ message: `Link copied at ${t.toFixed(2)}s`, tone: "status" });
  } catch {
    setSnack({ message: "Could not copy link", tone: "error" });
  }
}, [coach, location.pathname]);
```

(`location` is the existing `useLocation()` value; `coach` is the loaded `CoachStageResponse`. Adjust the variable names only if the file's differ - read the surrounding code first.)

Pass to the player (L451-473):

```tsx
momentTime={momentTime}
onCopyMoment={handleCopyMoment}
```

- [ ] **Step 2: Verify with typecheck + existing suite**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm vitest run src/components/results src/lib/moment.test.ts`
Expected: PASS, no type errors.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/ResultsStage.tsx
git commit -m "feat(ui): timestamp deep links on the results video page"
```

---

### Task 4: Compare arrival - apply a moment once after bundle load

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Compare.tsx` (state L84-92, `scrubTo` L252-265, bundle effect keyed `[stageNumber]` around L123-129)

**Interfaces:**
- Consumes: `parseMoment`, `resolveMomentView` (Task 1); existing `bundle`, `setVisibleSlugs`, `setAudioSlug`, `scrubTo`, `videoRefs`.
- Produces: user-facing behavior only.

- [ ] **Step 1: Implement**

Add imports: `useSearchParams` to the react-router import (L41); `parseMoment, resolveMomentView` from `@/lib/moment`.

Add after the bundle-loading effect:

```ts
const [searchParams] = useSearchParams();
const momentAppliedRef = useRef(false);
useEffect(() => {
  if (!bundle || momentAppliedRef.current) return;
  const moment = parseMoment(searchParams);
  if (!moment) return;
  momentAppliedRef.current = true;
  const slugs = new Set(bundle.shooters.map((s) => s.slug));
  const view = resolveMomentView(moment, slugs);
  if (view.who) setVisibleSlugs(new Set(view.who));
  if (view.cam) setAudioSlug(view.cam);
  scrubTo(moment.t);
  // scrubTo writes currentTime immediately, but a video element that has
  // not reached HAVE_METADATA can drop that write - re-apply once per
  // element when its metadata arrives. Arrival is paused (isPlaying
  // defaults to false), so nothing else moves the clock in between.
  videoRefs.current.forEach((el, slug) => {
    if (el.readyState >= 1) return;
    const shooter = bundle.shooters.find((s) => s.slug === slug);
    if (!shooter || shooter.beep_offset_in_clip == null) return;
    const offset = shooter.beep_offset_in_clip;
    el.addEventListener(
      "loadedmetadata",
      () => {
        el.currentTime = Math.max(0, offset + moment.t);
      },
      { once: true },
    );
  });
}, [bundle, searchParams, scrubTo]);
```

Reset `momentAppliedRef.current = false` inside the existing stage-change effect (where `setBundle(null)` runs, ~L123) so navigating between stages re-arms the apply for a new URL.

- [ ] **Step 2: Verify with typecheck**

Run: `cd src/splitsmith/ui_static && pnpm typecheck`
Expected: PASS. (Behavioral verification for Compare happens in Task 10's staging/manual check; the validation logic itself is unit-tested via `resolveMomentView` in Task 1.)

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Compare.tsx
git commit -m "feat(ui): apply moment deep links on the compare page"
```

---

### Task 5: Compare capture - copy button and marker in TransportDock

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/compare/TransportDock.tsx` (props L74-92, track rendering)
- Modify: `src/splitsmith/ui_static/src/pages/Compare.tsx` (TransportDock render L513-522; add Snackbar)
- Test: `src/splitsmith/ui_static/src/pages/compare/TransportDock.moment.test.tsx` (extend an existing TransportDock test file instead if one exists)

**Interfaces:**
- Consumes: `momentHref`, `parseMoment` (Task 1); `Snackbar`/`SnackState` from `@/components/Snackbar`; existing `timeSinceBeep`, `audioSlug`, `visibleSlugs`, `playableShooters`, `location.pathname` (`useLocation` already imported in Compare, L41).
- Produces: TransportDock props gain `momentT?: number | null` (marker position, seconds after beep) and `onCopyMoment: () => void` (button).

- [ ] **Step 1: Write the failing test**

```tsx
// src/splitsmith/ui_static/src/pages/compare/TransportDock.moment.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TransportDock } from "@/pages/compare/TransportDock";

const baseProps = {
  shooters: [],
  maxTime: 10,
  timeSinceBeep: 2,
  audioSlug: null,
  isPlaying: false,
  onTogglePlay: () => {},
  onScrub: () => {},
  onPickAudio: () => {},
  onCopyMoment: () => {},
};

describe("TransportDock moment support", () => {
  it("renders a labelled marker when momentT is set", () => {
    render(<TransportDock {...baseProps} momentT={4.32} />);
    expect(screen.getByLabelText(/moment at 4\.32s/i)).toBeTruthy();
  });

  it("renders no marker when momentT is absent", () => {
    render(<TransportDock {...baseProps} />);
    expect(screen.queryByLabelText(/moment at/i)).toBeNull();
  });

  it("fires onCopyMoment", () => {
    const onCopyMoment = vi.fn();
    render(<TransportDock {...baseProps} onCopyMoment={onCopyMoment} />);
    fireEvent.click(screen.getByRole("button", { name: /copy link at moment/i }));
    expect(onCopyMoment).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/pages/compare/TransportDock.moment.test.tsx`
Expected: FAIL - unknown props / missing elements.

- [ ] **Step 3: Implement**

TransportDock props (L74-92): add

```ts
  momentT?: number | null;
  onCopyMoment: () => void;
```

In the track markup, position the marker by the same math the playhead uses (fraction of `maxTime`), diamond shape as in Task 2:

```tsx
{momentT != null && momentT >= 0 && momentT <= maxTime && (
  <div
    className="moment-marker"
    role="img"
    aria-label={`Moment at ${momentT.toFixed(2)}s`}
    style={{ left: `${(momentT / maxTime) * 100}%` }}
  />
)}
```

Add the copy button beside the play/pause control, mirroring its sibling's structure/classes, `aria-label="Copy link at moment"`, `onClick={onCopyMoment}`.

In Compare.tsx: add `Snackbar` + snack state (mirror ResultsStage L496):

```ts
const [snack, setSnack] = useState<SnackState | null>(null);
```

```ts
const handleCopyMoment = useCallback(async () => {
  const t = Math.round(timeSinceBeep * 100) / 100;
  const who = playableShooters.filter((s) => visibleSlugs.has(s.slug)).map((s) => s.slug);
  const href = `${window.location.origin}${momentHref(location.pathname, {
    t,
    cam: audioSlug ?? undefined,
    who,
  })}`;
  try {
    await navigator.clipboard.writeText(href);
    setSnack({ message: `Link copied at ${t.toFixed(2)}s`, tone: "status" });
  } catch {
    setSnack({ message: "Could not copy link", tone: "error" });
  }
}, [timeSinceBeep, playableShooters, visibleSlugs, audioSlug, location.pathname]);
```

Pass `momentT={parseMoment(searchParams)?.t ?? null}` and `onCopyMoment={handleCopyMoment}` at the TransportDock render (L513-522); render `<Snackbar snack={snack} onDismiss={() => setSnack(null)} />` at the page root (mirror ResultsStage). Memoize the `parseMoment` call if it ends up in render scope: `const urlMoment = useMemo(() => parseMoment(searchParams), [searchParams]);` and pass `momentT={urlMoment?.t ?? null}`.

- [ ] **Step 4: Run to verify pass**

Run: `cd src/splitsmith/ui_static && pnpm typecheck && pnpm vitest run src/pages/compare/TransportDock.moment.test.tsx`
Expected: PASS (3 tests), no type errors.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/compare/TransportDock.tsx src/splitsmith/ui_static/src/pages/compare/TransportDock.moment.test.tsx src/splitsmith/ui_static/src/pages/Compare.tsx
git commit -m "feat(ui): copy link at moment and track marker on the compare page"
```

---

### Task 6: Card models and HTML - `StageCard.moment_t`, `CompareCard`, badge

**Files:**
- Modify: `src/splitsmith/src/splitsmith/share_card.py` - wait, correct path: `src/splitsmith/share_card.py` (StageCard L122-133, card_hash L136-145)
- Modify: `src/splitsmith/share_card_html.py` (`_style` L52-91, `stage_card_html` L159-188)
- Test: `tests/test_share_card_html_moment.py` (new; follow the style of existing card tests in `tests/test_share_card_render.py`)

**Interfaces:**
- Consumes: existing `StageCard`, `MatchCard`, `card_hash`, `stage_card_html(card, *, theme)`, `match_card_html`.
- Produces:
  - `StageCard.moment_t: float | None = None`
  - `class CompareCard(BaseModel)` (frozen): `stage_number: int`, `stage_name: str`, `match_name: str`, `shooter_names: list[str]`, `moment_t: float | None = None`
  - `card_hash(card: MatchCard | StageCard | CompareCard) -> str` (same body, widened type)
  - `compare_card_html(card: CompareCard, *, theme) -> str` in `share_card_html.py`
  - Badge rendering: any card with `moment_t` set shows a `MOMENT {t:.2f}s` strip.

Note: adding `moment_t: None` to `StageCard` changes `card_hash` output for every existing stage card (the hash covers `model_dump`). That is safe by design - a moved hash is a moved URL, crawlers refetch, storage re-warms on first fetch. Do not add compatibility shims.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_share_card_html_moment.py
"""Moment badge + CompareCard HTML (spec 2026-08-12)."""

from splitsmith.overlay_theme import load_theme
from splitsmith.share_card import CompareCard, StageCard, StageFigures, card_hash
from splitsmith.share_card_html import compare_card_html, stage_card_html

THEME = load_theme("splitsmith")

FIGURES = StageFigures(draw=1.1, avg_split=0.25, split_count=4, interval_count=6, source="coach")


def _stage_card(moment_t: float | None) -> StageCard:
    return StageCard(
        stage_number=3,
        stage_name="Standards",
        shooter_name="Alice",
        match_name="Test Match",
        shot_count=6,
        stage_time=12.34,
        figures=FIGURES,
        moment_t=moment_t,
    )


def test_stage_card_without_moment_renders_no_badge() -> None:
    assert "MOMENT" not in stage_card_html(_stage_card(None), theme=THEME)


def test_stage_card_with_moment_renders_badge() -> None:
    assert "MOMENT 4.32s" in stage_card_html(_stage_card(4.32), theme=THEME)


def test_negative_moment_renders_signed() -> None:
    assert "MOMENT -1.50s" in stage_card_html(_stage_card(-1.5), theme=THEME)


def test_moment_moves_the_card_hash() -> None:
    assert card_hash(_stage_card(None)) != card_hash(_stage_card(4.32))


def test_compare_card_lists_shooters_and_escapes() -> None:
    card = CompareCard(
        stage_number=3,
        stage_name="Standards <b>",
        match_name="Test Match",
        shooter_names=["Alice", "Bob & Carol"],
        moment_t=None,
    )
    html = compare_card_html(card, theme=THEME)
    assert "Standards &lt;b&gt;" in html
    assert "Bob &amp; Carol" in html
    assert "MOMENT" not in html


def test_compare_card_with_moment_renders_badge() -> None:
    card = CompareCard(
        stage_number=3,
        stage_name="Standards",
        match_name="Test Match",
        shooter_names=["Alice"],
        moment_t=2.5,
    )
    assert "MOMENT 2.50s" in compare_card_html(card, theme=THEME)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -n0 tests/test_share_card_html_moment.py -q`
Expected: FAIL - `ImportError: cannot import name 'CompareCard'`.

- [ ] **Step 3: Implement**

`share_card.py`:

```python
class StageCard(BaseModel):
    """One shooter's run on one stage."""

    model_config = ConfigDict(frozen=True)

    stage_number: int
    stage_name: str
    shooter_name: str
    match_name: str
    shot_count: int
    stage_time: float | None = None
    figures: StageFigures
    #: Seconds after the beep a moment link points at, or None for the
    #: plain stage card. Part of the model dump, so part of card_hash -
    #: a moment variant hashes (and caches) differently by construction.
    moment_t: float | None = None


class CompareCard(BaseModel):
    """A stage comparison: who is being compared, on which stage."""

    model_config = ConfigDict(frozen=True)

    stage_number: int
    stage_name: str
    match_name: str
    shooter_names: list[str] = Field(default_factory=list)
    moment_t: float | None = None
```

Widen `card_hash`'s annotation to `MatchCard | StageCard | CompareCard` (body unchanged).

`share_card_html.py`:
- In `_style` add a `.badge` rule consistent with the existing look (LED-red accent per the instrument-panel theme tokens already used in this file - reuse the theme's accent color variable the way `.kick`/`.num` do):

```python
.badge {{ display:inline-block; padding: 6px 14px; border: 2px solid {accent};
          color: {accent}; font-family: {mono}; font-size: 28px;
          letter-spacing: 0.08em; border-radius: 6px; }}
```

(`{accent}`/`{mono}` here mean: interpolate the same theme values `_style` already interpolates for the existing red/mono styles - read `_style` L52-91 and use its exact variable names.)

- Shared helper + badge in `stage_card_html` (add to the `meta` row area, near the `Stage {n}` / shots interpolation):

```python
def _moment_badge(moment_t: float | None) -> str:
    if moment_t is None:
        return ""
    return f'<div class="badge">MOMENT {moment_t:.2f}s</div>'
```

Interpolate `{_moment_badge(card.moment_t)}` into the stage card body, after the meta/kick row.

- New `compare_card_html(card, *, theme)`: mirror `match_card_html`'s structure (brand row, `.kick` meta line with `Stage {card.stage_number} - {escape(card.stage_name)}` and `escape(card.match_name)`, roster-style listing of `escape(name)` for each of `card.shooter_names`, `_FOOTER`), plus `{_moment_badge(card.moment_t)}`. Every user string passes `html.escape` exactly like the existing builders.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest -n0 tests/test_share_card_html_moment.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/share_card.py src/splitsmith/share_card_html.py tests/test_share_card_html_moment.py
git commit -m "feat: moment badge on stage cards and new CompareCard model"
```

---

### Task 7: Render layer - CompareCard dispatch, storage key, uncached render path

**Files:**
- Modify: `src/splitsmith/share_card_render.py` (`storage_key` L63-68, `render_card` L71-78, `cached_card_png` L81-...)
- Test: extend `tests/test_share_card_render.py` (fakes `_FakeRasterizer` / `_BrokenRasterizer` at L32-51 already exist - reuse them)

**Interfaces:**
- Consumes: `CompareCard` (Task 6); existing `RenderedCard(png, fell_back)`, `render_card`, `storage_key`, `cached_card_png`, `RasterizerUnavailableError`, `FALLBACK_PNG_PATH`.
- Produces:
  - `storage_key` handles `CompareCard`: `f"share-cards/{token}/compare-{card.stage_number}-{digest}.png"`
  - `render_card` dispatches `CompareCard -> compare_card_html`
  - New `render_card_png(card, *, theme, rasterizer_factory) -> RenderedCard`: renders WITHOUT any storage read/write, falling back to the plate on `RasterizerUnavailableError` exactly like the cached path. `cached_card_png` is refactored to call it for the render-on-miss step so the fallback rule lives once.

- [ ] **Step 1: Write the failing tests (append to tests/test_share_card_render.py)**

```python
def test_compare_card_storage_key_shape() -> None:
    card = CompareCard(
        stage_number=3, stage_name="Standards", match_name="M", shooter_names=["A"],
    )
    key = storage_key("tok", card)
    assert key.startswith("share-cards/tok/compare-3-")
    assert key.endswith(".png")


def test_render_card_png_never_touches_storage() -> None:
    card = CompareCard(
        stage_number=3, stage_name="Standards", match_name="M", shooter_names=["A"],
        moment_t=4.32,
    )
    rendered = render_card_png(card, theme=THEME, rasterizer_factory=_Factory(_FakeRasterizer()))
    assert rendered.fell_back is False
    assert rendered.png  # the fake's bytes


def test_render_card_png_serves_the_plate_when_the_browser_is_gone() -> None:
    card = CompareCard(stage_number=3, stage_name="S", match_name="M", shooter_names=["A"])
    rendered = render_card_png(card, theme=THEME, rasterizer_factory=_Factory(_BrokenRasterizer()))
    assert rendered.fell_back is True
```

Match the existing file's import style and the actual constructor signatures of `_Factory`/`_FakeRasterizer` (read them at L32-51 first; the names above come from that file). Add `CompareCard`, `render_card_png`, `storage_key` to the imports as needed, and reuse the file's existing `THEME` constant if one exists (add one via `load_theme("splitsmith")` if not).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -n0 tests/test_share_card_render.py -q`
Expected: new tests FAIL - `ImportError: cannot import name 'render_card_png'`.

- [ ] **Step 3: Implement in share_card_render.py**

```python
def render_card_png(
    card: MatchCard | StageCard | CompareCard,
    *,
    theme: OverlayTheme,
    rasterizer_factory: Callable[[], AbstractContextManager[Rasterizer]],
) -> RenderedCard:
    """Render a card with no storage involved - the moment-variant path.

    Moment cards carry a continuous ``t``; writing one object per distinct
    ``t`` would let anyone holding a share token mint unbounded storage
    writes. They are rendered per fetch and HTTP-cached instead (the URL
    carries ``t`` and ``v``, so it is self-versioning). Same plate rule as
    the cached path: a fallback plate is a degraded response, flagged via
    ``fell_back`` so the route can short-cache it.
    """
    try:
        with rasterizer_factory() as rasterizer:
            return RenderedCard(png=render_card(card, theme=theme, rasterizer=rasterizer), fell_back=False)
    except RasterizerUnavailableError:
        return RenderedCard(png=FALLBACK_PNG_PATH.read_bytes(), fell_back=True)
```

Then refactor `cached_card_png`'s miss branch to call `render_card_png` (keeping its existing "do not write the plate to storage" behavior: only `storage.put` when `fell_back is False`). In `render_card` add the dispatch arm `isinstance(card, CompareCard) -> compare_card_html`; in `storage_key` add the `CompareCard` branch shown in Interfaces. Widen type annotations (`MatchCard | StageCard | CompareCard`) on `cached_card_png`, `render_card`, `storage_key`.

- [ ] **Step 4: Run to verify pass (whole file - guards the refactor of cached_card_png)**

Run: `uv run pytest -n0 tests/test_share_card_render.py -q`
Expected: PASS, including all pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/share_card_render.py tests/test_share_card_render.py
git commit -m "feat: uncached moment-card render path and CompareCard dispatch"
```

---

### Task 8: Moment-aware stage OG (png + meta + shell forwarding)

**Files:**
- Modify: `src/splitsmith/ui/share_og.py` (`build_stage_card` L100-152, `share_stage_png` L341-349, `share_stage_meta` L385-404, `share_stage_shell` L618-625)
- Test: extend `tests/test_share_og_routes.py` and `tests/test_share_og_meta.py`

**Interfaces:**
- Consumes: `render_card_png` (Task 7), `StageCard.moment_t` (Task 6); existing `_png_response`, `_PNG_HEADERS`, `_FALLBACK_PNG_HEADERS`, `_chromium_factory`, `card_hash`, `OgMeta`, `_fetch_og_meta`, `hosted_app` fixtures (`tests/hosted_helpers.py:69`), `monkeypatch.setattr(share_og, "_chromium_factory", ...)` pattern (test_share_og_routes.py L147).
- Produces:
  - `_parse_moment_t(value: str | None) -> float | None` (parse float, require finite, clamp [-60, 3600], round 2) - reused by Task 9.
  - `_uncached_png_response(state, card) -> Response` - reused by Task 9.
  - `build_stage_card(state, slug, stage_number, *, moment_t: float | None = None)`.
  - `GET /api/og/{slug}/{stage}.png?t=4.32` -> badge card, no storage write, year-long HTTP cache (plate short-cached).
  - `GET /api/og-meta/{slug}/{stage}?t=4.32` -> title suffixed `" - moment at 4.32s"`, `image_path` carrying `&t=4.32`.
  - Stage shell forwards a valid `t` onto the og-meta sub-request path.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_share_og_routes.py` (reuse that file's fixtures/idioms - `hosted_app_with_storage`, the `_no_browser` monkeypatch, and its existing share-creation helper for minting a token; read the top of the file and mirror how `test_stage_png_for_an_unknown_stage_falls_back_to_the_match_card` builds its client and token):

```python
def test_moment_stage_png_renders_without_a_storage_write(hosted_app_with_storage, monkeypatch):
    client, token = ...  # mint exactly as the neighboring stage-png tests do
    calls: list[object] = []
    real = share_og.render_card_png

    def _spy(card, **kwargs):
        calls.append(card)
        return real(card, **kwargs)

    monkeypatch.setattr(share_og, "render_card_png", _spy)
    put_count_before = ...  # count objects under share-cards/ in the moto bucket
    resp = client.get(f"/api/share/{token}/og/{SLUG}/1.png?t=4.32")
    assert resp.status_code == 200
    assert calls and calls[0].moment_t == 4.32
    assert ... == put_count_before  # no new share-cards/ objects


def test_moment_t_is_clamped_and_rounded(hosted_app_with_storage, monkeypatch):
    # t=999999 clamps to 3600.0; t=-500 clamps to -60.0; junk t falls back
    # to the cached moment-free card (route behaves as if t were absent).
    ...


def test_junk_t_serves_the_plain_stage_card(hosted_app_with_storage, monkeypatch):
    ...  # ?t=abc -> 200, card built with moment_t=None (spy on build_stage_card)
```

Append to `tests/test_share_og_meta.py` (mirror its existing stage-meta test setup):

```python
def test_stage_meta_with_moment_suffixes_title_and_image(hosted_app, ...):
    resp = client.get(f"/api/share/{token}/og-meta/{SLUG}/1?t=4.32")
    meta = resp.json()
    assert meta["title"].endswith(" - moment at 4.32s")
    assert "&t=4.32" in meta["image_path"]


def test_stage_shell_forwards_t_to_og_meta(hosted_app, monkeypatch):
    seen: list[str] = []
    async def _capture(request, path):
        seen.append(path)
        return None
    monkeypatch.setattr(share_og, "_fetch_og_meta", _capture)
    client.get(f"/share/{token}/results/{SLUG}/1?t=4.32")
    assert seen == [f"/api/share/{token}/og-meta/{SLUG}/1?t=4.32"]


def test_stage_shell_drops_junk_t(hosted_app, monkeypatch):
    ...  # ?t=abc -> og_meta_path has no query string
```

The `...` bodies above are structural placeholders ONLY for fixture-minting lines that must be copied from the neighboring tests in the same file (each file already contains the exact client/token setup to copy); every assertion shown is the real assertion to keep.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -n0 tests/test_share_og_routes.py tests/test_share_og_meta.py -q`
Expected: new tests FAIL (`render_card_png` not in share_og namespace; no title suffix; shell path carries no query).

- [ ] **Step 3: Implement in share_og.py**

Imports: add `math`, `render_card_png` (from `..share_card_render`), `CompareCard` (Task 9 will use it - add now or then).

```python
def _parse_moment_t(value: str | None) -> float | None:
    """A moment timestamp from a query string, or None for anything else.

    Same defensive stance as _parse_positive_int: a malformed value
    degrades to the moment-free variant, never an error. Clamp bounds and
    2-decimal rounding are the spec's cache-cardinality bound - at most
    100 distinct keys per clamped second.
    """
    if value is None:
        return None
    try:
        t = float(value)
    except ValueError:
        return None
    if not math.isfinite(t):
        return None
    return round(max(-60.0, min(3600.0, t)), 2)


def _uncached_png_response(state: Any, card: MatchCard | StageCard | CompareCard) -> Response:
    """Moment-variant cards: rendered per fetch, HTTP-cached only.

    No storage involved by design - see render_card_png's docstring. The
    plate keeps the short cache for the same reason _FALLBACK_PNG_HEADERS
    exists on the cached path.
    """
    rendered = render_card_png(
        card, theme=load_theme("splitsmith"), rasterizer_factory=_chromium_factory
    )
    headers = _FALLBACK_PNG_HEADERS if rendered.fell_back else _PNG_HEADERS
    return Response(content=rendered.png, media_type="image/png", headers=headers)
```

`build_stage_card` gains a keyword arg and passes it through:

```python
def build_stage_card(
    state: Any, slug: str, stage_number: int, *, moment_t: float | None = None
) -> StageCard | None:
    ...
    return StageCard(
        ...,
        moment_t=moment_t,
    )
```

`share_stage_png` branches:

```python
@router.get("/api/og/{slug}/{stage}.png", include_in_schema=False)
def share_stage_png(slug: str, stage: int, request: Request) -> Response:
    _hosted_gate()
    state = _state(request)
    token = _share_token(request)
    moment_t = _parse_moment_t(request.query_params.get("t"))
    card = build_stage_card(state, slug, stage, moment_t=moment_t)
    if card is None:
        return _png_response(state, token, build_match_card(state), None)
    if moment_t is not None:
        return _uncached_png_response(state, card)
    return _png_response(state, token, card, slug)
```

`share_stage_meta`: parse `t` the same way; when set, build the card with `moment_t=t`, suffix the title, and append `&t={t:.2f}` to `image_path`:

```python
    moment_t = _parse_moment_t(request.query_params.get("t"))
    card = build_stage_card(state, slug, stage, moment_t=moment_t)
    ...
    title = f"{card.shooter_name} - {card.stage_name} ({card.match_name})"
    image_path = f"/api/share/{token}/og/{slug}/{stage}.png?v={card_hash(card)}"
    if moment_t is not None:
        title = f"{title} - moment at {moment_t:.2f}s"
        image_path = f"{image_path}&t={moment_t:.2f}"
```

`share_stage_shell`: forward a valid `t` (and only a valid one) onto the sub-request path:

```python
    og_meta_path = f"/api/share/{quote(token, safe='')}/og-meta/{quote(slug, safe='')}/{stage_number}"
    moment_t = _parse_moment_t(request.query_params.get("t"))
    if moment_t is not None:
        og_meta_path = f"{og_meta_path}?t={moment_t:.2f}"
```

No `_SHARE_PATH_RE` change is needed for this task: the moment rides the query string, and `_share_alias` never touches `request.scope["query_string"]` (verified: the rewrite is path-only, server.py L6614-6616).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest -n0 tests/test_share_og_routes.py tests/test_share_og_meta.py -q`
Expected: PASS including all pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/share_og.py tests/test_share_og_routes.py tests/test_share_og_meta.py
git commit -m "feat: moment-aware stage OG cards, meta and shell forwarding"
```

---

### Task 9: Compare OG (png + meta + shell)

**Files:**
- Modify: `src/splitsmith/ui/share_og.py`
- Test: extend `tests/test_share_og_routes.py` and `tests/test_share_og_meta.py`

**Interfaces:**
- Consumes: `CompareCard` (Task 6), `render_card_png`/storage-backed `cached_card_png` via `_png_response` (Task 7), `_parse_moment_t` / `_uncached_png_response` (Task 8), `_parse_positive_int`, `_shell_response`, `_generic_tags`.
- Produces:
  - `_parse_who(value: str | None) -> list[str] | None` (comma-split, strip, drop empties, cap at 12 entries to bound card size).
  - `build_compare_card(state, stage_number, *, who=None, moment_t=None) -> CompareCard | None`.
  - `GET /api/og/compare/{stage}.png[?t=...&who=...]` and `GET /api/og-meta/compare/{stage}[?t=...&who=...]` - both defined ABOVE their `{slug}` counterparts in the file (registration order is load-bearing).
  - `GET /share/{token}/compare/{stage}` shell.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_share_og_routes.py` (same fixture-copying rule as Task 8):

```python
def test_compare_png_is_reachable_anonymously(hosted_app_with_storage, monkeypatch):
    resp = client.get(f"/api/share/{token}/og/compare/1.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_compare_png_with_moment_skips_storage(hosted_app_with_storage, monkeypatch):
    ...  # same spy/put-count shape as Task 8's moment test, path og/compare/1.png?t=2.5


def test_compare_route_wins_over_a_shooter_slugged_compare(hosted_app_with_storage, monkeypatch):
    # A literal 'compare' segment must dispatch to the compare card even
    # though /api/og/{slug}/{stage}.png would also match. Spy on
    # build_compare_card and assert it ran.
    ...
```

Append to `tests/test_share_og_meta.py`:

```python
def test_compare_meta_lists_shooters(hosted_app, ...):
    meta = client.get(f"/api/share/{token}/og-meta/compare/1").json()
    assert "comparison" in meta["title"]
    assert SHOOTER_NAME in meta["description"]
    assert f"/og/compare/1.png?v=" in meta["image_path"]


def test_compare_meta_with_moment_and_who(hosted_app, ...):
    meta = client.get(f"/api/share/{token}/og-meta/compare/1?t=2.50&who={SLUG}").json()
    assert meta["title"].endswith(" - moment at 2.50s")
    assert "&t=2.50" in meta["image_path"]
    assert f"&who={SLUG}" in meta["image_path"]


def test_compare_meta_unknown_who_falls_back_to_full_roster(hosted_app, ...):
    meta = client.get(f"/api/share/{token}/og-meta/compare/1?who=ghost").json()
    assert SHOOTER_NAME in meta["description"]


def test_compare_shell_serves_tags(hosted_app, monkeypatch):
    seen: list[str] = []
    async def _capture(request, path):
        seen.append(path)
        return None
    monkeypatch.setattr(share_og, "_fetch_og_meta", _capture)
    resp = client.get(f"/share/{token}/compare/1?t=2.5&who=alice")
    assert resp.status_code == 200
    assert seen == [f"/api/share/{token}/og-meta/compare/1?t=2.50&who=alice"]


def test_compare_shell_with_junk_stage_serves_generic_tags(hosted_app):
    resp = client.get(f"/share/{token}/compare/nope")
    assert resp.status_code == 200
    assert "og:image" not in resp.text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -n0 tests/test_share_og_routes.py tests/test_share_og_meta.py -q`
Expected: new tests FAIL (404s on compare paths, missing builders).

- [ ] **Step 3: Implement in share_og.py**

```python
_WHO_MAX = 12


def _parse_who(value: str | None) -> list[str] | None:
    if not value:
        return None
    slugs = [s.strip() for s in value.split(",")]
    slugs = [s for s in slugs if s][:_WHO_MAX]
    return slugs or None


def build_compare_card(
    state: Any, stage_number: int, *, who: list[str] | None = None, moment_t: float | None = None
) -> CompareCard | None:
    """None only when no shooter survives filtering - the caller then
    serves the match card, mirroring build_stage_card's ladder. An
    unknown 'who' entry is dropped, not an error; a 'who' that filters
    everyone away falls back to the full roster (a wrong-but-plausible
    who must not blank the card)."""
    match = state.match()
    slugs = [s for s in match.shooters if who is None or s in who]
    if not slugs and who is not None:
        slugs = list(match.shooters)
    names: list[str] = []
    stage_name: str | None = None
    for slug in slugs:
        try:
            project = state.shooter_project(slug)
        except HTTPException:
            names.append(slug)
            continue
        names.append(project.competitor_name or slug)
        if stage_name is None:
            try:
                stage_name = project.stage(stage_number).stage_name or None
            except (KeyError, ValueError, HTTPException):
                pass
    if not names:
        return None
    return CompareCard(
        stage_number=stage_number,
        stage_name=stage_name or f"Stage {stage_number}",
        match_name=match.name or "Splitsmith match",
        shooter_names=names,
        moment_t=moment_t,
    )
```

Routes - place BOTH physically above `share_stage_png` / `share_stage_meta` in the module (the decorators register in file order, and `{slug}` would otherwise capture the literal `compare`; a shooter actually slugged `compare` loses the stage-card URL shape, accepted per spec):

```python
@router.get("/api/og/compare/{stage}.png", include_in_schema=False)
def share_compare_png(stage: int, request: Request) -> Response:
    _hosted_gate()
    state = _state(request)
    token = _share_token(request)
    moment_t = _parse_moment_t(request.query_params.get("t"))
    who = _parse_who(request.query_params.get("who"))
    card = build_compare_card(state, stage, who=who, moment_t=moment_t)
    if card is None:
        return _png_response(state, token, build_match_card(state), None)
    if moment_t is not None or who is not None:
        return _uncached_png_response(state, card)
    return _png_response(state, token, card, None)
```

(`who`-only variants also skip storage: `who` is client-controlled with combinatorial cardinality - same abuse vector as `t`.)

```python
@router.get("/api/og-meta/compare/{stage}", response_model=OgMeta, include_in_schema=False)
def share_compare_meta(stage: int, request: Request) -> OgMeta:
    _hosted_gate()
    state = _state(request)
    token = _share_token(request)
    moment_t = _parse_moment_t(request.query_params.get("t"))
    who = _parse_who(request.query_params.get("who"))
    card = build_compare_card(state, stage, who=who, moment_t=moment_t)
    if card is None:
        return share_match_meta(request)
    title = f"{card.stage_name} comparison ({card.match_name})"
    image_path = f"/api/share/{token}/og/compare/{stage}.png?v={card_hash(card)}"
    if moment_t is not None:
        title = f"{title} - moment at {moment_t:.2f}s"
        image_path = f"{image_path}&t={moment_t:.2f}"
    if who is not None:
        image_path = f"{image_path}&who={quote(','.join(who), safe=',')}"
    return OgMeta(
        title=title,
        description=", ".join(card.shooter_names),
        image_path=image_path,
        alt=f"Splitsmith compare card for {card.stage_name}",
    )
```

Shell (place with the other shells; extract the query-forwarding into a helper shared with Task 8's stage shell):

```python
def _moment_query(request: Request, *, include_who: bool) -> str:
    parts: list[str] = []
    moment_t = _parse_moment_t(request.query_params.get("t"))
    if moment_t is not None:
        parts.append(f"t={moment_t:.2f}")
    if include_who:
        who = _parse_who(request.query_params.get("who"))
        if who:
            parts.append("who=" + quote(",".join(who), safe=","))
    return f"?{'&'.join(parts)}" if parts else ""


@router.get("/share/{token}/compare/{stage}", include_in_schema=False)
async def share_compare_shell(token: str, stage: str, request: Request) -> Response:
    _hosted_gate()
    stage_number = _parse_positive_int(stage)
    if stage_number is None:
        return _shell(_generic_tags())
    og_meta_path = (
        f"/api/share/{quote(token, safe='')}/og-meta/compare/{stage_number}"
        f"{_moment_query(request, include_who=True)}"
    )
    return await _shell_response(request, og_meta_path)
```

Refactor Task 8's stage-shell forwarding to use `_moment_query(request, include_who=False)`.

`_SHARE_PATH_RE` (server.py L988-1007): the existing alternatives `og/[^/]+/\d+\.png` and `og-meta/[^/]+/\d+` already admit the `compare` literal (it matches `[^/]+`). Make that intentional rather than incidental: add a one-line comment next to those alternatives noting the compare routes ride them, no pattern change. The anonymous-reachability test in Step 1 is the proof.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest -n0 tests/test_share_og_routes.py tests/test_share_og_meta.py -q`
Expected: PASS including all pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/share_og.py src/splitsmith/ui/server.py tests/test_share_og_routes.py tests/test_share_og_meta.py
git commit -m "feat: compare share OG shell, meta and card - closes the compare unfurl gap"
```

---

### Task 10: End-of-branch gates and manual verification

**Files:** none new.

- [ ] **Step 1: Frontend gates**

Run, from `src/splitsmith/ui_static/`:

```bash
pnpm typecheck && pnpm test && pnpm exec eslint src/lib/moment.ts src/lib/moment.test.ts src/pages/Compare.tsx src/pages/ResultsStage.tsx src/pages/compare/TransportDock.tsx src/components/results/ResultsPlayer.tsx
```

Expected: all pass, zero eslint errors.

- [ ] **Step 2: Backend gates**

Run, from repo root:

```bash
uv run ruff check src/splitsmith/share_card.py src/splitsmith/share_card_html.py src/splitsmith/share_card_render.py src/splitsmith/ui/share_og.py tests/test_share_card_html_moment.py
uv run black --check src/splitsmith/share_card.py src/splitsmith/share_card_html.py src/splitsmith/share_card_render.py src/splitsmith/ui/share_og.py tests/test_share_card_html_moment.py tests/test_share_card_render.py tests/test_share_og_routes.py tests/test_share_og_meta.py
uv run pytest -n0 tests/test_share_card_html_moment.py tests/test_share_card_render.py tests/test_share_og_routes.py tests/test_share_og_meta.py tests/test_share_routes.py -q
```

Expected: clean. (Known env-dependent local failures exist in the FULL suite - compare against main before blaming the branch; per repo memory, verify scoped files only here and let CI run the full suite.)

- [ ] **Step 3: ASCII sweep of added lines**

```bash
git diff main...HEAD | grep '^+' | grep -nP '[\x{2013}\x{2014}\x{2018}\x{2019}\x{201C}\x{201D}\x{2026}\x{00A0}\x{200B}]' && echo "FIX THESE" || echo "clean"
```

Expected: `clean`.

- [ ] **Step 4: Manual verification (dev server)**

1. Open a local match's results stage page, click "Copy link at moment" mid-clip, paste the URL into a new tab: video sits paused at the moment, marker visible on the scrub bar.
2. Same on a compare stage: select a subset of shooters + a focus camera, copy at a moment, open in a fresh tab: same shooters visible, same camera focused, clock at `t`, paused.
3. Junk params (`?t=abc`, `?cam=ghost`): pages load normally.
4. Screenshot check per the repo's bounded-headless recipe (route is `/match/:matchId/...` singular; use `domcontentloaded`, not networkidle - live SSE hangs Playwright's default wait).

- [ ] **Step 5: Commit any gate fixes, then hand off**

```bash
git add -u && git commit -m "chore: gate fixes for moment deep links branch"
```

Then follow `superpowers:finishing-a-development-branch` (PR against main; run `gh run watch` after opening - merge-when-green is NOT enforced on this repo).

---

## Self-review notes (resolved inline)

- Spec coverage: Moment module (T1), capture UX both surfaces (T2/T3/T5), arrival semantics (T2/T3/T4), moment-aware stage OG (T8), compare OG gap + CompareCard (T6/T7/T9), no-storage moment caching (T7/T8/T9), t clamp/round both sides (T1/T8), forward-design-only bookmarks (no task - by design), testing section (per-task + T10).
- Deliberate deviations from spec text: none. `who`-only compare variants also bypass storage (spec only mandated it for `t`; same cardinality argument applies - noted in T9).
- Type consistency: `momentTime` (absolute clip seconds, ResultsPlayer) vs `momentT`/`t` (seconds after beep, everywhere else) - conversion happens exactly once, in ResultsStage (T3). `render_card_png` name consistent across T7/T8/T9.
