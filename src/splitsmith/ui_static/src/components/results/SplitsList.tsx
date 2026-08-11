/**
 * SplitsList - per-shot list for the Results stage view. One row per
 * shot: a seek button (number, time from beep, split, tier chip - text
 * label + color dot, never color alone; omitted when no baseline
 * judgment is possible) plus a trailing interval-class chip and
 * improvement flag, with the coaching note when present. Tap the row to
 * seek the video. The active row highlights and, while playing, scrolls
 * into view (instant under prefers-reduced-motion).
 *
 * Share mounts render read-only: the chip is a plain span (unclassified
 * shots get no affordance at all). Operator mounts pass onReclassify,
 * which turns the chip into its own button - the slice-5 reclassify
 * entry point - kept a sibling of the seek button since nested buttons
 * are invalid HTML.
 */
import { Flag } from "lucide-react";
import { useEffect, useRef } from "react";

import type { CoachShot } from "@/lib/api";
import { INTERVAL_LABEL, INTERVAL_TONE, type TierBaselines, gapTier } from "@/lib/splits";
import { cn } from "@/lib/utils";

interface SplitsListProps {
  shots: CoachShot[];
  activeShotNumber: number | null;
  onSeek: (shot: CoachShot) => void;
  /** Auto-scroll the active row into view only while playing, so a
   *  manual tap on a row doesn't yank the list around. */
  isPlaying: boolean;
  /** Match-scope per-class baselines; null degrades to unjudged rows. */
  baselines: TierBaselines | null;
  /** Operator-only: makes the interval chip a tap target that opens the
   *  reclassify sheet. Omitted on share mounts, where the chip stays a
   *  read-only span (and unclassified shots get no affordance at all). */
  onReclassify?: (shot: CoachShot) => void;
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

export function SplitsList({
  shots,
  activeShotNumber,
  onSeek,
  isPlaying,
  baselines,
  onReclassify,
}: SplitsListProps) {
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!isPlaying || activeShotNumber == null) return;
    const row = listRef.current?.querySelector<HTMLElement>(
      `[data-shot-number="${activeShotNumber}"]`,
    );
    if (!row) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    row.scrollIntoView({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
  }, [activeShotNumber, isPlaying]);

  return (
    <section className="overflow-hidden rounded-2xl border border-rule-strong bg-surface">
      <div className="border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-4 py-3 font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
        Shots
        <span className="ml-2 font-mono text-[0.625rem] font-medium tracking-[0.06em] text-muted">
          {shots.length} total
        </span>
      </div>
      <div ref={listRef} className="divide-y divide-rule">
        {shots.map((shot) => {
          const tier = gapTier(shot.split, shot.interval_class, baselines);
          const active = activeShotNumber === shot.shot_number;
          const chipTone = shot.interval_class
            ? INTERVAL_TONE[shot.interval_class]
            : "text-muted border-rule bg-surface-2";
          const chipLabel = shot.interval_class ? INTERVAL_LABEL[shot.interval_class] : "Classify";
          const chip = (
            <span
              className={cn(
                "inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 font-mono text-[0.625rem] uppercase",
                chipTone,
              )}
            >
              {chipLabel}
            </span>
          );
          return (
            <div
              key={shot.shot_number}
              data-shot-number={shot.shot_number}
              className={cn(
                "relative flex min-h-11 items-center transition-colors hover:bg-surface-2",
                "max-lg:scroll-mt-[calc(var(--shell-header-h,0px)+var(--results-player-h,0px)+8px)]",
                active && "bg-surface-2",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "absolute inset-y-0 left-0 w-[3px] bg-led shadow-[0_0_12px_var(--color-led-glow)]",
                  active ? "opacity-100" : "opacity-0",
                )}
              />
              <button
                type="button"
                onClick={() => onSeek(shot)}
                className="min-h-11 flex-1 px-4 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led focus-visible:ring-inset"
              >
                <span className="flex items-center gap-3">
                  <span className="w-8 shrink-0 font-mono text-xs font-bold tabular-nums text-muted">
                    {pad2(shot.shot_number)}
                  </span>
                  <span className="w-14 shrink-0 text-right font-mono text-sm tabular-nums text-ink-2">
                    {shot.time_from_beep.toFixed(2)}
                  </span>
                  <span className="w-16 shrink-0 text-right font-mono text-sm font-bold tabular-nums text-ink">
                    {shot.split.toFixed(3)}
                  </span>
                  {tier ? (
                    <span className="inline-flex shrink-0 items-center gap-1 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                      <span aria-hidden className="size-2 rounded-full" style={{ backgroundColor: tier.color }} />
                      {tier.label}
                    </span>
                  ) : null}
                </span>
                {shot.coaching_note ? (
                  <span className="mt-1 block pl-11 text-xs text-muted">{shot.coaching_note}</span>
                ) : null}
              </button>
              <span className="flex shrink-0 items-center gap-2 pr-4">
                {onReclassify ? (
                  <button
                    type="button"
                    aria-label={`Reclassify shot ${shot.shot_number} (${
                      shot.interval_class ? INTERVAL_LABEL[shot.interval_class] : "unclassified"
                    })`}
                    onClick={() => onReclassify(shot)}
                    className="flex min-h-11 min-w-11 items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
                  >
                    {chip}
                  </button>
                ) : shot.interval_class ? (
                  chip
                ) : null}
                {shot.improvement_flag ? (
                  <Flag
                    role="img"
                    aria-label="Flagged for improvement"
                    className="size-3.5 shrink-0 text-led"
                  />
                ) : null}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
