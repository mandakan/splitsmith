import { act, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetTimelinePrefsForTests } from "@/lib/timelinePrefs";
import type { Zoom } from "@/lib/timelineView";

import { Timeline } from "./Timeline";

const VIEWPORT = 1000;

function Harness(props: { currentTime?: number; onSeek?: (t: number) => void; initialZoom?: Zoom; onZoom?: (z: Zoom) => void }) {
  const [zoom, setZoom] = React.useState<Zoom>(props.initialZoom ?? null);
  return (
    <Timeline
      duration={10}
      origin={0}
      fps={30}
      currentTime={props.currentTime ?? 0}
      onSeek={props.onSeek ?? vi.fn()}
      zoom={zoom}
      onZoomChange={(z) => {
        setZoom(z);
        props.onZoom?.(z);
      }}
      tracks={[{ id: "a", rows: [{ label: "Audio", height: 40 }], render: () => <div data-testid="track-a" /> }]}
    />
  );
}

beforeEach(() => {
  vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(VIEWPORT);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const w = this.dataset.testid === "timeline-content" ? parseFloat(this.style.width) || VIEWPORT : VIEWPORT;
    const left = this.dataset.testid === "timeline-content" ? -(screen.queryByTestId("timeline-host")?.scrollLeft ?? 0) : 0;
    return { width: w, height: 40, left, top: 0, right: left + w, bottom: 40, x: left, y: 0, toJSON: () => ({}) };
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  resetTimelinePrefsForTests();
});

const wheel = (el: Element, init: WheelEventInit) => {
  const e = new WheelEvent("wheel", { bubbles: true, cancelable: true, ...init });
  act(() => {
    el.dispatchEvent(e);
  });
  return e;
};

describe("Timeline", () => {
  it("leaves a plain vertical wheel to the page by default", () => {
    const onZoom = vi.fn();
    render(<Harness onZoom={onZoom} />);
    const e = wheel(screen.getByTestId("timeline-host"), { deltaY: 100, clientX: 500 });
    expect(e.defaultPrevented).toBe(false);
    expect(onZoom).not.toHaveBeenCalled();
  });

  it("zooms around the pointer on Ctrl+wheel and consumes it", () => {
    const onZoom = vi.fn();
    render(<Harness onZoom={onZoom} />);
    const e = wheel(screen.getByTestId("timeline-host"), { deltaY: -200, ctrlKey: true, clientX: 800 });
    expect(e.defaultPrevented).toBe(true);
    expect(onZoom).toHaveBeenCalledWith(expect.any(Number));
    expect(onZoom.mock.calls[0][0]).toBeGreaterThan(1);
  });

  it("consumes a sideways swipe even at Fit, so it never navigates back", () => {
    render(<Harness />);
    const e = wheel(screen.getByTestId("timeline-host"), { deltaX: -60, clientX: 100 });
    expect(e.defaultPrevented).toBe(true);
  });

  it("zooms on a plain wheel once the switch is on, and the switch is shared", () => {
    const onZoom = vi.fn();
    render(<Harness onZoom={onZoom} />);
    fireEvent.click(screen.getByRole("button", { name: "Timeline options" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: /Wheel zooms the timeline/ }));
    const e = wheel(screen.getByTestId("timeline-host"), { deltaY: -100, clientX: 500 });
    expect(e.defaultPrevented).toBe(true);
    expect(onZoom).toHaveBeenCalled();
    expect(window.localStorage.getItem("splitsmith.timeline.wheelZooms")).toBe("on");
  });

  it("steps with the buttons and returns to Fit", () => {
    const onZoom = vi.fn();
    render(<Harness onZoom={onZoom} />);
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(onZoom).toHaveBeenLastCalledWith(1.5);
    expect(screen.getByTestId("timeline-content").style.width).toBe("1500px");
    fireEvent.click(screen.getByRole("button", { name: "Fit" }));
    expect(onZoom).toHaveBeenLastCalledWith(null);
    expect(screen.getByTestId("timeline-content").style.width).toBe("1000px");
  });

  it("zooms with the keys", () => {
    const onZoom = vi.fn();
    render(<Harness onZoom={onZoom} />);
    fireEvent.keyDown(window, { key: "+" });
    expect(onZoom).toHaveBeenLastCalledWith(1.5);
    fireEvent.keyDown(window, { key: "0" });
    expect(onZoom).toHaveBeenLastCalledWith(null);
  });

  it("seeks from a ruler click without snapping", () => {
    const onSeek = vi.fn();
    render(<Harness onSeek={onSeek} />);
    fireEvent.click(screen.getByTestId("timeline-ruler"), { clientX: 437 });
    expect(onSeek).toHaveBeenCalledWith(expect.closeTo(4.37, 3));
  });

  it("labels the gutter and renders each track", () => {
    render(<Harness />);
    expect(screen.getByText("Audio")).toBeInTheDocument();
    expect(screen.getByTestId("track-a")).toBeInTheDocument();
  });

  it("follows the playhead while playing, but not while a pointer is down in the band", () => {
    const { rerender } = render(<Harness initialZoom={4} currentTime={0} />);
    const host = screen.getByTestId("timeline-host");
    // 4000 px of content: t = 9.5 s is at 3800 px, outside the first window.
    fireEvent.pointerDown(screen.getByTestId("track-a"), { pointerId: 1, button: 0 });
    rerender(<Harness initialZoom={4} currentTime={9.5} />);
    expect(host.scrollLeft).toBe(0);
    act(() => {
      window.dispatchEvent(new Event("pointerup"));
    });
    rerender(<Harness initialZoom={4} currentTime={9.6} />);
    expect(host.scrollLeft).toBeGreaterThan(0);
  });
});
