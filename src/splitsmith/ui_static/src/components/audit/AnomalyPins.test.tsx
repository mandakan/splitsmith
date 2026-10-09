import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Anomaly } from "@/lib/anomalies";

import { AnomalyPins } from "./AnomalyPins";

const pin = (time: number, message: string): Anomaly => ({
  kind: "long_pause",
  severity: "warn",
  message,
  shot_number: 1,
  time,
});

// The band's Flags row: a view as wide as the content, so x is content x.
const VIEW = { contentWidth: 1000, viewportWidth: 1000, scrollLeft: 0 };

describe("AnomalyPins on the band's Flags row", () => {
  it("places a pin at its content x", () => {
    render(<AnomalyPins anomalies={[pin(5, "mid")]} duration={20} view={VIEW} onJump={vi.fn()} />);
    expect(screen.getByRole("button", { name: "mid" }).style.left).toBe("250px");
  });

  it("keeps a pin at t=0 and t=duration wholly inside the row", () => {
    render(
      <AnomalyPins anomalies={[pin(0, "start"), pin(20, "end")]} duration={20} view={VIEW} onJump={vi.fn()} />,
    );
    // Half the 18 px pin in from each edge: never clipped, never past the content.
    expect(screen.getByRole("button", { name: "start" }).style.left).toBe("9px");
    expect(screen.getByRole("button", { name: "end" }).style.left).toBe("991px");
  });

  it("jumps to the anomaly's own time, not the clamped x", () => {
    const onJump = vi.fn();
    const a = pin(0, "start");
    render(<AnomalyPins anomalies={[a]} duration={20} view={VIEW} onJump={onJump} />);
    fireEvent.click(screen.getByRole("button", { name: "start" }));
    expect(onJump).toHaveBeenCalledWith(a);
  });
});
