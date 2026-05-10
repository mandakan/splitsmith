import { useEffect, useRef, type RefObject } from "react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Releases decoded media buffers held by an HTMLMediaElement on unmount.
// Why: Chrome (and others) keep demux + decoded-frame state attached to
// the element even after React removes the node; the only reliable way
// to free it is pause + remove src + load(). We snapshot ref.current
// after every commit so the cleanup still has a handle even when the
// element mounts later than the parent (conditional rendering) and even
// after React clears the original ref during the unmount commit.
export function useReleaseMediaOnUnmount<T extends HTMLMediaElement>(
  ref: RefObject<T | null>,
): void {
  // Use a separate ref so React's commit-phase ref nulling doesn't
  // strip our handle before the cleanup runs.
  const lastSeen = useRef<T | null>(null);
  // Re-snapshot on every commit so we always have the most recent
  // mounted element (handles conditional <audio>/<video> rendering).
  useEffect(() => {
    if (ref.current) lastSeen.current = ref.current;
  });
  useEffect(() => {
    return () => {
      const el = lastSeen.current;
      if (!el) return;
      try {
        el.pause();
        el.removeAttribute("src");
        el.load();
      } catch {
        /* element already detached */
      }
      lastSeen.current = null;
    };
    // lastSeen is stable for the lifetime of the component instance;
    // we only want one cleanup, on unmount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
