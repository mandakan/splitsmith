import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TransportDock } from "@/pages/compare/TransportDock";

const baseProps = {
  shooters: [],
  maxTime: 10,
  timeSinceBeep: 2,
  audioSlug: null,
  isPlaying: false,
  onTogglePlay: () => {},
  onScrub: () => {},
  onPickAudio: () => {},
  onCopyMoment: () => {},
};

describe("TransportDock moment support", () => {
  it("renders a labelled marker when momentT is set", () => {
    render(<TransportDock {...baseProps} momentT={4.32} />);
    expect(screen.getByLabelText(/moment at 4\.32s/i)).toBeTruthy();
  });

  it("renders no marker when momentT is absent", () => {
    render(<TransportDock {...baseProps} />);
    expect(screen.queryByLabelText(/moment at/i)).toBeNull();
  });

  it("fires onCopyMoment", () => {
    const onCopyMoment = vi.fn();
    render(<TransportDock {...baseProps} onCopyMoment={onCopyMoment} />);
    fireEvent.click(screen.getByRole("button", { name: /copy link at moment/i }));
    expect(onCopyMoment).toHaveBeenCalledTimes(1);
  });
});
