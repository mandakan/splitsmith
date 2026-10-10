import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CoachShot, StageEvent } from "@/lib/api";

import { StageStrip } from "./StageStrip";

function shot(n: number, t: number, cls: CoachShot["interval_class"]): CoachShot {
  return {
    id: `c${n}`,
    shot_number: n,
    ms_after_beep: t * 1000,
    time_from_beep: t,
    time_absolute: t + 5,
    split: 0,
    interval_class: cls,
    interval_class_source: "auto",
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
  };
}

const SHOTS = [shot(1, 2, "first_shot"), shot(2, 5, "split"), shot(3, 8, "movement")];
const EVENTS: StageEvent[] = [
  { id: "m", kind: "movement", start: 3, end: 7, source: "manual" },
  { id: "r", kind: "reload", start: 4, end: 5, source: "manual" },
  { id: "p", kind: "reload", start: 8.5, end: 9, source: "auto" },
];

function renderStrip(onSeek = vi.fn(), active: number | null = 2) {
  const view = render(
    <StageStrip shots={SHOTS} events={EVENTS} stageTime={10} tFromBeep={5} activeShotNumber={active} onSeek={onSeek} />,
  );
  const track = screen.getByTestId("stage-strip");
  track.getBoundingClientRect = () => ({ left: 100, width: 1000, top: 0, height: 48, right: 1100, bottom: 48, x: 100, y: 0, toJSON: () => ({}) });
  return { ...view, track, onSeek };
}

describe("StageStrip", () => {
  it("is a labelled group of shot buttons with one tab stop, the current shot", () => {
    renderStrip();
    const group = screen.getByRole("group", { name: "Stage strip" });
    const buttons = within(group).getAllByRole("button");
    expect(buttons.map((b) => b.getAttribute("aria-label"))).toEqual([
      "Shot 01, 2.00 s, draw",
      "Shot 02, 5.00 s, fire",
      "Shot 03, 8.00 s, movement",
    ]);
    expect(buttons.map((b) => b.tabIndex)).toEqual([-1, 0, -1]);
    expect(buttons[1]).toHaveAttribute("aria-current", "true");
  });

  it("draws confirmed regions only, the reload after the movement so it lies over it", () => {
    const { container } = renderStrip();
    const bars = [...container.querySelectorAll("[data-strip-bar]")].map((b) => b.getAttribute("data-strip-bar"));
    expect(bars).toEqual(["movement", "reload"]);
  });

  it("puts the playhead at its time", () => {
    renderStrip();
    expect(screen.getByTestId("strip-playhead")).toHaveStyle({ left: "50%" });
  });

  it("a tap near a tick lands on the shot, elsewhere on the raw time", () => {
    const { track, onSeek } = renderStrip();
    fireEvent.click(track, { clientX: 100 + 806, detail: 1 });
    expect(onSeek).toHaveBeenLastCalledWith(8, 3);
    fireEvent.click(track, { clientX: 100 + 650, detail: 1 });
    expect(onSeek.mock.lastCall![0]).toBeCloseTo(6.5);
    expect(onSeek.mock.lastCall![1]).toBeNull();
  });

  it("Left / Right / Home / End move between shots and seek", () => {
    const { onSeek } = renderStrip();
    const current = screen.getByRole("button", { name: /Shot 02/ });
    fireEvent.keyDown(current, { key: "ArrowRight" });
    expect(onSeek).toHaveBeenLastCalledWith(8, 3);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /Shot 03/ }));
    fireEvent.keyDown(current, { key: "Home" });
    expect(onSeek).toHaveBeenLastCalledWith(2, 1);
  });

  it("activating a shot button from the keyboard seeks to it", () => {
    const { onSeek } = renderStrip(vi.fn(), null);
    fireEvent.click(screen.getByRole("button", { name: /Shot 03/ }));
    expect(onSeek).toHaveBeenCalledTimes(1);
    expect(onSeek).toHaveBeenCalledWith(8, 3);
  });
});
