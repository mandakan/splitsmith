import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CompareShooterRecord } from "@/lib/api";

import { timeFromTrackX, TransportDock } from "./TransportDock";

function shooter(
  slug: string,
  name: string,
  stageTime: number,
  shotTimes: number[],
): CompareShooterRecord {
  return {
    slug,
    name,
    stage_time_seconds: stageTime,
    beep_offset_in_clip: 0,
    video_ref: `trimmed/${slug}.mp4`,
    shots: shotTimes.map((t, i) => ({
      shot_number: i + 1,
      time_after_beep: t,
      source: i === 0 ? "manual" : "detected",
      interval_class: null,
    })),
  } as CompareShooterRecord;
}

const baseProps = {
  maxTime: 10,
  muted: new Set<string>(),
  allMuted: false,
  isPlaying: false,
  onTogglePlay: () => {},
  onScrub: () => {},
  onSpeaker: () => {},
  onToggleAll: () => {},
  onCopyMoment: () => {},
};

describe("timeFromTrackX", () => {
  it("maps pixels linearly and clamps to [0, maxTime]", () => {
    expect(timeFromTrackX(0, 1000, 20)).toBe(0);
    expect(timeFromTrackX(500, 1000, 20)).toBe(10);
    expect(timeFromTrackX(2000, 1000, 20)).toBe(20);
    expect(timeFromTrackX(-50, 1000, 20)).toBe(0);
    expect(timeFromTrackX(500, 0, 20)).toBe(0);
  });
});

describe("TransportDock", () => {
  it("renders fired shots solid and upcoming shots hollow", () => {
    render(
      <TransportDock
        {...baseProps}
        shooters={[shooter("a", "Fast Shooter", 9.5, [1.0, 2.0, 8.0])]}
        timeSinceBeep={5.0}
      />,
    );
    expect(screen.getByTestId("shot-a-1")).toHaveAttribute(
      "data-fired",
      "true",
    );
    expect(screen.getByTestId("shot-a-2")).toHaveAttribute(
      "data-fired",
      "true",
    );
    expect(screen.getByTestId("shot-a-3")).toHaveAttribute(
      "data-fired",
      "false",
    );
  });

  it("a lane mutes or unmutes its shooter, Alt-click hears only them", () => {
    const onSpeaker = vi.fn();
    render(
      <TransportDock
        {...baseProps}
        muted={new Set(["a"])}
        onSpeaker={onSpeaker}
        shooters={[
          shooter("a", "Fast Shooter", 9.5, [1.0]),
          shooter("b", "Slow Shooter", 9.9, [1.2]),
        ]}
        timeSinceBeep={0}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Mute Slow Shooter" }));
    expect(onSpeaker).toHaveBeenLastCalledWith("b", false);
    fireEvent.click(screen.getByRole("button", { name: "Unmute Fast Shooter" }), { altKey: true });
    expect(onSpeaker).toHaveBeenLastCalledWith("a", true);
    expect(screen.getByRole("button", { name: "Unmute Fast Shooter" })).toHaveAttribute("aria-pressed", "false");
  });

  it("one button mutes or unmutes everyone", () => {
    const onToggleAll = vi.fn();
    const { rerender } = render(<TransportDock {...baseProps} shooters={[]} timeSinceBeep={0} onToggleAll={onToggleAll} />);
    fireEvent.click(screen.getByRole("button", { name: "Mute all" }));
    expect(onToggleAll).toHaveBeenCalledTimes(1);
    rerender(<TransportDock {...baseProps} shooters={[]} timeSinceBeep={0} allMuted onToggleAll={onToggleAll} />);
    expect(screen.getByRole("button", { name: "Unmute all" })).toBeInTheDocument();
  });

  it("scrubs via the range slider", () => {
    const onScrub = vi.fn();
    render(
      <TransportDock
        {...baseProps}
        onScrub={onScrub}
        shooters={[shooter("a", "Fast Shooter", 9.5, [1.0])]}
        timeSinceBeep={0}
      />,
    );
    fireEvent.change(screen.getByRole("slider"), { target: { value: "4.5" } });
    expect(onScrub).toHaveBeenCalledWith(4.5);
  });

  it("thins ruler ticks on long stages", () => {
    const { container: container120 } = render(
      <TransportDock
        {...baseProps}
        maxTime={120}
        shooters={[shooter("a", "Fast Shooter", 9.5, [1.0])]}
        timeSinceBeep={0}
      />,
    );
    expect(
      container120.querySelectorAll("[data-ruler-tick]").length,
    ).toBe(25);

    const { container: container10 } = render(
      <TransportDock
        {...baseProps}
        maxTime={10}
        shooters={[shooter("a", "Fast Shooter", 9.5, [1.0])]}
        timeSinceBeep={0}
      />,
    );
    expect(container10.querySelectorAll("[data-ruler-tick]").length).toBe(11);
  });
});
