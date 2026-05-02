/**
 * Static-peaks waveform with pointer-driven scrubbing.
 *
 * Issue #15: the audit screen renders the primary's audio as a static canvas
 * and overlays the active video's playhead. Scrubbing the waveform updates
 * the playback time directly -- no two-element sync drift.
 *
 * This component is the substrate. It is intentionally markerless; the marker
 * layer composes on top in a sibling overlay (added in Step 3).
 *
 * Contract:
 *   - `peaks` is an array of normalized magnitudes (0..1) computed server-side
 *     by `splitsmith.waveform.compute_peaks`. The component does not decode
 *     audio.
 *   - `currentTime` drives the playhead position. The component does not own
 *     playback state; the parent feeds it from the active video element via
 *     `requestAnimationFrame` (see Step 2's VideoPanel).
 *   - `onScrub(t)` fires while the user drags or clicks. The parent is
 *     responsible for setting `video.currentTime` (with beep-offset math when
 *     the active video is a secondary). Throttling to rAF cadence happens
 *     here so the parent doesn't have to.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";

interface WaveformProps {
  peaks: number[];
  duration: number;
  currentTime: number;
  onScrub: (timeSeconds: number) => void;
  onScrubEnd?: () => void;
  beepTime?: number | null;
  height?: number;
  className?: string;
  ariaLabel?: string;
}

export function Waveform({
  peaks,
  duration,
  currentTime,
  onScrub,
  onScrubEnd,
  beepTime,
  height = 128,
  className,
  ariaLabel = "Audio waveform -- drag to scrub",
}: WaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const dpr = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
  const draggingRef = useRef(false);
  const pendingTimeRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);

  // Track container width so the canvas re-renders crisply on resize.
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.floor(entry.contentRect.width);
        if (w > 0) setWidth(w);
      }
    });
    observer.observe(el);
    setWidth(Math.floor(el.getBoundingClientRect().width));
    return () => observer.disconnect();
  }, []);

  const cssVar = useCallback((name: string, fallback: string) => {
    if (typeof window === "undefined") return fallback;
    const root = document.documentElement;
    const value = getComputedStyle(root).getPropertyValue(name).trim();
    return value || fallback;
  }, []);

  // Draw bars + playhead + optional beep marker.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || width === 0) return;

    const cssWidth = width;
    const cssHeight = height;
    canvas.width = Math.floor(cssWidth * dpr);
    canvas.height = Math.floor(cssHeight * dpr);
    canvas.style.width = `${cssWidth}px`;
    canvas.style.height = `${cssHeight}px`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);

    const barColor = cssVar("--waveform-bar", "#888");
    const playheadColor = cssVar("--waveform-playhead", "#d55e00");
    const beepColor = cssVar("--waveform-beep", "#0072b2");

    // Bars: one per peak, scaled to canvas width so the strip fills the box.
    const n = peaks.length;
    if (n > 0) {
      const barStride = cssWidth / n;
      const barWidth = Math.max(1, barStride * 0.9);
      const halfH = cssHeight / 2;
      ctx.fillStyle = barColor;
      for (let i = 0; i < n; i++) {
        const p = peaks[i];
        const h = Math.max(1, p * (cssHeight - 2));
        const x = i * barStride + (barStride - barWidth) / 2;
        ctx.fillRect(x, halfH - h / 2, barWidth, h);
      }
    }

    if (beepTime != null && duration > 0 && beepTime >= 0 && beepTime <= duration) {
      const x = (beepTime / duration) * cssWidth;
      ctx.strokeStyle = beepColor;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(x + 0.5, 0);
      ctx.lineTo(x + 0.5, cssHeight);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    if (duration > 0) {
      const x = (Math.min(Math.max(currentTime, 0), duration) / duration) * cssWidth;
      ctx.strokeStyle = playheadColor;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, cssHeight);
      ctx.stroke();
    }
  }, [peaks, duration, currentTime, beepTime, width, height, dpr, cssVar]);

  const flushScrub = useCallback(() => {
    rafRef.current = null;
    const t = pendingTimeRef.current;
    if (t != null) {
      pendingTimeRef.current = null;
      onScrub(t);
    }
  }, [onScrub]);

  const queueScrub = useCallback(
    (t: number) => {
      pendingTimeRef.current = t;
      if (rafRef.current == null) {
        rafRef.current = requestAnimationFrame(flushScrub);
      }
    },
    [flushScrub],
  );

  const timeFromEvent = useCallback(
    (clientX: number): number => {
      const el = containerRef.current;
      if (!el || duration <= 0) return 0;
      const rect = el.getBoundingClientRect();
      const ratio = (clientX - rect.left) / rect.width;
      return Math.min(Math.max(ratio, 0), 1) * duration;
    },
    [duration],
  );

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const el = containerRef.current;
      if (!el) return;
      el.setPointerCapture(e.pointerId);
      draggingRef.current = true;
      queueScrub(timeFromEvent(e.clientX));
    },
    [queueScrub, timeFromEvent],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingRef.current) return;
      queueScrub(timeFromEvent(e.clientX));
    },
    [queueScrub, timeFromEvent],
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      const el = containerRef.current;
      if (el && el.hasPointerCapture(e.pointerId)) {
        el.releasePointerCapture(e.pointerId);
      }
      // Flush any pending rAF immediately so the final frame lands.
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
        flushScrub();
      }
      onScrubEnd?.();
    },
    [flushScrub, onScrubEnd],
  );

  // Cancel any in-flight rAF on unmount.
  useEffect(() => {
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  const ariaValueText = useMemo(() => {
    if (duration <= 0) return "0:00 of 0:00";
    return `${formatTime(currentTime)} of ${formatTime(duration)}`;
  }, [currentTime, duration]);

  return (
    <div
      ref={containerRef}
      role="slider"
      aria-label={ariaLabel}
      aria-valuemin={0}
      aria-valuemax={Math.max(duration, 0)}
      aria-valuenow={Math.min(Math.max(currentTime, 0), Math.max(duration, 0))}
      aria-valuetext={ariaValueText}
      tabIndex={0}
      className={cn(
        "relative w-full select-none rounded-md bg-muted/40 ring-1 ring-border",
        "cursor-ew-resize touch-none",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
      style={{ height }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
    >
      <canvas ref={canvasRef} className="block" />
    </div>
  );
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  const ms = Math.floor((seconds - total) * 1000);
  return `${m}:${s.toString().padStart(2, "0")}.${ms.toString().padStart(3, "0")}`;
}
