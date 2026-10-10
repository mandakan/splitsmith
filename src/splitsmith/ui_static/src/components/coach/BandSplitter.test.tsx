import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BandSplitter } from "./BandSplitter";

const LIMITS = { min: 189, max: 447 };
const ROOM = 700;

function Harness({ onCommit = vi.fn(), onToggleLarge = vi.fn() }: { onCommit?: (b: number) => void; onToggleLarge?: () => void }) {
  const [stored, setStored] = useState(300);
  const [live, setLive] = useState<number | null>(null);
  return (
    <>
      <BandSplitter
        band={live ?? stored}
        limits={LIMITS}
        room={ROOM}
        onDrag={setLive}
        onCommit={(b) => {
          setLive(null);
          setStored(b);
          onCommit(b);
        }}
        onCancel={() => setLive(null)}
        onToggleLarge={onToggleLarge}
      />
      <span data-testid="band">{live ?? stored}</span>
    </>
  );
}

beforeEach(() => {
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
  HTMLElement.prototype.releasePointerCapture = vi.fn();
});

describe("BandSplitter", () => {
  it("is a horizontal separator whose value is the viewer's height", () => {
    render(<Harness />);
    const sep = screen.getByRole("separator", { name: "Resize the timeline" });
    expect(sep).toHaveAttribute("aria-orientation", "horizontal");
    expect(sep).toHaveAttribute("tabindex", "0");
    expect(sep).toHaveAttribute("aria-valuenow", "400");
    expect(sep).toHaveAttribute("aria-valuemin", String(ROOM - LIMITS.max));
    expect(sep).toHaveAttribute("aria-valuemax", String(ROOM - LIMITS.min));
  });

  it("a drag up grows the band live, within its limits, and remembers it on release", () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    const sep = screen.getByRole("separator");
    fireEvent.pointerDown(sep, { pointerId: 1, clientY: 500, button: 0 });
    fireEvent.pointerMove(sep, { pointerId: 1, clientY: 440 });
    expect(screen.getByTestId("band")).toHaveTextContent("360");
    expect(onCommit).not.toHaveBeenCalled();
    // Past the viewer's floor: the band stops at its max.
    fireEvent.pointerMove(sep, { pointerId: 1, clientY: 0 });
    expect(screen.getByTestId("band")).toHaveTextContent(String(LIMITS.max));
    // Past the band's floor the other way.
    fireEvent.pointerMove(sep, { pointerId: 1, clientY: 900 });
    expect(screen.getByTestId("band")).toHaveTextContent(String(LIMITS.min));
    fireEvent.pointerMove(sep, { pointerId: 1, clientY: 480 });
    fireEvent.pointerUp(sep, { pointerId: 1, clientY: 480 });
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledWith(320);
    expect(screen.getByTestId("band")).toHaveTextContent("320");
  });

  it("a cancelled drag goes back to the remembered split; a press without movement remembers nothing", () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    const sep = screen.getByRole("separator");
    fireEvent.pointerDown(sep, { pointerId: 2, clientY: 500, button: 0 });
    fireEvent.pointerMove(sep, { pointerId: 2, clientY: 450 });
    fireEvent.pointerCancel(sep, { pointerId: 2 });
    expect(screen.getByTestId("band")).toHaveTextContent("300");
    fireEvent.pointerDown(sep, { pointerId: 3, clientY: 500, button: 0 });
    fireEvent.pointerUp(sep, { pointerId: 3, clientY: 500 });
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("a lost pointer capture ends the drag without remembering it", () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    const sep = screen.getByRole("separator");
    fireEvent.pointerDown(sep, { pointerId: 4, clientY: 500, button: 0 });
    fireEvent.pointerMove(sep, { pointerId: 4, clientY: 450 });
    expect(screen.getByTestId("band")).toHaveTextContent("350");
    fireEvent.lostPointerCapture(sep, { pointerId: 4 });
    expect(screen.getByTestId("band")).toHaveTextContent("300");
    // The drag is over: a later move or up does nothing.
    fireEvent.pointerMove(sep, { pointerId: 4, clientY: 400 });
    fireEvent.pointerUp(sep, { pointerId: 4, clientY: 400 });
    expect(screen.getByTestId("band")).toHaveTextContent("300");
    expect(onCommit).not.toHaveBeenCalled();
  });

  it("the grab area is taller than the drawn handle and starts a drag", () => {
    render(<Harness />);
    const hit = screen.getByTestId("band-splitter-hit");
    expect(hit).toHaveClass("-top-1.5", "-bottom-1.5");
    fireEvent.pointerDown(hit, { pointerId: 5, clientY: 500, button: 0 });
    fireEvent.pointerMove(hit, { pointerId: 5, clientY: 480 });
    expect(screen.getByTestId("band")).toHaveTextContent("320");
  });

  it("arrow keys step, Shift steps further, Home and End go to the limits", () => {
    render(<Harness />);
    const sep = screen.getByRole("separator");
    fireEvent.keyDown(sep, { key: "ArrowUp" });
    expect(screen.getByTestId("band")).toHaveTextContent("316");
    fireEvent.keyDown(sep, { key: "ArrowDown", shiftKey: true });
    expect(screen.getByTestId("band")).toHaveTextContent("252");
    fireEvent.keyDown(sep, { key: "Home" });
    expect(screen.getByTestId("band")).toHaveTextContent(String(LIMITS.max));
    expect(sep).toHaveAttribute("aria-valuenow", String(ROOM - LIMITS.max));
    fireEvent.keyDown(sep, { key: "ArrowUp" });
    expect(screen.getByTestId("band")).toHaveTextContent(String(LIMITS.max));
    fireEvent.keyDown(sep, { key: "End" });
    expect(screen.getByTestId("band")).toHaveTextContent(String(LIMITS.min));
  });

  it("a double-click asks for the band-large preset", () => {
    const onToggleLarge = vi.fn();
    render(<Harness onToggleLarge={onToggleLarge} />);
    fireEvent.doubleClick(screen.getByRole("separator"));
    expect(onToggleLarge).toHaveBeenCalledTimes(1);
  });
});
