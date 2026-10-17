import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EdgePad } from "@/components/breakdown/mobile/EdgePad";
import { FocusStrip } from "@/components/breakdown/mobile/FocusStrip";
import type { StageEvent } from "@/lib/api";

beforeEach(() => {
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.releasePointerCapture = vi.fn();
});

describe("EdgePad", () => {
  function pad() {
    const onStart = vi.fn();
    const onMove = vi.fn();
    const onEnd = vi.fn();
    const onCancel = vi.fn();
    render(<EdgePad onStart={onStart} onMove={onMove} onEnd={onEnd} onCancel={onCancel} label="Drag to move the start" />);
    const el = screen.getByTestId("edge-pad");
    Object.defineProperty(el, "clientWidth", { value: 300 });
    return { el, onStart, onMove, onEnd, onCancel };
  }
  it("reports the finger's travel as a share of its width", () => {
    const { el, onStart, onMove, onEnd } = pad();
    fireEvent.pointerDown(el, { pointerId: 1, clientX: 100 });
    expect(onStart).toHaveBeenCalledTimes(1);
    fireEvent.pointerMove(el, { pointerId: 1, clientX: 175 });
    expect(onMove).toHaveBeenLastCalledWith(0.25, 300);
    fireEvent.pointerUp(el, { pointerId: 1, clientX: 175 });
    expect(onEnd).toHaveBeenCalledTimes(1);
  });
  it("cancels on pointercancel and never ends", () => {
    const { el, onEnd, onCancel } = pad();
    fireEvent.pointerDown(el, { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(el, { pointerId: 1, clientX: 140 });
    fireEvent.pointerCancel(el, { pointerId: 1 });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onEnd).not.toHaveBeenCalled();
  });
  it("ignores a second finger", () => {
    const { el, onMove } = pad();
    fireEvent.pointerDown(el, { pointerId: 1, clientX: 100 });
    fireEvent.pointerDown(el, { pointerId: 2, clientX: 200 });
    fireEvent.pointerMove(el, { pointerId: 2, clientX: 250 });
    expect(onMove).not.toHaveBeenCalled();
  });
});

describe("FocusStrip", () => {
  const region: StageEvent = { id: "r", kind: "reload", start: 8, end: 9.4, source: "auto" };
  it("draws the region with two 44 px handles that open edge mode", () => {
    const onEdge = vi.fn();
    render(
      <FocusStrip
        window={{ from: 6, to: 11.4 }}
        events={[region]}
        region={region}
        shots={[{ shot_number: 7, time_from_beep: 9.5 }]}
        tFromBeep={8.5}
        readOnly={false}
        activeEdge={null}
        onEdge={onEdge}
      />,
    );
    const start = screen.getByRole("button", { name: "Adjust start, 8.00 s" });
    expect(start.className).toContain("w-11");
    expect(start.className).toContain("h-12");
    fireEvent.click(start);
    expect(onEdge).toHaveBeenCalledWith("start");
    fireEvent.click(screen.getByRole("button", { name: "Adjust end, 9.40 s" }));
    expect(onEdge).toHaveBeenLastCalledWith("end");
  });
  it("drags a handle: start, travel over the strip's width, end; never a tap", () => {
    const onEdge = vi.fn();
    const drag = { start: vi.fn(), move: vi.fn(), end: vi.fn(), cancel: vi.fn() };
    render(
      <FocusStrip
        window={{ from: 6, to: 11.4 }}
        events={[region]}
        region={region}
        shots={[]}
        tFromBeep={8}
        readOnly={false}
        activeEdge={null}
        onEdge={onEdge}
        onHandleDrag={drag}
      />,
    );
    Object.defineProperty(screen.getByTestId("focus-strip"), "clientWidth", { value: 300 });
    const end = screen.getByRole("button", { name: /Adjust end/ });
    fireEvent.pointerDown(end, { pointerId: 1, clientX: 200 });
    fireEvent.pointerMove(end, { pointerId: 1, clientX: 203 }); // under the threshold: still a tap
    expect(drag.start).not.toHaveBeenCalled();
    fireEvent.pointerMove(end, { pointerId: 1, clientX: 230 });
    expect(drag.start).toHaveBeenCalledWith("end");
    expect(drag.move).toHaveBeenLastCalledWith(0.1, 300);
    fireEvent.pointerUp(end, { pointerId: 1, clientX: 230 });
    expect(drag.end).toHaveBeenCalledTimes(1);
    fireEvent.click(end); // the click a moved press still fires is not a tap
    expect(onEdge).not.toHaveBeenCalled();
    fireEvent.pointerDown(end, { pointerId: 2, clientX: 200 });
    fireEvent.pointerCancel(end, { pointerId: 2 });
    expect(drag.cancel).not.toHaveBeenCalled(); // nothing started, nothing to cancel
  });
  it("labels the ruler sparsely enough to read on a phone", () => {
    const { container } = render(
      <FocusStrip
        window={{ from: 1.64, to: 8.86 }}
        events={[region]}
        region={region}
        shots={[]}
        tFromBeep={8}
        readOnly
        activeEdge={null}
        onEdge={vi.fn()}
      />,
    );
    const labels = Array.from(container.querySelectorAll("[data-ruler-label]")).map((el) => el.textContent);
    expect(labels).toEqual(["2", "4", "6", "8"]);
  });
  it("has no handles when read-only", () => {
    render(
      <FocusStrip
        window={{ from: 6, to: 11.4 }}
        events={[region]}
        region={region}
        shots={[]}
        tFromBeep={8}
        readOnly
        activeEdge={null}
        onEdge={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /Adjust/ })).toBeNull();
  });
});
