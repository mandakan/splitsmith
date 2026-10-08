import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StageEvent } from "@/lib/api";

import { LaneEditor } from "./LaneEditor";

const WIDTH = 1000;
const STAGE = 10; // 100 px per second
const SHOTS = [1, 2, 3, 6, 7].map((t, i) => ({ shot_number: i + 1, time_from_beep: t }));
const ev = (id: string, kind: StageEvent["kind"], start: number, end: number, source: StageEvent["source"] = "manual"): StageEvent =>
  ({ id, kind, start, end, source });

function Harness(props: Partial<React.ComponentProps<typeof LaneEditor>> & { initial?: StageEvent[] }) {
  const [events, setEvents] = React.useState<StageEvent[]>(props.initial ?? []);
  const [selected, setSelected] = React.useState<string | null>(props.selectedId ?? null);
  return (
    <LaneEditor
      shots={SHOTS}
      events={events}
      stageTime={STAGE}
      fps={50}
      currentTime={0}
      selectedId={selected}
      onSelect={setSelected}
      onSeek={props.onSeek ?? vi.fn()}
      onChange={(next, commit) => {
        setEvents(next);
        props.onChange?.(next, commit);
      }}
      readOnly={props.readOnly}
    />
  );
}
import React from "react";

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: WIDTH, height: 32, left: 0, top: 0, right: WIDTH, bottom: 32, x: 0, y: 0, toJSON: () => ({}),
  });
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
  HTMLElement.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => vi.restoreAllMocks());

const lastCommit = (onChange: ReturnType<typeof vi.fn>) =>
  [...onChange.mock.calls].reverse().find((c) => c[1] === true)?.[0] as StageEvent[] | undefined;

