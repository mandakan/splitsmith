/**
 * The band's audio track. Draws only the visible window into a canvas the
 * size of the viewport (a full-content canvas passes Chromium's 32767 px
 * side limit at high zoom), held in view with `sticky`. Bars are the
 * loudest peak under each column (`lib/timelineView.columnPeaks`).
 */
import { useEffect, useRef } from "react";

import { columnPeaks } from "@/lib/timelineView";

import type { TimelineGeom } from "./types";

export interface WaveformTrackProps {
  /** Server peaks over the clip, or null when there is no audio for this stage. */
  peaks: number[] | null;
  clipDuration: number;
  /** Clip seconds at the band's left and right edge. */
  from: number;
  to: number;
  geom: TimelineGeom;
  height: number;
}

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function WaveformTrack({ peaks, clipDuration, from, to, geom, height }: WaveformTrackProps) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const { contentWidth, viewportWidth, scrollLeft } = geom;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !peaks || viewportWidth <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.floor(viewportWidth));
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(height * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, height);
    ctx.fillStyle = cssVar("--color-muted", "#8E939B");
    const cols = columnPeaks(peaks, clipDuration, from, to, contentWidth, scrollLeft, w);
    const mid = height / 2;
    for (let x = 0; x < w; x += 2) {
      const p = Math.max(cols[x] ?? 0, cols[x + 1] ?? 0);
      const h = Math.max(1, p * (height - 4));
      ctx.fillRect(x, mid - h / 2, 1.5, h);
    }
  }, [peaks, clipDuration, from, to, contentWidth, viewportWidth, scrollLeft, height]);

  if (!peaks) {
    return (
      <div
        className="sticky left-0 flex items-center px-2 text-sm text-muted"
        style={{ width: viewportWidth || "100%", height }}
      >
        No audio
      </div>
    );
  }
  return <canvas ref={ref} data-testid="waveform-track" className="pointer-events-none sticky left-0 block" />;
}
