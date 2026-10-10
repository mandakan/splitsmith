import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";
import { WALK_DECIDED_EVENT, WALK_METHOD, readGuideOpen, writeGuideOpen } from "@/lib/walk";

import { Walk } from "./Walk";

function envelope(duration: number, onsets: number[]): { peaks: number[]; duration: number } {
  const n = Math.round(duration * 1000);
  const peaks = new Array<number>(n).fill(0.01);
  for (const t of onsets) {
    const i0 = Math.round(t * 1000);
    for (let k = 0; k < 120 && i0 + k < n; k++) {
      peaks[i0 + k] = Math.max(peaks[i0 + k], k < 3 ? 0.3 + 0.35 * k : Math.exp(-(k - 3) / 25));
    }
  }
  return { peaks, duration };
}

const m = (id: string, kind: AuditMarker["kind"], time: number): AuditMarker => ({
  id,
  kind,
  time,
  candidateNumber: id.startsWith("cand-") ? Number(id.slice(5)) : null,
  confidence: null,
  peakAmplitude: null,
  note: "",
});

/** The page's side of the walk, reduced to the markers and the events. */
function Harness({
  initial,
  savedEvents = [],
  expectedRounds = null,
  scope = "all",
  onDone = () => {},
  record,
}: {
  initial: AuditMarker[];
  savedEvents?: Array<{ kind: string; payload: Record<string, unknown> }>;
  expectedRounds?: number | null;
  scope?: "all" | "shots";
  onDone?: () => void;
  record: (kind: string, payload: Record<string, unknown>) => void;
}) {
  const [markers, setMarkers] = useState(initial);
  // The page's guide wiring (pages/Review.tsx): the walk toggles, the page draws.
  const [guideOpen, setGuideOpen] = useState(readGuideOpen);
  const toggle = () =>
    setGuideOpen((open) => {
      writeGuideOpen(!open);
      return !open;
    });
  return (
    <>
      <Walk
        markers={markers}
        peaks={envelope(3, [0.5, 1.0, 1.5])}
        savedEvents={savedEvents}
        from={0}
        expectedRounds={expectedRounds}
        snapDisplacementMs={() => null}
        onSetTime={(id, t) => setMarkers((ms) => ms.map((x) => (x.id === id ? { ...x, time: t } : x)))}
        onSetKind={(id, kind) => setMarkers((ms) => ms.map((x) => (x.id === id ? { ...x, kind } : x)))}
        onAddShot={(t) => {
          setMarkers((ms) => [...ms, m("manual-new", "manual", t)]);
          return "manual-new";
        }}
        onRemove={(id) => setMarkers((ms) => ms.filter((x) => x.id !== id))}
        onRecord={record}
        onFocus={() => {}}
        onListen={() => {}}
        onDone={onDone}
        onExit={() => {}}
        busy={false}
        guideOpen={guideOpen}
        onToggleGuide={toggle}
        audio={null}
        scope={scope}
        onScopeChange={() => {}}
      />
    </>
  );
}

const press = (key: string) => fireEvent.keyDown(document.body, { key });
const markersAll = [m("cand-1", "detected", 0.5), m("cand-2", "rejected", 1.0)];

