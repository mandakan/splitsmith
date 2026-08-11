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
});
