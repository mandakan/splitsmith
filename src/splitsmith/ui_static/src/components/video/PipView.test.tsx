/**
 * PipView over fake media elements: jsdom has no decoder, so every
 * <video> gets a plain clock (currentTime, paused, playbackRate) and a
 * fixed box. The big player is the harness's own, as on a page.
 */
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PipCamera } from "@/lib/pip";
import { PIP_CORNER_KEY, resetPipPrefsForTests } from "@/lib/pipPrefs";
import { usePip } from "@/lib/usePip";

import { PipView } from "./PipView";

interface FakeClock {
  t: number;
  paused: boolean;
  rate: number;
}
const clocks = new WeakMap<HTMLMediaElement, FakeClock>();
const clock = (el: HTMLMediaElement): FakeClock => {
  let c = clocks.get(el);
  if (!c) {
    c = { t: 0, paused: true, rate: 1 };
    clocks.set(el, c);
  }
  return c;
};

const saved: Record<string, PropertyDescriptor | undefined> = {};
const PATCHED = ["currentTime", "paused", "playbackRate", "readyState", "duration", "play", "pause"] as const;

beforeEach(() => {
  for (const k of PATCHED) saved[k] = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, k);
  Object.defineProperties(HTMLMediaElement.prototype, {
    currentTime: {
      configurable: true,
      get(this: HTMLMediaElement) {
        return clock(this).t;
      },
      set(this: HTMLMediaElement, v: number) {
        clock(this).t = v;
      },
    },
    paused: {
      configurable: true,
      get(this: HTMLMediaElement) {
        return clock(this).paused;
      },
    },
    playbackRate: {
      configurable: true,
      get(this: HTMLMediaElement) {
        return clock(this).rate;
      },
      set(this: HTMLMediaElement, v: number) {
        clock(this).rate = v;
      },
    },
    readyState: { configurable: true, get: () => 4 },
    duration: { configurable: true, get: () => 60 },
    play: {
      configurable: true,
      value(this: HTMLMediaElement) {
        clock(this).paused = false;
        return Promise.resolve();
      },
    },
    pause: {
      configurable: true,
      value(this: HTMLMediaElement) {
        clock(this).paused = true;
      },
    },
  });
  // Every box is 800 x 450 at the origin: the frame is the whole host.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ left: 0, top: 0, right: 800, bottom: 450, width: 800, height: 450, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect,
  );
  resetPipPrefsForTests();
});

afterEach(() => {
  for (const k of PATCHED) {
    const d = saved[k];
    if (d) Object.defineProperty(HTMLMediaElement.prototype, k, d);
    else delete (HTMLMediaElement.prototype as unknown as Record<string, unknown>)[k];
  }
  vi.restoreAllMocks();
});

const cam = (n: number, beep: number): PipCamera => ({
  id: `c${n}`,
  label: `Cam ${n}`,
  primary: n === 1,
  beepInClip: beep,
  src: `/c${n}.mp4`,
});

const TWO = [cam(1, 5), cam(2, 3)];
const THREE = [cam(1, 5), cam(2, 3), cam(3, 4)];

function Harness({ cameras, stage = 1, onBig }: { cameras: PipCamera[]; stage?: number; onBig?: (id: string | null) => void }) {
  const pip = usePip({ cameras, stageKey: stage, onBigChange: (c) => onBig?.(c?.id ?? null) });
  const [big, setBig] = useState<HTMLVideoElement | null>(null);
  return (
    <div style={{ position: "relative" }}>
      <video ref={setBig} data-testid="big" data-cam={pip.big?.id} src={pip.big?.src ?? undefined} />
      <PipView pip={pip} bigVideo={big} topInset={26} />
    </div>
  );
}

const insetVideo = () => screen.getByTestId("pip-inset-video") as HTMLVideoElement;
const bigVideo = () => screen.getByTestId("big") as HTMLVideoElement;

