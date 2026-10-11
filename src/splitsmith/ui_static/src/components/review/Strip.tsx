/**
 * One window of the stage as the review walk draws it (desktop and phone).
 */

import { useMemo } from "react";

import type { AuditMarker } from "@/components/MarkerLayer";
import type { SnapPeaks } from "@/lib/peak-snap";
import type { DecodedAudio } from "@/lib/useDecodedAudio";
import { windowBins } from "@/lib/walk";

/** The strips' heights follow the window: the two close-ups are where the
 *  decision is made, so they take what the screen can give. */
export const CLOSE_HEIGHT = "clamp(140px, 21vh, 300px)";
export const CONTEXT_HEIGHT = "clamp(64px, 9vh, 130px)";
/** viewBox height: every y below is in these units, stretched to the CSS height. */
const H = 100;

/** One window of the stage: the wave itself when the audio is decoded,
 *  drawn like the guide's example figures (a line through every sample,
 *  ``fit="window"`` scaled to the window's own loudest point, ms ticks from
 *  the stop), else its per-millisecond level; every marker in it, the rise
 *  foot as a reference and the current shot on top. ``fit="stage"`` scales
 *  to the stage's typical shot instead, so shots compare with each other.
 *  A click places the shot there when ``onPlace`` is given. */
export function Strip({
  label,
  audio,
  peaks,
  center,
  half,
  level,
  fit,
  markers,
  currentId,
  currentTime,
  candidateTime,
  foot,
  onPlace,
  onPick,
  numbered = false,
  tickMs,
  band,
  cssHeight,
}: {
  label: string;
  audio: DecodedAudio | null;
  peaks: SnapPeaks;
  center: number;
  half: number;
  /** The stage's typical shot level, as a share of the clip's loudest sample. */
  level: number;
  fit: "window" | "stage";
  markers: ReadonlyArray<AuditMarker>;
  currentId: string | null;
  currentTime: number | null;
  /** Where the stop is when it is not a shot (drawn dashed). */
  candidateTime: number | null;
  foot: number | null;
  onPlace?: (t: number) => void;
  /** A click picks this time (the overview's jump to a stop). */
  onPick?: (t: number) => void;
  /** Number the kept shots, in time order over the whole stage. */
  numbered?: boolean;
  /** Ticks every this many ms, counted from the stop (0 at the red line). */
  tickMs?: number;
  /** A span to shade behind the wave (what the loop plays). */
  band?: { start: number; end: number } | null;
  cssHeight: string;
}) {
  const t0 = center - half;
  const span = 2 * half;
  const x = (t: number) => ((t - t0) / span) * 1000;
  const mid = H / 2;
  const inView = (t: number) => t >= t0 && t <= t0 + span;
  const others = markers.filter((m) => m.id !== currentId && inView(m.time));
  const kept = useMemo(
    () => markers.filter((m) => m.kind !== "rejected").sort((a, b) => a.time - b.time),
    [markers],
  );
  const zero = currentTime ?? candidateTime;

  // The window's loudest point, in the clip's units (the level's and the
  // normalized samples' alike).
  const windowMax = useMemo(() => {
    if (audio) {
      const { samples, sampleRate, maxAbs } = audio;
      const s0 = Math.max(0, Math.floor(t0 * sampleRate));
      const s1 = Math.min(samples.length, Math.ceil((t0 + span) * sampleRate));
      let m = 0;
      for (let i = s0; i < s1; i++) m = Math.max(m, Math.abs(samples[i]));
      return m / maxAbs;
    }
    return Math.max(0, ...windowBins(peaks, center, half).map((b) => b.v));
  }, [audio, peaks, t0, span, center, half]);
  const scale = Math.max(1e-6, fit === "window" ? windowMax * 1.05 : level);

  const wave = useMemo(() => {
    if (!audio) return null;
    const { samples, sampleRate, maxAbs } = audio;
    const s0 = Math.max(0, Math.floor(t0 * sampleRate));
    const s1 = Math.min(samples.length, Math.ceil((t0 + span) * sampleRate));
    const y = (v: number) => (mid - Math.max(-1, Math.min(1, v / maxAbs / scale)) * (mid - 1)).toFixed(2);
    const px = (i: number) => (((i / sampleRate - t0) / span) * 1000).toFixed(2);
    if (s1 - s0 <= 24000) {
      // A line through every sample: the guide's figures, sample for sample.
      const pts: string[] = [];
      for (let i = s0; i < s1; i++) pts.push(`${px(i)},${y(samples[i])}`);
      return { kind: "line" as const, d: pts.length ? `M${pts.join("L")}` : "" };
    }
    // Too many samples for a line: the outline of the wave, filled.
    const cols = 1200;
    const top: string[] = [];
    const bottom: string[] = [];
    for (let c = 0; c < cols; c++) {
      const a = s0 + Math.floor(((s1 - s0) * c) / cols);
      const b = Math.max(a + 1, s0 + Math.floor(((s1 - s0) * (c + 1)) / cols));
      let lo = Infinity;
      let hi = -Infinity;
      for (let i = a; i < b && i < samples.length; i++) {
        if (samples[i] < lo) lo = samples[i];
        if (samples[i] > hi) hi = samples[i];
      }
      if (lo === Infinity) continue;
      top.push(`${px(a)},${y(hi)}`);
      bottom.push(`${px(a)},${y(lo)}`);
    }
    return { kind: "fill" as const, d: top.length ? `M${top.join("L")}L${bottom.reverse().join("L")}Z` : "" };
    // x positions depend only on t0 and span.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audio, t0, span, scale]);

  const bins = useMemo(() => (audio ? [] : windowBins(peaks, center, half)), [audio, peaks, center, half]);
  const binW = peaks.duration / Math.max(1, peaks.peaks.length);

  const ticks: Array<{ t: number; ms: number }> = [];
  if (tickMs && zero != null) {
    const step = tickMs / 1000;
    for (let k = Math.ceil((t0 - zero) / step); zero + k * step <= t0 + span; k++) {
      ticks.push({ t: zero + k * step, ms: Math.round(k * tickMs) });
    }
  }
  const loudness =
    fit === "window" && level > 0 ? ` · loudest point ${Math.round((windowMax / level) * 100)} % of a typical shot` : "";

  return (
    <div>
      <div className="mb-0.5 text-xs text-muted">
        {label}
        {loudness}
      </div>
      <div className="relative">
        {numbered
          ? kept.map((m, i) =>
              inView(m.time) ? (
                <span
                  key={m.id}
                  className="pointer-events-none absolute top-0 -translate-x-1/2 font-mono text-xs text-ink"
                  style={{ left: `${(x(m.time) / 1000) * 100}%` }}
                >
                  {i + 1}
                </span>
              ) : null,
            )
          : null}
        <svg
          viewBox={`0 0 1000 ${H}`}
          preserveAspectRatio="none"
          className={
            onPlace
              ? "block w-full cursor-crosshair select-none rounded-sm bg-surface"
              : onPick
                ? "block w-full cursor-pointer select-none rounded-sm bg-surface"
                : "block w-full select-none rounded-sm bg-surface"
          }
          style={{ height: cssHeight }}
          role="img"
          aria-label={label}
          onClick={
            onPlace || onPick
              ? (e) => {
                  const r = e.currentTarget.getBoundingClientRect();
                  const t = t0 + ((e.clientX - r.left) / r.width) * span;
                  if (onPlace) onPlace(t);
                  else onPick!(t);
                }
              : undefined
          }
        >
          {band ? (
            <rect
              x={x(Math.max(t0, band.start))}
              width={Math.max(0, x(Math.min(t0 + span, band.end)) - x(Math.max(t0, band.start)))}
              y={0}
              height={H}
              className="fill-current text-ink"
              fillOpacity={0.12}
            />
          ) : null}
          {ticks.map((k) => (
            <line
              key={k.ms}
              x1={x(k.t)}
              x2={x(k.t)}
              y1={0}
              y2={H}
              className="stroke-current text-rule"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          <line x1={0} x2={1000} y1={mid} y2={mid} className="stroke-current text-rule" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          {wave != null ? (
            wave.kind === "line" ? (
              <path d={wave.d} className="stroke-current text-ink" strokeWidth={1} fill="none" vectorEffect="non-scaling-stroke" />
            ) : (
              <path d={wave.d} className="fill-current text-ink-2" stroke="none" />
            )
          ) : (
            bins.map((b) => {
              const h = Math.min(1, b.v / scale) * (mid - 1);
              return (
                <rect
                  key={b.t}
                  x={x(b.t)}
                  y={mid - h}
                  width={Math.max(0.5, (binW / span) * 1000 - (span > 0.1 ? 0 : 1))}
                  height={Math.max(0.5, 2 * h)}
                  className="fill-current text-ink-2"
                  opacity={0.6}
                />
              );
            })
          )}
          {others.map((m) => (
            <line
              key={m.id}
              x1={x(m.time)}
              x2={x(m.time)}
              y1={numbered ? 16 : 0}
              y2={H}
              className={m.kind === "rejected" ? "stroke-current text-muted" : "stroke-current text-ink"}
              strokeWidth={m.kind === "rejected" ? 1 : 2}
              strokeDasharray={m.kind === "rejected" ? "4 4" : undefined}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {foot != null && inView(foot) ? (
            <line
              x1={x(foot)}
              x2={x(foot)}
              y1={0}
              y2={H}
              className="stroke-current text-status-complete"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
          {candidateTime != null && inView(candidateTime) ? (
            <line
              x1={x(candidateTime)}
              x2={x(candidateTime)}
              y1={0}
              y2={H}
              className="stroke-current text-led"
              strokeWidth={2}
              strokeDasharray="6 4"
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
          {currentTime != null && inView(currentTime) ? (
            <line
              x1={x(currentTime)}
              x2={x(currentTime)}
              y1={0}
              y2={H}
              className="stroke-current text-led"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
        </svg>
        {ticks.length > 0 ? (
          <div className="relative h-4 font-mono text-xs text-muted">
            {ticks.map((k) => (
              <span
                key={k.ms}
                className="absolute top-0 -translate-x-1/2"
                style={{ left: `${(x(k.t) / 1000) * 100}%` }}
              >
                {k.ms}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
