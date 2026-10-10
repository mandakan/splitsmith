/**
 * CoachShotTable -- every shot on the Coach stage page (UX PR 7): the
 * same vocabulary as Audit's and the stage page's shot tables (ordinal,
 * time, tiered split, interval chip) plus the note. Rows seek the video
 * and select the shot; the current row carries the led inset.
 */
import { useEffect, useRef } from "react";

import { Chip } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import type { CoachShot } from "@/lib/api";
import { nearestScrollTop } from "@/lib/breakdown";
import { gapTier, type TierBaselines } from "@/lib/splits";
import { BUDGET_LABEL, BUDGET_TICK } from "@/lib/timeBudget";
import { cn } from "@/lib/utils";

export interface CoachShotTableProps {
  shots: CoachShot[];
  activeShotNumber: number | null;
  baselines: TierBaselines | null;
  onSelect: (shot: CoachShot) => void;
  className?: string;
  /** Fill the parent's height and scroll inside it (Breakdown's inspector)
   *  instead of capping the list at 70 % of the viewport. */
  fill?: boolean;
}

const GRID = "grid grid-cols-[30px_54px_62px_minmax(0,1fr)_minmax(0,1fr)] items-center gap-2";
const TIER_TEXT = { quick: "text-done", typical: "text-ink", long: "text-live" } as const;

export function CoachShotTable({ shots, activeShotNumber, baselines, onSelect, className, fill = false }: CoachShotTableProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (activeShotNumber == null) return;
    const list = ref.current;
    const el = list?.querySelector<HTMLElement>(`[data-shot-number="${activeShotNumber}"]`);
    if (!list || !el) return;
    if (fill) {
      // Inside Breakdown's inspector: scroll the list alone. scrollIntoView
      // scrolls every scrollable ancestor too, which opened the inspector
      // scrolled and clipped the card above the list.
      list.scrollTop = nearestScrollTop(list.scrollTop, list.clientHeight, el.offsetTop, el.offsetHeight);
      return;
    }
    if (typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest" });
  }, [activeShotNumber, fill]);
  return (
    <section
      aria-label="Shots"
      className={cn("overflow-hidden rounded-[10px] border border-rule bg-surface", fill && "flex min-h-0 flex-col", className)}
    >
      <div className={cn(GRID, "border-b border-rule-strong px-3 py-2")}>
        <Label>#</Label>
        <Label className="text-right">T</Label>
        <Label className="text-right">Split</Label>
        <Label>Interval</Label>
        <Label>Note</Label>
      </div>
      <div ref={ref} className={fill ? "relative min-h-0 flex-1 overflow-y-auto" : "max-h-[70vh] overflow-y-auto"}>
        {shots.map((shot) => {
          const tier = gapTier(shot.split, shot.interval_class, baselines);
          const active = shot.shot_number === activeShotNumber;
          return (
            <button
              key={shot.shot_number}
              type="button"
              data-shot-number={shot.shot_number}
              onClick={() => onSelect(shot)}
              className={cn(
                GRID,
                "numeral w-full border-b border-rule px-3 py-1.5 text-left text-sm text-ink-2 last:border-b-0 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led",
                active && "bg-surface-2 shadow-[inset_2px_0_0_var(--color-led)]",
              )}
            >
              <span className="text-muted">{String(shot.shot_number).padStart(2, "0")}</span>
              <span className="text-right">{shot.time_from_beep.toFixed(2)}</span>
              <span className={cn("text-right font-medium", tier ? TIER_TEXT[tier.label] : "text-ink")}>{shot.split.toFixed(3)}</span>
              <span className="font-sans">
                {shot.interval_class ? <Chip tick={BUDGET_TICK[shot.interval_class]}>{BUDGET_LABEL[shot.interval_class]}</Chip> : null}
              </span>
              <span className="truncate font-sans text-muted" title={shot.coaching_note ?? undefined}>
                {shot.improvement_flag ? <i aria-label="Flagged" className="mr-1.5 inline-block size-1.5 rounded-full bg-live align-middle" /> : null}
                {shot.coaching_note ?? ""}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
