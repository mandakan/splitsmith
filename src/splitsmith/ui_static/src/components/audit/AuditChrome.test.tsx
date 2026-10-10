import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_FILTERS } from "@/components/AuditControls";
import type { AuditMarker } from "@/components/MarkerLayer";
import { shotRows } from "@/lib/auditStep";

import { AuditFooter } from "./AuditFooter";
import { CurrentShotLine } from "./CurrentShotLine";
import { ShotList } from "./ShotList";
import { LegendKey, TransportLine, TransportMenuItems } from "./TransportLine";

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

  it("follows the current shot by scrolling the list, never the page (#1067)", () => {
    // scrollIntoView scrolls every ancestor, the document included: placing
    // or toggling a marker jumped the whole Audit page.
    const spy = vi.fn();
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = spy;
    try {
      const { rerender } = render(
        <ShotList rows={shotRows(MARKERS, [])} currentMarkerId="a" onJump={vi.fn()} />,
      );
      rerender(<ShotList rows={shotRows(MARKERS, [])} currentMarkerId="c" onJump={vi.fn()} />);
      expect(spy).not.toHaveBeenCalled();
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });
});

describe("TransportLine", () => {
  function renderLine(over: Partial<React.ComponentProps<typeof TransportLine>> = {}) {
    const props: React.ComponentProps<typeof TransportLine> = {
      isPlaying: false,
      onTogglePlay: vi.fn(),
      currentTime: 7.29,
      duration: 42.13,
      loopMode: false,
      onToggleLoop: vi.fn(),
      camera: "Head cam",
      filters: DEFAULT_FILTERS,
      counts: { detected: 30, rejected: 89, manual: 0, beep: 1 },
      onFiltersChange: vi.fn(),
      peeking: false,
      onPeekStart: vi.fn(),
      onPeekEnd: vi.fn(),
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

  // Zoom is the timeline band's own (#1352); "steps with the buttons and
  // returns to Fit" in components/timeline/Timeline.test.tsx covers it.
  it("has no zoom controls; names the camera in place of the band's title", () => {
    renderLine({ camera: "Chest cam" });
    expect(screen.queryByRole("button", { name: /zoom|fit/i })).toBeNull();
    expect(screen.getByText("Chest cam")).toBeInTheDocument();
  });

  // The camera column's transport row (#1359) folded in here: its play and
  // clock were copies, its loop was not (its frame steps went to the menu).
  it("plays and loops", () => {
    const props = renderLine({ loopMode: true });
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(props.onTogglePlay).toHaveBeenCalledTimes(1);
    const loop = screen.getByRole("button", { name: "Loop on (L)" });
    expect(loop).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(loop);
    expect(props.onToggleLoop).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /Step frame/ })).toBeNull();
  });

  it("says when the peaks are loading", () => {
    renderLine({ loading: true });
    expect(screen.getByText("Loading")).toBeInTheDocument();
  });
});

describe("TransportMenuItems", () => {
  function renderItems(over: Partial<React.ComponentProps<typeof TransportMenuItems>> = {}) {
    const props: React.ComponentProps<typeof TransportMenuItems> = {
      onStepFrame: vi.fn(),
      kAutoProgress: true,
      onToggleKAuto: vi.fn(),
      ...over,
    };
    render(
      <div role="menu">
        <TransportMenuItems {...props} />
      </div>,
    );
    return props;
  }

  it("steps one frame back and forward", () => {
    const props = renderItems();
    fireEvent.click(screen.getByRole("button", { name: "Step frame back" }));
    fireEvent.click(screen.getByRole("button", { name: "Step frame forward" }));
    expect(props.onStepFrame).toHaveBeenNthCalledWith(1, -1);
    expect(props.onStepFrame).toHaveBeenNthCalledWith(2, 1);
  });

  it("toggles auto-step", () => {
    const props = renderItems();
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: /auto-step/i }));
    expect(props.onToggleKAuto).toHaveBeenCalled();
  });

  it("offers the full-resolution switch only when the page passes one", () => {
    renderItems();
    expect(screen.queryByRole("menuitemcheckbox", { name: /full-resolution video/i })).toBeNull();
  });

  it("toggles full-resolution video", () => {
    const onToggleFullResVideo = vi.fn();
    renderItems({ fullResVideo: false, onToggleFullResVideo });
    const item = screen.getByRole("menuitemcheckbox", { name: /full-resolution video/i });
    expect(item).toHaveAttribute("aria-checked", "false");
    fireEvent.click(item);
    expect(onToggleFullResVideo).toHaveBeenCalledTimes(1);
  });

  it("ends with the page's action and then the readouts", () => {
    renderItems({
      action: (
        <button type="button" role="menuitem">
          Detect shots
        </button>
      ),
      readouts: ["1500 peaks · 44.69 s", "All 2 cameras linked"],
    });
    const menu = screen.getByRole("menu");
    const text = menu.textContent ?? "";
    expect(text.indexOf("Auto-step")).toBeLessThan(text.indexOf("Detect shots"));
    expect(text.indexOf("Detect shots")).toBeLessThan(text.indexOf("1500 peaks"));
    expect(text.indexOf("1500 peaks")).toBeLessThan(text.indexOf("All 2 cameras linked"));
  });
});

describe("LegendKey", () => {
  it("folds the legend to its swatches and opens the labels on click", () => {
    render(<LegendKey />);
    const key = screen.getByRole("button", { name: "Marker key" });
    expect(screen.queryByText("Timer stop")).toBeNull();
    fireEvent.click(key);
    expect(key).toHaveAttribute("aria-expanded", "true");
    // A popover of labels, not a menu: no menu role, the key as a list.
    expect(screen.queryByRole("menu")).toBeNull();
    const dialog = screen.getByRole("dialog", { name: "Marker key" });
    const items = within(dialog).getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["Beep", "Timer stop", "Shot", "Manual", "Rejected", "Flag", "Current"]);
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
