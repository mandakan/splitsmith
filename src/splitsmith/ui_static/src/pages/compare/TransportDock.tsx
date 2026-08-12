/** Cockpit bottom dock: the Transport bar and the SyncTimeline fused
 *  into one panel behind one playhead. The lane gutter is HTML (real
 *  buttons, no SVG-text distortion); the track SVG renders at measured
 *  pixel width (ResizeObserver, Audit.tsx idiom) so nothing stretches.
 *  Scrub by dragging anywhere on the tracks or via the range slider
 *  (the keyboard-accessible control). */

import { Link2, MoveLeft, MoveRight, Pause, Play, Volume2 } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { type CompareShooterRecord } from "@/lib/api";
import { cn } from "@/lib/utils";

const TRACK_PALETTE: string[] = [
  "var(--color-led)",
  "var(--color-shooter-jl)",
  "var(--color-shooter-pe)",
  "var(--color-shooter-rj)",
  "var(--color-manual)",
];

const GUTTER_W = 224;
const TRACK_H = 38;
const RULER_H = 24;
const PAD_BOTTOM = 6;
const PAD_RIGHT = 56; // room for the end-of-run time label

export function timeFromTrackX(
  px: number,
  width: number,
  maxTime: number,
): number {
  if (width <= 0) return 0;
  return Math.max(0, Math.min((px / width) * maxTime, maxTime));
}

