import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CompareShooterRecord } from "@/lib/api";

import { LeaderboardRail } from "./LeaderboardRail";

function shooter(
  slug: string,
  name: string,
  stageTime: number | null,
  shotTimes: number[],
): CompareShooterRecord {
  return {
    slug,
    name,
    stage_time_seconds: stageTime,
    beep_offset_in_clip: 0,
    video_ref: `trimmed/${slug}.mp4`,
    shots: shotTimes.map((t, i) => ({
      shot_number: i + 1,
      time_after_beep: t,
      source: "detected",
      interval_class: null,
    })),
  } as CompareShooterRecord;
}

describe("LeaderboardRail", () => {
  it("ranks by stage time and shows the delta to the leader", () => {
    render(
      <LeaderboardRail
        shooters={[
          shooter("b", "Slow Shooter", 15.08, [1.31, 1.62, 15.08]),
          shooter("a", "Fast Shooter", 14.32, [1.18, 1.46, 14.32]),
        ]}
      />,
    );
    const names = screen
      .getAllByTestId("rail-name")
      .map((el) => el.textContent);
    expect(names).toEqual(["Fast Shooter", "Slow Shooter"]);
    expect(screen.getByText("+0.76s")).toBeInTheDocument();
  });

  it("computes draw, fastest and avg split from statistic splits", () => {
    // 0.28 and 0.31 are shot splits; the 9.0 -> 14.32 gap is movement and
    // must be excluded by statisticSplits (behavior under test, #774).
    render(
      <LeaderboardRail
        shooters={[
          shooter("a", "Fast Shooter", 14.32, [1.18, 1.46, 1.77, 9.0, 14.32]),
        ]}
      />,
    );
    expect(screen.getByTestId("rail-draw")).toHaveTextContent("1.18");
    expect(screen.getByTestId("rail-fast")).toHaveTextContent("0.280");
  });

  it("renders dashes for a shooter with no shots", () => {
    render(
      <LeaderboardRail shooters={[shooter("a", "Empty Shooter", null, [])]} />,
    );
    expect(screen.getByTestId("rail-draw")).toHaveTextContent("-");
    expect(screen.getByTestId("rail-fast")).toHaveTextContent("-");
    expect(screen.getByTestId("rail-avg")).toHaveTextContent("-");
  });
});
