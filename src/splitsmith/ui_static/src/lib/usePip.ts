/**
 * The PiP state a page holds (issue #1406): which camera is big, which is
 * in the inset, and the actions its key handler and the inset's buttons
 * call. The rules are lib/pip.ts; this only keeps the state, resets it to
 * the primary when ``stageKey`` changes and reports a new big camera.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  cyclePip,
  initialPip,
  normalizePip,
  pipCounter,
  swapPip,
  type PipCamera,
  type PipState,
} from "@/lib/pip";

export interface PipController {
  cameras: readonly PipCamera[];
  state: PipState;
  /** The camera the page shows in its own big player. */
  big: PipCamera | null;
  /** The camera in the inset; ``null`` hides it. */
  inset: PipCamera | null;
  /** "2 / 3" with three or more syncable cameras, else ``null``. */
  counter: { n: number; total: number } | null;
  /** The primary camera (audio and beep source), wherever it sits. */
  primary: PipCamera | null;
  swap: () => void;
  /** C (+1) / Shift+C (-1). */
  cycle: (dir: 1 | -1) => void;
}

export function usePip(args: {
  cameras: readonly PipCamera[];
  /** Any value naming the stage (and shooter); a change resets to the
   *  primary, the audio and beep source. */
  stageKey: string | number;
  /** Called when the big camera changes (a swap, a two-camera cycle, a
   *  reset on a new stage); not on mount. */
  onBigChange?: (camera: PipCamera | null) => void;
}): PipController {
  const { cameras, stageKey, onBigChange } = args;
  const [held, setHeld] = useState(() => ({ key: stageKey, state: initialPip(cameras) }));

  // A new stage: reset during render, so the first frame of the new stage
  // never shows the old stage's swap.
  let current = held;
  if (held.key !== stageKey) {
    current = { key: stageKey, state: initialPip(cameras) };
    setHeld(current);
  }
  const state = normalizePip(current.state, cameras);

  const swap = useCallback(() => {
    setHeld((prev) => ({ key: prev.key, state: swapPip(normalizePip(prev.state, cameras)) }));
  }, [cameras]);

  const cycle = useCallback(
    (dir: 1 | -1) => {
      setHeld((prev) => ({ key: prev.key, state: cyclePip(normalizePip(prev.state, cameras), cameras, dir) }));
    },
    [cameras],
  );

  const big = cameras.find((c) => c.id === state.big) ?? null;
  const inset = state.inset === null ? null : (cameras.find((c) => c.id === state.inset) ?? null);
  const primary = cameras.find((c) => c.primary) ?? cameras[0] ?? null;

  const lastBig = useRef<string | null>(state.big);
  const report = useRef(onBigChange);
  useEffect(() => {
    report.current = onBigChange;
  }, [onBigChange]);
  useEffect(() => {
    if (lastBig.current === state.big) return;
    lastBig.current = state.big;
    report.current?.(cameras.find((c) => c.id === state.big) ?? null);
  }, [state.big, cameras]);

  return { cameras, state, big, inset, counter: pipCounter(state, cameras), primary, swap, cycle };
}
