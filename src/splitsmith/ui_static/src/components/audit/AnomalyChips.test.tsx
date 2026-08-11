import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AnomalyChips } from "./AnomalyChips";

const anomaly = {
  kind: "long_pause" as const,
  severity: "warn" as const,
  message: "Long pause after shot 3",
  shot_number: 3,
  time: 5.2,
};

describe("AnomalyChips", () => {
  it("renders chips with onJump handler and they are clickable", () => {
    const onJump = vi.fn();
    render(<AnomalyChips anomalies={[anomaly]} onJump={onJump} />);
    expect(screen.getByText(/long pause/i)).toBeInTheDocument();
    const button = screen.getByRole("button");
    expect(button).toBeInTheDocument();
  });

  it("chips render without onJump and are not clickable", () => {
    render(<AnomalyChips anomalies={[anomaly]} />);
    expect(screen.getByText(/long pause/i)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders each chip as a direct child of the strip, no wrapper div", () => {
    const onJump = vi.fn();
    const anomalies = [
      anomaly,
      { ...anomaly, kind: "shot_count_high" as const, shot_number: 4, message: "Extra shot after 4" },
      { ...anomaly, kind: "long_pause" as const, shot_number: 5, message: "Long pause after shot 5" },
    ];
    const { container } = render(<AnomalyChips anomalies={anomalies} onJump={onJump} />);
    const strip = container.firstElementChild as HTMLElement;
    // First child is the "Anomalies" label; every remaining direct child
    // is a chip - one per anomaly, with nothing wrapping it.
    const chipChildren = Array.from(strip.children).slice(1);
    expect(chipChildren).toHaveLength(anomalies.length);
    expect(chipChildren.every((el) => el.tagName === "BUTTON")).toBe(true);
  });
});
