import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CoachVideoEntry } from "@/lib/api";
import { CamPicker } from "@/components/results/CamPicker";

function entry(path: string, role: "primary" | "secondary", beep: number | null): CoachVideoEntry {
  return { path, role, beep_in_clip: beep };
}

const srcFor = (e: CoachVideoEntry) => `http://localhost/${e.path}`;

describe("CamPicker", () => {
  it("renders nothing for a single camera", () => {
    const { container } = render(
      <CamPicker entries={[entry("a.mp4", "primary", 5)]} activeIndex={0} onSelect={() => {}} srcFor={srcFor} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders one labelled button per camera and marks the active one", () => {
    render(
      <CamPicker
        entries={[entry("a.mp4", "primary", 5), entry("b.mp4", "secondary", 3)]}
        activeIndex={1}
        onSelect={() => {}}
        srcFor={srcFor}
      />,
    );
    const primary = screen.getByRole("button", { name: /camera 1 of 2/i });
    const cam2 = screen.getByRole("button", { name: /camera 2 of 2/i });
    expect(primary).toHaveAttribute("aria-pressed", "false");
    expect(cam2).toHaveAttribute("aria-pressed", "true");
  });

  it("selects on click and disables beepless cameras", () => {
    const onSelect = vi.fn();
    render(
      <CamPicker
        entries={[entry("a.mp4", "primary", 5), entry("b.mp4", "secondary", 3), entry("c.mp4", "secondary", null)]}
        activeIndex={0}
        onSelect={onSelect}
        srcFor={srcFor}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /camera 2 of 3/i }));
    expect(onSelect).toHaveBeenCalledWith(1);
    expect(screen.getByRole("button", { name: /camera 3 of 3/i })).toBeDisabled();
  });
});
