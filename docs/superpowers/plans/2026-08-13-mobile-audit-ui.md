# Mobile Audit UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build step 6 of the mobile audit design - the phone-shaped audit screen - so an operator can run a complete shot-audit pass on a stage from a phone.

**Architecture:** A new `MobileAudit` page renders the whole stage as a wrapped stack of waveform rows (nothing scrolls), with a fixed footer holding a zoom lane (playhead pinned centre, dashed target band), transport, and a three-state action area. All edits reuse the desktop's marker vocabulary (`deriveMarkers`/`buildAuditJson` from `lib/audit-doc.ts` and the five `marker_*` event kinds), so the save path and sync merge shipped in PR #848 work unchanged. The route branches on `useIsMobile` exactly like `BeepReviewRoute`; the desktop screen is untouched.

**Tech Stack:** React 18 + TypeScript, Tailwind (v4 tokens in `styles/index.css`), vitest + @testing-library/react, Web Audio API for grain scrub. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-12-mobile-audit-design.md` (read its two corrections). Handover: `docs/superpowers/handover-2026-08-12-shots-as-synced-entity.md`. Backend (spec steps 1-5) is fully shipped; this plan is UI-only - zero Python changes.

## Global Constraints

- New copy and comments use a single ASCII dash "-", never em dash, never "--".
- `src/splitsmith/ui_static` is pnpm-only; run `pnpm typecheck`, `pnpm test`, `pnpm exec eslint` from that directory. Never npm.
- No new dependencies without asking the user first.
- Touch targets are `min-h-11` (44 px), matching `MobileBeepReview`.
- Color is never the sole state carrier (WCAG 2.2 AA): the amber target marker is also named in the action-area readout; the dashed band is a shape, not just a colour. Respect `prefers-reduced-motion` via `motion-safe:` variants.
- Peaks are requested at 8192 bins (the endpoint cap), not `PEAK_BINS` (1500).
- Do not stack PRs. One branch (`feat/mobile-audit-ui`) off current `main`, one PR.
- Commit prefix `feat(ui):` / `fix(ui):` / `test(ui):` (bare `ui:` is dropped from the changelog).
- Every new test must fail against the pre-change code (for new files, the red step of TDD is that proof; for `App.tsx`, delete the branch and watch the routing test fail).

## Locked decisions (from the prototype; revisit only in the real-phone pass)

| decision | value |
|---|---|
| row count | 11 (`DEFAULT_ROWS`) |
| default zoom | 3x (chips 2x / 3x / 5x) |
| target band | +/- 120 ms, fixed in time (`TARGET_BAND_S = 0.12`) |
| grab threshold | 6 px pointer movement before a touch counts as a grab (`GRAB_PX = 6`) |
| loop | 1.4 s, centred on its anchor, anchored once when switched on (`LOOP_S = 1.4`) |
| nudge step | +/- 10 ms per tap |
| shot readout middle figure | split to the previous kept shot; time-after-beep for the first shot |

## Key existing interfaces (verified 2026-08-13; do not re-derive)

- `api.getStageAudit(slug, n): Promise<StageAudit | null>` - `null` means no audit doc yet (200 null, not an error).
- `api.saveStageAudit(slug, n, payload: StageAudit): Promise<StageAudit>` - PUT of the whole doc; no version field; server answers a race with 409 `{detail: {code: "version_conflict", ...}}` via `StateConflictError`.
- `api.getStagePeaks(slug, n, bins): Promise<PeaksResult>` - `{duration, sample_rate, bins, peaks: number[], beep_time: number | null, trimmed: boolean}`; `beep_time` is already in the served clip's local timeline.
- `api.stageAudioUrl(slug, n): string` - the audit WAV, served `audio/wav` directly (no 307).
- `api.videoStreamUrl(slug, videoPath, kind)` - `kind: "auto" | "trim" | "source" | "proxy"`.
- `deriveMarkers(audit: StageAudit | null): AuditMarker[]` and `buildAuditJson({base, stage, primaryBeepInClip, markers, appendEvents}): StageAudit` from `@/lib/audit-doc`.
- `AuditMarker` (from `@/components/MarkerLayer`): `{id, kind: "detected" | "rejected" | "manual", time, candidateNumber, confidence, peakAmplitude, note, shotId?}`.
- `snapToPeak(time, {peaks, duration}, toleranceS = 0.025): number | null` from `@/lib/peak-snap`.
- `capabilityDenied(ctx?.capabilities, "review")` gates the audit PUT (the server's `required_capability` maps `PUT .../audit` to `review`; desktop-origin mirrors HAVE `review`).
- Event vocabulary (payload keys exactly as the desktop emits): `marker_added_manual {id, time}`, `marker_kept {id, time, candidate_number}`, `marker_rejected {id, time, candidate_number}`, `marker_deleted {id, time, kind}` (manual only), `marker_time_changed {id, from_time, to_time}`; plus the synthetic `save {shots_count}` appended at save time (clears an open triage flag server-side).
- Deleting a *detected* marker means flipping it to `kind: "rejected"` + `marker_rejected` (it returns to the candidate pool); only *manual* markers are removed with `marker_deleted`.
- Manual ids are minted client-side: `` `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` `` with `shotId` set to the same id.
- `MatchShellOutletContext` (from `@/components/match/MatchShell`): `{project, origin, capabilities, refresh, ...}`.
- Mobile idioms: `useIsMobile()` (767 px matchMedia), `MobileConfirmSheet {open, title, body, confirmLabel, confirmDisabled?, onConfirm, onCancel}`, `Snackbar {snack: SnackState | null, onDismiss}`, `Portal`, z tokens `z-takeover < z-drawer < z-modal < z-toast`.
- Colour tokens: `--color-waveform-bar`, `--color-waveform-playhead`, `--color-waveform-loop`, `--color-marker-detected` (cyan), `--color-marker-rejected`, `--color-marker-manual` (violet), `--color-status-warning` (amber, the target). Fonts: `font-display` (Antonio) for headers, `font-mono` (JetBrains Mono) for numeric readouts.

## File structure

```
src/splitsmith/ui_static/src/
  lib/audit-target.ts                       (new)  pure target resolution
  lib/audit-target.test.ts                  (new)
  lib/scrub-audio.ts                        (new)  grain scrubber, degrades to null
  lib/scrub-audio.test.ts                   (new)
  lib/useAuditPlayback.ts                   (new)  audio element + playhead + loop + speed
  lib/useAuditPlayback.test.ts              (new)
  components/audit/mobile/WrappedWaveform.tsx      (new)  the row stack
  components/audit/mobile/WrappedWaveform.test.tsx (new)
  components/audit/mobile/ZoomLane.tsx             (new)  pinned playhead, band, jog
  components/audit/mobile/ZoomLane.test.tsx        (new)
  components/audit/mobile/AuditTransport.tsx       (new)  play, loop, speed
  components/audit/mobile/ActionArea.tsx           (new)  the three states
  components/audit/mobile/footer.test.tsx          (new)  transport + action area
  pages/MobileAudit.tsx                     (new)  the screen
  pages/MobileAudit.test.tsx                (new)
  App.tsx                                   (modify) AuditRoute branch, lines ~241-249
```

`MarkerLayer`, `Waveform`, `AuditControls` are desktop-shaped and deliberately not reused.

---

### Task 1: `lib/audit-target.ts` - pure target resolution

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/audit-target.ts`
- Test: `src/splitsmith/ui_static/src/lib/audit-target.test.ts`

**Interfaces:**
- Consumes: `AuditMarker` from `@/components/MarkerLayer`.
- Produces: `TARGET_BAND_S = 0.12`, `type AuditTarget`, `resolveTarget(markers, playhead, heldId?, bandS?)`. Tasks 6 and 7 import all three.

- [ ] **Step 1: Write the failing tests**

```ts
// src/splitsmith/ui_static/src/lib/audit-target.test.ts
import { describe, expect, it } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";
import { TARGET_BAND_S, resolveTarget } from "@/lib/audit-target";

function marker(over: Partial<AuditMarker>): AuditMarker {
  return {
    id: "cand-1",
    kind: "detected",
    time: 1.0,
    candidateNumber: 1,
    confidence: 0.9,
    peakAmplitude: null,
    note: "",
    ...over,
  };
}

describe("resolveTarget", () => {
  it("returns none when nothing is inside the band", () => {
    const t = resolveTarget([marker({ time: 5.0 })], 1.0);
    expect(t.kind).toBe("none");
  });

  it("a kept shot inside the band is the target", () => {
    const m = marker({ time: 1.05 });
    const t = resolveTarget([m], 1.0);
    expect(t).toEqual({ kind: "shot", marker: m });
  });

  it("a kept shot beats a nearer rejected candidate", () => {
    const kept = marker({ id: "cand-1", time: 1.1 });
    const rej = marker({ id: "cand-2", kind: "rejected", time: 1.01, candidateNumber: 2 });
    const t = resolveTarget([rej, kept], 1.0);
    expect(t).toEqual({ kind: "shot", marker: kept });
  });

  it("a rejected candidate beats nothing", () => {
    const rej = marker({ id: "cand-2", kind: "rejected", time: 1.05, candidateNumber: 2 });
    const t = resolveTarget([rej], 1.0);
    expect(t).toEqual({ kind: "candidate", marker: rej });
  });

  it("the nearest of two kept shots wins", () => {
    const near = marker({ id: "cand-1", time: 1.02 });
    const far = marker({ id: "cand-2", time: 1.09, candidateNumber: 2 });
    expect(resolveTarget([far, near], 1.0)).toEqual({ kind: "shot", marker: near });
  });

  it("manual markers count as kept shots", () => {
    const m = marker({ id: "manual-1", kind: "manual", candidateNumber: null });
    expect(resolveTarget([m], 1.0).kind).toBe("shot");
  });

  it("the band is fixed in time: exactly TARGET_BAND_S away is in, a hair past is out", () => {
    const edge = marker({ time: 1.0 + TARGET_BAND_S });
    expect(resolveTarget([edge], 1.0).kind).toBe("shot");
    const past = marker({ time: 1.0 + TARGET_BAND_S + 0.001 });
    expect(resolveTarget([past], 1.0).kind).toBe("none");
  });

  it("a held id stays the target even after the marker walks out of the band", () => {
    const nudged = marker({ id: "cand-1", time: 1.5 });
    const t = resolveTarget([nudged], 1.0, "cand-1");
    expect(t).toEqual({ kind: "shot", marker: nudged });
  });

  it("a held id that no longer exists falls back to the band rule", () => {
    const m = marker({ time: 1.05 });
    expect(resolveTarget([m], 1.0, "gone")).toEqual({ kind: "shot", marker: m });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/lib/audit-target.test.ts`
