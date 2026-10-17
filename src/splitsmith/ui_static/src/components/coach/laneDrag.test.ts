import { describe, expect, it } from "vitest";

import { dragFrame, type Drag } from "@/components/coach/laneDrag";
import type { StageEvent, StageEventKind } from "@/lib/api";

const draw: StageEvent = { id: "evt-1", kind: "draw", start: 0, end: 1.4, source: "auto" };
const identity = (t: number) => t;
const create = (kind: StageEventKind, anchorT: number): Drag => ({
  mode: "create",
  kind,
  anchorT,
  id: null,
  pointerId: 1,
  element: document.createElement("div"),
  startX: 0,
  startY: 0,
  thresholdPx: 0,
  moved: true,
  startEvents: [],
});

describe("dragFrame on the Draw lane", () => {
  it("creates no second draw: a stage has one", () => {
    const frame = dragFrame(create("draw", 2), [draw], 3, identity, 20);
    expect(frame).toBeNull();
  });

  it("creates the first draw", () => {
    const frame = dragFrame(create("draw", 2), [], 3, identity, 20);
    expect(frame?.events.map((e) => [e.kind, e.start, e.end])).toEqual([["draw", 2, 3]]);
  });

  it("still creates a second region on any other lane", () => {
    const move: StageEvent = { id: "evt-1", kind: "movement", start: 0, end: 1.4, source: "manual" };
    const frame = dragFrame(create("movement", 2), [move], 3, identity, 20);
    expect(frame?.events).toHaveLength(2);
  });
});