describe("PipView", () => {
  it("shows the next camera in the inset, top right below the pill, and the primary marked big", () => {
    render(<Harness cameras={TWO} />);
    const inset = screen.getByTestId("pip-inset");
    expect(inset).toHaveAttribute("aria-label", "Inset camera: Cam 2");
    expect(inset).toHaveAttribute("data-corner", "tr");
    // 28% of 800 = 224 wide, 126 high; 10 px in, 26 px below the pill.
    expect(inset.style.width).toBe("224px");
    expect(inset.style.height).toBe("126px");
    expect(inset.style.left).toBe(`${800 - 224 - 10}px`);
    expect(inset.style.top).toBe("36px");
    expect(screen.getByTestId("pip-big-label")).toHaveTextContent(/Cam 1.*Primary/);
    expect(screen.queryByTestId("pip-counter")).toBeNull();
    expect(screen.queryByRole("button", { name: /next camera/i })).toBeNull();
  });

  it("swaps on the swap button; the primary marking and the audio note follow", () => {
    const onBig = vi.fn();
    render(<Harness cameras={TWO} onBig={onBig} />);
    fireEvent.click(screen.getByRole("button", { name: "Swap with the big camera" }));
    expect(bigVideo().dataset.cam).toBe("c2");
    expect(screen.getByTestId("pip-inset")).toHaveAttribute("aria-label", "Inset camera: Cam 1");
    expect(screen.getByTestId("pip-big-label")).toHaveTextContent("Audio + beep: Cam 1");
    expect(screen.getByTestId("pip-inset")).toHaveTextContent("Primary");
    expect(onBig).toHaveBeenCalledTimes(1);
    expect(onBig).toHaveBeenLastCalledWith("c2");
  });

  it("swaps on a click anywhere on the inset, once", () => {
    render(<Harness cameras={TWO} />);
    fireEvent.click(screen.getByTestId("pip-inset"));
    expect(bigVideo().dataset.cam).toBe("c2");
  });

  it("the swap button is a real button: Enter and Space reach it from the keyboard", async () => {
    const user = userEvent.setup();
    render(<Harness cameras={TWO} />);
    await user.tab();
    const button = screen.getByRole("button", { name: "Swap with the big camera" });
    expect(document.activeElement).toBe(button);
    await user.keyboard("{Enter}");
    expect(bigVideo().dataset.cam).toBe("c2");
    // The inset re-rendered with the primary; its swap button is the same one.
    screen.getByRole("button", { name: "Swap with the big camera" }).focus();
    await user.keyboard(" ");
    expect(bigVideo().dataset.cam).toBe("c1");
  });

  it("cycles with three cameras: the counter, the next button, and the hook's cycle both ways", () => {
    let api: ReturnType<typeof usePip> | null = null;
    function Probe() {
      const pip = usePip({ cameras: THREE, stageKey: 1 });
      api = pip;
      const [big, setBig] = useState<HTMLVideoElement | null>(null);
      return (
        <div>
          <video ref={setBig} />
          <PipView pip={pip} bigVideo={big} />
        </div>
      );
    }
    render(<Probe />);
    expect(screen.getByTestId("pip-counter")).toHaveTextContent("2 / 3");
    fireEvent.click(screen.getByRole("button", { name: "Next camera (C)" }));
    expect(screen.getByTestId("pip-counter")).toHaveTextContent("3 / 3");
    expect(api!.big?.id).toBe("c1");
    act(() => api!.cycle(-1));
    expect(screen.getByTestId("pip-counter")).toHaveTextContent("2 / 3");
    act(() => api!.cycle(-1));
    expect(screen.getByTestId("pip-counter")).toHaveTextContent("3 / 3");
  });

  it("the inset follows the big video's seek, play, pause and rate through the beep offsets", async () => {
    render(<Harness cameras={TWO} />);
    const big = bigVideo();
    const inset = insetVideo();
    // Primary beep at 5, Cam 2's at 3: big 0 -> inset 0 (clamped).
    expect(inset.muted).toBe(true);

    big.currentTime = 9;
    fireEvent(big, new Event("seeking"));
    expect(inset.currentTime).toBe(7);

    await act(async () => {
      await big.play();
      fireEvent(big, new Event("play"));
    });
    expect(inset.paused).toBe(false);

    big.playbackRate = 0.5;
    fireEvent(big, new Event("ratechange"));
    expect(inset.playbackRate).toBe(0.5);

    big.pause();
    fireEvent(big, new Event("pause"));
    expect(inset.paused).toBe(true);
  });

  it("corrects drift on timeupdate only past the threshold while playing", async () => {
    render(<Harness cameras={TWO} />);
    const big = bigVideo();
    const inset = insetVideo();
    await act(async () => {
      await big.play();
      fireEvent(big, new Event("play"));
    });
    big.currentTime = 10; // inset target 8
    inset.currentTime = 8.1;
    fireEvent(big, new Event("timeupdate"));
    expect(inset.currentTime).toBe(8.1);
    inset.currentTime = 8.5;
    fireEvent(big, new Event("timeupdate"));
    expect(inset.currentTime).toBe(8);
  });

  it("drags to a corner, snaps there and remembers it", () => {
    const { unmount } = render(<Harness cameras={TWO} />);
    const inset = screen.getByTestId("pip-inset");
    // Inset centre starts at (800-10-112, 36+63) = (678, 99); drag it down-left.
    fireEvent.pointerDown(inset, { pointerId: 1, button: 0, clientX: 678, clientY: 99 });
    fireEvent.pointerMove(inset, { pointerId: 1, clientX: 300, clientY: 380 });
    expect(inset.style.transform).toBe("translate(-378px, 281px)");
    fireEvent.pointerUp(inset, { pointerId: 1, clientX: 300, clientY: 380 });
    fireEvent.click(inset); // the click that ends a drag is not a swap
    expect(bigVideo().dataset.cam).toBe("c1");
    expect(inset).toHaveAttribute("data-corner", "bl");
    expect(inset.style.transform).toBe("");
    expect(window.localStorage.getItem(PIP_CORNER_KEY)).toBe("bl");

    unmount();
    resetPipPrefsForTests();
    render(<Harness cameras={TWO} />);
    expect(screen.getByTestId("pip-inset")).toHaveAttribute("data-corner", "bl");
    expect(screen.getByTestId("pip-inset").style.top).toBe(`${450 - 126 - 10}px`);
  });

  it("a press without movement is a click: it swaps and keeps the corner", () => {
    render(<Harness cameras={TWO} />);
    const inset = screen.getByTestId("pip-inset");
    fireEvent.pointerDown(inset, { pointerId: 1, button: 0, clientX: 678, clientY: 99 });
    fireEvent.pointerMove(inset, { pointerId: 1, clientX: 680, clientY: 100 });
    fireEvent.pointerUp(inset, { pointerId: 1, clientX: 680, clientY: 100 });
    fireEvent.click(inset);
    expect(bigVideo().dataset.cam).toBe("c2");
    expect(inset).toHaveAttribute("data-corner", "tr");
  });

  it("has no inset with one camera", () => {
    render(<Harness cameras={[cam(1, 5)]} />);
    expect(screen.queryByTestId("pip-inset")).toBeNull();
  });
});

describe("usePip", () => {
  it("resets to the primary on a new stage", () => {
    const onBigChange = vi.fn();
    const { result, rerender } = renderHook(({ stage }) => usePip({ cameras: THREE, stageKey: stage, onBigChange }), {
      initialProps: { stage: 1 },
    });
    act(() => result.current.swap());
    expect(result.current.big?.id).toBe("c2");
    rerender({ stage: 2 });
    expect(result.current.state).toEqual({ big: "c1", inset: "c2" });
    expect(onBigChange.mock.calls.map((c) => c[0]?.id)).toEqual(["c2", "c1"]);
  });

  it("keeps the swap while the stage stays", () => {
    const { result, rerender } = renderHook(({ stage }) => usePip({ cameras: TWO, stageKey: stage }), {
      initialProps: { stage: 1 },
    });
    act(() => result.current.cycle(1));
    rerender({ stage: 1 });
    expect(result.current.big?.id).toBe("c2");
  });
});