export function TransportDock({
  shooters,
  maxTime,
  timeSinceBeep,
  audioSlug,
  isPlaying,
  onTogglePlay,
  onScrub,
  onPickAudio,
  momentT,
  onCopyMoment,
}: {
  shooters: CompareShooterRecord[];
  maxTime: number;
  timeSinceBeep: number;
  audioSlug: string | null;
  isPlaying: boolean;
  onTogglePlay: () => void;
  onScrub: (tsb: number) => void;
  onPickAudio: (slug: string) => void;
  /** Seconds after beep for a shared moment (?t=). Renders a labelled
   *  diamond marker on the track, positioned with the same xOf() math
   *  as the playhead. */
  momentT?: number | null;
  onCopyMoment: () => void;
}) {
  const [trackW, setTrackW] = useState(960);
  const observerRef = useRef<ResizeObserver | null>(null);
  const svgRef = useCallback((el: SVGSVGElement | null) => {
    observerRef.current?.disconnect();
    observerRef.current = null;
    if (!el) return;
    const write = () =>
      setTrackW(Math.max(240, el.getBoundingClientRect().width));
    write();
    const ro = new ResizeObserver(write);
    ro.observe(el);
    observerRef.current = ro;
  }, []);

  const svgH = RULER_H + shooters.length * TRACK_H + PAD_BOTTOM;
  const plotW = trackW - PAD_RIGHT;
  const xOf = (tsb: number) => (tsb / maxTime) * plotW;
  const clampedT = Math.max(0, Math.min(timeSinceBeep, maxTime));

  const scrubFromPointer = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    onScrub(
      timeFromTrackX(
        e.clientX - rect.left,
        rect.width - PAD_RIGHT,
        maxTime,
      ),
    );
  };

  // Time ruler ticks every second; labels every second up to 20s span,
  // every 5s beyond that so long stages stay legible. Thin tick lines on
  // long stages (maxTime > 60s) to match label frequency, avoiding visual smear.
  const labelEvery = maxTime > 20 ? 5 : 1;
  const tickEvery = maxTime > 60 ? labelEvery : 1;
  const ticks: number[] = [];
  for (let t = 0; t <= maxTime + 0.001; t += tickEvery) ticks.push(t);

  return (
    <div
      data-testid="transport-dock"
      className="flex-none rounded-2xl border border-rule-strong bg-bg-glow px-4 pb-2 pt-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]"
    >
      {/* Transport row */}
      <div className="flex flex-wrap items-center gap-3 pb-2">
        <button
          type="button"
          onClick={() => onScrub(0)}
          aria-label="Jump to beep"
          title="Jump to beep"
          className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-3 text-muted transition-colors hover:bg-surface-4 hover:text-ink"
        >
          <MoveLeft className="size-4" />
        </button>
        <button
          type="button"
          onClick={onTogglePlay}
          aria-label={isPlaying ? "Pause" : "Play"}
          className="inline-flex size-11 items-center justify-center rounded-full bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] transition-colors hover:bg-led-soft"
        >
          {isPlaying ? <Pause className="size-5" /> : <Play className="size-5" />}
        </button>
        <button
          type="button"
          onClick={() => onScrub(maxTime)}
          aria-label="Jump to end"
          title="Jump to end"
          className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-3 text-muted transition-colors hover:bg-surface-4 hover:text-ink"
        >
          <MoveRight className="size-4" />
        </button>
        <button
          type="button"
          onClick={onCopyMoment}
          aria-label="Copy link at moment"
          title="Copy link at moment"
          className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-3 text-muted transition-colors hover:bg-surface-4 hover:text-ink"
        >
          <Link2 className="size-4" />
        </button>
        <div className="ml-2 flex items-center gap-4 font-mono tabular-nums">
          <span className="flex flex-col items-start gap-0.5">
            <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
              t-beep
            </span>
            <span className="font-mono text-base font-bold leading-none text-led-text [text-shadow:0_0_10px_var(--color-led-glow)]">
              {timeSinceBeep.toFixed(3)}s
            </span>
          </span>
          <span className="flex flex-col items-start gap-0.5">
            <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
              span
            </span>
            <span className="font-mono text-base font-bold leading-none text-ink">
              {maxTime.toFixed(2)}s
            </span>
          </span>
        </div>
        <input
          type="range"
          aria-label="Scrub time since beep"
          className="min-w-[160px] flex-1 accent-led"
          min={0}
          max={maxTime}
          step={0.01}
          value={clampedT}
          onChange={(e) => onScrub(parseFloat(e.target.value))}
        />
        <span className="hidden font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle lg:inline">
          drag the tracks to scrub - click a lane for audio
        </span>
      </div>

      {/* Lane gutter + track SVG */}
      <div className="flex items-stretch">
        <div
          className="flex flex-none flex-col"
          style={{ width: GUTTER_W, paddingTop: RULER_H }}
        >
          {shooters.map((s, i) => {
            const isAudio = audioSlug === s.slug;
            const color = TRACK_PALETTE[i % TRACK_PALETTE.length];
            return (
              <button
                key={s.slug}
                type="button"
                onClick={() => onPickAudio(s.slug)}
                aria-pressed={isAudio}
                aria-label={`${s.name} - use as audio source`}
                title={`${s.name} - use as audio source`}
                className={cn(
                  "flex items-center gap-2 rounded-md pr-3 text-left transition-colors hover:bg-surface-2",
                  isAudio ? "text-ink" : "text-ink-2",
                )}
                style={{ height: TRACK_H }}
              >
                <span
                  aria-hidden="true"
                  className="size-2.5 flex-none rounded-full"
                  style={{ background: color }}
                />
                <span className="min-w-0 truncate font-display text-[0.75rem] font-bold uppercase tracking-[0.05em]">
                  {s.name}
                </span>
                {isAudio ? (
                  <Volume2 className="size-3 flex-none text-led" />
                ) : null}
                <span className="ml-auto font-mono text-[0.75rem] font-bold tabular-nums text-muted">
                  {s.stage_time_seconds != null
                    ? s.stage_time_seconds.toFixed(2)
                    : "-"}
                </span>
              </button>
            );
          })}
        </div>
        <svg
          ref={svgRef}
          role="presentation"
          height={svgH}
          className="min-w-0 flex-1 cursor-crosshair touch-none select-none"
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            scrubFromPointer(e);
          }}
          onPointerMove={(e) => {
            if (e.buttons & 1) scrubFromPointer(e);
          }}
        >
          {/* Time ruler */}
          {ticks.map((t) => (
            <g key={`tick-${t}`}>
              <line
                data-ruler-tick
                x1={xOf(t)}
                x2={xOf(t)}
                y1={RULER_H - (t % 5 === 0 ? 9 : 5)}
                y2={RULER_H}
                stroke="var(--color-rule)"
                strokeWidth={1}
              />
              {t % labelEvery === 0 ? (
                <text
                  x={xOf(t)}
                  y={RULER_H - 12}
                  textAnchor="middle"
                  fill="var(--color-subtle)"
                  fontFamily="JetBrains Mono, monospace"
                  fontSize={9}
                >
                  {t}s
                </text>
              ) : null}
            </g>
          ))}
          {/* Per-shooter tracks */}
          {shooters.map((s, i) => {
            const yMid = RULER_H + i * TRACK_H + TRACK_H / 2;
            const color = TRACK_PALETTE[i % TRACK_PALETTE.length];
            const endT = s.stage_time_seconds ?? maxTime;
            return (
              <g key={s.slug}>
                <line
                  x1={xOf(0)}
                  x2={xOf(endT)}
                  y1={yMid}
                  y2={yMid}
                  stroke={color}
                  strokeWidth={2.5}
                  strokeOpacity={0.3}
                  strokeLinecap="round"
                />
                {/* Progress segment up to the playhead */}
                <line
                  x1={xOf(0)}
                  x2={xOf(Math.min(clampedT, endT))}
                  y1={yMid}
                  y2={yMid}
                  stroke={color}
                  strokeWidth={2.5}
                  strokeOpacity={0.7}
                  strokeLinecap="round"
                />
                {/* End-of-run cap + total */}
                <line
                  x1={xOf(endT)}
                  x2={xOf(endT)}
                  y1={yMid - 9}
                  y2={yMid + 9}
                  stroke={color}
                  strokeWidth={2.5}
                  strokeLinecap="round"
                />
                <text
                  x={xOf(endT) + 7}
                  y={yMid + 3.5}
                  textAnchor="start"
                  fill="var(--color-ink)"
                  fontFamily="JetBrains Mono, monospace"
                  fontSize={10}
                  fontWeight={700}
                >
                  {(s.stage_time_seconds ?? 0).toFixed(2)}s
                </text>
                {/* Shot markers: fired solid, upcoming hollow */}
                {s.shots.map((shot) => {
                  const fired = shot.time_after_beep <= clampedT + 0.0005;
                  const isManual = shot.source === "manual";
                  const markerColor = isManual
                    ? "var(--color-manual)"
                    : color;
                  return (
                    <circle
                      key={`${s.slug}-${shot.shot_number}`}
                      data-testid={`shot-${s.slug}-${shot.shot_number}`}
                      data-fired={fired ? "true" : "false"}
                      cx={xOf(shot.time_after_beep)}
                      cy={yMid}
                      r={isManual ? 5 : 4}
                      fill={fired ? markerColor : "var(--color-surface)"}
                      stroke={fired ? "var(--color-bg)" : markerColor}
                      strokeWidth={fired ? 1.5 : 1.5}
                      strokeOpacity={fired ? 1 : 0.6}
                    />
                  );
                })}
              </g>
            );
          })}
          {/* Beep marker at t=0 */}
          <line
            x1={xOf(0)}
            x2={xOf(0)}
            y1={RULER_H - 2}
            y2={svgH - PAD_BOTTOM + 2}
            stroke="var(--color-beep)"
            strokeWidth={1.5}
            strokeDasharray="4 4"
            strokeOpacity={0.8}
          />
          {/* Shared-moment marker: diamond + label, matching the
              ResultsPlayer idiom (shape + text, never color-only). */}
          {momentT != null && momentT >= 0 && momentT <= maxTime && (
            <rect
              role="img"
              aria-label={`Moment at ${momentT.toFixed(2)}s`}
              x={xOf(momentT) - 5}
              y={RULER_H - 5}
              width={10}
              height={10}
              transform={`rotate(45 ${xOf(momentT)} ${RULER_H})`}
              fill="var(--color-manual)"
              fillOpacity={0.4}
              stroke="var(--color-manual)"
              strokeWidth={2}
            />
          )}
          {/* Playhead */}
          <line
            x1={xOf(clampedT)}
            x2={xOf(clampedT)}
            y1={RULER_H - 6}
            y2={svgH - PAD_BOTTOM}
            stroke="var(--color-led)"
            strokeWidth={2}
            style={{ filter: "drop-shadow(0 0 4px var(--color-led-glow))" }}
          />
        </svg>
      </div>
    </div>
  );
}
