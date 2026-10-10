import { fireEvent, render } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { MarkerLayer, type AuditMarker } from "@/components/MarkerLayer";
import type { MarkerKind } from "@/components/MarkerGlyph";

function makeMarker(id: string, kind: MarkerKind, time: number): AuditMarker {
  return {
    id,
    kind,
    time,
    candidateNumber: null,
    confidence: null,
    peakAmplitude: null,
    note: "",
  };
}

const MARKERS = [
  makeMarker("d1", "detected", 1),
  makeMarker("r1", "rejected", 2),
  makeMarker("r2", "rejected", 3),
];

function renderLayer(props: Partial<Parameters<typeof MarkerLayer>[0]> = {}) {
  return render(
    <MarkerLayer
      markers={MARKERS}
      duration={10}
      focusedId={null}
      onFocusChange={vi.fn()}
      onClick={vi.fn()}
      onDelete={vi.fn()}
      onTimeChange={vi.fn()}
      {...props}
    />,
  );
}

function renderedIds(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("[data-audit-marker-id]")).map(
    (el) => el.getAttribute("data-audit-marker-id")!,
  );
}

describe("MarkerLayer visibility filtering", () => {
  it("hides kinds not in visibleKinds", () => {
    const { container } = renderLayer({
      visibleKinds: new Set<MarkerKind>(["detected", "manual"]),
    });
    expect(renderedIds(container)).toEqual(["d1"]);
  });

  it("renders only the forcedVisibleId marker from a hidden kind, not the whole kind (#666)", () => {
    const { container } = renderLayer({
      visibleKinds: new Set<MarkerKind>(["detected", "manual"]),
      forcedVisibleId: "r1",
    });
    expect(renderedIds(container)).toEqual(["d1", "r1"]);
  });

  it("renders everything when visibleKinds is absent", () => {
    const { container } = renderLayer({});
    expect(renderedIds(container)).toEqual(["d1", "r1", "r2"]);
  });
});

describe("MarkerLayer focus and placement", () => {
  it("marks the focused marker without a red ring around the waveform", () => {
    const { container } = renderLayer({ focusedId: "d1" });
    const btn = container.querySelector('[data-audit-marker-id="d1"]')!;
    expect(btn.className).not.toMatch(/ring-led/);
    expect(btn.getAttribute("data-focused")).toBe("true");
  });

  it("nudges by 1 ms, 10 ms with Shift", () => {
    const onTimeChange = vi.fn();
    const { container } = renderLayer({ onTimeChange });
    const btn = container.querySelector('[data-audit-marker-id="d1"]')!;
    fireEvent.keyDown(btn, { key: "ArrowRight" });
    expect(onTimeChange.mock.lastCall?.[1]).toBeCloseTo(1.001, 6);
    fireEvent.keyDown(btn, { key: "ArrowLeft", shiftKey: true });
    expect(onTimeChange.mock.lastCall?.[1]).toBeCloseTo(0.99, 6);
  });

  /** Drag d1 to 3.012 s on a band ``widthPx`` wide over 10 s and drop it.
   *  The audio has one shot whose rise starts at 3.000 s. */
  function dropAt(widthPx: number, shiftKey = false) {
    const onTimeChangeCommit = vi.fn();
    const peaks = Array.from({ length: 10000 }, (_, i) =>
      i < 3000 ? 0.01 : i < 3010 ? 0.01 + (i - 2999) * 0.099 : Math.max(0.01, Math.exp(-(i - 3010) / 15)),
    );
    function Harness() {
      const [markers, setMarkers] = useState(MARKERS);
      return (
        <MarkerLayer
          markers={markers}
          duration={10}
          focusedId={null}
          onFocusChange={vi.fn()}
          onClick={vi.fn()}
          onDelete={vi.fn()}
          onTimeChange={(id, t) =>
            setMarkers((ms) => ms.map((m) => (m.id === id ? { ...m, time: t } : m)))
          }
          onTimeChangeCommit={onTimeChangeCommit}
          snapPeaks={{ peaks, duration: 10 }}
        />
      );
    }
    const { container } = render(<Harness />);
    const btn = container.querySelector('[data-audit-marker-id="d1"]') as HTMLElement;
    const parent = btn.parentElement as HTMLElement;
    parent.getBoundingClientRect = () =>
      ({ left: 0, width: widthPx, top: 0, height: 100 }) as DOMRect;
    btn.setPointerCapture = vi.fn();
    btn.hasPointerCapture = () => true;
    btn.releasePointerCapture = vi.fn();
    const x = (3.012 / 10) * widthPx;
    fireEvent.pointerDown(btn, { button: 0, pointerId: 1, clientX: widthPx / 10, clientY: 5 });
    fireEvent.pointerMove(btn, { pointerId: 1, clientX: x, clientY: 5 });
    fireEvent.pointerUp(btn, { pointerId: 1, clientX: x, clientY: 5, shiftKey });
    return onTimeChangeCommit.mock.lastCall?.[1] as number;
  }

  it("snaps a drop to the shot's leading edge when zoomed out", () => {
    // 1000 px over 10 s: 10 ms per pixel.
    expect(dropAt(1000)).toBeCloseTo(3.0, 6);
  });

  it("places a drop exactly when zoomed in to 2 ms per pixel or finer", () => {
    expect(dropAt(10000)).toBeCloseTo(3.012, 6);
  });

  it("places a drop exactly with Shift at any zoom", () => {
    expect(dropAt(1000, true)).toBeCloseTo(3.012, 6);
  });
});