Expected: FAIL - cannot resolve `@/lib/audit-target`.

- [ ] **Step 3: Implement**

```ts
// src/splitsmith/ui_static/src/lib/audit-target.ts
/**
 * Target resolution for the mobile audit screen. There is no selection
 * state: whichever marker falls inside the +/- TARGET_BAND_S band around
 * the playhead is the target. The band is fixed in time, not pixels, so
 * zoom never changes which marker it selects. A held id (set while
 * nudging) overrides the band until the playhead next moves - the page
 * owns that lifecycle; this module only honours the override.
 */
import type { AuditMarker } from "@/components/MarkerLayer";

export const TARGET_BAND_S = 0.12;

export type AuditTarget =
  | { kind: "shot"; marker: AuditMarker }
  | { kind: "candidate"; marker: AuditMarker }
  | { kind: "none" };

const isKept = (m: AuditMarker) => m.kind === "detected" || m.kind === "manual";

export function resolveTarget(
  markers: AuditMarker[],
  playhead: number,
  heldId: string | null = null,
  bandS: number = TARGET_BAND_S,
): AuditTarget {
  if (heldId != null) {
    const held = markers.find((m) => m.id === heldId);
    if (held) return { kind: isKept(held) ? "shot" : "candidate", marker: held };
  }
  const inBand = markers.filter((m) => Math.abs(m.time - playhead) <= bandS);
  const nearest = (ms: AuditMarker[]) =>
    ms.reduce((a, b) => (Math.abs(b.time - playhead) < Math.abs(a.time - playhead) ? b : a));
  const kept = inBand.filter(isKept);
  if (kept.length > 0) return { kind: "shot", marker: nearest(kept) };
  const rejected = inBand.filter((m) => m.kind === "rejected");
  if (rejected.length > 0) return { kind: "candidate", marker: nearest(rejected) };
  return { kind: "none" };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/lib/audit-target.test.ts`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/audit-target.ts src/splitsmith/ui_static/src/lib/audit-target.test.ts
git commit -m "feat(ui): band-based target resolution for the mobile audit screen"
```

---

### Task 2: `lib/scrub-audio.ts` - the grain scrubber

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/scrub-audio.ts`
- Test: `src/splitsmith/ui_static/src/lib/scrub-audio.test.ts`

**Interfaces:**
- Produces: `interface Scrubber {grainAt(time: number): void; dispose(): void}`, `createScrubber(url, makeContext?): Promise<Scrubber | null>`. Task 7 calls `createScrubber(api.stageAudioUrl(slug, n))` once and treats `null` as "degrade to silent seeking".

- [ ] **Step 1: Write the failing tests**

```ts
// src/splitsmith/ui_static/src/lib/scrub-audio.test.ts
import { afterEach, describe, expect, it, vi } from "vitest";

import { GRAIN_S, createScrubber } from "@/lib/scrub-audio";

function fakeContext() {
  const gainNode = {
    gain: {
      setValueAtTime: vi.fn(),
      linearRampToValueAtTime: vi.fn(),
    },
    connect: vi.fn(() => ({ connect: vi.fn() })),
  };
  const source = {
    buffer: null as unknown,
    connect: vi.fn(() => gainNode),
    start: vi.fn(),
  };
  const ctx = {
    currentTime: 0,
    destination: {},
    createBufferSource: vi.fn(() => source),
    createGain: vi.fn(() => gainNode),
    decodeAudioData: vi.fn(async () => ({ duration: 10 })),
    close: vi.fn(async () => undefined),
  };
  return { ctx, source };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createScrubber", () => {
  it("returns null when the audio fetch fails (degrade to silent seeking)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404 })));
    const { ctx } = fakeContext();
    expect(await createScrubber("/audio", () => ctx as unknown as AudioContext)).toBeNull();
  });

  it("returns null when decode fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) })),
    );
    const { ctx } = fakeContext();
    ctx.decodeAudioData = vi.fn(async () => {
      throw new Error("bad data");
    });
    expect(await createScrubber("/audio", () => ctx as unknown as AudioContext)).toBeNull();
  });

  it("grainAt fires a windowed grain at the clamped offset", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) })),
    );
    const { ctx, source } = fakeContext();
    const scrubber = await createScrubber("/audio", () => ctx as unknown as AudioContext);
    expect(scrubber).not.toBeNull();
    scrubber?.grainAt(2.5);
    expect(source.start).toHaveBeenCalledWith(0, 2.5, GRAIN_S);
    scrubber?.grainAt(-1);
    // second call inside the throttle gap is dropped
    expect(source.start).toHaveBeenCalledTimes(1);
    ctx.currentTime = 1;
    scrubber?.grainAt(-1);
    expect(source.start).toHaveBeenLastCalledWith(1, 0, GRAIN_S);
  });

  it("dispose closes the context", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) })),
    );
    const { ctx } = fakeContext();
    const scrubber = await createScrubber("/audio", () => ctx as unknown as AudioContext);
    scrubber?.dispose();
    expect(ctx.close).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/lib/scrub-audio.test.ts`
Expected: FAIL - cannot resolve `@/lib/scrub-audio`.

- [ ] **Step 3: Implement**

```ts
// src/splitsmith/ui_static/src/lib/scrub-audio.ts
/**
 * Grain-based scrub audio: the clip is decoded once into an AudioBuffer
 * and dragging fires short windowed grains - an imitation of continuous
 * varispeed. Every failure path returns null and the caller degrades to
 * silent seeking; scrubbing must never block the audit pass.
 */
export const GRAIN_S = 0.06;
const GRAIN_GAP_S = 0.03;
const RAMP_S = 0.01;

export interface Scrubber {
  grainAt(time: number): void;
  dispose(): void;
}

export async function createScrubber(
  url: string,
  makeContext: () => AudioContext = () => new AudioContext(),
): Promise<Scrubber | null> {
  let ctx: AudioContext;
  let buffer: AudioBuffer;
  try {
    ctx = makeContext();
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`audio fetch ${resp.status}`);
    buffer = await ctx.decodeAudioData(await resp.arrayBuffer());
  } catch {
    return null;
  }
  let lastAt = -Infinity;
  return {
    grainAt(time: number) {
      const now = ctx.currentTime;
      if (now - lastAt < GRAIN_GAP_S) return;
      lastAt = now;
      const offset = Math.max(0, Math.min(time, buffer.duration - GRAIN_S));
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      const gain = ctx.createGain();
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(1, now + RAMP_S);
      gain.gain.setValueAtTime(1, now + GRAIN_S - RAMP_S);
      gain.gain.linearRampToValueAtTime(0, now + GRAIN_S);
      source.connect(gain).connect(ctx.destination);
      source.start(now, offset, GRAIN_S);
    },
    dispose() {
      void ctx.close();
    },
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/lib/scrub-audio.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/scrub-audio.ts src/splitsmith/ui_static/src/lib/scrub-audio.test.ts
git commit -m "feat(ui): grain scrub audio that degrades to silent seeking"
```

---

### Task 3: `lib/useAuditPlayback.ts` - playback engine

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/useAuditPlayback.ts`
- Test: `src/splitsmith/ui_static/src/lib/useAuditPlayback.test.ts`

**Interfaces:**
- Produces:

```ts
export const LOOP_S = 1.4;
export type PlaybackSpeed = 1 | 0.5 | 0.25;
export interface LoopRegion { start: number; end: number }
export interface AuditPlayback {
  playhead: number;
  playing: boolean;
  speed: PlaybackSpeed;
  loop: LoopRegion | null;
  playFrom(t: number): void;
  stop(): void;
  seek(t: number): void;      // silent - updates playhead + element time, no play
  setSpeed(s: PlaybackSpeed): void;
  toggleLoop(anchor: number): void;
}
export function useAuditPlayback(
  src: string | null,
  createAudio?: (src: string) => HTMLAudioElement,
): AuditPlayback
```

Task 7 wires this to every gesture. The `createAudio` parameter exists for tests only.

- [ ] **Step 1: Write the failing tests**

```ts
// src/splitsmith/ui_static/src/lib/useAuditPlayback.test.ts
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LOOP_S, useAuditPlayback } from "@/lib/useAuditPlayback";

class FakeAudio {
  currentTime = 0;
  playbackRate = 1;
  preservesPitch = false;
  paused = true;
  src: string;
  constructor(src: string) {
    this.src = src;
  }
  play = vi.fn(async () => {
    this.paused = false;
  });
  pause = vi.fn(() => {
    this.paused = true;
  });
  addEventListener = vi.fn();
  removeEventListener = vi.fn();
}

function setup() {
  const created: FakeAudio[] = [];
  const hook = renderHook(() =>
    useAuditPlayback("/audio.wav", (src) => {
      const a = new FakeAudio(src);
      created.push(a);
      return a as unknown as HTMLAudioElement;
    }),
  );
  return { hook, el: () => created[0] };
}

