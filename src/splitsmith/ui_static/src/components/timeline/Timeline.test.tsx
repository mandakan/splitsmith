import { act, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetTimelinePrefsForTests } from "@/lib/timelinePrefs";
import type { Zoom } from "@/lib/timelineView";

import { Timeline } from "./Timeline";

const VIEWPORT = 1000;

function Harness(props: {
  currentTime?: number;
  onSeek?: (t: number) => void;
  initialZoom?: Zoom;
  onZoom?: (z: Zoom) => void;
  playing?: boolean;
  onScrubEnd?: () => void;
  onDoubleClick?: (t: number, shiftKey: boolean) => void;
}) {
  const [zoom, setZoom] = React.useState<Zoom>(props.initialZoom ?? null);
  return (
    <Timeline
      duration={10}
      origin={0}
      fps={30}
      currentTime={props.currentTime ?? 0}
      playing={props.playing}
      onSeek={props.onSeek ?? vi.fn()}
      onScrubEnd={props.onScrubEnd}
      zoom={zoom}
      onZoomChange={(z) => {
        setZoom(z);
        props.onZoom?.(z);
      }}
      tracks={[
        { id: "a", rows: [{ label: "Audio", height: 40 }], render: () => <div data-testid="track-a" />, seekable: true },
        { id: "b", rows: [{ label: "Shots", height: 40 }], render: () => <div data-testid="track-b" /> },
        {
          id: "s",
          rows: [{ label: "Scrub", height: 40 }],
          seekable: true,
          onDoubleClick: props.onDoubleClick,
          render: () => (
            <div data-testid="track-s">
              <button data-audit-marker data-testid="marker" />
            </div>
          ),
        },
      ]}
    />
  );
}

/**
 * jsdom has no rAF timing. Stub it on top of fake timers so a scheduled
 * frame becomes a pending timer `vi.runOnlyPendingTimers()` can flush,
 * matching the real one-callback-per-frame contract (latest queued wins).
 */
