import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SpectrogramTrack } from "@/components/audit/SpectrogramTrack";

const SPEC = { start: 0, duration: 2, hz: [0, 2000, 4000, 6000], columns: 2, tone_band: [2200, 3500] as [number, number], data: btoa(String.fromCharCode(0, 10, 200, 0, 0, 10, 200, 0)) };
const GEOM = { contentWidth: 1000, viewportWidth: 300, scrollLeft: 100, pxPerSec: 500 };

describe("SpectrogramTrack", () => {
  it("draws only the visible window and outlines the band", () => {
    const created: number[] = [];
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
      () =>
        ({
          setTransform() {},
          clearRect() {},
          createImageData: (w: number, h: number) => {
            created.push(w);
            return { data: new Uint8ClampedArray(w * h * 4), width: w, height: h };
          },
          putImageData() {},
        }) as unknown as CanvasRenderingContext2D,
    );
    render(<SpectrogramTrack spectrum={SPEC} from={0} to={2} geom={GEOM} height={60} band={{ lo: 2200, hi: 3500 }} />);
    expect(created[0]).toBe(300);
    expect(screen.getByTestId("spectrum-band-lo")).toBeInTheDocument();
    expect(screen.getByTestId("spectrum-band-hi")).toBeInTheDocument();
  });

  it("says when there is no spectrum yet", () => {
    render(<SpectrogramTrack spectrum={null} from={0} to={2} geom={GEOM} height={60} band={null} />);
    expect(screen.getByText("Preparing spectrum")).toBeInTheDocument();
  });
});
