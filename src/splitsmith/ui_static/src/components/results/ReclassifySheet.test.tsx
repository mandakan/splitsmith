import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ReclassifySheet } from "@/components/results/ReclassifySheet";
import type { CoachShot } from "@/lib/api";

function shot(overrides: Partial<CoachShot> = {}): CoachShot {
  return {
    shot_number: 5,
    ms_after_beep: 2100,
    time_from_beep: 2.1,
    time_absolute: 4.1,
    split: 0.61,
    interval_class: "split",
    interval_class_source: "auto",
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
    ...overrides,
  };
}

describe("ReclassifySheet", () => {
  it("renders nothing when shot is null", () => {
    render(<ReclassifySheet shot={null} busy={false} onApply={() => {}} onCancel={() => {}} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers all six classes with the current one pre-selected", () => {
    render(<ReclassifySheet shot={shot()} busy={false} onApply={() => {}} onCancel={() => {}} />);
    expect(screen.getAllByRole("radio")).toHaveLength(6);
    expect(screen.getByRole("radio", { name: "Fire" })).toBeChecked();
  });

  it("applying a new class emits a manual-override patch", () => {
    const onApply = vi.fn();
    render(<ReclassifySheet shot={shot()} busy={false} onApply={onApply} onCancel={() => {}} />);
    fireEvent.click(screen.getByRole("radio", { name: "Movement" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ shot_number: 5 }), {
      interval_class: "movement",
      interval_class_source: "manual",
    });
  });

  it("applying with nothing changed just cancels", () => {
    const onApply = vi.fn();
    const onCancel = vi.fn();
    render(<ReclassifySheet shot={shot()} busy={false} onApply={onApply} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("a note edit rides the patch", () => {
    const onApply = vi.fn();
    render(<ReclassifySheet shot={shot()} busy={false} onApply={onApply} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText(/note/i), { target: { value: "slow entry" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith(expect.anything(), { coaching_note: "slow entry" });
  });
});
