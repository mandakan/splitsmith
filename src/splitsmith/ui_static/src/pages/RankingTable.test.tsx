import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CompareShooterRecord, CompareShotPoint } from "@/lib/api";

import { RankingTable } from "./Compare";

function shooter(
  slug: string,
  name: string,
  stageTime: number | null,
  shots: { t: number; c: CompareShotPoint["interval_class"] }[],
): CompareShooterRecord {
  return {
    slug,
    name,
    video_path: null,
    beep_offset_in_clip: null,
    duration_seconds: null,
    stage_time_seconds: stageTime,
    shots: shots.map((s, i) => ({
      shot_number: i + 1,
      time_after_beep: s.t,
      source: "detected",
      interval_class: s.c,
    })),
  };
}

describe("RankingTable", () => {
  it("excludes non-split intervals from Fastest/Avg on a classified stage", () => {
    render(
      <RankingTable
        shooters={[
          shooter("anna", "Anna", 12.3, [
            { t: 1.5, c: "first_shot" },
            { t: 1.8, c: "split" },
            { t: 4.4, c: "reload" },
            { t: 4.7, c: "split" },
          ]),
        ]}
      />,
    );
    // Draw from the first shot; stats over split-classed gaps only -
    // the 2.6s reload no longer poses as data.
    expect(screen.getByText("Draw")).toBeInTheDocument();
    expect(screen.getByText("1.50s")).toBeInTheDocument();
    expect(screen.getAllByText("0.300s")).toHaveLength(2); // Fastest + Avg
  });

  it("falls back to the threshold rule when unclassified", () => {
    render(
      <RankingTable
        shooters={[
          shooter("bo", "Bo", 9.1, [
            { t: 2.0, c: null },
            { t: 2.4, c: null },
            { t: 3.0, c: null },
          ]),
        ]}
      />,
    );
    // Draw 2.00s; gaps 0.4 (counts) and 0.6 (over split_max, excluded).
    expect(screen.getByText("2.00s")).toBeInTheDocument();
    expect(screen.getAllByText("0.400s")).toHaveLength(2);
  });

  it("renders placeholders when no interval counts as a split", () => {
    render(
      <RankingTable
        shooters={[
          shooter("cy", "Cy", 20.0, [
            { t: 3.0, c: "first_shot" },
            { t: 8.0, c: "movement" },
          ]),
        ]}
      />,
    );
    expect(screen.getByText("3.00s")).toBeInTheDocument(); // Draw still shows
    expect(screen.getAllByText("-")).toHaveLength(2); // Fastest + Avg empty
  });
});
