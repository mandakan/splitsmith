/**
 * SplitsList -- the shot table on the stage page (UX PR 4, spec s4.5).
 * One row per shot: ordinal, time from beep, split, interval. The split
 * numeral carries the tier colour (green quick, amber long, ink typical
 * or unjudged) against the shooter's own match baseline; the interval is
 * a neutral chip with a coloured tick. Tap the row to seek the video.
 * The active row is surface-2 with the led inset (current position, no
 * glow: glow is for live state) and, while playing, scrolls into view.
 *
 * Share mounts render read-only: the chip is a plain span and an
 * unclassified shot gets no affordance. Operator mounts pass
 * onReclassify, which turns the chip into its own button (the reclassify
 * entry point), kept a sibling of the seek button since nested buttons
 * are invalid HTML.
 */
import { Flag } from "lucide-react";
import { useEffect, useRef } from "react";

import { Chip, type ChipTick } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import type { CoachIntervalClass, CoachShot } from "@/lib/api";
import { INTERVAL_LABEL, type TierBaselines, gapTier } from "@/lib/splits";
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
   *  reclassify sheet. Omitted on share mounts. */
  onReclassify?: (shot: CoachShot) => void;
}

const TICK: Record<CoachIntervalClass, ChipTick> = {
  first_shot: "draw",
  split: "fire",
  transition: "transition",
  movement: "movement",
  reload: "reload",
  activation: "activation",
};

const TIER_TEXT = { quick: "text-done", typical: "text-ink", long: "text-live" } as const;

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

const GRID = "grid grid-cols-[34px_56px_70px_minmax(0,1fr)] items-center gap-2.5";

export function SplitsList({ shots, activeShotNumber, onSeek, isPlaying, baselines, onReclassify }: SplitsListProps) {
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!isPlaying || activeShotNumber == null) return;
    const row = listRef.current?.querySelector<HTMLElement>(`[data-shot-number="${activeShotNumber}"]`);
    if (!row) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    row.scrollIntoView({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
  }, [activeShotNumber, isPlaying]);

  return (
    <section aria-label="Shots" className="overflow-hidden rounded-[10px] border border-rule bg-surface">
      <div className={cn(GRID, "border-b border-rule-strong px-3.5 py-2")}>
        <Label>#</Label>
        <Label className="text-right">T</Label>
        <Label className="text-right">Split</Label>
        <Label className="text-right">Interval</Label>
      </div>
      <div ref={listRef}>
        {shots.map((shot) => {
          const tier = gapTier(shot.split, shot.interval_class, baselines);
          const active = activeShotNumber === shot.shot_number;
          const chip = (
            <Chip tick={shot.interval_class ? TICK[shot.interval_class] : "muted"}>
              {shot.interval_class ? INTERVAL_LABEL[shot.interval_class] : "Classify"}
            </Chip>
          );
          return (
            <div
              key={shot.shot_number}
              data-shot-number={shot.shot_number}
              className={cn(
                GRID,
                "min-h-11 border-b border-rule px-3.5 last:border-b-0 transition-colors hover:bg-surface-2",
                "max-lg:scroll-mt-[calc(var(--shell-header-h,0px)+var(--results-player-h,0px)+8px)]",
                active && "bg-surface-2 shadow-[inset_2px_0_0_var(--color-led)]",
              )}
            >
              <button
                type="button"
                onClick={() => onSeek(shot)}
                className={cn(GRID, "col-span-3 -mx-3.5 min-h-11 px-3.5 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led")}
              >
                <span className="font-mono text-sm text-muted">{pad2(shot.shot_number)}</span>
                <span className="numeral text-right text-md text-ink-2">{shot.time_from_beep.toFixed(2)}</span>
                <span className={cn("numeral text-right text-md font-medium", tier ? TIER_TEXT[tier.label] : "text-ink")}>
                  {shot.split.toFixed(3)}
                </span>
              </button>
              <span className="flex items-center justify-end gap-2 py-1">
                {shot.improvement_flag ? (
                  <Flag role="img" aria-label="Flagged for improvement" className="size-3.5 shrink-0 text-live" />
                ) : null}
                {onReclassify ? (
                  <button
                    type="button"
                    aria-label={`Reclassify shot ${shot.shot_number} (${
                      shot.interval_class ? INTERVAL_LABEL[shot.interval_class] : "unclassified"
                    })`}
                    onClick={() => onReclassify(shot)}
                    className="flex min-h-9 items-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
                  >
                    {chip}
                  </button>
                ) : shot.interval_class ? (
                  chip
                ) : null}
              </span>
              {shot.coaching_note ? (
                <span className="col-start-2 col-span-3 pb-2 text-sm text-muted">{shot.coaching_note}</span>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}
