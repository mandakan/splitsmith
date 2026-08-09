import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StageStats } from "./StageStats";

describe("StageStats", () => {
  it("shows draw alongside the split statistics", () => {
    render(
      <StageStats
        stageTime={12.34}
        shotCount={4}
        draw={1.5}
        fastestSplit={0.2}
        avgSplit={0.3}
      />,
    );
    expect(screen.getByText("Draw")).toBeInTheDocument();
    expect(screen.getByText("1.50s")).toBeInTheDocument();
    expect(screen.getByText("0.200s")).toBeInTheDocument();
    expect(screen.getByText("0.300s")).toBeInTheDocument();
  });

  it("renders placeholders, never zeros, when figures are absent", () => {
    render(
      <StageStats
        stageTime={null}
        shotCount={0}
        draw={null}
        fastestSplit={null}
        avgSplit={null}
      />,
    );
    // One "-" per absent figure: stage time, draw, fastest, avg.
    expect(screen.getAllByText("-")).toHaveLength(4);
  });
});