describe("useAuditPlayback", () => {
  it("creates the element with preservesPitch on", () => {
    const { el } = setup();
    expect(el().preservesPitch).toBe(true);
  });

  it("playFrom seeks then plays; stop pauses and stays put", () => {
    const { hook, el } = setup();
    act(() => hook.result.current.playFrom(3.2));
    expect(el().currentTime).toBeCloseTo(3.2);
    expect(el().play).toHaveBeenCalled();
    act(() => hook.result.current.stop());
    expect(el().pause).toHaveBeenCalled();
    expect(hook.result.current.playhead).toBeCloseTo(3.2);
  });

  it("seek moves the playhead without playing", () => {
    const { hook, el } = setup();
    act(() => hook.result.current.seek(7.5));
    expect(hook.result.current.playhead).toBeCloseTo(7.5);
    expect(el().play).not.toHaveBeenCalled();
  });

  it("setSpeed drives playbackRate", () => {
    const { hook, el } = setup();
    act(() => hook.result.current.setSpeed(0.25));
    expect(el().playbackRate).toBe(0.25);
  });

  it("toggleLoop anchors a centred LOOP_S region and clamps at zero", () => {
    const { hook } = setup();
    act(() => hook.result.current.toggleLoop(0.3));
    expect(hook.result.current.loop).toEqual({ start: 0, end: expect.closeTo(LOOP_S, 5) });
    act(() => hook.result.current.toggleLoop(0.3));
    expect(hook.result.current.loop).toBeNull();
  });

  it("the loop region does not move when the playhead is seeked inside it", () => {
    const { hook } = setup();
    act(() => hook.result.current.toggleLoop(5.0));
    const before = hook.result.current.loop;
    act(() => hook.result.current.seek(5.3));
    expect(hook.result.current.loop).toEqual(before);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/lib/useAuditPlayback.test.ts`
Expected: FAIL - cannot resolve `@/lib/useAuditPlayback`.

- [ ] **Step 3: Implement**

```ts
// src/splitsmith/ui_static/src/lib/useAuditPlayback.ts
/**
 * Playback engine for the mobile audit screen: one hidden audio element
 * over the stage audit WAV, a rAF playhead, an anchored loop region and
 * time-stretched slow playback (preservesPitch stays on). The loop is
 * anchored once when switched on and held there, so jogging inside it
 * does not drag the region along.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export const LOOP_S = 1.4;
export type PlaybackSpeed = 1 | 0.5 | 0.25;
export interface LoopRegion {
  start: number;
  end: number;
}

export interface AuditPlayback {
  playhead: number;
  playing: boolean;
  speed: PlaybackSpeed;
  loop: LoopRegion | null;
  playFrom(t: number): void;
  stop(): void;
  seek(t: number): void;
  setSpeed(s: PlaybackSpeed): void;
  toggleLoop(anchor: number): void;
}

export function useAuditPlayback(
  src: string | null,
  createAudio: (src: string) => HTMLAudioElement = (s) => new Audio(s),
): AuditPlayback {
  const elRef = useRef<HTMLAudioElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const loopRef = useRef<LoopRegion | null>(null);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeedState] = useState<PlaybackSpeed>(1);
  const [loop, setLoop] = useState<LoopRegion | null>(null);
  loopRef.current = loop;

  useEffect(() => {
    if (src == null) return undefined;
    const el = createAudio(src);
    // Safari still needs the prefixed property; the standard one is a
    // no-op there rather than an error.
    el.preservesPitch = true;
    (el as unknown as { webkitPreservesPitch?: boolean }).webkitPreservesPitch = true;
    elRef.current = el;
    return () => {
      el.pause();
      elRef.current = null;
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
    // createAudio is a test seam; recreating on its identity would tear
    // down playback on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src]);

  const tick = useCallback(() => {
    const el = elRef.current;
    if (el == null) return;
    const region = loopRef.current;
    if (region != null && el.currentTime >= region.end) {
      el.currentTime = region.start;
    }
    setPlayhead(el.currentTime);
    if (!el.paused) rafRef.current = requestAnimationFrame(tick);
  }, []);

  const playFrom = useCallback(
    (t: number) => {
      const el = elRef.current;
      if (el == null) return;
      el.currentTime = Math.max(0, t);
      setPlayhead(el.currentTime);
      void el.play();
      setPlaying(true);
      rafRef.current = requestAnimationFrame(tick);
    },
    [tick],
  );

  const stop = useCallback(() => {
    const el = elRef.current;
    if (el == null) return;
    el.pause();
    setPlaying(false);
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    setPlayhead(el.currentTime);
  }, []);

  const seek = useCallback((t: number) => {
    const el = elRef.current;
    if (el == null) return;
    el.currentTime = Math.max(0, t);
    setPlayhead(el.currentTime);
  }, []);

  const setSpeed = useCallback((s: PlaybackSpeed) => {
    setSpeedState(s);
    const el = elRef.current;
    if (el != null) el.playbackRate = s;
  }, []);

  const toggleLoop = useCallback((anchor: number) => {
    setLoop((cur) => {
      if (cur != null) return null;
      const start = Math.max(0, anchor - LOOP_S / 2);
      return { start, end: start + LOOP_S };
    });
  }, []);

  return useMemo(
    () => ({ playhead, playing, speed, loop, playFrom, stop, seek, setSpeed, toggleLoop }),
    [playhead, playing, speed, loop, playFrom, stop, seek, setSpeed, toggleLoop],
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/lib/useAuditPlayback.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/useAuditPlayback.ts src/splitsmith/ui_static/src/lib/useAuditPlayback.test.ts
git commit -m "feat(ui): audit playback hook with anchored loop and pitch-preserving speed"
```

---

### Task 4: `WrappedWaveform.tsx` - the row stack

**Files:**
- Create: `src/splitsmith/ui_static/src/components/audit/mobile/WrappedWaveform.tsx`
- Test: `src/splitsmith/ui_static/src/components/audit/mobile/WrappedWaveform.test.tsx`

**Interfaces:**
- Consumes: `AuditMarker` from `@/components/MarkerLayer`, `LoopRegion` from `@/lib/useAuditPlayback`.
- Produces:

```ts
export const DEFAULT_ROWS = 11;
export const GRAB_PX = 6;
export interface WrappedWaveformProps {
  peaks: number[];
  duration: number;
  rows?: number;                 // default DEFAULT_ROWS
  playhead: number;
  markers: AuditMarker[];        // kept only; the page filters out rejected
  targetId: string | null;
  loop: LoopRegion | null;
  onTap(time: number): void;
  onGrabStart(): void;           // fired once, after GRAB_PX of movement
  onScrub(time: number): void;
  onGrabEnd(): void;
}
export function WrappedWaveform(props: WrappedWaveformProps): JSX.Element
```

The two-verb gesture contract (shared with Task 5): a pointerdown that moves less than `GRAB_PX` before pointerup is a tap (`onTap` at the mapped time); crossing `GRAB_PX` fires `onGrabStart` once, then `onScrub` per move, then `onGrabEnd` on release. The stop-on-movement guard is the spec's answer to a stray graze halting playback.

- [ ] **Step 1: Write the failing tests**

```tsx
// src/splitsmith/ui_static/src/components/audit/mobile/WrappedWaveform.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";
import { DEFAULT_ROWS, WrappedWaveform } from "@/components/audit/mobile/WrappedWaveform";

const marker = (over: Partial<AuditMarker>): AuditMarker => ({
  id: "cand-1",
  kind: "detected",
  time: 1.0,
  candidateNumber: 1,
  confidence: 0.9,
  peakAmplitude: null,
  note: "",
  ...over,
});

const peaks = Array.from({ length: 880 }, (_, i) => (i % 10) / 10);

function renderRows(over: Partial<Parameters<typeof WrappedWaveform>[0]> = {}) {
  const props = {
    peaks,
    duration: 44,
    playhead: 0,
    markers: [] as AuditMarker[],
    targetId: null,
    loop: null,
    onTap: vi.fn(),
    onGrabStart: vi.fn(),
    onScrub: vi.fn(),
    onGrabEnd: vi.fn(),
    ...over,
  };
  render(<WrappedWaveform {...props} />);
  return props;
}

describe("WrappedWaveform", () => {
  it("renders DEFAULT_ROWS rows, each with its start-time gutter", () => {
    renderRows();
    const rows = screen.getAllByTestId("wave-row");
    expect(rows).toHaveLength(DEFAULT_ROWS);
    // 44 s / 11 rows = 4 s per row; second row starts at 4 s
    expect(screen.getByText("0:04")).toBeInTheDocument();
  });

  it("places a marker in the row containing its time", () => {
    renderRows({ markers: [marker({ time: 6.0 })] });
    const rows = screen.getAllByTestId("wave-row");
    // 6 s with 4 s rows -> row index 1
    expect(rows[1].querySelector('[data-marker-id="cand-1"]')).not.toBeNull();
    expect(rows[0].querySelector('[data-marker-id="cand-1"]')).toBeNull();
  });

  it("marks the target marker distinctly", () => {
    renderRows({ markers: [marker({ time: 6.0 })], targetId: "cand-1" });
    const el = document.querySelector('[data-marker-id="cand-1"]');
    expect(el).toHaveAttribute("data-target", "true");
  });

  it("a short press is a tap at the mapped time", () => {
    const props = renderRows();
    const row = screen.getAllByTestId("wave-row")[2];
    row.getBoundingClientRect = () =>
      ({ left: 0, width: 100, top: 0, height: 40 }) as DOMRect;
    fireEvent.pointerDown(row, { clientX: 50, clientY: 10, pointerId: 1 });
    fireEvent.pointerUp(row, { clientX: 51, clientY: 10, pointerId: 1 });
    // row 2 covers 8-12 s; halfway across is 10 s
    expect(props.onTap).toHaveBeenCalledWith(expect.closeTo(10, 1));
    expect(props.onGrabStart).not.toHaveBeenCalled();
  });

  it("movement past the threshold is a grab: stop, scrub, end", () => {
    const props = renderRows();
    const row = screen.getAllByTestId("wave-row")[0];
    row.getBoundingClientRect = () =>
      ({ left: 0, width: 100, top: 0, height: 40 }) as DOMRect;
    fireEvent.pointerDown(row, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(row, { clientX: 30, clientY: 10, pointerId: 1 });
    fireEvent.pointerUp(row, { clientX: 30, clientY: 10, pointerId: 1 });
    expect(props.onGrabStart).toHaveBeenCalledTimes(1);
    expect(props.onScrub).toHaveBeenCalledWith(expect.closeTo(1.2, 1));
    expect(props.onGrabEnd).toHaveBeenCalledTimes(1);
    expect(props.onTap).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/components/audit/mobile/WrappedWaveform.test.tsx`
Expected: FAIL - cannot resolve the component.

- [ ] **Step 3: Implement**

```tsx
// src/splitsmith/ui_static/src/components/audit/mobile/WrappedWaveform.tsx
/**
 * The stage's waveform wrapped into stacked rows like a text editor
 * wraps a long line: whole stage on one screen, playhead sweeping row
 * to row, nothing scrolls. Rejected candidates are deliberately absent
 * here - they surface only inside the zoom lane's target band.
 */
import { useRef } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import type { AuditMarker } from "@/components/MarkerLayer";
import type { LoopRegion } from "@/lib/useAuditPlayback";

export const DEFAULT_ROWS = 11;
export const GRAB_PX = 6;

export interface WrappedWaveformProps {
  peaks: number[];
  duration: number;
  rows?: number;
  playhead: number;
  markers: AuditMarker[];
  targetId: string | null;
  loop: LoopRegion | null;
  onTap(time: number): void;
  onGrabStart(): void;
  onScrub(time: number): void;
  onGrabEnd(): void;
}

function formatRowStart(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

const markerColor = (m: AuditMarker, isTarget: boolean): string => {
  if (isTarget) return "var(--color-status-warning)";
  return m.kind === "manual" ? "var(--color-marker-manual)" : "var(--color-marker-detected)";
};

export function WrappedWaveform({
  peaks,
  duration,
  rows = DEFAULT_ROWS,
  playhead,
  markers,
  targetId,
  loop,
  onTap,
  onGrabStart,
  onScrub,
  onGrabEnd,
}: WrappedWaveformProps) {
  const gesture = useRef<{ pointerId: number; startX: number; row: number; grabbed: boolean } | null>(null);
  const rowDur = duration > 0 ? duration / rows : 0;
  const binsPerRow = Math.ceil(peaks.length / rows);

  const timeAt = (row: number, el: Element, clientX: number): number => {
    const rect = el.getBoundingClientRect();
    const fx = rect.width > 0 ? Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)) : 0;
    return (row + fx) * rowDur;
  };

  const down = (row: number) => (e: ReactPointerEvent<HTMLDivElement>) => {
    gesture.current = { pointerId: e.pointerId, startX: e.clientX, row, grabbed: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (row: number) => (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (g == null || g.pointerId !== e.pointerId) return;
    if (!g.grabbed && Math.abs(e.clientX - g.startX) < GRAB_PX) return;
    if (!g.grabbed) {
      g.grabbed = true;
      onGrabStart();
    }
    onScrub(timeAt(row, e.currentTarget, e.clientX));
  };
  const up = (row: number) => (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (g == null || g.pointerId !== e.pointerId) return;
    gesture.current = null;
    if (g.grabbed) onGrabEnd();
    else onTap(timeAt(row, e.currentTarget, e.clientX));
  };

  if (duration <= 0 || peaks.length === 0) return <div className="flex-1" />;
  const playRow = Math.min(rows - 1, Math.floor(playhead / rowDur));

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-px" data-testid="wrapped-waveform">
      {Array.from({ length: rows }, (_, r) => {
        const rowStart = r * rowDur;
        const rowPeaks = peaks.slice(r * binsPerRow, (r + 1) * binsPerRow);
        const rowMarkers = markers.filter((m) => m.time >= rowStart && m.time < rowStart + rowDur);
        const loopIn =
          loop != null && loop.start < rowStart + rowDur && loop.end > rowStart ? loop : null;
        const toX = (t: number) => ((t - rowStart) / rowDur) * 1000;
        return (
          <div key={r} data-testid="wave-row" className="flex min-h-0 flex-1 items-stretch gap-1">
            <span className="w-8 shrink-0 self-center text-right font-mono text-[10px] text-[var(--color-text-dim,inherit)] opacity-60">
              {formatRowStart(rowStart)}
            </span>
            <div
              className="relative min-w-0 flex-1 touch-none"
              onPointerDown={down(r)}
              onPointerMove={move(r)}
              onPointerUp={up(r)}
              onPointerCancel={up(r)}
            >
              <svg viewBox="0 0 1000 100" preserveAspectRatio="none" className="h-full w-full" aria-hidden>
                {loopIn != null && (
                  <rect
                    x={toX(Math.max(loopIn.start, rowStart))}
                    width={
                      toX(Math.min(loopIn.end, rowStart + rowDur)) -
                      toX(Math.max(loopIn.start, rowStart))
                    }
                    y={0}
                    height={100}
                    fill="var(--color-waveform-loop)"
                  />
                )}
                {rowPeaks.map((p, i) => {
                  const h = Math.max(2, p * 96);
                  return (
                    <rect
                      key={i}
                      x={(i / rowPeaks.length) * 1000}
                      width={Math.max(1, 1000 / rowPeaks.length - 0.4)}
                      y={50 - h / 2}
                      height={h}
                      fill="var(--color-waveform-bar)"
                    />
                  );
                })}
                {rowMarkers.map((m) => (
                  <line
                    key={m.id}
                    data-marker-id={m.id}
                    data-target={m.id === targetId ? "true" : undefined}
                    x1={toX(m.time)}
                    x2={toX(m.time)}
                    y1={4}
                    y2={96}
                    stroke={markerColor(m, m.id === targetId)}
                    strokeWidth={m.id === targetId ? 5 : 3}
                  />
                ))}
                {r === playRow && (
                  <line
                    x1={toX(playhead)}
                    x2={toX(playhead)}
                    y1={0}
                    y2={100}
                    stroke="var(--color-waveform-playhead)"
                    strokeWidth={3}
                  />
                )}
              </svg>
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/components/audit/mobile/WrappedWaveform.test.tsx`
Expected: PASS (5 tests). If the pointer-capture call throws under jsdom, guard it: `if (e.currentTarget.setPointerCapture) e.currentTarget.setPointerCapture(e.pointerId);` - `MobileBeepReview`'s waveform tests are the precedent for jsdom pointer quirks.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/audit/mobile/WrappedWaveform.tsx src/splitsmith/ui_static/src/components/audit/mobile/WrappedWaveform.test.tsx
git commit -m "feat(ui): wrapped waveform row stack for the mobile audit pass"
```

---

### Task 5: `ZoomLane.tsx` - pinned playhead, band, jog

**Files:**
- Create: `src/splitsmith/ui_static/src/components/audit/mobile/ZoomLane.tsx`
- Test: `src/splitsmith/ui_static/src/components/audit/mobile/ZoomLane.test.tsx`

**Interfaces:**
- Consumes: `AuditMarker`, `TARGET_BAND_S` from `@/lib/audit-target`, `GRAB_PX` from `WrappedWaveform`.
- Produces:

```ts
export type ZoomFactor = 2 | 3 | 5;
export interface ZoomLaneProps {
  peaks: number[];
  duration: number;
  rows: number;                  // the row count, so the lane derives its scale
  playhead: number;
  zoom: ZoomFactor;
  onZoomChange(z: ZoomFactor): void;
  markers: AuditMarker[];        // ALL markers, kept and rejected
  targetId: string | null;
  onTap(time: number): void;
  onGrabStart(): void;
  onJog(time: number): void;     // absolute time the playhead should move to
  onGrabEnd(): void;
}
export function ZoomLane(props: ZoomLaneProps): JSX.Element
```

The lane's visible window is `duration / rows / zoom` seconds (zoom is a multiple of the row scale). The window is centred on the playhead. Jogging by `dx` pixels moves the playhead by `-dx * windowS / laneWidth` seconds from the grab-start playhead. Rejected candidates render only when `|t - playhead| <= TARGET_BAND_S`, as dim lollipops with opacity `0.25 + 0.6 * (confidence ?? 0.2)`. The zoom chips live inside the lane (top-right) - the transport row overflows a 393 px screen otherwise.

- [ ] **Step 1: Write the failing tests**

```tsx
// src/splitsmith/ui_static/src/components/audit/mobile/ZoomLane.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";
import { ZoomLane } from "@/components/audit/mobile/ZoomLane";

const marker = (over: Partial<AuditMarker>): AuditMarker => ({
  id: "cand-1",
  kind: "detected",
  time: 5.0,
  candidateNumber: 1,
  confidence: 0.9,
  peakAmplitude: null,
  note: "",
  ...over,
});

function renderLane(over: Partial<Parameters<typeof ZoomLane>[0]> = {}) {
  const props = {
    peaks: Array.from({ length: 880 }, () => 0.5),
    duration: 44,
    rows: 11,
    playhead: 5.0,
    zoom: 3 as const,
    onZoomChange: vi.fn(),
    markers: [] as AuditMarker[],
    targetId: null,
    onTap: vi.fn(),
    onGrabStart: vi.fn(),
    onJog: vi.fn(),
    onGrabEnd: vi.fn(),
    ...over,
  };
  render(<ZoomLane {...props} />);
  return props;
}

describe("ZoomLane", () => {
  it("renders the dashed target band and the pinned playhead", () => {
    renderLane();
    expect(screen.getByTestId("target-band")).toBeInTheDocument();
    expect(screen.getByTestId("lane-playhead")).toBeInTheDocument();
  });

  it("shows a rejected candidate only inside the band", () => {
    renderLane({
      markers: [
        marker({ id: "cand-7", kind: "rejected", time: 5.05, candidateNumber: 7, confidence: 0.1 }),
        marker({ id: "cand-8", kind: "rejected", time: 5.5, candidateNumber: 8, confidence: 0.1 }),
      ],
    });
    expect(document.querySelector('[data-marker-id="cand-7"]')).not.toBeNull();
    expect(document.querySelector('[data-marker-id="cand-8"]')).toBeNull();
  });

  it("kept markers render across the whole window", () => {
    renderLane({ markers: [marker({ time: 5.5 })] });
    expect(document.querySelector('[data-marker-id="cand-1"]')).not.toBeNull();
  });

  it("zoom chips call onZoomChange and mark the active factor", () => {
    const props = renderLane();
    fireEvent.click(screen.getByRole("button", { name: "5x" }));
    expect(props.onZoomChange).toHaveBeenCalledWith(5);
    expect(screen.getByRole("button", { name: "3x" })).toHaveAttribute("aria-pressed", "true");
  });

  it("dragging jogs the playhead against the drag direction", () => {
    const props = renderLane();
    const lane = screen.getByTestId("zoom-lane");
    lane.getBoundingClientRect = () =>
      ({ left: 0, width: 400, top: 0, height: 80 }) as DOMRect;
    fireEvent.pointerDown(lane, { clientX: 200, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(lane, { clientX: 300, clientY: 10, pointerId: 1 });
    expect(props.onGrabStart).toHaveBeenCalledTimes(1);
    // window = 44/11/3 = 1.333 s over 400 px; +100 px drag moves time back 0.333 s
    expect(props.onJog).toHaveBeenCalledWith(expect.closeTo(5.0 - 0.333, 2));
    fireEvent.pointerUp(lane, { clientX: 300, clientY: 10, pointerId: 1 });
    expect(props.onGrabEnd).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/components/audit/mobile/ZoomLane.test.tsx`
Expected: FAIL - cannot resolve the component.

- [ ] **Step 3: Implement**

```tsx
// src/splitsmith/ui_static/src/components/audit/mobile/ZoomLane.tsx
/**
 * The placement surface: a fixed lane with the playhead pinned dead
 * centre and a dashed +/- TARGET_BAND_S band around it. The band is
 * fixed in time, so zoom changes how wide it looks but never which
 * marker it selects. Rejected candidates exist only in here, and only
 * inside the band - the band is the scoping that replaces an opt-in
 * candidates mode.
 */
import { useRef } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import type { AuditMarker } from "@/components/MarkerLayer";
import { TARGET_BAND_S } from "@/lib/audit-target";
import { GRAB_PX } from "@/components/audit/mobile/WrappedWaveform";

export type ZoomFactor = 2 | 3 | 5;
const ZOOMS: ZoomFactor[] = [2, 3, 5];

export interface ZoomLaneProps {
  peaks: number[];
  duration: number;
  rows: number;
  playhead: number;
  zoom: ZoomFactor;
  onZoomChange(z: ZoomFactor): void;
  markers: AuditMarker[];
  targetId: string | null;
  onTap(time: number): void;
  onGrabStart(): void;
  onJog(time: number): void;
  onGrabEnd(): void;
}

const keptColor = (m: AuditMarker, isTarget: boolean): string => {
  if (isTarget) return "var(--color-status-warning)";
  return m.kind === "manual" ? "var(--color-marker-manual)" : "var(--color-marker-detected)";
};

export function ZoomLane({
  peaks,
  duration,
  rows,
  playhead,
  zoom,
  onZoomChange,
  markers,
  targetId,
  onTap,
  onGrabStart,
  onJog,
  onGrabEnd,
}: ZoomLaneProps) {
  const gesture = useRef<{ pointerId: number; startX: number; startPlayhead: number; grabbed: boolean } | null>(null);
  const windowS = duration > 0 ? duration / rows / zoom : 0;
  const winStart = playhead - windowS / 2;

  const toX = (t: number) => ((t - winStart) / windowS) * 1000;
  const timeAt = (el: Element, clientX: number): number => {
    const rect = el.getBoundingClientRect();
    const fx = rect.width > 0 ? (clientX - rect.left) / rect.width : 0.5;
    return winStart + fx * windowS;
  };

  const down = (e: ReactPointerEvent<HTMLDivElement>) => {
    gesture.current = { pointerId: e.pointerId, startX: e.clientX, startPlayhead: playhead, grabbed: false };
    if (e.currentTarget.setPointerCapture) e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (g == null || g.pointerId !== e.pointerId) return;
    const dx = e.clientX - g.startX;
    if (!g.grabbed && Math.abs(dx) < GRAB_PX) return;
    if (!g.grabbed) {
      g.grabbed = true;
      onGrabStart();
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const pxPerS = rect.width > 0 ? rect.width / windowS : 1;
    onJog(g.startPlayhead - dx / pxPerS);
  };
  const up = (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (g == null || g.pointerId !== e.pointerId) return;
    gesture.current = null;
    if (g.grabbed) onGrabEnd();
    else onTap(timeAt(e.currentTarget, e.clientX));
  };

  if (duration <= 0 || peaks.length === 0) return <div className="h-20" />;

  const binDur = duration / peaks.length;
  const firstBin = Math.max(0, Math.floor(winStart / binDur));
  const lastBin = Math.min(peaks.length, Math.ceil((winStart + windowS) / binDur));
  const inWindow = (t: number) => t >= winStart && t <= winStart + windowS;

  return (
    <div
      data-testid="zoom-lane"
      className="relative h-20 touch-none border-y border-rule"
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
    >
      <svg viewBox="0 0 1000 100" preserveAspectRatio="none" className="h-full w-full" aria-hidden>
        <rect
          data-testid="target-band"
          x={toX(playhead - TARGET_BAND_S)}
          width={toX(playhead + TARGET_BAND_S) - toX(playhead - TARGET_BAND_S)}
          y={2}
          height={96}
          fill="none"
          stroke="var(--color-status-warning)"
          strokeOpacity={0.55}
          strokeDasharray="6 4"
        />
        {Array.from({ length: lastBin - firstBin }, (_, i) => {
          const bin = firstBin + i;
          const h = Math.max(2, peaks[bin] * 92);
          return (
            <rect
              key={bin}
              x={toX(bin * binDur)}
              width={Math.max(1.2, 1000 / ((lastBin - firstBin) || 1) - 0.4)}
              y={50 - h / 2}
              height={h}
              fill="var(--color-waveform-bar)"
            />
          );
        })}
        {markers
          .filter((m) => m.kind !== "rejected" && inWindow(m.time))
          .map((m) => (
            <line
              key={m.id}
              data-marker-id={m.id}
              data-target={m.id === targetId ? "true" : undefined}
              x1={toX(m.time)}
              x2={toX(m.time)}
              y1={6}
              y2={94}
              stroke={keptColor(m, m.id === targetId)}
              strokeWidth={m.id === targetId ? 6 : 3.5}
            />
          ))}
        {markers
          .filter((m) => m.kind === "rejected" && Math.abs(m.time - playhead) <= TARGET_BAND_S)
          .map((m) => (
            <g
              key={m.id}
              data-marker-id={m.id}
              data-target={m.id === targetId ? "true" : undefined}
              opacity={0.25 + 0.6 * (m.confidence ?? 0.2)}
            >
              <line
                x1={toX(m.time)}
                x2={toX(m.time)}
                y1={40}
                y2={94}
                stroke="var(--color-marker-rejected)"
                strokeWidth={3}
              />
              <circle cx={toX(m.time)} cy={34} r={7} fill="var(--color-marker-rejected)" />
            </g>
          ))}
        <line
          data-testid="lane-playhead"
          x1={500}
          x2={500}
          y1={0}
          y2={100}
          stroke="var(--color-waveform-playhead)"
          strokeWidth={3}
        />
      </svg>
      <div className="absolute right-1 top-1 flex gap-1">
        {ZOOMS.map((z) => (
          <button
            key={z}
            type="button"
            aria-pressed={z === zoom}
            onClick={() => onZoomChange(z)}
            onPointerDown={(e) => e.stopPropagation()}
            className={`min-h-8 rounded px-2 font-mono text-xs ${
              z === zoom ? "btn-led-fill" : "border border-rule opacity-70"
            }`}
          >
            {z}x
          </button>
        ))}
      </div>
    </div>
  );
}
```

Note the `onPointerDown={(e) => e.stopPropagation()}` on the zoom chips - without it a chip tap also registers as a lane tap and starts playback.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/components/audit/mobile/ZoomLane.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/audit/mobile/ZoomLane.tsx src/splitsmith/ui_static/src/components/audit/mobile/ZoomLane.test.tsx
git commit -m "feat(ui): zoom lane with pinned playhead, target band and band-scoped candidates"
```

---

### Task 6: `AuditTransport.tsx` + `ActionArea.tsx` - the footer controls

**Files:**
- Create: `src/splitsmith/ui_static/src/components/audit/mobile/AuditTransport.tsx`
- Create: `src/splitsmith/ui_static/src/components/audit/mobile/ActionArea.tsx`
- Test: `src/splitsmith/ui_static/src/components/audit/mobile/footer.test.tsx`

**Interfaces:**
- Consumes: `AuditTarget` from `@/lib/audit-target`, `PlaybackSpeed` from `@/lib/useAuditPlayback`.
- Produces:

```ts
export interface AuditTransportProps {
  playing: boolean;
  onPlayPause(): void;
  loopActive: boolean;
  onLoopToggle(): void;
  speed: PlaybackSpeed;
  onSpeedChange(s: PlaybackSpeed): void;
}
export function AuditTransport(props: AuditTransportProps): JSX.Element

export interface ActionAreaProps {
  target: AuditTarget;
  shotOrdinal: { index: number; total: number } | null; // 1-based, kind === "shot" only
  splitS: number | null;      // split to previous kept shot; time-after-beep for shot 1
  nudgeMs: number;            // accumulated offset being dialled in; 0 when no hold
  readOnly: boolean;
  onNudge(deltaMs: -10 | 10): void;
  onDeleteShot(): void;
  onShowVideo(): void;
  onPromote(): void;
  onAddShot(): void;
}
export function ActionArea(props: ActionAreaProps): JSX.Element
```

Readout copy, exactly (`.` separators with spaces, `font-mono`):
- shot: `shot 17/37 . 0.447 s . +20 ms` (the `+20 ms` part appears only while `nudgeMs !== 0`)
- candidate: `rejected candidate . conf 0.10` (conf omitted when `confidence == null`)
- none: `no shot at playhead`

When `readOnly` is true every mutating button is disabled (not hidden) and the readout row gains the suffix ` . read-only`.

- [ ] **Step 1: Write the failing tests**

```tsx
// src/splitsmith/ui_static/src/components/audit/mobile/footer.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";
import { ActionArea } from "@/components/audit/mobile/ActionArea";
import { AuditTransport } from "@/components/audit/mobile/AuditTransport";

const marker = (over: Partial<AuditMarker>): AuditMarker => ({
  id: "cand-17",
  kind: "detected",
  time: 12.5,
  candidateNumber: 17,
  confidence: 0.8,
  peakAmplitude: null,
  note: "",
  ...over,
});

function renderArea(over: Partial<Parameters<typeof ActionArea>[0]> = {}) {
  const props = {
    target: { kind: "none" } as const,
    shotOrdinal: null,
    splitS: null,
    nudgeMs: 0,
    readOnly: false,
    onNudge: vi.fn(),
    onDeleteShot: vi.fn(),
    onShowVideo: vi.fn(),
    onPromote: vi.fn(),
    onAddShot: vi.fn(),
    ...over,
  };
  render(<ActionArea {...props} />);
  return props;
}

describe("ActionArea", () => {
  it("shot state names the shot and offers nudge, delete, video", () => {
    const props = renderArea({
      target: { kind: "shot", marker: marker({}) },
      shotOrdinal: { index: 17, total: 37 },
      splitS: 0.447,
      nudgeMs: 20,
    });
    expect(screen.getByText("shot 17/37 . 0.447 s . +20 ms")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "-10 ms" }));
    expect(props.onNudge).toHaveBeenCalledWith(-10);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(props.onDeleteShot).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Video" }));
    expect(props.onShowVideo).toHaveBeenCalled();
  });

  it("candidate state offers promote and shows confidence", () => {
    const props = renderArea({
      target: { kind: "candidate", marker: marker({ kind: "rejected", confidence: 0.1 }) },
    });
    expect(screen.getByText("rejected candidate . conf 0.10")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Promote candidate" }));
    expect(props.onPromote).toHaveBeenCalled();
  });

  it("empty state offers add at playhead", () => {
    const props = renderArea();
    expect(screen.getByText("no shot at playhead")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add shot at playhead" }));
    expect(props.onAddShot).toHaveBeenCalled();
  });

  it("read-only disables the mutating buttons and says so", () => {
    renderArea({ readOnly: true });
    expect(screen.getByText(/read-only/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add shot at playhead" })).toBeDisabled();
  });
});

describe("AuditTransport", () => {
  it("wires play, loop and speed", () => {
    const props = {
      playing: false,
      onPlayPause: vi.fn(),
      loopActive: false,
      onLoopToggle: vi.fn(),
      speed: 1 as const,
      onSpeedChange: vi.fn(),
    };
    render(<AuditTransport {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(props.onPlayPause).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Loop" }));
    expect(props.onLoopToggle).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "0.5x" }));
    expect(props.onSpeedChange).toHaveBeenCalledWith(0.5);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/components/audit/mobile/footer.test.tsx`
Expected: FAIL - cannot resolve the components.

- [ ] **Step 3: Implement both components**

```tsx
// src/splitsmith/ui_static/src/components/audit/mobile/AuditTransport.tsx
import { Pause, Play, Repeat } from "lucide-react";

import type { PlaybackSpeed } from "@/lib/useAuditPlayback";

const SPEEDS: PlaybackSpeed[] = [1, 0.5, 0.25];

export interface AuditTransportProps {
  playing: boolean;
  onPlayPause(): void;
  loopActive: boolean;
  onLoopToggle(): void;
  speed: PlaybackSpeed;
  onSpeedChange(s: PlaybackSpeed): void;
}

export function AuditTransport({
  playing,
  onPlayPause,
  loopActive,
  onLoopToggle,
  speed,
  onSpeedChange,
}: AuditTransportProps) {
  return (
    <div className="flex items-center gap-2 px-2 py-1">
      <button
        type="button"
        aria-label={playing ? "Pause" : "Play"}
        onClick={onPlayPause}
        className="btn-led-fill flex min-h-11 min-w-11 items-center justify-center rounded-md"
      >
        {playing ? <Pause className="size-5" aria-hidden /> : <Play className="size-5" aria-hidden />}
      </button>
      <button
        type="button"
        aria-label="Loop"
        aria-pressed={loopActive}
        onClick={onLoopToggle}
        className={`flex min-h-11 min-w-11 items-center justify-center rounded-md border border-rule ${
          loopActive ? "text-[var(--color-waveform-beep)]" : "opacity-70"
        }`}
      >
        <Repeat className="size-5" aria-hidden />
      </button>
      <div className="ml-auto flex gap-1">
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={s === speed}
            onClick={() => onSpeedChange(s)}
            className={`min-h-11 rounded px-2 font-mono text-xs ${
              s === speed ? "btn-led-fill" : "border border-rule opacity-70"
            }`}
          >
            {s}x
          </button>
        ))}
      </div>
    </div>
  );
}
```

```tsx
// src/splitsmith/ui_static/src/components/audit/mobile/ActionArea.tsx
/**
 * One slot that always names what it will act on: a kept shot, a
 * rejected candidate (promote preserves the detector's provenance) or
 * nothing (add). Read-only disables rather than hides, so the operator
 * on a share-less mirror still sees what the surface can do.
 */
import type { AuditTarget } from "@/lib/audit-target";

export interface ActionAreaProps {
  target: AuditTarget;
  shotOrdinal: { index: number; total: number } | null;
  splitS: number | null;
  nudgeMs: number;
  readOnly: boolean;
  onNudge(deltaMs: -10 | 10): void;
  onDeleteShot(): void;
  onShowVideo(): void;
  onPromote(): void;
  onAddShot(): void;
}

function readout(props: ActionAreaProps): string {
  const { target, shotOrdinal, splitS, nudgeMs } = props;
  let text: string;
  if (target.kind === "shot") {
    const ord = shotOrdinal != null ? `shot ${shotOrdinal.index}/${shotOrdinal.total}` : "shot";
    const split = splitS != null ? ` . ${splitS.toFixed(3)} s` : "";
    const nudge = nudgeMs !== 0 ? ` . ${nudgeMs > 0 ? "+" : ""}${nudgeMs} ms` : "";
    text = `${ord}${split}${nudge}`;
  } else if (target.kind === "candidate") {
    const conf = target.marker.confidence;
    text = conf != null ? `rejected candidate . conf ${conf.toFixed(2)}` : "rejected candidate";
  } else {
    text = "no shot at playhead";
  }
  return props.readOnly ? `${text} . read-only` : text;
}

const btn = "min-h-11 rounded-md border border-rule px-3 font-mono text-sm disabled:opacity-50";

export function ActionArea(props: ActionAreaProps) {
  const { target, readOnly, onNudge, onDeleteShot, onShowVideo, onPromote, onAddShot } = props;
  return (
    <div className="flex flex-col gap-1 px-2 pb-2">
      <div aria-live="polite" className="truncate font-mono text-sm">
        {readout(props)}
      </div>
      <div className="flex gap-2">
        {target.kind === "shot" && (
          <>
            <button type="button" className={btn} disabled={readOnly} onClick={() => onNudge(-10)}>
              -10 ms
            </button>
            <button type="button" className={btn} disabled={readOnly} onClick={() => onNudge(10)}>
              +10 ms
            </button>
            <button type="button" className={btn} disabled={readOnly} onClick={onDeleteShot}>
              Delete
            </button>
            <button type="button" className={`${btn} ml-auto`} onClick={onShowVideo}>
              Video
            </button>
          </>
        )}
        {target.kind === "candidate" && (
          <button type="button" className="btn-led-fill min-h-11 flex-1 rounded-md" disabled={readOnly} onClick={onPromote}>
            Promote candidate
          </button>
        )}
        {target.kind === "none" && (
          <button type="button" className={`${btn} flex-1`} disabled={readOnly} onClick={onAddShot}>
            Add shot at playhead
          </button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/components/audit/mobile/footer.test.tsx`
Expected: PASS (5 tests). If `lucide-react` icon names differ, check what `MobileBeepReview.tsx` imports and use those.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/audit/mobile/AuditTransport.tsx src/splitsmith/ui_static/src/components/audit/mobile/ActionArea.tsx src/splitsmith/ui_static/src/components/audit/mobile/footer.test.tsx
git commit -m "feat(ui): mobile audit transport and three-state action area"
```

---

### Task 7: `pages/MobileAudit.tsx` - the screen

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/MobileAudit.tsx`
- Test: `src/splitsmith/ui_static/src/pages/MobileAudit.test.tsx`

**Interfaces:**
- Consumes: everything above, plus `api`, `ApiError`, `capabilityDenied`, `deriveMarkers`, `buildAuditJson`, `snapToPeak`, `Snackbar`/`SnackState`, `Portal`, `MobileConfirmSheet`, `useOutletContext<MatchShellOutletContext>`, `useParams`, `useNavigate`.
- Produces: `export function MobileAudit(): JSX.Element` - default-less named export, mounted by Task 8's `AuditRoute`.

**Behavioural contract** (each bullet is asserted by a test in this task):

1. Loads `api.getStageAudit(slug, n)` and `api.getStagePeaks(slug, n, 8192)` on mount; derives markers with `deriveMarkers`.
2. No audit doc (`null`): an empty state - "Nothing to audit yet - run shot detection first" with a link to the match jobs page. No waveform, no save.
3. Peaks 404 (`ApiError.status === 404`): "Waiting for the desktop to sync this stage's audio"; any other peaks error: "Audio failed to load" plus the error text (#757 distinction).
4. The layout is a full-viewport takeover rendered through `Portal` (`fixed inset-0 z-takeover`, `flex flex-col`, background `bg-[var(--color-bg,#0a0a0a)]`) - the page itself never scrolls, and no header-height arithmetic is done against the shell (the overlay-architecture rule). Its own compact header: a back button (navigates to `..` results via `useNavigate`), `Audit . stage {n}` in `font-display`, and a Save button showing a dirty dot when unsaved edits exist.
5. Gestures: row/lane `onTap(t)` -> `playback.playFrom(t)`; `onGrabStart` -> `playback.stop()`; row `onScrub(t)` and lane `onJog(t)` -> `playback.seek(t)` + `scrubber?.grainAt(t)`; `onGrabEnd` -> nothing (playback stays stopped, per the two-verb rule).
6. Target = `resolveTarget(markers, playback.playhead, heldId)`. `heldId` is set by a nudge and cleared by any playhead change that was not caused by that nudge; `nudgeMs` accumulates while held and resets on release.
7. Actions mutate the marker array and append to a `sessionEvents` ref, exactly the desktop vocabulary:
   - nudge: marker `time += delta / 1000`, event `marker_time_changed {id, from_time, to_time}`, hold the target.
   - delete a detected shot: flip marker to `kind: "rejected"`, event `marker_rejected {id, time, candidate_number}` (it returns to the candidate pool).
   - delete a manual shot: remove the marker, event `marker_deleted {id, time, kind: "manual"}` - routed through `MobileConfirmSheet` since it is destructive.
   - promote: flip `rejected` -> `detected`, event `marker_kept {id, time, candidate_number}`.
   - add: `const snapped = snapPeaks ? snapToPeak(playhead, snapPeaks) : null; const t = snapped ?? playhead;` mint `` `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` ``, marker `{id, shotId: id, kind: "manual", time: t, candidateNumber: null, confidence: null, peakAmplitude: null, note: ""}`, event `marker_added_manual {id, time: t}`. `snapPeaks` is `{peaks: peaksResult.peaks, duration: peaksResult.duration}` - the 8192-bin fetch doubles as the snap source (4.8 ms bins beat the desktop's dedicated snap fetch).
   - every event object is `{ts: new Date().toISOString(), kind, payload}`.
8. Save: append a `save` event `{shots_count}` (kept markers only - it clears an open triage flag server-side), then

```ts
const payload = buildAuditJson({
  base: audit,
  stage: {
    stage_number: stageNumber,
    stage_name: audit.stage_name,
    time_seconds: audit.stage_time_seconds ?? 0,
  },
  primaryBeepInClip: peaksResult?.beep_time ?? audit.beep_time ?? null,
  markers,
  appendEvents: [...sessionEvents.current, saveEvent],
});
const saved = await api.saveStageAudit(slug, stageNumber, payload);
```

   On success: `setAudit(saved)`, re-derive markers, clear events + dirty, `outletCtx?.refresh?.()`, snackbar "Saved".
   On `ApiError` 409: refetch the doc, re-derive, clear events, snackbar error "This stage changed elsewhere - reloaded, local edits were discarded".
   On `ApiError` 403: snackbar error "Save refused - this mirror's audit gate should be open. This is a bug." (spec: unreachable once the allow-list entry ships; if seen, say so.)
   Anything else: snackbar error with the error text.
9. `readOnly = capabilityDenied(outletCtx?.capabilities, "review")` - the audit PUT maps to the `review` capability. Read-only disables Save and passes `readOnly` down to `ActionArea`.
10. Loop toggle anchors on the target shot's time if `target.kind === "shot"`, else the playhead.
11. Video: the Video button opens a `Portal`-rendered overlay (`z-modal`, close button, `role="dialog"` `aria-modal` `aria-label="Shot video"`) with a `<video controls playsInline>` whose src comes from the primary video's stream URL and which seeks to `max(0, target.time - 1.5)` on `loadedmetadata`. The primary video path comes from the project payload the same way `Audit.tsx` picks `activeVideo` (grep `activeVideo` in `Audit.tsx` around line 1600 and copy that selection: the stage's primary video entry, `api.videoStreamUrl(slug, video.path, "trim")` falling back to `"auto"` when no trim). If the project payload offers no video for the stage, the Video button is not rendered.
12. No stage param (`audit/:slug` on mobile): render a plain list of the shooter's stages as links to `audit/:slug/{n}` (stage numbers from the outlet project payload, same source the desktop `DefaultShooterRedirect`/stage pickers use), inside the normal page flow (not the takeover).

- [ ] **Step 1: Write the failing tests**

The test mocks `@/lib/useAuditPlayback` (to control the playhead without real audio), `@/lib/scrub-audio` (`createScrubber` -> `null`), and `@/lib/api`. It renders `<MobileAudit />` inside a `MemoryRouter` route `/match/m1/audit/alice/3` with a mocked outlet context (pattern: `vi.mock("react-router-dom", ...)` spreading the original and overriding `useOutletContext`, or wrap with a fixture `MatchShell`-less `Outlet` provider - copy the approach used in `Triage.tsx`'s test if one exists; otherwise the `useOutletContext` mock is the simple road).

```tsx
// src/splitsmith/ui_static/src/pages/MobileAudit.test.tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StageAudit } from "@/lib/api";
import { MobileAudit } from "@/pages/MobileAudit";

const playback = vi.hoisted(() => ({
  state: {
    playhead: 0,
    playing: false,
    speed: 1 as const,
    loop: null,
    playFrom: vi.fn(),
    stop: vi.fn(),
    seek: vi.fn(),
    setSpeed: vi.fn(),
    toggleLoop: vi.fn(),
  },
}));
vi.mock("@/lib/useAuditPlayback", async (orig) => ({
  ...(await orig<typeof import("@/lib/useAuditPlayback")>()),
  useAuditPlayback: () => playback.state,
}));
vi.mock("@/lib/scrub-audio", () => ({
  createScrubber: vi.fn(async () => null),
  GRAIN_S: 0.06,
}));

const ctx = vi.hoisted(() => ({
  value: {
    project: null,
    origin: "hosted",
    capabilities: ["edit", "review", "share_manage"],
    refresh: vi.fn(),
  } as Record<string, unknown>,
}));
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  useOutletContext: () => ctx.value,
}));

const doc = (): StageAudit => ({
  stage_number: 3,
  stage_name: "Stage 3",
  beep_time: 1.0,
  stage_time_seconds: 20.5,
  shots: [
    { shot_number: 1, candidate_number: 1, time: 2.0, ms_after_beep: 1000, source: "detected", id: "cand-1" },
    { shot_number: 2, candidate_number: 2, time: 2.4, ms_after_beep: 1400, source: "detected", id: "cand-2" },
  ],
  _candidates_pending_audit: {
    candidates: [
      { candidate_number: 1, time: 2.0, ms_after_beep: 1000, confidence: 0.9 },
      { candidate_number: 2, time: 2.4, ms_after_beep: 1400, confidence: 0.8 },
      { candidate_number: 3, time: 3.1, ms_after_beep: 2100, confidence: 0.1 },
    ],
  },
  audit_events: [],
});

const apiMock = vi.hoisted(() => ({
  getStageAudit: vi.fn(),
  getStagePeaks: vi.fn(),
  saveStageAudit: vi.fn(),
  stageAudioUrl: vi.fn(() => "/audio.wav"),
  videoStreamUrl: vi.fn(() => "/video.mp4"),
}));
vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, ...apiMock } };
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/match/m1/audit/alice/3"]}>
      <Routes>
        <Route path="/match/:matchId/audit/:slug/:stage" element={<MobileAudit />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  playback.state.playhead = 0;
  apiMock.getStageAudit.mockResolvedValue(doc());
  apiMock.getStagePeaks.mockResolvedValue({
    duration: 22,
    sample_rate: 48000,
    bins: 8192,
    peaks: Array.from({ length: 8192 }, () => 0.4),
    beep_time: 1.0,
    trimmed: true,
  });
  apiMock.saveStageAudit.mockImplementation(async (_s: string, _n: number, p: StageAudit) => p);
});

describe("MobileAudit", () => {
  it("requests peaks at the 8192-bin cap and renders the row stack", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("wrapped-waveform")).toBeInTheDocument());
    expect(apiMock.getStagePeaks).toHaveBeenCalledWith("alice", 3, 8192);
  });

  it("shows the empty state when there is no audit doc", async () => {
    apiMock.getStageAudit.mockResolvedValue(null);
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/nothing to audit yet/i)).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("wrapped-waveform")).toBeNull();
  });

  it("promote flips a band candidate to kept and the save carries marker_kept", async () => {
    playback.state.playhead = 3.1; // on the rejected candidate cand-3
    renderPage();
    await waitFor(() => expect(screen.getByTestId("wrapped-waveform")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Promote candidate" }));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(apiMock.saveStageAudit).toHaveBeenCalled());
    const payload = apiMock.saveStageAudit.mock.calls[0][2] as StageAudit;
    expect(payload.shots.map((s) => s.candidate_number)).toContain(3);
    const kinds = (payload.audit_events ?? []).map((e) => e.kind);
    expect(kinds).toContain("marker_kept");
    expect(kinds).toContain("save");
  });

  it("nudge emits marker_time_changed and dials the readout", async () => {
    playback.state.playhead = 2.0; // on cand-1
    renderPage();
    await waitFor(() => expect(screen.getByTestId("wrapped-waveform")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "+10 ms" }));
    expect(screen.getByText(/\+10 ms/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(apiMock.saveStageAudit).toHaveBeenCalled());
    const payload = apiMock.saveStageAudit.mock.calls[0][2] as StageAudit;
    const moved = (payload.audit_events ?? []).find((e) => e.kind === "marker_time_changed");
    expect(moved?.payload).toMatchObject({ id: "cand-1", from_time: 2.0 });
    expect(payload.shots.find((s) => s.candidate_number === 1)?.time).toBeCloseTo(2.01, 3);
  });

  it("a 409 on save reloads the doc and says the stage changed elsewhere", async () => {
    const { ApiError } = await import("@/lib/api");
    playback.state.playhead = 3.1;
    apiMock.saveStageAudit.mockRejectedValue(
      new ApiError(409, { code: "version_conflict", message: "..." }),
    );
    renderPage();
    await waitFor(() => expect(screen.getByTestId("wrapped-waveform")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Promote candidate" }));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(screen.getByText(/changed elsewhere/i)).toBeInTheDocument());
    expect(apiMock.getStageAudit).toHaveBeenCalledTimes(2);
  });

  it("read-only capabilities disable save and the action area", async () => {
    ctx.value = { ...ctx.value, capabilities: ["share_manage"] };
    renderPage();
    await waitFor(() => expect(screen.getByTestId("wrapped-waveform")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
    expect(screen.getByText(/read-only/)).toBeInTheDocument();
    ctx.value = { ...ctx.value, capabilities: ["edit", "review", "share_manage"] };
  });

  it("a peaks 404 names the desktop sync, not a generic failure", async () => {
    const { ApiError } = await import("@/lib/api");
    apiMock.getStagePeaks.mockRejectedValue(new ApiError(404, "not found"));
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/waiting for the desktop to sync/i)).toBeInTheDocument(),
    );
  });
});
```

Adjust the `ApiError` constructor call to its real signature (check `lib/api.ts` - if it takes `(status, detail)` in a different order or shape, mirror it; the assertions stand).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/pages/MobileAudit.test.tsx`
Expected: FAIL - cannot resolve `@/pages/MobileAudit`.

- [ ] **Step 3: Implement the page**

Skeleton (the behavioural contract above is the authority; this pins the wiring):

```tsx
// src/splitsmith/ui_static/src/pages/MobileAudit.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { ArrowLeft, X } from "lucide-react";

import { ActionArea } from "@/components/audit/mobile/ActionArea";
import { AuditTransport } from "@/components/audit/mobile/AuditTransport";
import { WrappedWaveform, DEFAULT_ROWS } from "@/components/audit/mobile/WrappedWaveform";
import { ZoomLane, type ZoomFactor } from "@/components/audit/mobile/ZoomLane";
import type { AuditMarker } from "@/components/MarkerLayer";
import { MobileConfirmSheet } from "@/components/MobileConfirmSheet";
import { Snackbar, type SnackState } from "@/components/Snackbar";
import { Portal } from "@/components/ui/Portal";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { ApiError, api, capabilityDenied, type AuditEvent, type PeaksResult, type StageAudit } from "@/lib/api";
import { buildAuditJson, deriveMarkers } from "@/lib/audit-doc";
import { resolveTarget } from "@/lib/audit-target";
import { snapToPeak } from "@/lib/peak-snap";
import { createScrubber, type Scrubber } from "@/lib/scrub-audio";
import { useAuditPlayback } from "@/lib/useAuditPlayback";

export function MobileAudit() {
  const { slug = "", stage } = useParams();
  const stageNumber = stage != null ? Number(stage) : null;
  // ... state: audit, peaksResult, peaksError, markers, heldId, nudgeMs,
  // dirty, saving, snack, zoom (useState<ZoomFactor>(3)), videoAt,
  // confirmDeleteId, scrubberRef
  // ... sessionEvents = useRef<AuditEvent[]>([])
  // ... playback = useAuditPlayback(audioSrc) where audioSrc =
  //     stageNumber != null ? api.stageAudioUrl(slug, stageNumber) : null
  // ... load effects, action callbacks, save handler per the contract
}
```

Implementation notes that are contract, not taste:

- `recordEvent(kind, payload)` helper: pushes `{ts: new Date().toISOString(), kind, payload}` and sets dirty - mirror `Audit.tsx:803-810`.
- The held-target release: keep `lastNudgePlayheadRef`; a `useEffect` on `playback.playhead` clears `heldId` and `nudgeMs` whenever the playhead value changes (nudges never move the playhead, so any change is a real movement).
- Kept markers passed to `WrappedWaveform` are `markers.filter((m) => m.kind !== "rejected")`; `ZoomLane` gets all of them.
- `shotOrdinal`/`splitS` for `ActionArea`: sort kept markers by time; ordinal is 1-based index of the target; `splitS` = `target.time - previousKept.time`, or `beep != null ? target.time - beep : null` for the first shot (`beep = peaksResult?.beep_time ?? audit.beep_time ?? null`).
- The takeover wrapper:

```tsx
<Portal>
  <div className="fixed inset-0 z-takeover flex flex-col bg-[var(--color-bg,#0b0d10)]">
    <header className="flex min-h-11 items-center gap-2 border-b border-rule px-2">
      <button type="button" aria-label="Back" onClick={() => navigate(`/match/${matchId}/results/${slug}/${stageNumber}`)} className="flex min-h-11 min-w-11 items-center justify-center">
        <ArrowLeft className="size-5" aria-hidden />
      </button>
      <span className="font-display text-sm uppercase tracking-wide">Audit . stage {stageNumber}</span>
      <button type="button" disabled={readOnly || !dirty || saving} onClick={handleSave} className="btn-led-fill ml-auto min-h-9 rounded-md px-4 disabled:opacity-50">
        {saving ? "Saving..." : dirty ? "Save *" : "Save"}
      </button>
    </header>
    {/* WrappedWaveform (flex-1) */}
    {/* footer: ZoomLane, AuditTransport, ActionArea */}
  </div>
</Portal>
```

  (`matchId` from `useParams`; add it to the destructure.)
- The background token: check `styles/index.css` for the real page-background variable name (`--color-bg` is an assumption - use whatever `MatchShell`'s root uses; the `feedback_css_var_token_names` rule applies: verify the token exists, bare `var(--foo)` falls back silently).
- Scrubber lifecycle: create in an effect after `audioSrc` is known, store in a ref, dispose on unmount. `onScrub`/`onJog` call `playback.seek(t)` then `scrubberRef.current?.grainAt(t)`.
- The no-stage-param branch (contract point 12) renders before the takeover: a `max-w-md` list of stage links, reusing whatever stage list the outlet `project` carries (find the stage array on the project payload by greping how `Triage.tsx` builds its stage rows; reuse that access path).
- Delete-manual confirm sheet copy: title "Delete manual shot", body names the time, confirm "Delete".

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/pages/MobileAudit.test.tsx`
Expected: PASS (7 tests).

- [ ] **Step 5: Run the full frontend suite to catch regressions**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run`
Expected: PASS - nothing outside the new files should change behaviour.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/MobileAudit.tsx src/splitsmith/ui_static/src/pages/MobileAudit.test.tsx
git commit -m "feat(ui): mobile audit screen - wrapped rows, zoom lane, three-state actions"
```

---

### Task 8: Route branch - lift `DesktopGate` for the phone only

**Files:**
- Modify: `src/splitsmith/ui_static/src/App.tsx:241-249` (the two audit routes) and the route-helper block around line 63 (next to `BeepReviewRoute`)
- Test: `src/splitsmith/ui_static/src/App.routes.audit.test.tsx` (new)

**Interfaces:**
- Consumes: `MobileAudit` from Task 7, `useIsMobile`, `DesktopGate`, existing `Audit`.
- Produces: the `audit/:slug` and `audit/:slug/:stage` routes render `MobileAudit` below 768 px and the unchanged desktop `Audit` (still gated) at or above it.

- [ ] **Step 1: Write the failing test**

```tsx
// src/splitsmith/ui_static/src/App.routes.audit.test.tsx
// Mirrors App.routes.share.test.tsx's approach: mock useIsMobile, mock the
// two page modules to sentinels, render the router at the audit URL, and
// assert which sentinel mounts.
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => mobile.value }));
vi.mock("@/pages/MobileAudit", () => ({
  MobileAudit: () => <div data-testid="mobile-audit" />,
}));
vi.mock("@/pages/Audit", () => ({ default: () => <div data-testid="desktop-audit" /> }));
// Follow App.routes.share.test.tsx for the remaining mocks the router needs
// (AuthGate, providers, api) - copy its setup block verbatim.

describe("audit route", () => {
  it("mounts MobileAudit below the breakpoint", async () => {
    mobile.value = true;
    // render the app at /match/m1/audit/alice/3 the way App.routes.share.test.tsx does
    // ...
    expect(await screen.findByTestId("mobile-audit")).toBeInTheDocument();
  });

  it("keeps the desktop Audit above the breakpoint", async () => {
    mobile.value = false;
    // ...
    expect(await screen.findByTestId("desktop-audit")).toBeInTheDocument();
  });
});
```

Check whether `pages/Audit.tsx` is a default or named export and mock accordingly. If `App.routes.share.test.tsx`'s harness cannot reach a match-scoped route without heavy shell mocking, an acceptable narrower test is rendering `<AuditRoute />` directly (export it from App.tsx for the test) with the two `useIsMobile` values.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/App.routes.audit.test.tsx`
Expected: FAIL - `AuditRoute`/`MobileAudit` not wired.

- [ ] **Step 3: Implement the branch**

Next to `BeepReviewRoute` (`App.tsx:63-70`), following its comment style:

```tsx
/* Audit joins beep review as a match-scoped screen with a real mobile
 * surface (mobile audit design, 2026-08-12). Below 768 px this renders
 * the wrapped-row MobileAudit; the desktop screen stays behind
 * DesktopGate untouched. */
function AuditRoute() {
  const isMobile = useIsMobile();
  return isMobile ? (
    <MobileAudit />
  ) : (
    <DesktopGate screen="Audit">
      <Audit />
    </DesktopGate>
  );
}
```

Replace both audit route elements:

```tsx
<Route path="audit/:slug" element={<ShooterScopedRoute element={<AuditRoute />} />} />
<Route path="audit/:slug/:stage" element={<ShooterScopedRoute element={<AuditRoute />} />} />
```

Add the `MobileAudit` import next to the other page imports (check whether pages are lazy-loaded in App.tsx - if `Audit` is `lazy(() => import(...))`, load `MobileAudit` the same way).

- [ ] **Step 4: Run tests to verify they pass, and prove the test bites**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run src/App.routes.audit.test.tsx`
Expected: PASS. Then temporarily revert the `AuditRoute` element back to the gated desktop form, re-run, and confirm the mobile-branch test FAILS - that is the proof this test would have caught the pre-change behaviour. Restore.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/App.tsx src/splitsmith/ui_static/src/App.routes.audit.test.tsx
git commit -m "feat(ui): audit route branches to MobileAudit below the breakpoint"
```

---

### Task 9: Gates, visual verification, PR

**Files:** none new (fixes only if gates fail).

- [ ] **Step 1: Full frontend gates**

```bash
cd src/splitsmith/ui_static
pnpm typecheck
pnpm test -- --run
pnpm exec eslint src/pages/MobileAudit.tsx src/components/audit/mobile src/lib/audit-target.ts src/lib/scrub-audio.ts src/lib/useAuditPlayback.ts src/App.tsx
```

Expected: all green. No Python changed, so `ruff`/`black`/`pytest` gates are not in play; do not skip them if any Python file was touched after all.

- [ ] **Step 2: ASCII sweep of the new copy**

```bash
git diff main -- src/splitsmith/ui_static | grep -nP '[\x{2013}\x{2014}\x{2018}\x{2019}\x{201C}\x{201D}\x{2026}\x{00A0}]' && echo "FIX THESE" || echo "clean"
```

Expected: `clean`.

- [ ] **Step 3: Visual verification at phone width**

Launch the dev UI against a local match with an audited stage, then take a bounded headless screenshot (Playwright MCP `navigate` hangs on the SPA's live SSE - use the script pattern with `domcontentloaded`):

```bash
cd src/splitsmith/ui_static && pnpm dev &   # note the port
uv run --frozen python - <<'EOF'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 393, "height": 852})
    page.goto("http://localhost:5173/match/<id>/audit/<slug>/<n>", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    page.screenshot(path="/Users/mathias/.claude-tmp/mobile-audit-393.png")
    b.close()
EOF
```

Read the screenshot. Check against the spec: 11 rows fill the viewport with no page scroll, gutter times legible, footer shows lane + transport + action area without overflow at 393 px, target band visible, no clipped buttons. Fix and re-shoot until it matches.

- [ ] **Step 4: Verify the empty and error states visually too**

Same recipe against a stage with no audit doc (expect the empty state, not a blank takeover).

- [ ] **Step 5: Open the PR**

```bash
git push -u origin feat/mobile-audit-ui
gh pr create --title "feat(ui): mobile audit screen (mobile audit design, step 6)" --body "..."
```

PR body: link the spec, the handover, and state plainly which locked decisions ship (11 rows, 3x default, 6 px grab threshold) and that the four spec open questions (row count feel, default zoom, grain-scrub fidelity, graze sensitivity) are explicitly deferred to a real-phone pass - file that follow-up issue and link it. Wait for CI green before merging (`gh run watch` - merge-when-green is not enforced on this repo).

- [ ] **Step 6: Staging verification**

After merge deploys to staging: log in on the phone via the staging-login flow, open an audited stage's audit URL, and run one real pass: play through, promote one candidate, nudge it, save, then pull on the desktop and confirm the edit survived (the #848 merge is the thing this UI exists to exercise). Note results on the PR.

---

## Self-review notes

- Spec coverage: wrapped rows (T4), zoom lane + band + candidates (T5), target/no-selection (T1), three-state action area (T6), transport verbs + loop + speed (T3, T6), grain scrub + degrade (T2), save vocabulary + 409/403/404 copy (T7), writability via capability (T7), route lift (T8), phone-width visual + staging (T9). Video button: T7 contract point 11. Stage list for the bare `audit/:slug` route: T7 point 12.
- Deliberately not in scope, per spec non-goals: multi-cam, a browsable candidate list, offline, replacing the desktop screen. The four open questions ship with the locked defaults and a follow-up issue for the real-phone pass.
- Two places intentionally send the implementer to read neighbouring code rather than inlining it (Audit.tsx `activeVideo` selection; the project payload's stage list): both are one grep away, and inlining a guessed shape would be worse than pointing at the live one.
