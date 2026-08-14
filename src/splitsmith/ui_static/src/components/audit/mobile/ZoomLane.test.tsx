import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";
import { ZoomLane } from "@/components/audit/mobile/ZoomLane";

const marker = (over: Partial<AuditMarker>): AuditMarker => ({
  id: "cand-1",
  kind: "detected",
  time: 5.0,
  candidateNumber: 1,
  confidence: 0.9,
  peakAmplitude: null,
  note: "",
  ...over,
});

function renderLane(over: Partial<Parameters<typeof ZoomLane>[0]> = {}) {
  const props = {
    peaks: Array.from({ length: 880 }, () => 0.5),
    duration: 44,
    rows: 11,
    playhead: 5.0,
    zoom: 3 as const,
    onZoomChange: vi.fn(),
    markers: [] as AuditMarker[],
    targetId: null,
    onTap: vi.fn(),
    onGrabStart: vi.fn(),
    onJog: vi.fn(),
    onGrabEnd: vi.fn(),
    ...over,
  };
  render(<ZoomLane {...props} />);
  return props;
}

describe("ZoomLane", () => {
  it("renders the dashed target band and the pinned playhead", () => {
    renderLane();
    expect(screen.getByTestId("target-band")).toBeInTheDocument();
    expect(screen.getByTestId("lane-playhead")).toBeInTheDocument();
  });

  it("shows a rejected candidate only inside the band", () => {
    renderLane({
      markers: [
        marker({ id: "cand-7", kind: "rejected", time: 5.05, candidateNumber: 7, confidence: 0.1 }),
        marker({ id: "cand-8", kind: "rejected", time: 5.5, candidateNumber: 8, confidence: 0.1 }),
      ],
    });
    expect(document.querySelector('[data-marker-id="cand-7"]')).not.toBeNull();
    expect(document.querySelector('[data-marker-id="cand-8"]')).toBeNull();
  });

  it("kept markers render across the whole window", () => {
    renderLane({ markers: [marker({ time: 5.5 })] });
    expect(document.querySelector('[data-marker-id="cand-1"]')).not.toBeNull();
  });

  it("zoom chips call onZoomChange and mark the active factor", () => {
    const props = renderLane();
    fireEvent.click(screen.getByRole("button", { name: "5x" }));
    expect(props.onZoomChange).toHaveBeenCalledWith(5);
    expect(screen.getByRole("button", { name: "3x" })).toHaveAttribute("aria-pressed", "true");
  });

  it("dragging jogs the playhead against the drag direction", () => {
    const props = renderLane();
    const lane = screen.getByTestId("zoom-lane");
    lane.getBoundingClientRect = () =>
      ({ left: 0, width: 400, top: 0, height: 80 }) as DOMRect;
    fireEvent.pointerDown(lane, { clientX: 200, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(lane, { clientX: 300, clientY: 10, pointerId: 1 });
    expect(props.onGrabStart).toHaveBeenCalledTimes(1);
    // window = 44/11/3 = 1.333 s over 400 px; +100 px drag moves time back 0.333 s
    expect(props.onJog).toHaveBeenCalledWith(expect.closeTo(5.0 - 0.333, 2));
    fireEvent.pointerUp(lane, { clientX: 300, clientY: 10, pointerId: 1 });
    expect(props.onGrabEnd).toHaveBeenCalledTimes(1);
  });

  it("pointerCancel clears gesture and does not fire onTap", () => {
    const props = renderLane();
    const lane = screen.getByTestId("zoom-lane");
    fireEvent.pointerDown(lane, { clientX: 100, clientY: 10, pointerId: 1 });
    fireEvent.pointerCancel(lane, { clientX: 100, clientY: 10, pointerId: 1 });
    expect(props.onTap).not.toHaveBeenCalled();
    expect(props.onGrabEnd).not.toHaveBeenCalled();
  });

  it("pointerCancel after grab fires onGrabEnd exactly once", () => {
    const props = renderLane();
    const lane = screen.getByTestId("zoom-lane");
    lane.getBoundingClientRect = () =>
      ({ left: 0, width: 400, top: 0, height: 80 }) as DOMRect;
    fireEvent.pointerDown(lane, { clientX: 200, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(lane, { clientX: 300, clientY: 10, pointerId: 1 });
    expect(props.onGrabStart).toHaveBeenCalledTimes(1);
    fireEvent.pointerCancel(lane, { clientX: 300, clientY: 10, pointerId: 1 });
    expect(props.onGrabEnd).toHaveBeenCalledTimes(1);
    expect(props.onTap).not.toHaveBeenCalled();
  });

  it("ignores a second pointer while a gesture is active", () => {
    const props = renderLane();
    const lane = screen.getByTestId("zoom-lane");
    lane.getBoundingClientRect = () =>
      ({ left: 0, width: 400, top: 0, height: 80 }) as DOMRect;
    fireEvent.pointerDown(lane, { clientX: 200, clientY: 10, pointerId: 1 });
    fireEvent.pointerDown(lane, { clientX: 250, clientY: 10, pointerId: 2 });
    fireEvent.pointerMove(lane, { clientX: 300, clientY: 10, pointerId: 1 });
    expect(props.onGrabStart).toHaveBeenCalledTimes(1);
    // pointerId: 2 down should be ignored, so a move with pointerId: 2 has no effect
    fireEvent.pointerMove(lane, { clientX: 350, clientY: 10, pointerId: 2 });
    expect(props.onJog).toHaveBeenCalledTimes(1); // only the pointerId: 1 move
    fireEvent.pointerUp(lane, { clientX: 300, clientY: 10, pointerId: 1 });
    expect(props.onGrabEnd).toHaveBeenCalledTimes(1);
  });
});
