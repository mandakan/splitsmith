import { describe, expect, it } from "vitest";

import type { StageEvent } from "@/lib/api";
import { addRegion } from "@/lib/addRegion";
import { validateLanes } from "@/lib/events";

const shotTimes = [2.4, 3.0, 6.5, 7.0];
const span = 10;
const DRAW: StageEvent = { id: "evt-3", kind: "draw", start: 0.4, end: 2.4, source: "auto" };

describe("addRegion", () => {
  it("over an open region of another kind takes its span: a movement during the draw", () => {
    const r = addRegion([DRAW], "movement", { over: DRAW, at: 1, shotTimes, span });
    expect(r?.id).toBe("evt-4");
    expect(r?.events.find((e) => e.id === r.id)).toEqual({ id: "evt-4", kind: "movement", start: 0.4, end: 2.4, source: "manual" });
    expect(validateLanes(r!.events)).toBeNull();
  });

  it("with nothing open, fills the gap between the shots around the playhead", () => {
    const r = addRegion([], "reload", { over: null, at: 4.2, shotTimes, span });
    expect(r?.events[0]).toMatchObject({ kind: "reload", start: 3.0, end: 6.5, source: "manual" });
  });

  it("before the first shot the gap starts at the beep; after the last it ends at the stage's end", () => {
    expect(addRegion([], "movement", { over: null, at: 1, shotTimes, span })?.events[0]).toMatchObject({ start: 0, end: 2.4 });
    expect(addRegion([], "movement", { over: null, at: 8, shotTimes, span })?.events[0]).toMatchObject({ start: 7.0, end: 10 });
  });

  it("is cut to the free room in its own lane", () => {
    const m: StageEvent = { id: "m", kind: "movement", start: 3.0, end: 4.0, source: "manual" };
    const r = addRegion([m], "movement", { over: null, at: 5, shotTimes, span });
    expect(r?.events.find((e) => e.id === r.id)).toMatchObject({ start: 4.0, end: 6.5 });
    expect(validateLanes(r!.events)).toBeNull();
  });

  it("refuses a lane taken at the anchor, the same kind over the open region, and a second draw", () => {
    const m: StageEvent = { id: "m", kind: "movement", start: 3.0, end: 6.5, source: "manual" };
    expect(addRegion([m], "movement", { over: null, at: 4, shotTimes, span })).toBeNull();
    expect(addRegion([m], "movement", { over: m, at: 4, shotTimes, span })).toBeNull();
    expect(addRegion([DRAW], "draw", { over: null, at: 1, shotTimes, span })).toBeNull();
  });

  it("a draw with nothing open runs from the beep to the first shot", () => {
    expect(addRegion([], "draw", { over: null, at: 5, shotTimes, span })?.events[0]).toMatchObject({ start: 0, end: 2.4 });
  });
});
