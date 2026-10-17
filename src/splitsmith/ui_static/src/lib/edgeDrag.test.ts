import { describe, expect, it } from "vitest";

import type { StageEvent } from "@/lib/api";
import { beginEdge, edgeFrame, restoreRegion, snappedTo, stepEdge } from "@/lib/edgeDrag";
import { MIN_EVENT_S } from "@/lib/events";

const reload: StageEvent = { id: "r", kind: "reload", start: 8, end: 9.4, source: "auto" };
const before: StageEvent = { id: "p", kind: "reload", start: 5, end: 7.9, source: "manual" };
const LIST = [before, reload];
const opts = { snap: false, shotTimes: [7.95, 9.5], duration: 20, padPx: 300 };

describe("a relative edge drag", () => {
  it("moves the start by the finger's travel, one pad width a second, and confirms the region", () => {
    const g = beginEdge(LIST, "r", "start")!;
    const f = edgeFrame(g, LIST, 0.25, opts)!;
    const r = f.events.find((e) => e.id === "r")!;
    expect(r.start).toBeCloseTo(8.25);
    expect(r.end).toBe(9.4);
    expect(r.source).toBe("manual");
    expect(f.seek).toBeCloseTo(8.25);
    expect(f.edge).toBeCloseTo(8.25);
  });
  it("seeks to the unsnapped edge while saving the snapped one", () => {
    const g = beginEdge(LIST, "r", "end")!;
    const f = edgeFrame(g, LIST, 0.09, { ...opts, snap: true })!; // 9.49, 10 ms from the shot at 9.5
    expect(f.edge).toBe(9.5);
    expect(f.seek).toBeCloseTo(9.49);
  });
  it("does not snap with snapping off", () => {
    const g = beginEdge(LIST, "r", "end")!;
    expect(edgeFrame(g, LIST, 0.09, opts)!.edge).toBeCloseTo(9.49);
  });
  it("stops at a same-lane neighbour and at the minimum length", () => {
    const g = beginEdge(LIST, "r", "start")!;
    expect(edgeFrame(g, LIST, -1, opts)!.edge).toBe(7.9);
    expect(edgeFrame(g, LIST, 5, opts)!.edge).toBeCloseTo(9.4 - MIN_EVENT_S);
  });
  it("slides the whole region, keeping its length", () => {
    const g = beginEdge(LIST, "r", "body")!;
    const r = edgeFrame(g, LIST, 0.5, opts)!.events.find((e) => e.id === "r")!;
    expect(r.start).toBeCloseTo(8.5);
    expect(r.end - r.start).toBeCloseTo(1.4);
  });
  it("judges every frame from where the gesture began, not from the last frame", () => {
    const g = beginEdge(LIST, "r", "start")!;
    const once = edgeFrame(g, LIST, 0.2, opts)!.events;
    expect(edgeFrame(g, once, 0.2, opts)!.edge).toBeCloseTo(8.2);
  });
  it("restores the region as it was when the gesture began, source included", () => {
    const g = beginEdge(LIST, "r", "start")!;
    const moved = edgeFrame(g, LIST, 0.3, opts)!.events;
    expect(restoreRegion(moved, g)).toEqual(LIST);
  });
  it("knows nothing of a region that is gone", () => {
    expect(beginEdge(LIST, "nope", "start")).toBeNull();
  });
});

describe("frame steps and the snap label", () => {
  it("steps one frame of the clip's own rate, never snapping", () => {
    const at50 = stepEdge(LIST, "r", "start", 1, 50)!.find((e) => e.id === "r")!;
    expect(at50.start).toBeCloseTo(8.02);
    const at30 = stepEdge(LIST, "r", "end", -1, 30)!.find((e) => e.id === "r")!;
    expect(at30.end).toBeCloseTo(9.4 - 1 / 30);
  });
  it("names what pulled the edge off the finger", () => {
    const shots = [{ shot_number: 7, time_from_beep: 9.5 }];
    expect(snappedTo(9.5, 9.49, shots)).toBe("shot 7");
    expect(snappedTo(0, 0.01, shots)).toBe("beep");
    expect(snappedTo(9.3, 9.3, shots)).toBeNull();
  });
});
