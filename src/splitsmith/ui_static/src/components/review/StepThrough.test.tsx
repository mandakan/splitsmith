import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";
import { StepThrough } from "@/components/review/StepThrough";

function peaksWithShotsAt(feet: number[]): { peaks: number[]; duration: number } {
  const peaks = new Array<number>(1000).fill(0.001);
  for (const foot of feet) {
    const start = Math.round(foot * 1000);
    peaks[start] = 0.3;
    peaks[start + 1] = 0.8;
    peaks[start + 2] = 1.0;
    for (let i = 3; i < 30; i++) peaks[start + i] = 1.0 - i * 0.03;
  }
  return { peaks, duration: 1 };
}

const marker = (id: string, time: number): AuditMarker => ({
  id,
  kind: "detected",
  time,
  candidateNumber: null,
  confidence: null,
  peakAmplitude: null,
  note: "",
});

function setup(markers: AuditMarker[]) {
  const props = {
    onSetTime: vi.fn(),
    onReject: vi.fn(),
    onRecord: vi.fn(),
    onFocus: vi.fn(),
    onDone: vi.fn(),
    onExit: vi.fn(),
  };
  const pageKeys = vi.fn();
  window.addEventListener("keydown", pageKeys);
  const view = render(
    <StepThrough markers={markers} peaks={peaksWithShotsAt([0.2, 0.6])} busy={false} {...props} />,
  );
  return { ...props, pageKeys, view, cleanup: () => window.removeEventListener("keydown", pageKeys) };
}

describe("StepThrough", () => {
  it("steps only the disagreeing shots and takes the rise foot on Enter", () => {
    const s = setup([marker("ok", 0.202), marker("late", 0.615)]);
    expect(screen.getByText("1 / 1 to check")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(s.onSetTime).toHaveBeenCalledWith("late", 0.6);
    expect(s.onRecord).toHaveBeenCalledWith("step_rise_foot_taken", {
      id: "late",
      from_time: 0.615,
      to_time: 0.6,
    });
    expect(screen.getByText(/All 1 disagreements decided/)).toBeTruthy();
    s.cleanup();
  });

  it("keeps Space and the arrows from the page's own handlers", () => {
    const s = setup([marker("late", 0.615)]);
    fireEvent.keyDown(document.body, { key: "ArrowLeft" });
    expect(s.onSetTime).toHaveBeenCalledWith("late", 0.614);
    fireEvent.keyDown(document.body, { key: " " });
    expect(s.onRecord).toHaveBeenCalledWith("step_time_kept", { id: "late", time: 0.615, rise_foot: 0.6 });
    expect(s.pageKeys).not.toHaveBeenCalled();
    s.cleanup();
  });

  it("signs off with Enter once every shot is decided", () => {
    const s = setup([marker("late", 0.615)]);
    fireEvent.keyDown(document.body, { key: "x" });
    expect(s.onReject).toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(s.onDone).toHaveBeenCalledTimes(1);
    s.cleanup();
  });

  it("goes straight to the sign-off when nothing disagrees", () => {
    const s = setup([marker("ok", 0.202)]);
    expect(screen.getByText(/No shot is more than 5 ms from its rise foot/)).toBeTruthy();
    s.cleanup();
  });
});