describe("Walk", () => {
  it("walks every candidate and the unmarked burst, recording each confirmation", () => {
    const record = vi.fn();
    render(<Harness initial={markersAll} record={record} />);
    expect(screen.getByText("Stop 1 of 3")).toBeTruthy();
    expect(screen.getByText(/^now: a shot at/)).toBeTruthy();
    press("Enter");
    expect(record).toHaveBeenLastCalledWith(WALK_DECIDED_EVENT, {
      stop: "cand-1",
      state: "shot",
      time: 0.5,
      method: WALK_METHOD,
      placement: "rule",
      rule_time: 0.5,
    });
    expect(screen.getByText("Stop 2 of 3")).toBeTruthy();
    expect(screen.getByText(/^now: not a shot at/)).toBeTruthy();
  });

  it("records a nudged shot as an override, says so, and F puts it back on the rule", () => {
    const record = vi.fn();
    render(<Harness initial={markersAll} record={record} />);
    expect(screen.getByText("On the rise foot (the rule).")).toBeTruthy();
    press("ArrowRight");
    press("ArrowRight");
    expect(screen.getByText(/2 ms after the rise foot: your placement/)).toBeTruthy();
    press("Enter");
    expect(record).toHaveBeenLastCalledWith(
      WALK_DECIDED_EVENT,
      expect.objectContaining({ stop: "cand-1", time: 0.502, placement: "override", rule_time: 0.5 }),
    );
    press("Backspace");
    press("f");
    expect(screen.getByText("On the rise foot (the rule).")).toBeTruthy();
  });

  it("makes a rejected candidate a shot on S, and an unmarked burst a shot at its onset", () => {
    const record = vi.fn();
    render(<Harness initial={markersAll} record={record} />);
    press("Enter");
    press("s");
    expect(screen.getByText(/^now: a shot at/)).toBeTruthy();
    press("Enter");
    expect(record).toHaveBeenLastCalledWith(WALK_DECIDED_EVENT, expect.objectContaining({ stop: "cand-2", state: "shot" }));
    expect(screen.getByText(/proposed no candidate/)).toBeTruthy();
    press("s");
    press("Enter");
    expect(record).toHaveBeenLastCalledWith(
      WALK_DECIDED_EVENT,
      expect.objectContaining({ stop: "burst-1500", state: "shot", time: 1.5 }),
    );
  });

  it("removes a manual shot on X instead of keeping a rejected copy", () => {
    const record = vi.fn();
    render(<Harness initial={[m("cand-1", "detected", 0.5), m("manual-shot-2", "manual", 1.0)]} record={record} />);
    press("Enter");
    press("x");
    expect(screen.getByText(/^now: not a shot at/)).toBeTruthy();
  });

  it("resumes at the first stop no saved decision covers", () => {
    render(
      <Harness
        initial={markersAll}
        record={vi.fn()}
        savedEvents={[
          { kind: WALK_DECIDED_EVENT, payload: { stop: "cand-1", state: "shot", time: 0.5, method: WALK_METHOD } },
        ]}
      />,
    );
    expect(screen.getByText("Stop 2 of 3")).toBeTruthy();
  });

  it("asks for a second Enter before signing off a count that does not match the stage", () => {
    const onDone = vi.fn();
    render(<Harness initial={[m("cand-1", "detected", 0.5)]} record={vi.fn()} expectedRounds={3} onDone={onDone} />);
    press("Enter"); // cand-1
    press("x"); // burst at 1.0: not a shot
    press("Enter");
    press("x"); // burst at 1.5
    press("Enter");
    expect(screen.getByText(/1 shots, but the stage has 3 rounds/)).toBeTruthy();
    press("Enter");
    expect(onDone).not.toHaveBeenCalled();
    press("Enter");
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("shows the guide until it is hidden, and remembers that", () => {
    window.localStorage.clear();
    const { unmount } = render(<Harness initial={markersAll} record={vi.fn()} />);
    expect(screen.getByRole("region", { name: "Review guide" })).toBeTruthy();
    press("?");
    expect(screen.queryByRole("region", { name: "Review guide" })).toBeNull();
    unmount();
    render(<Harness initial={markersAll} record={vi.fn()} />);
    expect(screen.queryByRole("region", { name: "Review guide" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Guide" }));
    expect(screen.getByRole("region", { name: "Review guide" })).toBeTruthy();
  });

  it("keeps its keys from the page's own play and scrub handlers", () => {
    const pageKey = vi.fn();
    window.addEventListener("keydown", pageKey);
    try {
      render(<Harness initial={markersAll} record={vi.fn()} />);
      press(" ");
      press("ArrowLeft");
      expect(pageKey).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("keydown", pageKey);
    }
  });
});
