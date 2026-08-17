/**
 * The dropdown's vocabulary follows truth, not kept -- the same rule as
 * the keyboard shortcuts in DevFixtureDetail. The regression this pins:
 * the dropdown used to gate the subclass list on ``kept && truth``, so
 * an FN row (rejected, truth=1 -- a real shot the ensemble dropped)
 * offered the FP reason list, which is unanswerable for a real shot.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LabEvalFixture } from "@/lib/api";

import { LabelDropdown } from "./LabelDropdown";

function candidate(
  over: Partial<LabEvalFixture["candidates"][number]> = {},
): LabEvalFixture["candidates"][number] {
  return {
    candidate_number: 5,
    time: 7.119,
    ms_after_beep: 2440,
    confidence: 0.287,
    peak_amplitude: 0.5,
    score_c: 0.4,
    clap_diff: 0.1,
    gunshot_prob: 0.6,
    vote_a: 1,
    vote_b: 1,
    vote_c: 1,
    vote_total: 3,
    apriori_boost: 0,
    ensemble_score: 4,
    kept: false,
    truth: 0,
    matched_shot_number: null,
    reason: null,
    subclass: null,
    ...over,
  };
}

function optionLabels(): string[] {
  return screen
    .getAllByRole("option")
    .map((o) => o.textContent ?? "")
    .filter(Boolean);
}

describe("LabelDropdown", () => {
  it("offers the subclass list for an FN (rejected but truth-positive)", () => {
    render(
      <LabelDropdown candidate={candidate({ kept: false, truth: 1 })} onChange={vi.fn()} saving={false} />,
    );
    const labels = optionLabels();
    expect(labels).toContain("paper");
    expect(labels).not.toContain("cross_bay");
  });

  it("offers the subclass list for a TP", () => {
    render(
      <LabelDropdown candidate={candidate({ kept: true, truth: 1 })} onChange={vi.fn()} saving={false} />,
    );
    expect(optionLabels()).toContain("steel");
  });

  it("offers the reason list for non-shots, kept or not", () => {
    for (const kept of [true, false]) {
      const { unmount } = render(
        <LabelDropdown candidate={candidate({ kept, truth: 0 })} onChange={vi.fn()} saving={false} />,
      );
      const labels = optionLabels();
      expect(labels).toContain("cross_bay");
      expect(labels).not.toContain("paper");
      unmount();
    }
  });
});
