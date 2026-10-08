import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { StageEvent, StageEventSummary } from "@/lib/api";
import {
  clampToLane,
  enclosingMovement,
  nextEventId,
  reloadFigures,
  rulerLabels,
  shotIsMoving,
  snapTime,
  summarize,
  timeFromX,
  validateLanes,
  withKind,
} from "./events";

interface Case {
  name: string;
  capacity: number | null;
  shots: number[];
  events: StageEvent[];
  expect: {
    moving: boolean[];
    reloads: { event_id: string; duration: number; moving: boolean; overhang: number | null }[];
    summary: StageEventSummary;
  };
}
interface Fixture {
  cases: Case[];
  lane_violations: { name: string; events: StageEvent[]; mentions: string[] }[];
  lane_ok: { name: string; events: StageEvent[] }[];
}

// The same file tests/test_events.py reads: a case lives on both sides or not at all.
const here = dirname(fileURLToPath(import.meta.url));
const FIXTURE = JSON.parse(
  readFileSync(join(here, "../../../../../tests/fixtures/events/cases.json"), "utf8"),
) as Fixture;

const r2 = (x: number) => Math.round(x * 100) / 100;

describe("events fixture parity", () => {
  it.each(FIXTURE.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(validateLanes(c.events)).toBeNull();
    expect(c.shots.map((t) => shotIsMoving(t, c.events))).toEqual(c.expect.moving);

    const figs = reloadFigures(c.events);
    expect(figs.map((f) => f.eventId)).toEqual(c.expect.reloads.map((r) => r.event_id));
    figs.forEach((f, i) => {
      const want = c.expect.reloads[i];
      expect(r2(f.duration)).toBeCloseTo(want.duration, 9);
      expect(f.moving).toBe(want.moving);
      if (want.overhang === null) expect(f.overhang).toBeNull();
      else expect(r2(f.overhang as number)).toBeCloseTo(want.overhang, 9);
    });

    const s = summarize(c.shots, c.events, c.capacity);
    expect(r2(s.movement_s)).toBeCloseTo(c.expect.summary.movement_s, 9);
    expect(s.moving_shots).toBe(c.expect.summary.moving_shots);
    expect(s.reloads).toBe(c.expect.summary.reloads);
    if (c.expect.summary.reload_avg_s === null) expect(s.reload_avg_s).toBeNull();
    else expect(r2(s.reload_avg_s as number)).toBeCloseTo(c.expect.summary.reload_avg_s, 9);
    expect(r2(s.overhang_s)).toBeCloseTo(c.expect.summary.overhang_s, 9);
    expect(s.capacity_warning).toBe(c.expect.summary.capacity_warning);
  });

  it.each(FIXTURE.lane_violations.map((c) => [c.name, c] as const))("violation: %s", (_n, c) => {
    const msg = validateLanes(c.events);
    expect(msg).not.toBeNull();
    for (const id of c.mentions) expect(msg).toContain(id);
  });

  it.each(FIXTURE.lane_ok.map((c) => [c.name, c] as const))("ok: %s", (_n, c) => {
    expect(validateLanes(c.events)).toBeNull();
  });
});

const ev = (
  id: string,
  kind: StageEvent["kind"],
  start: number,
  end: number,
  source: StageEvent["source"] = "manual",
): StageEvent => ({ id, kind, start, end, source });

describe("editor helpers", () => {
  it("nextEventId only grows and ignores foreign ids", () => {
    expect(nextEventId([])).toBe("evt-1");
    expect(nextEventId([{ id: "evt-7" }, { id: "evt-3" }, { id: "manual-x" }])).toBe("evt-8");
  });

  it("enclosingMovement picks the latest-ending overlapping movement", () => {
    const events = [ev("evt-1", "movement", 1.2, 3.0), ev("evt-2", "movement", 3.5, 5.0), ev("evt-3", "reload", 2.5, 5.5)];
    expect(enclosingMovement(events[2], events)?.id).toBe("evt-2");
    expect(enclosingMovement(ev("evt-9", "reload", 6, 7), events)).toBeNull();
  });

  it("clampToLane stops at same-lane neighbours and never inverts", () => {
    const events = [ev("evt-1", "movement", 1, 3), ev("evt-2", "movement", 4, 6), ev("evt-3", "reload", 0, 10)];
    // Dragging evt-2's start left past evt-1's end clamps at 3.
    expect(clampToLane(events, "evt-2", 2.0, 6)).toEqual({ start: 3, end: 6 });
    // Dragging evt-1's end right past evt-2's start clamps at 4.
    expect(clampToLane(events, "evt-1", 1, 5.5)).toEqual({ start: 1, end: 4 });
    // Review focus 4: start dragged beyond end stops MIN_EVENT_S short.
    expect(clampToLane(events, "evt-1", 3.5, 3)).toEqual({ start: 3 - 0.05, end: 3 });
    expect(clampToLane(events, "evt-1", 1, 0.5)).toEqual({ start: 1, end: 1.05 });
    // Other lanes do not clamp; the floor is 0.
    expect(clampToLane(events, "evt-3", -1, 10)).toEqual({ start: 0, end: 10 });
  });

  it("snapTime snaps within tolerance to the nearest target only", () => {
    expect(snapTime(4.33, [1.21, 4.35, 9.0], 0.05)).toBe(4.35);
    expect(snapTime(4.2, [1.21, 4.35, 9.0], 0.05)).toBe(4.2);
    expect(snapTime(4.34, [4.3, 4.35], 0.05)).toBe(4.35);
  });

  it("withKind moves a region to another lane as manual, or refuses an overlap there", () => {
    const events = [ev("evt-1", "movement", 1, 3), ev("evt-2", "reload", 2, 4, "auto"), ev("evt-3", "activation", 5, 6)];
    expect(withKind(events, "evt-2", "activation")?.find((e) => e.id === "evt-2")).toEqual({ ...events[1], kind: "activation", source: "manual" });
    expect(withKind(events, "evt-2", "movement")).toBeNull();
    expect(withKind(events, "evt-9", "movement")).toBeNull();
  });

  it("timeFromX maps and clamps", () => {
    expect(timeFromX(450, 900, 16.2)).toBeCloseTo(8.1);
    expect(timeFromX(-10, 900, 16.2)).toBe(0);
    expect(timeFromX(2000, 900, 16.2)).toBe(16.2);
    expect(timeFromX(10, 0, 16.2)).toBe(0);
  });
});

describe("rulerLabels", () => {
  it("keeps the beep and stage-time labels clear at the Coach page's desktop width", () => {
    // 32.12 s over 475 px (the 1280 px Coach page): a fixed 2 s step put "2"
    // on top of "Beep" and "30" on top of "32.12".
    expect(rulerLabels(32.12, 475)).toEqual([4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28]);
  });

  it("widens the step on a narrow strip so labels never crowd", () => {
    const labels = rulerLabels(32.12, 280);
    expect(labels).toEqual([10, 15, 20, 25]);
    const gaps = labels.slice(1).map((s, i) => (s - labels[i]) * (280 / 32.12));
    expect(Math.min(...gaps)).toBeGreaterThanOrEqual(28);
  });

  it("labels nothing before the strip is measured", () => {
    expect(rulerLabels(32.12, 0)).toEqual([]);
  });
});
