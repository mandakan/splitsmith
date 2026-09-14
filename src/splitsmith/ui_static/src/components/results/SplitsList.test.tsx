import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SplitsList } from "@/components/results/SplitsList";
import type { CoachShot } from "@/lib/api";

function shot(n: number, overrides: Partial<CoachShot> = {}): CoachShot {
  return {
    id: null,
    shot_number: n,
    ms_after_beep: n * 1000,
    time_from_beep: n,
    time_absolute: n + 2,
    split: 0.5,
    interval_class: "split",
    interval_class_source: "auto",
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
    ...overrides,
  };
}

describe("SplitsList", () => {
  it("without onReclassify the chip stays a non-interactive span (share contract)", () => {
    render(
      <SplitsList
        shots={[shot(1)]}
        activeShotNumber={null}
        onSeek={() => {}}
        isPlaying={false}
        baselines={null}
      />,
    );
    expect(screen.queryByRole("button", { name: /reclassify/i })).not.toBeInTheDocument();
    expect(screen.getByText("Fire")).toBeInTheDocument();
  });

  it("with onReclassify the chip is a button and does not trigger seek", () => {
    const onSeek = vi.fn();
    const onReclassify = vi.fn();
    render(
      <SplitsList
        shots={[shot(1)]}
        activeShotNumber={null}
        onSeek={onSeek}
        isPlaying={false}
        baselines={null}
        onReclassify={onReclassify}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Reclassify shot 1 (Fire)" }));
    expect(onReclassify).toHaveBeenCalledWith(expect.objectContaining({ shot_number: 1 }));
    expect(onSeek).not.toHaveBeenCalled();
  });

  it("an unclassified shot gets a Classify affordance only when interactive", () => {
    const unclassified = shot(2, { interval_class: null, interval_class_source: null });
    const { rerender } = render(
      <SplitsList
        shots={[unclassified]}
        activeShotNumber={null}
        onSeek={() => {}}
        isPlaying={false}
        baselines={null}
        onReclassify={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Reclassify shot 2 (unclassified)" })).toBeInTheDocument();
    rerender(
      <SplitsList
        shots={[unclassified]}
        activeShotNumber={null}
        onSeek={() => {}}
        isPlaying={false}
        baselines={null}
      />,
    );
    expect(screen.queryByRole("button", { name: /reclassify/i })).not.toBeInTheDocument();
  });

  it("row click still seeks", () => {
    const onSeek = vi.fn();
    render(
      <SplitsList
        shots={[shot(1)]}
        activeShotNumber={null}
        onSeek={onSeek}
        isPlaying={false}
        baselines={null}
        onReclassify={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "011.000.500" }));
    expect(onSeek).toHaveBeenCalledTimes(1);
  });

  it("the split numeral carries the tier colour when a baseline judges it", () => {
    const baselines = { split: { p25: 0.3, p75: 0.6, count: 8 } };
    render(
      <SplitsList
        shots={[shot(1, { split: 0.25 }), shot(2, { split: 0.5 }), shot(3, { split: 0.9 })]}
        activeShotNumber={null}
        onSeek={() => {}}
        isPlaying={false}
        baselines={baselines}
      />,
    );
    expect(screen.getByText("0.250")).toHaveClass("text-done");
    expect(screen.getByText("0.500")).toHaveClass("text-ink");
    expect(screen.getByText("0.900")).toHaveClass("text-live");
  });
});
