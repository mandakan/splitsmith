import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BeepReticle } from "./BeepReticle";

const WIDTH = 320;

function Harness({ initial = 20, onChange }: { initial?: number; onChange?: (t: number) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <BeepReticle
      peaks={new Array(3000).fill(0.2)}
      range={{ start: 10, end: 40 }}
      value={value}
      onChange={(t) => {
        setValue(t);
        onChange?.(t);
      }}
      markers={[{ time: 20, kind: "detected" }]}
      playhead={null}
      ariaLabel="Beep time"
    />
  );
}

const valueNow = () => Number(screen.getByRole("slider").getAttribute("aria-valuenow"));
const spanText = () => screen.getByText(/s across$/).textContent;

describe("BeepReticle", () => {
  beforeEach(() => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: WIDTH,
      height: 100,
      left: 0,
      top: 0,
      right: WIDTH,
      bottom: 100,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
  });
  afterEach(() => vi.restoreAllMocks());

  it("opens on an 8 s span centred on the value", () => {
    render(<Harness />);
    expect(spanText()).toBe("8.0 s across");
    expect(valueNow()).toBe(20);
  });

  it("dragging the waveform left moves later in time under the line", () => {
    render(<Harness />);
    const main = screen.getByTestId("reticle-main");
    fireEvent.pointerDown(main, { pointerId: 1, clientX: 200 });
    fireEvent.pointerMove(main, { pointerId: 1, clientX: 100 }); // 100 px at 40 px/s
    fireEvent.pointerMove(main, { pointerId: 1, clientX: 60 }); // builds on the last move
    fireEvent.pointerUp(main, { pointerId: 1 });
    expect(valueNow()).toBeCloseTo(23.5);
  });

  it("pans stop at the edges of the audio", () => {
    render(<Harness />);
    const main = screen.getByTestId("reticle-main");
    fireEvent.pointerDown(main, { pointerId: 1, clientX: 0 });
    fireEvent.pointerMove(main, { pointerId: 1, clientX: 5000 });
    expect(valueNow()).toBe(10);
  });

  it("pinching apart zooms in about the line", () => {
    render(<Harness />);
    const main = screen.getByTestId("reticle-main");
    fireEvent.pointerDown(main, { pointerId: 1, clientX: 140 });
    fireEvent.pointerDown(main, { pointerId: 2, clientX: 180 });
    fireEvent.pointerMove(main, { pointerId: 1, clientX: 120 });
    fireEvent.pointerMove(main, { pointerId: 2, clientX: 200 }); // 40 -> 80 px apart, midpoint back at 160
    expect(spanText()).toBe("4.0 s across");
    expect(valueNow()).toBeCloseTo(20);
  });

  it("tapping the overview jumps there, however far outside the view", () => {
    render(<Harness />);
    fireEvent.pointerDown(screen.getByTestId("reticle-overview"), { pointerId: 1, clientX: 288 }); // 90% of 10..40
    expect(valueNow()).toBeCloseTo(37);
  });

  it("zoom buttons stop at the whole clip and a quarter second", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Whole clip" }));
    expect(spanText()).toBe("30.0 s across");
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeDisabled();
    const zoomIn = screen.getByRole("button", { name: "Zoom in" });
    for (let i = 0; i < 20; i++) fireEvent.click(zoomIn);
    expect(spanText()).toBe("0.25 s across");
    expect(zoomIn).toBeDisabled();
  });

  it("arrow keys nudge 10 ms, shift 100 ms", () => {
    render(<Harness />);
    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(valueNow()).toBeCloseTo(20.01);
    fireEvent.keyDown(slider, { key: "ArrowLeft", shiftKey: true });
    expect(valueNow()).toBeCloseTo(19.91);
  });
});
