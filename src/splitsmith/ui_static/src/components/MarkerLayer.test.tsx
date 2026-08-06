import { render } from "@testing-library/react";
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
