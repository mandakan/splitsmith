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

  it("draws a sound under its marker on every row, whatever the bin count", () => {
    // A stage's 1,200 bins over 51.59 s do not split evenly into 11 rows:
    // cut by bin count, each row's wave started a bin later than its
    // markers, 0.39 s late by the last row.
    const duration = 51.59;
    const n = 1200;
    const loud = 1100; // in row 10
    const wave = Array.from({ length: n }, (_, i) => (i === loud ? 1 : 0.01));
    const t = ((loud + 0.5) / n) * duration;
    renderRows({ peaks: wave, duration, markers: [marker({ time: t })] });
    const row = screen.getAllByTestId("wave-row")[10];
    const line = row.querySelector('[data-marker-id="cand-1"]')!;
    const bar = [...row.querySelectorAll("rect")].find((r) => Number(r.getAttribute("height")) > 50)!;
    const barMid = Number(bar.getAttribute("x")) + Number(bar.getAttribute("width")) / 2;
    // Within half a bin (a row is 1000 units over 4.69 s; a bin is 43 ms).
    expect(Math.abs(barMid - Number(line.getAttribute("x1")))).toBeLessThan(6);
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

  it("gutter label presses are ignored", () => {
    const props = renderRows();
    // Get the outer row container which has the label as a child
    const rowContainer = document.querySelector('[data-testid="wave-row"]')?.parentElement;
    if (!rowContainer) throw new Error("Row container not found");
    const label = rowContainer.querySelector("span");
    if (!label) throw new Error("Label not found");
    label.getBoundingClientRect = () =>
      ({ left: 0, width: 50, top: 0, height: 40 }) as DOMRect;
    fireEvent.pointerDown(label, { clientX: 25, clientY: 10, pointerId: 1 });
    fireEvent.pointerUp(label, { clientX: 26, clientY: 10, pointerId: 1 });
    expect(props.onTap).not.toHaveBeenCalled();
    expect(props.onGrabStart).not.toHaveBeenCalled();
  });

  it("pointerCancel on a non-grabbed gesture fires no onTap", () => {
    const props = renderRows();
    const row = screen.getAllByTestId("wave-row")[0];
    row.getBoundingClientRect = () =>
      ({ left: 0, width: 100, top: 0, height: 40 }) as DOMRect;
    fireEvent.pointerDown(row, { clientX: 50, clientY: 10, pointerId: 1 });
    fireEvent.pointerCancel(row, { clientX: 51, clientY: 10, pointerId: 1 });
    expect(props.onTap).not.toHaveBeenCalled();
    expect(props.onGrabStart).not.toHaveBeenCalled();
    expect(props.onGrabEnd).not.toHaveBeenCalled();
  });

  it("pointerCancel on a grabbed gesture fires onGrabEnd but not onTap", () => {
    const props = renderRows();
    const row = screen.getAllByTestId("wave-row")[0];
    row.getBoundingClientRect = () =>
      ({ left: 0, width: 100, top: 0, height: 40 }) as DOMRect;
    fireEvent.pointerDown(row, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(row, { clientX: 30, clientY: 10, pointerId: 1 });
    fireEvent.pointerCancel(row, { clientX: 30, clientY: 10, pointerId: 1 });
    expect(props.onGrabEnd).toHaveBeenCalledTimes(1);
    expect(props.onTap).not.toHaveBeenCalled();
  });

  it("second concurrent pointer is ignored", () => {
    const props = renderRows();
    const row = screen.getAllByTestId("wave-row")[0];
    row.getBoundingClientRect = () =>
      ({ left: 0, width: 100, top: 0, height: 40 }) as DOMRect;
    // Start first pointer grab
    fireEvent.pointerDown(row, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(row, { clientX: 30, clientY: 10, pointerId: 1 });
    expect(props.onGrabStart).toHaveBeenCalledTimes(1);
    // Try to start second pointer mid-grab
    fireEvent.pointerDown(row, { clientX: 50, clientY: 10, pointerId: 2 });
    fireEvent.pointerMove(row, { clientX: 70, clientY: 10, pointerId: 2 });
    // onGrabStart should still be 1 (second pointer ignored)
    expect(props.onGrabStart).toHaveBeenCalledTimes(1);
    // Release first pointer
    fireEvent.pointerUp(row, { clientX: 30, clientY: 10, pointerId: 1 });
    expect(props.onGrabEnd).toHaveBeenCalledTimes(1);
    // onTap/onScrub from second pointer should not fire
    expect(props.onTap).not.toHaveBeenCalled();
  });
});
