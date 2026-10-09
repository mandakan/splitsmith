import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WaveformTrack } from "./WaveformTrack";

const geom = (zoomed: number) => ({
  contentWidth: 1200 * zoomed,
  viewportWidth: 1200,
  scrollLeft: 0,
  pxPerSec: (1200 * zoomed) / 60,
});

describe("WaveformTrack", () => {
  const originalDpr = window.devicePixelRatio;

  afterEach(() => {
    Object.defineProperty(window, "devicePixelRatio", { value: originalDpr, configurable: true });
  });

  it("never sizes its canvas past the viewport, even at 16x on a long stage", () => {
    Object.defineProperty(window, "devicePixelRatio", { value: 2, configurable: true });
    const ctx = { setTransform: vi.fn(), clearRect: vi.fn(), fillRect: vi.fn(), fillStyle: "" };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
    const { container } = render(
      <WaveformTrack peaks={new Array(4000).fill(0.5)} clipDuration={70} from={5} to={65} geom={geom(16)} height={56} />,
    );
    const canvas = container.querySelector("canvas")!;
    expect(canvas.width).toBe(2400);
    expect(canvas.style.width).toBe("1200px");
    expect(ctx.fillRect).toHaveBeenCalled();
  });

  it("says there is no audio when peaks are missing", () => {
    render(<WaveformTrack peaks={null} clipDuration={0} from={0} to={10} geom={geom(1)} height={56} />);
    expect(screen.getByText("No audio")).toBeInTheDocument();
  });
});
