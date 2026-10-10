import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StageEvent } from "@/lib/api";
import { validateLanes } from "@/lib/events";

import { LaneEditor, LaneHints } from "./LaneEditor";

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
      onCancel={props.onCancel}
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
  // jsdom has no scrollIntoView.
  HTMLElement.prototype.scrollIntoView = vi.fn();
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

  it("a body dragged left past a neighbour stops at the neighbour's end", () => {
    const onChange = vi.fn();
    render(
      <Harness initial={[ev("evt-1", "movement", 1, 3), ev("evt-2", "movement", 4, 5)]} selectedId="evt-2" onChange={onChange} />,
    );
    const body = screen.getByTestId("event-evt-2");
    fireEvent.pointerDown(body, { pointerId: 9, clientX: 400, clientY: 10, button: 0 });
    fireEvent.pointerMove(body, { pointerId: 9, clientX: 150, clientY: 10, altKey: true });
    fireEvent.pointerUp(body, { pointerId: 9, clientX: 150, clientY: 10, altKey: true });
    const e = lastCommit(onChange)!.find((x) => x.id === "evt-2")!;
    expect(e.start).toBeCloseTo(3.0, 3);
    expect(e.end).toBeCloseTo(4.0, 3);
  });

  it("a body dragged right past a neighbour stops at the neighbour's start", () => {
    const onChange = vi.fn();
    render(
      <Harness initial={[ev("evt-1", "movement", 1, 2), ev("evt-2", "movement", 4, 6)]} selectedId="evt-1" onChange={onChange} />,
    );
    const body = screen.getByTestId("event-evt-1");
    fireEvent.pointerDown(body, { pointerId: 10, clientX: 100, clientY: 10, button: 0 });
    fireEvent.pointerMove(body, { pointerId: 10, clientX: 550, clientY: 10, altKey: true });
    fireEvent.pointerUp(body, { pointerId: 10, clientX: 550, clientY: 10, altKey: true });
    const e = lastCommit(onChange)!.find((x) => x.id === "evt-1")!;
    expect(e.end).toBeCloseTo(4.0, 3);
    expect(e.start).toBeCloseTo(3.0, 3);
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
    const region = screen.getByTestId("event-evt-1");
    act(() => region.focus());
    fireEvent.keyDown(region, { key: "ArrowRight" });
    expect(lastCommit(onChange)![0].start).toBeCloseTo(4.02, 3); // 1/50 s
    fireEvent.keyDown(region, { key: "ArrowLeft", shiftKey: true });
    expect(lastCommit(onChange)![0].end).toBeCloseTo(4.98, 3);
    fireEvent.keyDown(region, { key: "ArrowRight", altKey: true });
    expect(lastCommit(onChange)![0].start).toBeCloseTo(4.12, 3);
    fireEvent.keyDown(region, { key: "Delete" });
    expect(lastCommit(onChange)).toEqual([]);
  });

  it("keys on anything but a region (the root, a handle) never nudge or delete", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} />);
    for (const el of [screen.getByTestId("lane-editor"), screen.getByTestId("lane-reload"), screen.getByTestId("handle-evt-1-end")]) {
      fireEvent.keyDown(el, { key: "ArrowRight" });
      fireEvent.keyDown(el, { key: "Delete" });
    }
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-end", "5");
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

  it("editable lanes refuse touch panning; read-only lanes leave it to the page", () => {
    // A touch pan mid-drag fires pointercancel, which undoes the drag (#1326).
    const { unmount } = render(<Harness />);
    for (const kind of ["movement", "reload", "activation"]) {
      expect(screen.getByTestId(`lane-${kind}`)).toHaveClass("touch-none");
    }
    unmount();
    render(<Harness readOnly />);
    expect(screen.getByTestId("lane-reload")).not.toHaveClass("touch-none");
  });

  it("a press whose snap lands inside a same-lane neighbour anchors on the raw press, never overlapping it", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 6.02)]} onChange={onChange} />);
    const lane = screen.getByTestId("lane-reload");
    // 6.05 s is outside evt-1, but 5 px from the shot at 6.0, which is inside it.
    fireEvent.pointerDown(lane, { pointerId: 9, clientX: 605, clientY: 10, button: 0 });
    fireEvent.pointerMove(lane, { pointerId: 9, clientX: 640, clientY: 10 });
    fireEvent.pointerMove(lane, { pointerId: 9, clientX: 680, clientY: 10, altKey: true });
    fireEvent.pointerUp(lane, { pointerId: 9, clientX: 680, clientY: 10, altKey: true });
    const committed = lastCommit(onChange)!;
    expect(validateLanes(committed)).toBeNull();
    const created = committed.find((x) => x.id === "evt-2")!;
    expect(created.start).toBeCloseTo(6.05, 3);
    expect(created.end).toBeCloseTo(6.8, 3);
  });

  it("a click on empty lane space clears the selection", () => {
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" />);
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("aria-selected", "true");
    const lane = screen.getByTestId("lane-movement");
    fireEvent.pointerDown(lane, { pointerId: 10, clientX: 800, clientY: 10, button: 0 });
    fireEvent.pointerUp(lane, { pointerId: 10, clientX: 800, clientY: 10 });
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("aria-selected", "false");
  });

  it("a region being created stops at its same-lane neighbour", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "movement", 5, 6)]} onChange={onChange} />);
    const lane = screen.getByTestId("lane-movement");
    fireEvent.pointerDown(lane, { pointerId: 11, clientX: 400, clientY: 10, button: 0 });
    fireEvent.pointerMove(lane, { pointerId: 11, clientX: 450, clientY: 10, altKey: true });
    fireEvent.pointerMove(lane, { pointerId: 11, clientX: 850, clientY: 10, altKey: true });
    fireEvent.pointerUp(lane, { pointerId: 11, clientX: 850, clientY: 10, altKey: true });
    const created = lastCommit(onChange)!.find((x) => x.id === "evt-2")!;
    expect(created.start).toBeCloseTo(4.0, 3);
    expect(created.end).toBeCloseTo(5.0, 3);
  });

  it("pointercancel undoes the gesture like Escape", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} />);
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 12, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 12, clientX: 700, clientY: 10, altKey: true });
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-end", "7");
    fireEvent.pointerCancel(handle, { pointerId: 12 });
    expect(onChange.mock.calls.filter((c) => c[1] === true)).toHaveLength(0);
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-end", "5");
  });

  it("Escape and pointercancel end the live gesture after emitting the restored list (#1322)", () => {
    const calls: string[] = [];
    const onChange = vi.fn((_next: StageEvent[], commit: boolean) => calls.push(commit ? "commit" : "frame"));
    const onCancel = vi.fn(() => calls.push("cancel"));
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} onCancel={onCancel} />);
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 13, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 13, clientX: 700, clientY: 10, altKey: true });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(calls).toEqual(["frame", "frame", "cancel"]);
    fireEvent.pointerDown(handle, { pointerId: 14, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 14, clientX: 700, clientY: 10, altKey: true });
    fireEvent.pointerCancel(handle, { pointerId: 14 });
    expect(calls.slice(3)).toEqual(["frame", "frame", "cancel"]);
    // A release commits and is not a cancel.
    fireEvent.pointerDown(handle, { pointerId: 15, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 15, clientX: 700, clientY: 10, altKey: true });
    fireEvent.pointerUp(handle, { pointerId: 15, clientX: 700, clientY: 10, altKey: true });
    expect(calls.slice(6)).toEqual(["frame", "commit"]);
  });

  it("labels an auto proposal and not a manual region", () => {
    render(<Harness initial={[ev("evt-1", "reload", 4, 5, "auto"), ev("evt-2", "movement", 1, 2)]} />);
    expect(screen.getByTestId("auto-evt-1")).toHaveTextContent("Auto ?");
    expect(screen.queryByTestId("auto-evt-2")).toBeNull();
  });

  it("a time pill with the frame number follows the moving edge and goes on release", () => {
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" />);
    expect(screen.queryByTestId("drag-pill")).toBeNull();
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 13, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 13, clientX: 540, clientY: 10, altKey: true });
    const pill = screen.getByTestId("drag-pill");
    expect(pill).toHaveTextContent("5.40 s");
    expect(pill).toHaveTextContent("f 270"); // 5.4 s at 50 fps
    expect(pill).toHaveStyle({ left: "54%" });
    fireEvent.pointerUp(handle, { pointerId: 13, clientX: 540, clientY: 10, altKey: true });
    expect(screen.queryByTestId("drag-pill")).toBeNull();
  });

  it("maps a drag past the visible window through the content rect (zoomed and scrolled)", () => {
    // The strip is 4000 px wide (4x on a 1000 px viewport) and scrolled by 1500 px:
    // its rect starts at -1500. A pointer at clientX 1200 is content x 2700 -> 6.75 s of 10.
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 4000, height: 32, left: -1500, top: 0, right: 2500, bottom: 32, x: -1500, y: 0, toJSON: () => ({}),
    });
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const lane = screen.getByTestId("lane-movement");
    fireEvent.pointerDown(lane, { pointerId: 1, clientX: 100, clientY: 10, button: 0, altKey: true });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 600, clientY: 10, altKey: true });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 1200, clientY: 10, altKey: true });
    fireEvent.pointerUp(lane, { pointerId: 1, clientX: 1200, clientY: 10, altKey: true });
    const committed = lastCommit(onChange)!;
    expect(committed[0].start).toBeCloseTo(4.0, 2);
    expect(committed[0].end).toBeCloseTo(6.75, 2);
  });

  it("a body drag back to its start commits nothing and ends the live state (#1325)", () => {
    const onChange = vi.fn();
    const onCancel = vi.fn();
    render(
      <Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} onCancel={onCancel} />,
    );
    const body = screen.getByTestId("event-evt-1");
    fireEvent.pointerDown(body, { pointerId: 20, clientX: 450, clientY: 10, button: 0 });
    fireEvent.pointerMove(body, { pointerId: 20, clientX: 480, clientY: 10, altKey: true });
    fireEvent.pointerMove(body, { pointerId: 20, clientX: 450, clientY: 10, altKey: true });
    fireEvent.pointerUp(body, { pointerId: 20, clientX: 450, clientY: 10, altKey: true });
    expect(onChange.mock.calls.filter((c) => c[1] === true)).toHaveLength(0);
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-start", "4");
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-end", "5");
  });

  it("an edge drag back to its start commits nothing and ends the live state (#1325)", () => {
    const onChange = vi.fn();
    const onCancel = vi.fn();
    render(
      <Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} onCancel={onCancel} />,
    );
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 21, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 21, clientX: 700, clientY: 10, altKey: true });
    fireEvent.pointerMove(handle, { pointerId: 21, clientX: 500, clientY: 10, altKey: true });
    fireEvent.pointerUp(handle, { pointerId: 21, clientX: 500, clientY: 10, altKey: true });
    expect(onChange.mock.calls.filter((c) => c[1] === true)).toHaveLength(0);
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-end", "5");
  });

  it("a nudge into a neighbour commits nothing (#1325)", () => {
    const onChange = vi.fn();
    render(
      <Harness
        initial={[ev("evt-1", "movement", 0, 3), ev("evt-2", "movement", 3, 5)]}
        selectedId="evt-2"
        onChange={onChange}
      />,
    );
    const region = screen.getByTestId("event-evt-2");
    act(() => region.focus());
    fireEvent.keyDown(region, { key: "ArrowLeft" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("a nudge at the floor commits nothing (#1325)", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "movement", 0, 2)]} selectedId="evt-1" onChange={onChange} />);
    const region = screen.getByTestId("event-evt-1");
    act(() => region.focus());
    fireEvent.keyDown(region, { key: "ArrowLeft" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("a real nudge still commits once (#1325)", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} />);
    const region = screen.getByTestId("event-evt-1");
    act(() => region.focus());
    fireEvent.keyDown(region, { key: "ArrowRight" });
    expect(onChange.mock.calls.filter((c) => c[1] === true)).toHaveLength(1);
    expect(lastCommit(onChange)![0].start).toBeCloseTo(4.02, 3);
  });

  it("a body drag back to its start on an auto region commits nothing and stays auto (#1325)", () => {
    const onChange = vi.fn();
    const onCancel = vi.fn();
    render(
      <Harness
        initial={[ev("evt-1", "reload", 4, 5, "auto")]}
        selectedId="evt-1"
        onChange={onChange}
        onCancel={onCancel}
      />,
    );
    const body = screen.getByTestId("event-evt-1");
    fireEvent.pointerDown(body, { pointerId: 22, clientX: 450, clientY: 10, button: 0 });
    fireEvent.pointerMove(body, { pointerId: 22, clientX: 480, clientY: 10, altKey: true });
    fireEvent.pointerMove(body, { pointerId: 22, clientX: 450, clientY: 10, altKey: true });
    fireEvent.pointerUp(body, { pointerId: 22, clientX: 450, clientY: 10, altKey: true });
    expect(onChange.mock.calls.filter((c) => c[1] === true)).toHaveLength(0);
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-source", "auto");
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-start", "4");
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-end", "5");
  });

  it("a nudge blocked by a clamp on an auto region commits nothing and stays auto (#1325)", () => {
    const onChange = vi.fn();
    render(
      <Harness
        initial={[ev("evt-1", "movement", 0, 3), ev("evt-2", "movement", 3, 5, "auto")]}
        selectedId="evt-2"
        onChange={onChange}
      />,
    );
    const region = screen.getByTestId("event-evt-2");
    act(() => region.focus());
    fireEvent.keyDown(region, { key: "ArrowLeft" });
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByTestId("event-evt-2")).toHaveAttribute("data-source", "auto");
  });

  it("a real move of an auto region still commits and marks it manual (#1325)", () => {
    const onChange = vi.fn();
    render(
      <Harness initial={[ev("evt-1", "reload", 4, 5, "auto")]} selectedId="evt-1" onChange={onChange} />,
    );
    const body = screen.getByTestId("event-evt-1");
    fireEvent.pointerDown(body, { pointerId: 23, clientX: 450, clientY: 10, button: 0 });
    fireEvent.pointerMove(body, { pointerId: 23, clientX: 480, clientY: 10, altKey: true });
    fireEvent.pointerUp(body, { pointerId: 23, clientX: 480, clientY: 10, altKey: true });
    const committed = lastCommit(onChange)!;
    expect(committed).toHaveLength(1);
    expect(committed[0].source).toBe("manual");
    expect(committed[0].start).toBeCloseTo(4.3, 3);
    expect(committed[0].end).toBeCloseTo(5.3, 3);
  });

  it("a create that collapses to nothing commits nothing (#1325)", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const lane = screen.getByTestId("lane-reload");
    // Threshold is measured in screen pixels, not time: a purely vertical
    // move crosses it with zero change in the time under the pointer, so
    // the create's moving edge never clears MIN_EVENT_S from its anchor.
    fireEvent.pointerDown(lane, { pointerId: 24, clientX: 400, clientY: 10, button: 0 });
    fireEvent.pointerMove(lane, { pointerId: 24, clientX: 400, clientY: 25 });
    fireEvent.pointerUp(lane, { pointerId: 24, clientX: 400, clientY: 25 });
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByTestId("event-evt-1")).toBeNull();
  });

  describe("keyboard and screen reader (#1327)", () => {
    const MIXED = [
      ev("evt-1", "movement", 5, 6),
      ev("evt-2", "movement", 1, 2.5),
      ev("evt-3", "reload", 8.05, 9.47, "auto"),
      ev("evt-4", "reload", 3, 4),
    ];

    it("the root is a named group holding one named listbox per lane", () => {
      render(<Harness initial={MIXED} />);
      expect(screen.getByRole("group", { name: "Region lanes" })).toBe(screen.getByTestId("lane-editor"));
      expect(screen.getAllByRole("listbox").map((l) => l.getAttribute("aria-label"))).toEqual([
        "Movement regions",
        "Reload regions",
        "Activation regions",
      ]);
      const reload = screen.getByRole("listbox", { name: "Reload regions" });
      expect(within(reload).getAllByRole("option")).toHaveLength(2);
      expect(within(screen.getByRole("listbox", { name: "Activation regions" })).queryAllByRole("option")).toEqual([]);
    });

    it("an option reads out its kind, start, end, length and whether it is a proposal", () => {
      render(<Harness initial={MIXED} />);
      expect(screen.getByRole("option", { name: "Reload 8.05 to 9.47, 1.42 seconds, proposed" })).toBe(
        screen.getByTestId("event-evt-3"),
      );
      expect(screen.getByRole("option", { name: "Movement 1.00 to 2.50, 1.50 seconds, confirmed" })).toBe(
        screen.getByTestId("event-evt-2"),
      );
    });

    it("options within a lane are in time order", () => {
      render(<Harness initial={MIXED} />);
      const movement = screen.getByRole("listbox", { name: "Movement regions" });
      expect(within(movement).getAllByRole("option").map((o) => o.dataset.testid)).toEqual(["event-evt-2", "event-evt-1"]);
    });

    it("Tab stops once per lane: the selected region, else the first in time; an empty lane is skipped", async () => {
      const user = userEvent.setup();
      render(<Harness initial={MIXED} selectedId="evt-3" />);
      expect(screen.getAllByRole("option").filter((o) => o.tabIndex === 0).map((o) => o.dataset.testid)).toEqual([
        "event-evt-2",
        "event-evt-3",
      ]);
      // Backwards from the page end: activation is empty, so the reload lane is the last stop.
      await user.tab({ shift: true });
      expect(screen.getByTestId("event-evt-3")).toHaveFocus(); // the selected one, though not first in time
      await user.tab({ shift: true });
      expect(screen.getByTestId("event-evt-2")).toHaveFocus(); // first in time, though listed second
      expect(screen.getByTestId("event-evt-2")).toHaveAttribute("aria-selected", "true");
      // Focus selected evt-2, so the reload lane's stop is now its first region.
      await user.tab();
      expect(screen.getByTestId("event-evt-4")).toHaveFocus();
      await user.tab();
      expect(document.body).toHaveFocus();
    });

    it("focusing a region selects it", () => {
      render(<Harness initial={MIXED} />);
      const region = screen.getByTestId("event-evt-4");
      act(() => region.focus());
      expect(region).toHaveAttribute("aria-selected", "true");
      expect(region).toHaveAttribute("tabindex", "0");
    });

    it("ArrowRight on the focused region commits one nudge of that region", () => {
      const onChange = vi.fn();
      render(<Harness initial={MIXED} onChange={onChange} />);
      const region = screen.getByTestId("event-evt-4");
      act(() => region.focus());
      fireEvent.keyDown(region, { key: "ArrowRight" });
      const commits = onChange.mock.calls.filter((c) => c[1] === true);
      expect(commits).toHaveLength(1);
      expect(commits[0][0].find((x: StageEvent) => x.id === "evt-4").start).toBeCloseTo(3.02, 3);
    });

    it("a nudge blocked by a neighbour on the focused region commits nothing", () => {
      const onChange = vi.fn();
      render(<Harness initial={[ev("evt-1", "reload", 2, 3), ev("evt-2", "reload", 3, 4)]} onChange={onChange} />);
      const region = screen.getByTestId("event-evt-2");
      act(() => region.focus());
      fireEvent.keyDown(region, { key: "ArrowLeft" });
      expect(onChange).not.toHaveBeenCalled();
    });

    it("ArrowDown / ArrowUp move focus and selection through the lane in time; Home / End jump", () => {
      const onChange = vi.fn();
      render(
        <Harness
          initial={[ev("evt-1", "reload", 6, 7), ev("evt-2", "reload", 1, 2), ev("evt-3", "reload", 3, 4), ev("evt-4", "movement", 2, 5)]}
          onChange={onChange}
        />,
      );
      const at = (id: string) => screen.getByTestId(`event-${id}`);
      act(() => at("evt-2").focus());
      fireEvent.keyDown(at("evt-2"), { key: "ArrowDown" });
      expect(at("evt-3")).toHaveFocus();
      expect(at("evt-3")).toHaveAttribute("aria-selected", "true");
      expect(at("evt-2")).toHaveAttribute("aria-selected", "false");
      fireEvent.keyDown(at("evt-3"), { key: "ArrowDown" });
      expect(at("evt-1")).toHaveFocus();
      fireEvent.keyDown(at("evt-1"), { key: "ArrowDown" }); // last in lane: stays, never crosses lanes
      expect(at("evt-1")).toHaveFocus();
      fireEvent.keyDown(at("evt-1"), { key: "ArrowUp" });
      expect(at("evt-3")).toHaveFocus();
      fireEvent.keyDown(at("evt-3"), { key: "Home" });
      expect(at("evt-2")).toHaveFocus();
      fireEvent.keyDown(at("evt-2"), { key: "End" });
      expect(at("evt-1")).toHaveFocus();
      expect(at("evt-1")).toHaveAttribute("aria-selected", "true");
      expect(onChange).not.toHaveBeenCalled();
    });

    it("Delete removes the focused region and moves focus to its neighbour in the lane", () => {
      const onChange = vi.fn();
      render(<Harness initial={[ev("evt-1", "reload", 1, 2), ev("evt-2", "reload", 3, 4), ev("evt-3", "reload", 6, 7)]} onChange={onChange} />);
      const at = (id: string) => screen.getByTestId(`event-${id}`);
      act(() => at("evt-2").focus());
      fireEvent.keyDown(at("evt-2"), { key: "Delete" });
      expect(lastCommit(onChange)!.map((x) => x.id)).toEqual(["evt-1", "evt-3"]);
      expect(screen.queryByTestId("event-evt-2")).toBeNull();
      expect(at("evt-3")).toHaveFocus(); // the next one in time
      fireEvent.keyDown(at("evt-3"), { key: "Enter" });
      fireEvent.keyDown(at("evt-3"), { key: "Backspace" });
      expect(at("evt-1")).toHaveFocus(); // none after: the previous one
    });

    it("a Delete leaves nothing selected, though focus moved to the neighbour", () => {
      render(<Harness initial={[ev("evt-1", "reload", 1, 2), ev("evt-2", "reload", 3, 4)]} />);
      const at = (id: string) => screen.getByTestId(`event-${id}`);
      act(() => at("evt-1").focus());
      fireEvent.keyDown(at("evt-1"), { key: "Delete" });
      expect(at("evt-2")).toHaveFocus();
      expect(screen.getAllByRole("option").filter((o) => o.getAttribute("aria-selected") === "true")).toEqual([]);
    });

    it("a second Delete right after the first removes nothing", () => {
      const onChange = vi.fn();
      render(<Harness initial={[ev("evt-1", "reload", 1, 2), ev("evt-2", "reload", 3, 4)]} onChange={onChange} />);
      const region = screen.getByTestId("event-evt-1");
      act(() => region.focus());
      fireEvent.keyDown(region, { key: "Delete" });
      fireEvent.keyDown(document.activeElement!, { key: "Delete" });
      expect(onChange.mock.calls.filter((c) => c[1] === true)).toHaveLength(1);
      expect(screen.getByTestId("event-evt-2")).toBeInTheDocument();
    });

    it("a held Delete (auto-repeat) removes exactly one region in the whole stage", () => {
      const onChange = vi.fn();
      render(
        <Harness
          initial={[ev("evt-1", "reload", 1, 2), ev("evt-2", "reload", 3, 4), ev("evt-3", "movement", 1, 5), ev("evt-4", "activation", 0, 1)]}
          onChange={onChange}
        />,
      );
      const region = screen.getByTestId("event-evt-1");
      act(() => region.focus());
      fireEvent.keyDown(region, { key: "Delete" });
      for (let n = 0; n < 6; n += 1) fireEvent.keyDown(document.activeElement!, { key: "Delete", repeat: true });
      expect(onChange.mock.calls.filter((c) => c[1] === true)).toHaveLength(1);
      expect(screen.getAllByRole("option").map((o) => o.dataset.testid)).toEqual(["event-evt-3", "event-evt-2", "event-evt-4"]);
    });

    it("an arrow on a focused but unselected region nudges nothing until Enter selects it", () => {
      const onChange = vi.fn();
      render(<Harness initial={[ev("evt-1", "reload", 1, 2), ev("evt-2", "reload", 3, 4, "auto")]} onChange={onChange} />);
      const at = (id: string) => screen.getByTestId(`event-${id}`);
      act(() => at("evt-1").focus());
      fireEvent.keyDown(at("evt-1"), { key: "Delete" });
      expect(at("evt-2")).toHaveFocus();
      onChange.mockClear();
      fireEvent.keyDown(at("evt-2"), { key: "ArrowRight" });
      fireEvent.keyDown(at("evt-2"), { key: "ArrowLeft", shiftKey: true });
      expect(onChange).not.toHaveBeenCalled();
      expect(at("evt-2")).toHaveAttribute("data-source", "auto");
      fireEvent.keyDown(at("evt-2"), { key: "Enter" });
      fireEvent.keyDown(at("evt-2"), { key: "ArrowRight" });
      expect(onChange.mock.calls.filter((c) => c[1] === true)).toHaveLength(1);
      expect(lastCommit(onChange)![0].start).toBeCloseTo(3.02, 3);
    });

    it("a repeat keydown never deletes, even on the selected region", () => {
      const onChange = vi.fn();
      render(<Harness initial={[ev("evt-1", "reload", 1, 2)]} selectedId="evt-1" onChange={onChange} />);
      const region = screen.getByTestId("event-evt-1");
      act(() => region.focus());
      fireEvent.keyDown(region, { key: "Delete", repeat: true });
      expect(onChange).not.toHaveBeenCalled();
    });

    it("Enter or Space selects the focused region, and then Delete removes it", () => {
      const onChange = vi.fn();
      render(<Harness initial={[ev("evt-1", "reload", 1, 2), ev("evt-2", "reload", 3, 4), ev("evt-3", "reload", 5, 6)]} onChange={onChange} />);
      const at = (id: string) => screen.getByTestId(`event-${id}`);
      act(() => at("evt-1").focus());
      fireEvent.keyDown(at("evt-1"), { key: "Delete" });
      expect(at("evt-2")).toHaveFocus();
      fireEvent.keyDown(at("evt-2"), { key: "Enter" });
      expect(at("evt-2")).toHaveAttribute("aria-selected", "true");
      fireEvent.keyDown(at("evt-2"), { key: "Delete" });
      expect(lastCommit(onChange)!.map((x) => x.id)).toEqual(["evt-3"]);
      expect(at("evt-3")).toHaveFocus();
      fireEvent.keyDown(at("evt-3"), { key: " " });
      expect(at("evt-3")).toHaveAttribute("aria-selected", "true");
    });

    it("deleting a lane's last region parks focus on the editor, never hopping to another lane", () => {
      render(<Harness initial={[ev("evt-1", "reload", 1, 2), ev("evt-2", "activation", 3, 4)]} />);
      act(() => screen.getByTestId("event-evt-1").focus());
      fireEvent.keyDown(screen.getByTestId("event-evt-1"), { key: "Delete" });
      expect(screen.getByTestId("lane-editor")).toHaveFocus();
      expect(screen.getByTestId("event-evt-2")).toHaveAttribute("aria-selected", "false");
    });

    it("a keyboard move scrolls the focused region into view in the zoomed band", () => {
      render(<Harness initial={[ev("evt-1", "reload", 1, 2), ev("evt-2", "reload", 8, 9)]} />);
      const first = screen.getByTestId("event-evt-1");
      act(() => first.focus());
      const scroll = vi.mocked(HTMLElement.prototype.scrollIntoView);
      scroll.mockClear();
      fireEvent.keyDown(first, { key: "ArrowDown" });
      expect(screen.getByTestId("event-evt-2")).toHaveFocus();
      expect(scroll).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
      expect(scroll.mock.contexts.at(-1)).toBe(screen.getByTestId("event-evt-2"));
    });

    it("a committed nudge is announced in a polite status region; a blocked one is not", () => {
      render(<Harness initial={[ev("evt-1", "reload", 4, 5), ev("evt-2", "reload", 5, 6)]} />);
      const status = screen.getByRole("status");
      expect(status).toHaveAttribute("aria-live", "polite");
      expect(status).toHaveTextContent(/^$/);
      const region = screen.getByTestId("event-evt-1");
      act(() => region.focus());
      fireEvent.keyDown(region, { key: "ArrowRight", shiftKey: true }); // end into evt-2: blocked
      expect(status).toHaveTextContent(/^$/);
      fireEvent.keyDown(region, { key: "ArrowRight", altKey: true });
      expect(status).toHaveTextContent("Reload 4.10 to 5.00, 0.90 seconds, confirmed");
    });

    it("the hints name Left/Right for nudges, Up/Down for moves and Enter for select", () => {
      render(<LaneHints />);
      const text = document.body.textContent ?? "";
      expect(text).toContain("Left/Right nudge a frame");
      expect(text).toContain("Up/Down moves between regions");
      expect(text).toContain("Enter selects");
      expect(text).not.toContain("Arrows");
    });

    it("read-only options are focusable and labelled, but arrows and Delete change nothing", () => {
      const onChange = vi.fn();
      render(<Harness initial={MIXED} readOnly onChange={onChange} />);
      const region = screen.getByRole("option", { name: "Reload 3.00 to 4.00, 1.00 seconds, confirmed" });
      expect(region).toHaveAttribute("tabindex", "0");
      act(() => region.focus());
      expect(region).toHaveFocus();
      fireEvent.keyDown(region, { key: "ArrowRight" });
      fireEvent.keyDown(region, { key: "ArrowLeft", shiftKey: true });
      fireEvent.keyDown(region, { key: "Delete" });
      fireEvent.keyDown(region, { key: "Backspace" });
      expect(onChange).not.toHaveBeenCalled();
      expect(screen.getByTestId("event-evt-4")).toBeInTheDocument();
    });

    it("clicking a region selects and focuses it", () => {
      render(<Harness initial={MIXED} readOnly />);
      fireEvent.click(screen.getByTestId("event-evt-1"));
      expect(screen.getByTestId("event-evt-1")).toHaveFocus();
      expect(screen.getByTestId("event-evt-1")).toHaveAttribute("aria-selected", "true");
    });

    it("a body or edge drag focuses the dragged region", () => {
      render(<Harness initial={[ev("evt-1", "reload", 4, 5), ev("evt-2", "movement", 1, 2)]} />);
      const body = screen.getByTestId("event-evt-1");
      fireEvent.pointerDown(body, { pointerId: 30, clientX: 450, clientY: 10, button: 0 });
      expect(body).toHaveFocus();
      fireEvent.pointerUp(body, { pointerId: 30, clientX: 450, clientY: 10 });
      const handle = screen.getByTestId("handle-evt-2-end");
      fireEvent.pointerDown(handle, { pointerId: 31, clientX: 200, clientY: 10, button: 0 });
      expect(screen.getByTestId("event-evt-2")).toHaveFocus();
      fireEvent.pointerUp(handle, { pointerId: 31, clientX: 200, clientY: 10 });
    });

    it("a committed create focuses the new region", () => {
      render(<Harness />);
      const lane = screen.getByTestId("lane-reload");
      fireEvent.pointerDown(lane, { pointerId: 32, clientX: 400, clientY: 10, button: 0 });
      fireEvent.pointerMove(lane, { pointerId: 32, clientX: 450, clientY: 10 });
      fireEvent.pointerMove(lane, { pointerId: 32, clientX: 540, clientY: 10 });
      fireEvent.pointerUp(lane, { pointerId: 32, clientX: 540, clientY: 10 });
      expect(screen.getByTestId("event-evt-1")).toHaveFocus();
    });

    // Not distinguishable from the pre-#1327 code (an empty-lane click cleared
    // the selection there too); it guards the new focus rule: without the
    // create press parking focus on the root, the region keeps focus and the
    // arrow nudges it (checked by mutation).
    it("a click on empty lane space takes focus off a region, so an arrow then nudges nothing", () => {
      const onChange = vi.fn();
      render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} onChange={onChange} />);
      const region = screen.getByTestId("event-evt-1");
      act(() => region.focus());
      const lane = screen.getByTestId("lane-movement");
      fireEvent.pointerDown(lane, { pointerId: 33, clientX: 800, clientY: 10, button: 0 });
      fireEvent.pointerUp(lane, { pointerId: 33, clientX: 800, clientY: 10 });
      expect(region).not.toHaveFocus();
      fireEvent.keyDown(document.activeElement ?? document.body, { key: "ArrowRight" });
      expect(onChange).not.toHaveBeenCalled();
    });
  });
});
