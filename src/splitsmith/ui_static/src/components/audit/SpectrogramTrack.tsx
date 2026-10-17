/**
 * The beep step's spectrogram row (spec 2026-10-17): time across, 0 to
 * 8 kHz up, drawn only for the visible window into a viewport-sized sticky
 * canvas like WaveformTrack. A lightness ramp, so it reads without colour;
 * the timer band is two hairlines with a label.
 */
import { useEffect, useMemo, useRef } from "react";

import type { TimelineGeom } from "@/components/timeline/types";
import type { Spectrogram } from "@/lib/api";

export interface SpectrogramTrackProps {
  spectrum: Spectrogram | null;
  /** Clip seconds at the band's left and right edge. */
  from: number;
  to: number;
  geom: TimelineGeom;
  height: number;
  band: { lo: number; hi: number } | null;
}

export function SpectrogramTrack({ spectrum, from, to, geom, height, band }: SpectrogramTrackProps) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const grid = useMemo(() => {
    if (!spectrum) return null;
    const raw = atob(spectrum.data);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }, [spectrum]);
  const { contentWidth, viewportWidth, scrollLeft } = geom;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !spectrum || !grid || viewportWidth <= 0) return;
    const w = Math.max(1, Math.floor(viewportWidth));
    const h = Math.max(1, Math.floor(height));
    canvas.width = w;
    canvas.height = h;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const img = ctx.createImageData(w, h);
    const bins = spectrum.hz.length;
    const top = spectrum.hz[bins - 1] || 1;
    const span = to - from;
    for (let x = 0; x < w; x++) {
      const t = from + ((scrollLeft + x) / contentWidth) * span;
      const col = Math.floor(((t - spectrum.start) / spectrum.duration) * spectrum.columns);
      if (col < 0 || col >= spectrum.columns) continue; // outside the data: left dark
      for (let y = 0; y < h; y++) {
        const hz = (1 - y / (h - 1)) * top;
        const bin = Math.min(bins - 1, Math.round((hz / top) * (bins - 1)));
        const v = grid[col * bins + bin];
        const l = Math.round(Math.pow(v / 255, 1.6) * 255); // dark floor, bright tone
        const o = (y * w + x) * 4;
        img.data[o] = l;
        img.data[o + 1] = l;
        img.data[o + 2] = l;
        img.data[o + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
  }, [spectrum, grid, from, to, contentWidth, viewportWidth, scrollLeft, height]);

  if (!spectrum) {
    return (
      <div className="sticky left-0 flex items-center px-2 text-sm text-muted" style={{ width: viewportWidth || "100%", height }}>
        Preparing spectrum
      </div>
    );
  }
  const top = spectrum.hz[spectrum.hz.length - 1] || 1;
  const yPct = (hz: number) => `${(1 - hz / top) * 100}%`;
  return (
    <div className="relative" style={{ height }}>
      <canvas ref={ref} data-testid="spectrogram-track" className="pointer-events-none sticky left-0 block" />
      {band ? (
        <>
          <div data-testid="spectrum-band-hi" className="pointer-events-none absolute inset-x-0 border-t border-dashed border-ink-2/70" style={{ top: yPct(band.hi) }} />
          <div data-testid="spectrum-band-lo" className="pointer-events-none absolute inset-x-0 border-t border-dashed border-ink-2/70" style={{ top: yPct(band.lo) }} />
          <span className="pointer-events-none sticky left-1 text-xs text-ink-2" style={{ position: "absolute", top: yPct(band.hi) }}>
            timer
          </span>
        </>
      ) : null}
    </div>
  );
}
