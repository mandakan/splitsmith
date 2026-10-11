import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { AuditMarker } from "@/components/MarkerLayer";
import { WALK_DECIDED_EVENT } from "@/lib/walk";

import { MobileWalk } from "./MobileWalk";

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

/** The page's side of the phone walk, reduced to the markers and the events. */
function Harness({
  initial,
  record,
  loop = false,
  onLoopStop = () => {},
  guideOpen = false,
  onToggleSlow,
}: {
  initial: AuditMarker[];
  record: (kind: string, payload: Record<string, unknown>) => void;
  loop?: boolean;
  onLoopStop?: (time: number | null) => void;
  guideOpen?: boolean;
  onToggleSlow?: () => void;
}) {
  const [markers, setMarkers] = useState(initial);
  return (
    <MobileWalk
      markers={markers}
      peaks={envelope(3, [0.5, 1.0, 1.5])}
      savedEvents={[]}
      from={0}
      expectedRounds={null}
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
      loop={loop}
      onToggleLoop={() => {}}
      onLoopStop={onLoopStop}
      slow={false}
      onToggleSlow={onToggleSlow}
      onDone={() => {}}
      onExit={() => {}}
      busy={false}
      guideOpen={guideOpen}
      onToggleGuide={() => {}}
      audio={null}
      scope="all"
      onScopeChange={() => {}}
    />
  );
}

const markersAll = [m("cand-1", "detected", 0.5), m("cand-2", "rejected", 1.0)];
const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

describe("MobileWalk", () => {
  it("asks for nothing to play until Start, then loops the first stop", () => {
    const onLoopStop = vi.fn();
    render(<Harness initial={markersAll} loop onLoopStop={onLoopStop} record={vi.fn()} />);
    expect(onLoopStop).not.toHaveBeenCalled();
    fireEvent.click(button("Start"));
    expect(onLoopStop).toHaveBeenLastCalledWith(0.5);
    expect(screen.getByText("Stop 1 of 3")).toBeTruthy();
  });

  it("Confirm records the stop and moves on, and the next stop loops", () => {
    const record = vi.fn();
    const onLoopStop = vi.fn();
    render(<Harness initial={markersAll} loop onLoopStop={onLoopStop} record={record} />);
    fireEvent.click(button("Start"));
    fireEvent.click(button("Confirm"));
    expect(record).toHaveBeenCalledWith(WALK_DECIDED_EVENT, expect.objectContaining({ stop: "cand-1" }));
    expect(screen.getByText("Stop 2 of 3")).toBeTruthy();
    expect(onLoopStop).toHaveBeenLastCalledWith(1.0);
  });

  it("disables what does nothing at this stop", () => {
    render(<Harness initial={markersAll} record={vi.fn()} />);
    fireEvent.click(button("Start"));
    fireEvent.click(button("Confirm")); // stop 2: a rejected candidate
    for (const name of ["Rise foot", "-5 ms", "-1 ms", "+1 ms", "+5 ms", "Not a shot"]) {
      expect(button(name).disabled).toBe(true);
    }
    expect(button("Shot").disabled).toBe(false);
  });

  it("nudges and puts the shot back on the rule with buttons", () => {
    render(<Harness initial={markersAll} record={vi.fn()} />);
    fireEvent.click(button("Start"));
    fireEvent.click(button("+5 ms"));
    expect(screen.getByText(/5 ms after the rise foot/)).toBeTruthy();
    fireEvent.click(button("Rise foot"));
    expect(screen.getByText("On the rise foot (the rule).")).toBeTruthy();
  });

  it("goes back a stop", () => {
    render(<Harness initial={markersAll} record={vi.fn()} />);
    fireEvent.click(button("Start"));
    fireEvent.click(button("Confirm"));
    fireEvent.click(button("Back"));
    expect(screen.getByText("Stop 1 of 3")).toBeTruthy();
  });

  it("keeps the guide closed until asked, whatever the desktop remembers", () => {
    render(<Harness initial={markersAll} record={vi.fn()} guideOpen />);
    fireEvent.click(button("Start"));
    expect(screen.queryByRole("dialog", { name: "Review guide" })).toBeNull();
    fireEvent.click(button("Guide"));
    expect(screen.getByRole("dialog", { name: "Review guide" })).toBeTruthy();
  });
});

describe("MobileWalk listening", () => {
  it("names a stop close enough to blur into this one, and offers half speed", () => {
    const onToggleSlow = vi.fn();
    render(
      <Harness
        initial={[m("cand-1", "detected", 0.5), m("cand-2", "rejected", 0.548)]}
        record={vi.fn()}
        onToggleSlow={onToggleSlow}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(
      screen.getByText("Another stop 48 ms after this one plays in the loop. Half speed hears them as two sounds."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Half speed" }));
    expect(onToggleSlow).toHaveBeenCalledTimes(1);
  });
});
