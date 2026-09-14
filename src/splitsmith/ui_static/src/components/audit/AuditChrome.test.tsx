import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_FILTERS } from "@/components/AuditControls";
import type { AuditMarker } from "@/components/MarkerLayer";
import { shotRows } from "@/lib/auditStep";

import { AuditFooter } from "./AuditFooter";
import { CurrentShotLine } from "./CurrentShotLine";
import { ShotList } from "./ShotList";
import { TransportLine } from "./TransportLine";

function marker(id: string, time: number, kind: AuditMarker["kind"] = "detected"): AuditMarker {
  return { id, kind, time, candidateNumber: null, confidence: 0.8, peakAmplitude: null, note: "" };
}

const MARKERS = [marker("a", 5), marker("r", 6, "rejected"), marker("b", 8), marker("c", 12)];
const FLAGS = [{ kind: "long_pause" as const, severity: "warn" as const, message: "missed shot?", shot_number: 2, time: 8 }];

describe("ShotList", () => {
  it("renders the flagged group first, strikes rejected rows, jumps on click", () => {
    const onJump = vi.fn();
    render(<ShotList rows={shotRows(MARKERS, FLAGS)} currentMarkerId="b" onJump={onJump} />);
    const list = screen.getByRole("region", { name: "Shots" });
    const buttons = within(list).getAllByRole("button");
    // First row is the flagged shot b (index 02), then the full list a, r, b, c.
    expect(buttons[0]).toHaveTextContent("02");
    expect(buttons[0]).toHaveTextContent("missed shot?");
    expect(buttons).toHaveLength(5);
    expect(buttons[2]).toHaveTextContent("rejected");
    fireEvent.click(buttons[4]);
    expect(onJump).toHaveBeenCalledWith(MARKERS[3]);
    expect(screen.getByText("Flagged · 1")).toBeInTheDocument();
  });

  it("measures the first shot's split from the beep, not from the clip start", () => {
    render(<ShotList rows={shotRows(MARKERS, [])} beep={3} currentMarkerId={null} onJump={vi.fn()} />);
    const list = screen.getByRole("region", { name: "Shots" });
    const buttons = within(list).getAllByRole("button");
    // Shot a at 5 s with the beep at 3 s: a 2.000 s draw. Shot b at 8 s
    // follows the kept shot a (the rejected r in between does not count).
    expect(buttons[0]).toHaveTextContent("2.000");
    expect(buttons[2]).toHaveTextContent("3.000");
  });
});

describe("TransportLine", () => {
  function renderLine(over: Partial<React.ComponentProps<typeof TransportLine>> = {}) {
    const props: React.ComponentProps<typeof TransportLine> = {
      isPlaying: false,
      onTogglePlay: vi.fn(),
      currentTime: 7.29,
      duration: 42.13,
      zoom: null,
      onZoomChange: vi.fn(),
      filters: DEFAULT_FILTERS,
      counts: { detected: 30, rejected: 89, manual: 0, beep: 1 },
      onFiltersChange: vi.fn(),
      peeking: false,
      onPeekStart: vi.fn(),
      onPeekEnd: vi.fn(),
      kAutoProgress: true,
      onToggleKAuto: vi.fn(),
      onOpenHelp: vi.fn(),
      ...over,
    };
    render(<TransportLine {...props} />);
    return props;
  }

  it("shows the clock and the show summary; the menu toggles a filter", () => {
    const props = renderLine();
    expect(screen.getByText("0:07.29 / 0:42.13")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show 30 detected/ }));
    fireEvent.click(screen.getByLabelText("Rejected (89)"));
    expect(props.onFiltersChange).toHaveBeenCalledWith({ ...DEFAULT_FILTERS, rejected: true });
  });

  it("zooms in from fit and the overflow menu toggles auto-step", () => {
    const props = renderLine();
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(props.onZoomChange).toHaveBeenCalledWith(1.5);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox"));
    expect(props.onToggleKAuto).toHaveBeenCalled();
  });
});

describe("CurrentShotLine", () => {
  it("shows the current shot's figures and flag; Reject and the note call back", () => {
    const onReject = vi.fn();
    const onNoteChange = vi.fn();
    render(
      <CurrentShotLine
        shots={[marker("a", 5), marker("b", 8)]}
        currentIndex={1}
        onStep={vi.fn()}
        flag="missed shot?"
        onNoteChange={onNoteChange}
        onReject={onReject}
        onAddHere={vi.fn()}
        canAddHere={false}
      />,
    );
    expect(screen.getByText("02 / 2")).toBeInTheDocument();
    expect(screen.getByText("3.000")).toBeInTheDocument();
    expect(screen.getByText("missed shot?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Reject/ }));
    expect(onReject).toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Notes for this shot"), { target: { value: "late" } });
    expect(onNoteChange).toHaveBeenCalledWith("b", "late");
    expect(screen.getByRole("button", { name: /Add shot here/ })).toBeDisabled();
  });

  it("reads the first shot's split as the draw from the beep", () => {
    render(
      <CurrentShotLine
        shots={[marker("a", 6.97)]}
        currentIndex={0}
        beep={5}
        onStep={vi.fn()}
        flag={null}
        onNoteChange={vi.fn()}
        onReject={vi.fn()}
        onAddHere={vi.fn()}
        canAddHere={false}
      />,
    );
    expect(screen.getByText("1.970")).toBeInTheDocument();
  });
});

describe("AuditFooter", () => {
  it("lists the keys and opens help", () => {
    const onOpenHelp = vi.fn();
    render(<AuditFooter onOpenHelp={onOpenHelp} />);
    expect(screen.getByText("next flag")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /all shortcuts/ }));
    expect(onOpenHelp).toHaveBeenCalled();
  });
});