function stubRequestAnimationFrame() {
  const realRaf = window.requestAnimationFrame;
  const realCaf = window.cancelAnimationFrame;
  let nextHandle = 1;
  const timeouts = new Map<number, ReturnType<typeof setTimeout>>();
  window.requestAnimationFrame = (cb: FrameRequestCallback) => {
    const handle = nextHandle++;
    timeouts.set(
      handle,
      setTimeout(() => {
        timeouts.delete(handle);
        cb(performance.now());
      }, 0),
    );
    return handle;
  };
  window.cancelAnimationFrame = (handle: number) => {
    const t = timeouts.get(handle);
    if (t !== undefined) {
      clearTimeout(t);
      timeouts.delete(handle);
    }
  };
  return () => {
    window.requestAnimationFrame = realRaf;
    window.cancelAnimationFrame = realCaf;
  };
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

  it("zooms on a plain wheel once the switch is on, and the switch is shared across mounted bands", () => {
    const onZoomA = vi.fn();
    const onZoomB = vi.fn();
    render(
      <>
        <Harness onZoom={onZoomA} />
        <Harness onZoom={onZoomB} />
      </>,
    );
    // Toggle the switch in the first band's menu...
    fireEvent.click(screen.getAllByRole("button", { name: "Timeline options" })[0]);
    fireEvent.click(screen.getAllByRole("menuitemcheckbox", { name: /Wheel zooms the timeline/ })[0]);
    // ...and a plain wheel over the second, untouched band zooms too.
    const e = wheel(screen.getAllByTestId("timeline-host")[1], { deltaY: -100, clientX: 500 });
    expect(e.defaultPrevented).toBe(true);
    expect(onZoomB).toHaveBeenCalled();
    expect(onZoomA).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("splitsmith.timeline.wheelZooms")).toBe("on");
  });

  it("pans with Shift+wheel, at a zoom where there is room to scroll", () => {
    render(<Harness initialZoom={4} />);
    const host = screen.getByTestId("timeline-host");
    const e = wheel(host, { deltaY: 60, shiftKey: true, clientX: 500 });
    expect(e.defaultPrevented).toBe(true);
    expect(host.scrollLeft).toBe(60);
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

  it("draws the playhead at its mapped position, at Fit and at a zoom", () => {
    // currentTime 4 on a 10 s domain: at Fit (1000 px viewport) that's 400 px;
    // at zoom 2 (2000 px content) it's 800 px.
    const first = render(<Harness currentTime={4} />);
    expect(screen.getByTestId("timeline-playhead")).toHaveStyle({ left: "400px" });
    first.unmount();
    render(<Harness currentTime={4} initialZoom={2} />);
    expect(screen.getByTestId("timeline-playhead")).toHaveStyle({ left: "800px" });
  });

  it("while playing, follows with the edge rule, but not while a pointer is down in the band", () => {
    const { rerender } = render(<Harness initialZoom={4} currentTime={0} playing />);
    const host = screen.getByTestId("timeline-host");
    // 4000 px of content: t = 9.5 s is at 3800 px, outside the first window.
    fireEvent.pointerDown(screen.getByTestId("track-a"), { pointerId: 1, button: 0 });
    rerender(<Harness initialZoom={4} currentTime={9.5} playing />);
    expect(host.scrollLeft).toBe(0);
    act(() => {
      window.dispatchEvent(new Event("pointerup"));
    });
    rerender(<Harness initialZoom={4} currentTime={9.6} playing />);
    expect(host.scrollLeft).toBeGreaterThan(0);
  });

  it("while paused, a seek that lands inside the visible window does not recentre (I1)", () => {
    // Regression for: Coach's currentTime arrives async (timeupdate) after a
    // seek, i.e. after pointerup already cleared pointerDown -- so a lane or
    // ruler click used to hit the playing edge-rule's 10 % margin and jump
    // the view even though the clicked time is already on screen. At zoom 4
    // (4000 px content, 1000 px viewport) a ruler click at viewport x 950
    // seeks to t = 2.375 s, whose playhead (950 px) is inside [0, 1000].
    function PausedHarness() {
      const [currentTime, setCurrentTime] = React.useState(0);
      const [zoom, setZoom] = React.useState<Zoom>(4);
      return (
        <Timeline
          duration={10}
          origin={0}
          fps={30}
          currentTime={currentTime}
          playing={false}
          onSeek={setCurrentTime}
          zoom={zoom}
          onZoomChange={setZoom}
          tracks={[{ id: "a", rows: [{ label: "Audio", height: 40 }], render: () => <div data-testid="track-a" /> }]}
        />
      );
    }
    render(<PausedHarness />);
    const host = screen.getByTestId("timeline-host");
    fireEvent.click(screen.getByTestId("timeline-ruler"), { clientX: 950 });
    expect(host.scrollLeft).toBe(0);
  });

  it("while paused, a seek fully off-screen scrolls the playhead into view (I1)", () => {
    function PausedHarness() {
      const [currentTime, setCurrentTime] = React.useState(0);
      const [zoom, setZoom] = React.useState<Zoom>(4);
      return (
        <Timeline
          duration={10}
          origin={0}
          fps={30}
          currentTime={currentTime}
          playing={false}
          onSeek={setCurrentTime}
          zoom={zoom}
          onZoomChange={setZoom}
          tracks={[{ id: "a", rows: [{ label: "Audio", height: 40 }], render: () => <div data-testid="track-a" /> }]}
        />
      );
    }
    render(<PausedHarness />);
    const host = screen.getByTestId("timeline-host");
    // t = 9.975 s -> playhead at 3990 px, wholly outside [0, 1000].
    fireEvent.click(screen.getByTestId("timeline-ruler"), { clientX: 3990 });
    expect(host.scrollLeft).toBe(3000);
  });

  it("keeps the left-edge time anchored when a resize changes the viewport while zoomed (I2)", () => {
    let capturedCb: ResizeObserverCallback | null = null;
    class CapturingResizeObserver {
      constructor(cb: ResizeObserverCallback) {
        capturedCb = cb;
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", CapturingResizeObserver);
    const widthSpy = vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(1000);
    render(<Harness initialZoom={4} />);
    const host = screen.getByTestId("timeline-host");
    // content 4000 px, pxPerSec 400: scrollLeft 500 is left-edge time 1.25 s.
    host.scrollLeft = 500;
    // The sidebar opens: viewport narrows to 500 px.
    widthSpy.mockReturnValue(500);
    act(() => {
      capturedCb?.([] as unknown as ResizeObserverEntry[], {} as ResizeObserver);
    });
    // content becomes round(500 * 4) = 2000 px, pxPerSec 200: scrollLeft 250
    // keeps the same 1.25 s left edge.
    expect(screen.getByTestId("timeline-content").style.width).toBe("2000px");
    expect(host.scrollLeft).toBeCloseTo(250, 0);
    vi.unstubAllGlobals();
  });

  it("seeks from a press on a seekable track row, but not a non-seekable one (M5)", () => {
    // Was a plain click; a press-and-release with no movement now seeks
    // once (the click handler was replaced by press-to-scrub below).
    const onSeek = vi.fn();
    render(<Harness onSeek={onSeek} />);
    fireEvent.pointerDown(screen.getByTestId("track-a"), { pointerId: 1, button: 0, clientX: 437 });
    expect(onSeek).toHaveBeenCalledWith(expect.closeTo(4.37, 3));
    fireEvent.pointerUp(screen.getByTestId("track-a"), { pointerId: 1, clientX: 437 });
    onSeek.mockClear();
    fireEvent.pointerDown(screen.getByTestId("track-b"), { pointerId: 2, button: 0, clientX: 437 });
    fireEvent.click(screen.getByTestId("track-b"), { clientX: 437 });
    expect(onSeek).not.toHaveBeenCalled();
  });

  it("anchors a Ctrl+wheel zoom under the pointer even with follow on", () => {
    // Follow defaults on and currentTime stays 0 (far outside the new
    // view), which is exactly the case that used to let the follow
    // effect re-run on the content-width change and overwrite the
    // anchored scroll back to 0.
    render(<Harness />);
    const host = screen.getByTestId("timeline-host");
    wheel(host, { deltaY: -400, ctrlKey: true, clientX: 900 });
    const after = parseFloat(screen.getByTestId("timeline-content").style.width);
    expect(host.scrollLeft).toBeCloseTo((900 / 1000) * after - 900, 1);
  });

  it("keeps the playhead's viewport position stable when zooming with the button", () => {
    // currentTime 3 on a 10 s domain is at 300 px at Fit (viewport 1000).
    render(<Harness currentTime={3} />);
    const host = screen.getByTestId("timeline-host");
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    const content = parseFloat(screen.getByTestId("timeline-content").style.width);
    const playheadViewportX = (3 / 10) * content - host.scrollLeft;
    expect(playheadViewportX).toBeGreaterThan(299);
    expect(playheadViewportX).toBeLessThan(301);
  });

  it("compounds two Ctrl+wheel zooms dispatched before a re-render", () => {
    const onZoom = vi.fn();
    render(<Harness onZoom={onZoom} />);
    const host = screen.getByTestId("timeline-host");
    act(() => {
      host.dispatchEvent(new WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: -200, ctrlKey: true, clientX: 500 }));
      host.dispatchEvent(new WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: -200, ctrlKey: true, clientX: 500 }));
    });
    expect(onZoom).toHaveBeenCalledTimes(2);
    expect(onZoom.mock.calls[0][0]).toBeCloseTo(1.6487, 3);
    expect(onZoom.mock.calls[1][0]).toBeCloseTo(2.718, 2);
  });

  describe("press-to-scrub and double-click on seekable tracks", () => {
    let restoreRaf: () => void;

    beforeEach(() => {
      vi.useFakeTimers();
      restoreRaf = stubRequestAnimationFrame();
    });

    afterEach(() => {
      restoreRaf();
      vi.useRealTimers();
    });

    it("scrubs while dragging a seekable track, one seek per frame, and ends once", () => {
      const onSeek = vi.fn();
      const onScrubEnd = vi.fn();
      render(<Harness onSeek={onSeek} onScrubEnd={onScrubEnd} />);
      const row = screen.getByTestId("track-s").parentElement!;
      fireEvent.pointerDown(row, { pointerId: 1, button: 0, clientX: 100 });
      fireEvent.pointerMove(row, { pointerId: 1, clientX: 200 });
      fireEvent.pointerMove(row, { pointerId: 1, clientX: 300 });
      act(() => vi.runOnlyPendingTimers());
      fireEvent.pointerUp(row, { pointerId: 1, clientX: 300 });
      expect(onSeek.mock.calls.map((c) => c[0])).toEqual([expect.closeTo(1, 2), expect.closeTo(3, 2)]);
      expect(onScrubEnd).toHaveBeenCalledTimes(1);
    });

    it("does not follow while scrubbing, and a paused release does not recentre", () => {
      // Zoom 4 (4000 px content, 1000 px viewport): a drag from clientX 900
      // to 980 seeks within [0, 10] s and must never move the host while
      // the pointer is down (I1's paused-release rule covers the release
      // itself, so this only has to show the drag doesn't fight it).
      const onSeek = vi.fn();
      const { rerender } = render(<Harness initialZoom={4} currentTime={0} onSeek={onSeek} />);
      const host = screen.getByTestId("timeline-host");
      const row = screen.getByTestId("track-a").parentElement!;
      fireEvent.pointerDown(row, { pointerId: 1, button: 0, clientX: 900 });
      rerender(<Harness initialZoom={4} currentTime={onSeek.mock.calls[0][0]} onSeek={onSeek} />);
      expect(host.scrollLeft).toBe(0);
      fireEvent.pointerMove(row, { pointerId: 1, clientX: 980 });
      act(() => vi.runOnlyPendingTimers());
      rerender(<Harness initialZoom={4} currentTime={onSeek.mock.calls.at(-1)![0]} onSeek={onSeek} />);
      expect(host.scrollLeft).toBe(0);
      fireEvent.pointerUp(row, { pointerId: 1, clientX: 980 });
      rerender(<Harness initialZoom={4} currentTime={onSeek.mock.calls.at(-1)![0]} onSeek={onSeek} />);
      expect(host.scrollLeft).toBe(0);
    });

    it("adds on double-click with the shift flag, but never on a marker", () => {
      const onDouble = vi.fn();
      render(<Harness onDoubleClick={onDouble} />);
      fireEvent.doubleClick(screen.getByTestId("track-s").parentElement!, { clientX: 437, shiftKey: true });
      expect(onDouble).toHaveBeenCalledWith(expect.closeTo(4.37, 3), true);
      onDouble.mockClear();
      fireEvent.doubleClick(screen.getByTestId("marker"), { clientX: 437 });
      expect(onDouble).not.toHaveBeenCalled();
    });

    it("a press on a marker inside a seekable track does not scrub", () => {
      const onSeek = vi.fn();
      render(<Harness onSeek={onSeek} />);
      fireEvent.pointerDown(screen.getByTestId("marker"), { pointerId: 2, button: 0, clientX: 437 });
      expect(onSeek).not.toHaveBeenCalled();
    });
  });
});
