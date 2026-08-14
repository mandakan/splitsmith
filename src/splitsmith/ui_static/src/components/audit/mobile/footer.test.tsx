import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";
import { ActionArea } from "@/components/audit/mobile/ActionArea";
import { AuditTransport } from "@/components/audit/mobile/AuditTransport";

const marker = (over: Partial<AuditMarker>): AuditMarker => ({
  id: "cand-17",
  kind: "detected",
  time: 12.5,
  candidateNumber: 17,
  confidence: 0.8,
  peakAmplitude: null,
  note: "",
  ...over,
});

function renderArea(over: Partial<Parameters<typeof ActionArea>[0]> = {}) {
  const props = {
    target: { kind: "none" } as const,
    shotOrdinal: null,
    splitS: null,
    nudgeMs: 0,
    readOnly: false,
    onNudge: vi.fn(),
    onDeleteShot: vi.fn(),
    onShowVideo: vi.fn(),
    onPromote: vi.fn(),
    onAddShot: vi.fn(),
    ...over,
  };
  render(<ActionArea {...props} />);
  return props;
}

describe("ActionArea", () => {
  it("shot state names the shot and offers nudge, delete, video", () => {
    const props = renderArea({
      target: { kind: "shot", marker: marker({}) },
      shotOrdinal: { index: 17, total: 37 },
      splitS: 0.447,
      nudgeMs: 20,
    });
    expect(screen.getByText("shot 17/37 . 0.447 s . +20 ms")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "-10 ms" }));
    expect(props.onNudge).toHaveBeenCalledWith(-10);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(props.onDeleteShot).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Video" }));
    expect(props.onShowVideo).toHaveBeenCalled();
  });

  it("candidate state offers promote and shows confidence", () => {
    const props = renderArea({
      target: { kind: "candidate", marker: marker({ kind: "rejected", confidence: 0.1 }) },
    });
    expect(screen.getByText("rejected candidate . conf 0.10")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Promote candidate" }));
    expect(props.onPromote).toHaveBeenCalled();
  });

  it("empty state offers add at playhead", () => {
    const props = renderArea();
    expect(screen.getByText("no shot at playhead")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add shot at playhead" }));
    expect(props.onAddShot).toHaveBeenCalled();
  });

  it("read-only disables the mutating buttons and says so", () => {
    renderArea({ readOnly: true });
    expect(screen.getByText(/read-only/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add shot at playhead" })).toBeDisabled();
  });
});

describe("AuditTransport", () => {
  it("wires play, loop and speed", () => {
    const props = {
      playing: false,
      onPlayPause: vi.fn(),
      loopActive: false,
      onLoopToggle: vi.fn(),
      speed: 1 as const,
      onSpeedChange: vi.fn(),
    };
    render(<AuditTransport {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(props.onPlayPause).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Loop" }));
    expect(props.onLoopToggle).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "0.5x" }));
    expect(props.onSpeedChange).toHaveBeenCalledWith(0.5);
  });
});
