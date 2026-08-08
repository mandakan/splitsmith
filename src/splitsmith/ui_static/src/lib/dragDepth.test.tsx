/**
 * Depth-counted drag tracking (add-videos UX rework).
 *
 * dragenter/dragleave fire per child crossed, so a naive boolean
 * flickers. These tests pin the counter behavior: nested enter/leave
 * pairs keep the state active, drop resets it, non-file drags are
 * ignored, and the disabled window hook attaches nothing.
 */
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useElementFileDrag, useWindowFileDrag } from "@/lib/dragDepth";

const fileDrag = { dataTransfer: { types: ["Files"] } };

describe("useWindowFileDrag", () => {
  it("stays active across nested dragenter/dragleave pairs", () => {
    const { result } = renderHook(() => useWindowFileDrag(true));
    act(() => {
      fireEvent.dragEnter(window, fileDrag);
    });
    act(() => {
      fireEvent.dragEnter(window, fileDrag);
    });
    act(() => {
      fireEvent.dragLeave(window, fileDrag);
    });
    expect(result.current).toBe(true);
    act(() => {
      fireEvent.dragLeave(window, fileDrag);
    });
    expect(result.current).toBe(false);
  });

  it("resets on drop", () => {
    const { result } = renderHook(() => useWindowFileDrag(true));
    act(() => {
      fireEvent.dragEnter(window, fileDrag);
    });
    expect(result.current).toBe(true);
    act(() => {
      fireEvent.drop(window, fileDrag);
    });
    expect(result.current).toBe(false);
  });

  it("ignores non-file drags and does nothing when disabled", () => {
    const { result } = renderHook(() => useWindowFileDrag(true));
    act(() => {
      fireEvent.dragEnter(window, { dataTransfer: { types: ["text/plain"] } });
    });
    expect(result.current).toBe(false);

    const off = renderHook(() => useWindowFileDrag(false));
    act(() => {
      fireEvent.dragEnter(window, fileDrag);
    });
    expect(off.result.current).toBe(false);
  });
});

function Zone() {
  const { dragging, reset, handlers } = useElementFileDrag();
  return (
    <div
      data-testid="zone"
      data-dragging={dragging ? "1" : "0"}
      {...handlers}
      onDrop={(e) => {
        e.preventDefault();
        reset();
      }}
    >
      <span data-testid="child">child</span>
    </div>
  );
}

describe("useElementFileDrag", () => {
  it("does not flicker when the cursor crosses a child element", () => {
    render(<Zone />);
    const zone = screen.getByTestId("zone");
    const child = screen.getByTestId("child");
    fireEvent.dragEnter(zone, fileDrag);
    fireEvent.dragEnter(child, fileDrag); // bubbles to zone -> depth 2
    fireEvent.dragLeave(child, fileDrag); // depth 1 -> still dragging
    expect(zone).toHaveAttribute("data-dragging", "1");
    fireEvent.dragLeave(zone, fileDrag);
    expect(zone).toHaveAttribute("data-dragging", "0");
  });

  it("reset() clears the state on drop", () => {
    render(<Zone />);
    const zone = screen.getByTestId("zone");
    fireEvent.dragEnter(zone, fileDrag);
    expect(zone).toHaveAttribute("data-dragging", "1");
    fireEvent.drop(zone, fileDrag);
    expect(zone).toHaveAttribute("data-dragging", "0");
  });
});