describe("LaneEditor", () => {
  it("creates a region by dragging empty lane space and seeks the moving edge", () => {
    const onChange = vi.fn();
    const onSeek = vi.fn();
    render(<Harness onChange={onChange} onSeek={onSeek} />);
    const lane = screen.getByTestId("lane-reload");
    fireEvent.pointerDown(lane, { pointerId: 1, clientX: 400, clientY: 10, button: 0 });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 450, clientY: 10 });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 540, clientY: 10 });
    expect(onSeek).toHaveBeenLastCalledWith(expect.closeTo(5.4, 2));
    fireEvent.pointerUp(lane, { pointerId: 1, clientX: 540, clientY: 10 });
    const committed = lastCommit(onChange);
    expect(committed).toHaveLength(1);
    expect(committed![0]).toMatchObject({ id: "evt-1", kind: "reload", source: "manual" });
    expect(committed![0].start).toBeCloseTo(4.0, 2);
    expect(committed![0].end).toBeCloseTo(5.4, 2);
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("aria-selected", "true");
  });

  it("a press under the threshold is a click that seeks and does not create", () => {
    const onChange = vi.fn();
    const onSeek = vi.fn();
    render(<Harness onChange={onChange} onSeek={onSeek} />);
    const lane = screen.getByTestId("lane-movement");
    fireEvent.pointerDown(lane, { pointerId: 1, clientX: 300, clientY: 10, button: 0 });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 303, clientY: 10 });
    fireEvent.pointerUp(lane, { pointerId: 1, clientX: 303, clientY: 10 });
    expect(onChange).not.toHaveBeenCalled();
    expect(onSeek).toHaveBeenCalledWith(expect.closeTo(3.0, 2));
  });

  it("dragging the end handle resizes, seeks to the handle and snaps to a shot", () => {
    const onChange = vi.fn();
    const onSeek = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} onSeek={onSeek} />);
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 2, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 2, clientX: 596, clientY: 10 }); // 5.96 s, 4 px from shot at 6.0
    expect(onSeek).toHaveBeenLastCalledWith(expect.closeTo(6.0, 2));
    fireEvent.pointerUp(handle, { pointerId: 2, clientX: 596, clientY: 10 });
    expect(lastCommit(onChange)![0].end).toBeCloseTo(6.0, 3);
  });

  it("Alt skips the snap", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} />);
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 2, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 2, clientX: 596, clientY: 10, altKey: true });
    fireEvent.pointerUp(handle, { pointerId: 2, clientX: 596, clientY: 10, altKey: true });
    expect(lastCommit(onChange)![0].end).toBeCloseTo(5.96, 3);
  });

  it("clamps against a same-lane neighbour and never crosses its own other edge", () => {
    const onChange = vi.fn();
    render(
      <Harness initial={[ev("evt-1", "movement", 1, 3), ev("evt-2", "movement", 4, 6)]} selectedId="evt-1" onChange={onChange} />,
    );
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 3, clientX: 300, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 3, clientX: 550, clientY: 10, altKey: true });
    fireEvent.pointerUp(handle, { pointerId: 3, clientX: 550, clientY: 10, altKey: true });
    expect(lastCommit(onChange)![0].end).toBeCloseTo(4.0, 3);

    const start = screen.getByTestId("handle-evt-1-start");
    fireEvent.pointerDown(start, { pointerId: 4, clientX: 100, clientY: 10, button: 0 });
    fireEvent.pointerMove(start, { pointerId: 4, clientX: 900, clientY: 10, altKey: true });
    fireEvent.pointerUp(start, { pointerId: 4, clientX: 900, clientY: 10, altKey: true });
    const e = lastCommit(onChange)![0];
    expect(e.end - e.start).toBeCloseTo(0.05, 3);
    expect(e.end).toBeCloseTo(4.0, 3);
  });

  it("dragging the body moves the region and seeks the leading edge", () => {
    const onChange = vi.fn();
    const onSeek = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} onSeek={onSeek} />);
    const body = screen.getByTestId("event-evt-1");
    fireEvent.pointerDown(body, { pointerId: 5, clientX: 450, clientY: 10, button: 0 });
    fireEvent.pointerMove(body, { pointerId: 5, clientX: 480, clientY: 10, altKey: true });
    fireEvent.pointerUp(body, { pointerId: 5, clientX: 480, clientY: 10, altKey: true });
    const e = lastCommit(onChange)![0];
    expect(e.start).toBeCloseTo(4.3, 3);
    expect(e.end).toBeCloseTo(5.3, 3);
    expect(onSeek).toHaveBeenLastCalledWith(expect.closeTo(4.3, 2));
  });

  it("Escape restores the pre-drag region and commits nothing new", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} />);
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 6, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 6, clientX: 700, clientY: 10, altKey: true });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onChange.mock.calls.filter((c) => c[1] === true)).toHaveLength(0);
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-end", "5");
  });

  it("arrow keys nudge by a frame, Shift moves the end, Alt moves 100 ms, Delete removes", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} />);
    const root = screen.getByTestId("lane-editor");
    root.focus();
    fireEvent.keyDown(root, { key: "ArrowRight" });
    expect(lastCommit(onChange)![0].start).toBeCloseTo(4.02, 3); // 1/50 s
    fireEvent.keyDown(root, { key: "ArrowLeft", shiftKey: true });
    expect(lastCommit(onChange)![0].end).toBeCloseTo(4.98, 3);
    fireEvent.keyDown(root, { key: "ArrowRight", altKey: true });
    expect(lastCommit(onChange)![0].start).toBeCloseTo(4.12, 3);
    fireEvent.keyDown(root, { key: "Delete" });
    expect(lastCommit(onChange)).toEqual([]);
  });

  it("renders auto proposals dashed and marks a touched one manual", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5, "auto")]} selectedId="evt-1" onChange={onChange} />);
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-source", "auto");
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 7, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 7, clientX: 540, clientY: 10, altKey: true });
    fireEvent.pointerUp(handle, { pointerId: 7, clientX: 540, clientY: 10, altKey: true });
    expect(lastCommit(onChange)![0].source).toBe("manual");
  });

  it("shots inside a movement get the moving cap; read-only renders no handles and ignores drags", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "movement", 1.5, 3.5)]} readOnly onChange={onChange} />);
    expect(screen.getByTestId("shot-2")).toHaveAttribute("data-moving", "true");
    expect(screen.getByTestId("shot-1")).toHaveAttribute("data-moving", "false");
    expect(screen.queryByTestId("handle-evt-1-end")).toBeNull();
    const lane = screen.getByTestId("lane-reload");
    fireEvent.pointerDown(lane, { pointerId: 8, clientX: 400, clientY: 10, button: 0 });
    fireEvent.pointerMove(lane, { pointerId: 8, clientX: 600, clientY: 10 });
    fireEvent.pointerUp(lane, { pointerId: 8, clientX: 600, clientY: 10 });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("clicking the ruler seeks and draws the playhead at currentTime", () => {
    const onSeek = vi.fn();
    render(<Harness onSeek={onSeek} />);
    fireEvent.click(screen.getByTestId("lane-ruler"), { clientX: 250 });
    expect(onSeek).toHaveBeenCalledWith(expect.closeTo(2.5, 2));
    expect(screen.getByTestId("playhead")).toHaveStyle({ left: "0%" });
  });
});
