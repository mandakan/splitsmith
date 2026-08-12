import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

import { ShotTicker } from "@/components/results/ShotTicker";
import type { CoachShot } from "@/lib/api";

// jsdom has no matchMedia; ShotTicker probes prefers-reduced-motion.
// matches: true also disables the pulse animation path in tests.
beforeAll(() => {
  window.matchMedia = ((query: string) => ({
    matches: true,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

const BEEP = 5;

function makeShot(n: number, timeFromBeep: number): CoachShot {
  return {
    id: null,
    shot_number: n,
    ms_after_beep: timeFromBeep * 1000,
    time_from_beep: timeFromBeep,
    time_absolute: BEEP + timeFromBeep,
    split: n === 1 ? timeFromBeep : 0.25,
    interval_class: null,
    interval_class_source: null,
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
  };
}

const SHOTS = [makeShot(1, 1.2), makeShot(2, 1.45)];

describe("ShotTicker elapsed clock", () => {
  it("tracks time between beep and last shot", () => {
    render(<ShotTicker shots={SHOTS} beepTime={BEEP} time={BEEP + 1.3} baselines={null} />);
    expect(screen.getByText("1.30")).toBeInTheDocument();
  });

  it("freezes at the stage time once past the last shot", () => {
    render(<ShotTicker shots={SHOTS} beepTime={BEEP} time={BEEP + 30} baselines={null} />);
    expect(screen.getByText("1.45")).toBeInTheDocument();
    expect(screen.queryByText("30.00")).not.toBeInTheDocument();
  });

  it("keeps counting when there are no shots to freeze on", () => {
    render(<ShotTicker shots={[]} beepTime={BEEP} time={BEEP + 3} baselines={null} />);
    expect(screen.getByText("3.00")).toBeInTheDocument();
  });
});
