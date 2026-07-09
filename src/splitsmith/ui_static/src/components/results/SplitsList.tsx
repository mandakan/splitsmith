/**
 * SplitsList - read-only per-shot list for the Results stage view.
 * One button row per shot: number, time from beep, split, tier chip
 * (text label + color dot - never color alone; omitted when no baseline
 * judgment is possible), interval-class chip,
 * improvement flag + coaching note when present. Tap seeks the video.
 * The active row highlights and, while playing, scrolls into view
 * (instant under prefers-reduced-motion). Read-only by contract: part
 * of the future share-link surface - no mutations here.
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
          return (
            <button
              key={shot.shot_number}
              type="button"
              data-shot-number={shot.shot_number}
              onClick={() => onSeek(shot)}
              className={cn(
                "relative block w-full min-h-11 px-4 py-2 text-left transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led focus-visible:ring-inset",
                // Mobile auto-scroll target must land below the sticky
                // player (document scroll can't see the pinned overlay).
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
                    <span
                      aria-hidden
                      className="size-2 rounded-full"
                      style={{ backgroundColor: tier.color }}
                    />
                    {tier.label}
                  </span>
                ) : null}
                {shot.interval_class ? (
                  <span
                    className={cn(
                      "inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 font-mono text-[0.625rem] uppercase",
                      INTERVAL_TONE[shot.interval_class],
                    )}
                  >
                    {INTERVAL_LABEL[shot.interval_class]}
                  </span>
                ) : null}
                {shot.improvement_flag ? (
                  <Flag
                    role="img"
                    aria-label="Flagged for improvement"
                    className="ml-auto size-3.5 shrink-0 text-led"
                  />
                ) : null}
              </span>
              {shot.coaching_note ? (
                <span className="mt-1 block pl-11 text-xs text-muted">{shot.coaching_note}</span>
              ) : null}
            </button>
          );
        })}
      </div>
    </section>
  );
}
