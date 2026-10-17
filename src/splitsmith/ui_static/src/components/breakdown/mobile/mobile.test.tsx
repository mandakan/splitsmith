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
    const onMove = vi.fn();
    const onEnd = vi.fn();
    const onCancel = vi.fn();
    render(<EdgePad onMove={onMove} onEnd={onEnd} onCancel={onCancel} label="Drag to move the start" />);
    const el = screen.getByTestId("edge-pad");
    Object.defineProperty(el, "clientWidth", { value: 300 });
    return { el, onMove, onEnd, onCancel };
  }
  it("reports the finger's travel as a share of its width", () => {
    const { el, onMove, onEnd } = pad();
    fireEvent.pointerDown(el, { pointerId: 1, clientX: 100 });
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
