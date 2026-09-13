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
  it("refuses to shrink inside a scroll column (the desktop clip, spec 7.1)", () => {
    const { container } = render(
      <StageStats stageTime={32.09} shotCount={30} draw={1.97} fastestSplit={0.249} avgSplit={0.386} />,
    );
    // jsdom has no layout; the class is the contract. ResultsStage mounts
    // this strip first in a `flex flex-col lg:overflow-y-auto` column, and
    // an overflow-hidden flex child with the default shrink collapses to
    // its labels there.
    expect(container.firstElementChild).toHaveClass("shrink-0");
  });

  it("leads with the stage time on a full row so five tiles never leave an orphan", () => {
    render(
      <StageStats stageTime={32.09} shotCount={30} draw={1.97} fastestSplit={0.249} avgSplit={0.386} />,
    );
    const stageTime = screen.getByText("Stage time").parentElement;
    const avgSplit = screen.getByText("Avg split").parentElement;
    expect(stageTime).toHaveClass("col-span-2");
    expect(stageTime).toHaveClass("md:col-span-1");
    expect(avgSplit).not.toHaveClass("col-span-2");
  });
});
