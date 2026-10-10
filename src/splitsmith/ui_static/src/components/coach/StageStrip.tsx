/**
 * StageStrip -- the read-only stage strip on the Coach review page (#1375):
 * the whole stage from the beep to the stage time in one row. Shots are
 * ticks in their interval class's hue, movement (and activation) a tall
 * translucent bar, a reload a short solid bar over it; the playhead is the
 * led line and the current shot's tick stands taller with a led ring.
 *
 * A tap or click anywhere on the strip seeks: to the nearest shot when its
 * tick is within a few px, else to the raw time. Nothing drags. The ticks
 * are buttons inside a labelled group, for the keyboard and screen readers:
 * one tab stop (the current shot), Left / Right / Home / End move between
 * shots, each named "Shot 07, 11.25 s, fire". Pointer input goes to the
 * strip itself, so a 2 px tick never has to be hit exactly on a touch screen.
 * Geometry is ``lib/stageStrip``.
 */
import { useMemo, useRef } from "react";

import { CHIP_TICK_BG as TICK_BG } from "@/components/ui/chipTicks";
import type { CoachShot, StageEvent, StageEventKind } from "@/lib/api";
import { playheadX, stepShot, stripGeometry, stripTarget } from "@/lib/stageStrip";
import { cn } from "@/lib/utils";

const BAR_BG: Record<StageEventKind, string> = {
  movement: "bg-beep/30",
  activation: "bg-ink-2/25",
  reload: "bg-live",
};

export interface StageStripProps {
  shots: CoachShot[];
  /** Every region; the strip draws the confirmed ones. */
  events: StageEvent[];
  /** Seconds from the beep the strip spans. */
  stageTime: number;
  /** The playhead, in seconds from the beep. */
  tFromBeep: number;
  activeShotNumber: number | null;
  /** Seek to seconds from the beep; ``shotNumber`` when it landed on a shot. */
  onSeek: (tFromBeep: number, shotNumber: number | null) => void;
  className?: string;
}

const pct = (x: number) => `${x * 100}%`;

export function StageStrip({ shots, events, stageTime, tFromBeep, activeShotNumber, onSeek, className }: StageStripProps) {
  const geometry = useMemo(() => stripGeometry(shots, events, stageTime), [shots, events, stageTime]);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const focusShot = (n: number) => {
    trackRef.current?.querySelector<HTMLButtonElement>(`[data-strip-shot="${n}"]`)?.focus();
  };
  const tabStop = geometry.ticks.some((t) => t.shotNumber === activeShotNumber)
    ? activeShotNumber
    : (geometry.ticks[0]?.shotNumber ?? null);
  return (
    <div
      role="group"
      aria-label="Stage strip"
      className={cn("select-none", className)}
      onKeyDown={(e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "Home" && e.key !== "End") return;
        const next = stepShot(geometry.ticks, activeShotNumber, e.key);
        if (next == null) return;
        e.preventDefault();
        const tick = geometry.ticks.find((t) => t.shotNumber === next)!;
        onSeek(tick.t, tick.shotNumber);
        focusShot(next);
      }}
    >
      <div
        ref={trackRef}
        data-testid="stage-strip"
        className="relative h-12 cursor-pointer touch-manipulation rounded-md bg-surface-2"
        onClick={(e) => {
          // A keyboard "click" on a tick button has no position: seek to it.
          if (e.detail === 0) return;
          const rect = e.currentTarget.getBoundingClientRect();
          const target = stripTarget(e.clientX - rect.left, rect.width, geometry);
          onSeek(target.t, target.shotNumber);
        }}
      >
        {geometry.bars.map((b) => (
          <span
            key={b.id}
            aria-hidden
            data-strip-bar={b.kind}
            className={cn("pointer-events-none absolute rounded-sm", BAR_BG[b.kind], b.tall ? "inset-y-1.5" : "top-[19px] h-2.5")}
            style={{ left: pct(b.x0), width: pct(b.x1 - b.x0) }}
          />
        ))}
        {geometry.ticks.map((t) => {
          const active = t.shotNumber === activeShotNumber;
          return (
            <button
              key={t.shotNumber}
              type="button"
              data-strip-shot={t.shotNumber}
              aria-label={t.label}
              aria-current={active ? "true" : undefined}
              tabIndex={t.shotNumber === tabStop ? 0 : -1}
              onClick={(e) => {
                if (e.detail !== 0) return;
                onSeek(t.t, t.shotNumber);
              }}
              className={cn(
                "pointer-events-none absolute -translate-x-1/2 rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led focus-visible:ring-offset-1 focus-visible:ring-offset-surface-2",
                TICK_BG[t.tick],
                active ? "inset-y-0 w-1 ring-1 ring-led ring-offset-1 ring-offset-surface-2" : "inset-y-1 w-0.5",
              )}
              style={{ left: pct(t.x) }}
            />
          );
        })}
        <span
          aria-hidden
          data-testid="strip-playhead"
          className="pointer-events-none absolute -inset-y-0.5 w-0.5 -translate-x-1/2 bg-led"
          style={{ left: pct(playheadX(tFromBeep, geometry.duration)) }}
        />
      </div>
      <div className="numeral mt-1 flex justify-between text-sm text-subtle" aria-hidden>
        <span>0.00</span>
        <span>{geometry.duration.toFixed(2)} s</span>
      </div>
    </div>
  );
}
