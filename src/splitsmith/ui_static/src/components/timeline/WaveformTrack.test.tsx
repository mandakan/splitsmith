import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

describe("WaveformTrack overlays", () => {
  beforeEach(() => {
    const ctx = { setTransform: vi.fn(), clearRect: vi.fn(), fillRect: vi.fn(), fillStyle: "" };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
  });

  it("positions the beep line at the right percentage", () => {
    render(<WaveformTrack peaks={[0.5]} clipDuration={10} from={0} to={10} geom={geom(1)} height={56} beepTime={2.5} />);
    expect(screen.getByTestId("wave-beep").style.left).toBe("25%");
  });

  it("positions the timer-stop line at the right percentage", () => {
    render(
      <WaveformTrack peaks={[0.5]} clipDuration={10} from={0} to={10} geom={geom(1)} height={56} timerStopTime={8} />,
    );
    expect(screen.getByTestId("wave-timer-stop").style.left).toBe("80%");
  });

  it("positions the loop region at the right left and width", () => {
    render(
      <WaveformTrack
        peaks={[0.5]}
        clipDuration={10}
        from={0}
        to={10}
        geom={geom(1)}
        height={56}
        loopRegion={{ start: 2, end: 4 }}
      />,
    );
    const loop = screen.getByTestId("wave-loop");
    expect(loop.style.left).toBe("20%");
    expect(loop.style.width).toBe("20%");
  });

  it("clamps a loop region straddling `from` to draw from 0% to its end", () => {
    render(
      <WaveformTrack
        peaks={[0.5]}
        clipDuration={10}
        from={0}
        to={10}
        geom={geom(1)}
        height={56}
        loopRegion={{ start: -2, end: 4 }}
      />,
    );
    const loop = screen.getByTestId("wave-loop");
    expect(loop.style.left).toBe("0%");
    expect(loop.style.width).toBe("40%");
  });

  it("clamps a loop region straddling `to` to draw to 100%", () => {
    render(
      <WaveformTrack
        peaks={[0.5]}
        clipDuration={10}
        from={0}
        to={10}
        geom={geom(1)}
        height={56}
        loopRegion={{ start: 8, end: 14 }}
      />,
    );
    const loop = screen.getByTestId("wave-loop");
    expect(loop.style.left).toBe("80%");
    expect(loop.style.width).toBe("20%");
  });

  it("renders nothing for a loop region wholly outside [from, to]", () => {
    render(
      <WaveformTrack
        peaks={[0.5]}
        clipDuration={10}
        from={0}
        to={10}
        geom={geom(1)}
        height={56}
        loopRegion={{ start: 12, end: 14 }}
      />,
    );
    expect(screen.queryByTestId("wave-loop")).not.toBeInTheDocument();
  });

  it("renders no overlays when every value is null", () => {
    render(
      <WaveformTrack
        peaks={[0.5]}
        clipDuration={10}
        from={0}
        to={10}
        geom={geom(1)}
        height={56}
        beepTime={null}
        timerStopTime={null}
        loopRegion={null}
      />,
    );
    expect(screen.queryByTestId("wave-beep")).not.toBeInTheDocument();
    expect(screen.queryByTestId("wave-timer-stop")).not.toBeInTheDocument();
    expect(screen.queryByTestId("wave-loop")).not.toBeInTheDocument();
  });

  it("renders no overlays when every value is outside [from, to]", () => {
    render(
      <WaveformTrack
        peaks={[0.5]}
        clipDuration={10}
        from={0}
        to={10}
        geom={geom(1)}
        height={56}
        beepTime={15}
        timerStopTime={-1}
        loopRegion={{ start: 12, end: 14 }}
      />,
    );
    expect(screen.queryByTestId("wave-beep")).not.toBeInTheDocument();
    expect(screen.queryByTestId("wave-timer-stop")).not.toBeInTheDocument();
    expect(screen.queryByTestId("wave-loop")).not.toBeInTheDocument();
  });

  it("marks every overlay pointer-events-none", () => {
    render(
      <WaveformTrack
        peaks={[0.5]}
        clipDuration={10}
        from={0}
        to={10}
        geom={geom(1)}
        height={56}
        beepTime={2.5}
        timerStopTime={8}
        loopRegion={{ start: 2, end: 4 }}
      />,
    );
    expect(screen.getByTestId("wave-beep").className).toMatch(/pointer-events-none/);
    expect(screen.getByTestId("wave-timer-stop").className).toMatch(/pointer-events-none/);
    expect(screen.getByTestId("wave-loop").className).toMatch(/pointer-events-none/);
  });
});
