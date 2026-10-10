import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CoachIntervalClass, CoachMatchDistributions, CoachShot, StageEvent } from "@/lib/api";
import { timeBudget } from "@/lib/timeBudget";

import { CoachShotTable } from "./CoachShotTable";
import { EventCard } from "./EventCard";
import { EventList } from "./EventList";
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

const E = (id: string, kind: StageEvent["kind"], start: number, end: number, source: StageEvent["source"] = "manual"): StageEvent =>
  ({ id, kind, start, end, source });

describe("EventCard", () => {
  const events = [E("evt-1", "movement", 7.6, 9.16), E("evt-2", "reload", 8.05, 9.47)];

  it("shows start, end, duration, the enclosing movement and the exposed time for a reload", () => {
    render(<EventCard event={events[1]} events={events} onKind={vi.fn()} onKeep={vi.fn()} onDelete={vi.fn()} onDone={vi.fn()} />);
    const card = screen.getByRole("region", { name: "Region" });
    expect(within(card).getByText("8.05")).toBeInTheDocument();
    expect(within(card).getByText("9.47")).toBeInTheDocument();
    expect(within(card).getByText("1.42")).toBeInTheDocument();
    expect(within(card).getByText(/Movement 7\.60.9\.16/)).toBeInTheDocument();
    // Exposed: 9.47 - 9.16, unsigned and in ink (no amber).
    expect(within(card).getByText("Exposed")).toBeInTheDocument();
    expect(within(card).getByText("0.31")).toHaveClass("text-ink");
    expect(within(card).queryByText("+0.31")).toBeNull();
    expect(within(card).getByRole("button", { name: "Reload" })).toHaveAttribute("aria-pressed", "true");
  });

  it("a standing reload is exposed for its whole duration; a movement shows no exposed row", () => {
    const standing = [E("evt-2", "reload", 8.05, 9.47)];
    const { rerender } = render(<EventCard event={standing[0]} events={standing} onKind={vi.fn()} onKeep={vi.fn()} onDelete={vi.fn()} onDone={vi.fn()} />);
    const exposed = screen.getByText("Exposed").parentElement!;
    expect(within(exposed).getByText("1.42")).toHaveClass("text-ink");
    expect(screen.getByText("Standing")).toBeInTheDocument();
    rerender(<EventCard event={events[0]} events={events} onKind={vi.fn()} onKeep={vi.fn()} onDelete={vi.fn()} onDone={vi.fn()} />);
    expect(screen.queryByText("Exposed")).toBeNull();
    expect(screen.queryByText("During")).toBeNull();
  });

  it("gives the Manual source chip a neutral tick, not a budget hue", () => {
    render(<EventCard event={events[1]} events={events} onKind={vi.fn()} onKeep={vi.fn()} onDelete={vi.fn()} onDone={vi.fn()} />);
    const tick = screen.getByText("Manual").querySelector("[data-tick]")!;
    expect(tick).toHaveClass("bg-ink");
    expect(tick).not.toHaveClass("bg-manual");
  });

  it("changes kind, deletes and closes", () => {
    const onKind = vi.fn(); const onDelete = vi.fn(); const onDone = vi.fn();
    render(<EventCard event={events[1]} events={events} onKind={onKind} onKeep={vi.fn()} onDelete={onDelete} onDone={onDone} />);
    fireEvent.click(screen.getByRole("button", { name: "Activation" }));
    expect(onKind).toHaveBeenCalledWith("activation");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onDone).toHaveBeenCalled();
  });

  it("disables a kind whose lane the region would overlap", () => {
    render(<EventCard event={events[1]} events={events} onKind={vi.fn()} onKeep={vi.fn()} onDelete={vi.fn()} onDone={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Movement" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Activation" })).toBeEnabled();
  });

  it("offers Keep left of Delete on an auto proposal only", () => {
    const onKeep = vi.fn();
    const auto = [E("evt-3", "reload", 13.3, 15.9, "auto")];
    const { rerender } = render(<EventCard event={auto[0]} events={auto} onKind={vi.fn()} onKeep={onKeep} onDelete={vi.fn()} onDone={vi.fn()} />);
    const buttons = within(screen.getByRole("region", { name: "Region" })).getAllByRole("button").map((b) => b.textContent);
    expect(buttons.indexOf("Keep")).toBeGreaterThanOrEqual(0);
    expect(buttons.indexOf("Keep")).toBeLessThan(buttons.indexOf("Delete"));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(onKeep).toHaveBeenCalledTimes(1);
    rerender(<EventCard event={events[1]} events={events} onKind={vi.fn()} onKeep={onKeep} onDelete={vi.fn()} onDone={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Keep" })).toBeNull();
  });

  it("names an auto proposal as such", () => {
    const auto = [E("evt-3", "reload", 13.3, 15.9, "auto")];
    render(<EventCard event={auto[0]} events={auto} onKind={vi.fn()} onKeep={vi.fn()} onDelete={vi.fn()} onDone={vi.fn()} />);
    expect(screen.getByText("Proposed")).toBeInTheDocument();
  });
});

describe("EventList", () => {
  it("lists one row per region with range or duration, moving-shot count and exposed time", () => {
    const events = [E("evt-1", "movement", 3.4, 6.1), E("evt-2", "movement", 7.6, 9.16), E("evt-3", "reload", 8.05, 9.47)];
    const shots = [4.35, 4.71, 5.12, 5.48, 10.6].map((t) => ({ time_from_beep: t }));
    render(<EventList events={events} shots={shots} />);
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent(/3\.40.6\.10/);
    expect(rows[0]).toHaveTextContent("4 shots");
    expect(rows[2]).toHaveTextContent("1.42");
    expect(rows[2]).toHaveTextContent("0.31 exposed");
    expect(rows[2]).not.toHaveTextContent("+0.31");
  });

  it("shows a standing reload's whole duration as exposed", () => {
    render(<EventList events={[E("evt-1", "reload", 8.05, 9.47)]} shots={[]} />);
    expect(screen.getByRole("listitem")).toHaveTextContent("1.42 exposed");
  });

  it("renders nothing for an empty list", () => {
    const { container } = render(<EventList events={[]} shots={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
