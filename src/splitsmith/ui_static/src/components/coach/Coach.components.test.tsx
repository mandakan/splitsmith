import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CoachIntervalClass, CoachMatchDistributions, CoachShot } from "@/lib/api";
import { timeBudget } from "@/lib/timeBudget";

import { CoachShotTable } from "./CoachShotTable";
import { ShotEditor } from "./ShotEditor";
import { TimeBudgetBar } from "./TimeBudgetBar";
import { TimeBudgetCard } from "./TimeBudgetCard";

function shot(n: number, t: number, split: number, cls: CoachIntervalClass | null, over: Partial<CoachShot> = {}): CoachShot {
  return { id: `c${n}`, shot_number: n, ms_after_beep: t * 1000, time_from_beep: t, time_absolute: t + 5, split, interval_class: cls, interval_class_source: cls ? "auto" : null, improvement_flag: false, coaching_note: null, stale: false, reload_hint: false, ...over };
}
const SHOTS = [shot(1, 1.97, 1.97, "first_shot"), shot(2, 3.28, 1.31, "movement"), shot(3, 4.21, 0.93, "transition"), shot(4, 8.0, 3.79, "movement", { coaching_note: "long run", improvement_flag: true })];
const DIST = { distributions: [{ interval_class: "movement", mean_s: 1.4, median_s: 1.4, count: 10 }] } as unknown as CoachMatchDistributions;

describe("TimeBudgetBar / TimeBudgetCard", () => {
  it("draws one segment per class and names the outlier as a button", () => {
    const b = timeBudget(SHOTS, DIST);
    const onSelect = vi.fn();
    render(<TimeBudgetCard budget={b} onSelectShot={onSelect} />);
    const bar = screen.getByRole("img");
    expect(bar.children).toHaveLength(3);
    expect(bar).toHaveAttribute("aria-label", expect.stringContaining("Movement 5.10 s"));
    expect(screen.getByText("8.00 s")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /shot 04/ }));
    expect(onSelect).toHaveBeenCalledWith(4);
    expect(screen.getByText("+1.15")).toHaveClass("text-live");
  });
  it("compact bars carry no labels and scale to the axis", () => {
    render(<TimeBudgetBar budget={timeBudget(SHOTS, null)} compact scale={0.5} />);
    const bar = screen.getByRole("img");
    expect(bar).toHaveStyle({ width: "50%" });
    expect(bar.textContent).toBe("");
  });
});

describe("ShotEditor", () => {
  it("presses the current class, writes a class on click, and makes Save primary only when the note is dirty", () => {
    const onClassify = vi.fn();
    const onSave = vi.fn();
    const { rerender } = render(
      <ShotEditor shot={SHOTS[1]} tier={null} noteDraft="" onNoteChange={vi.fn()} onSave={onSave} onClassify={onClassify} onToggleFlag={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Movement" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Transition" }));
    expect(onClassify).toHaveBeenCalledWith("transition");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    rerender(<ShotEditor shot={SHOTS[1]} tier={null} noteDraft="late" onNoteChange={vi.fn()} onSave={onSave} onClassify={onClassify} onToggleFlag={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalled();
  });
});

describe("CoachShotTable", () => {
  it("renders every shot with its chip and note, marks the active row, selects on click", () => {
    const onSelect = vi.fn();
    render(<CoachShotTable shots={SHOTS} activeShotNumber={2} baselines={null} onSelect={onSelect} />);
    const rows = within(screen.getByRole("region", { name: "Shots" })).getAllByRole("button");
    expect(rows).toHaveLength(4);
    expect(rows[1].className).toContain("inset_2px");
    expect(within(rows[3]).getByText("long run")).toBeInTheDocument();
    fireEvent.click(rows[2]);
    expect(onSelect).toHaveBeenCalledWith(SHOTS[2]);
  });
});
