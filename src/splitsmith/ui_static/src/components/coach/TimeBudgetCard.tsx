/**
 * TimeBudgetCard -- the stage's budget (UX PR 7, spec s4.6): the bar,
 * then one row per class with the total, the share, the count, the
 * average, the delta against the shooter's match, and the outliers
 * named as buttons that select the shot.
 */
import { Label } from "@/components/ui/Label";
import { BUDGET_LABEL, BUDGET_TICK, type TimeBudget } from "@/lib/timeBudget";
import { cn } from "@/lib/utils";

import { TimeBudgetBar } from "./TimeBudgetBar";

const TICK_BG: Record<string, string> = {
  draw: "bg-led",
  movement: "bg-beep",
  transition: "bg-manual",
  fire: "bg-done",
  reload: "bg-live",
  activation: "bg-ink-2",
  muted: "bg-muted",
};

export interface TimeBudgetCardProps {
  budget: TimeBudget;
  onSelectShot?: (shotNumber: number) => void;
  className?: string;
}

const GRID = "grid grid-cols-[minmax(110px,1fr)_repeat(5,minmax(0,72px))_minmax(0,2fr)] items-center gap-3 px-3.5";

function signed(v: number): string {
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}`;
}

export function TimeBudgetCard({ budget, onSelectShot, className }: TimeBudgetCardProps) {
  return (
    <section aria-label="Time budget" className={cn("overflow-hidden rounded-[10px] border border-rule bg-surface", className)}>
      <div className="flex items-center gap-3 border-b border-rule px-3.5 py-2">
        <Label>Time budget</Label>
        {!budget.classified ? <Label tone="subtle">unclassified</Label> : null}
        <span className="numeral ml-auto text-md text-ink">{budget.total.toFixed(2)} s</span>
      </div>
      <div className="px-3.5 pb-2 pt-3">
        <TimeBudgetBar budget={budget} />
      </div>
      <div className={cn(GRID, "border-t border-rule-strong py-1.5")}>
        <Label>Type</Label>
        <Label className="text-right">Total</Label>
        <Label className="text-right">Share</Label>
        <Label className="text-right">Count</Label>
        <Label className="text-right">Avg</Label>
        <Label className="text-right">vs match</Label>
        <Label>Outliers</Label>
      </div>
      {budget.segments.map((s) => (
        <div key={s.cls} className={cn(GRID, "numeral border-t border-rule py-1.5 text-md text-ink-2")}>
          <span className="inline-flex items-center gap-2 font-sans text-ink">
            <i aria-hidden className={cn("size-2 rounded-full", TICK_BG[BUDGET_TICK[s.cls]])} />
            {BUDGET_LABEL[s.cls]}
          </span>
          <span className="text-right">{s.seconds.toFixed(2)}</span>
          <span className="text-right">{Math.round(s.share * 100)} %</span>
          <span className="text-right">{s.count}</span>
          <span className="text-right">{s.avg != null ? s.avg.toFixed(3) : "—"}</span>
          <span className={cn("text-right", s.vsMatch == null ? "text-subtle" : s.vsMatch > 0 ? "text-live" : "text-done")}>
            {s.vsMatch != null ? signed(s.vsMatch) : "—"}
          </span>
          <span className="flex flex-wrap gap-x-3 font-sans text-sm text-live">
            {s.outliers.map((o) => (
              <button
                key={o.shotNumber}
                type="button"
                onClick={() => onSelectShot?.(o.shotNumber)}
                className="whitespace-nowrap hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
              >
                shot <b className="numeral font-medium">{String(o.shotNumber).padStart(2, "0")}</b>{" "}
                <span className="numeral">{o.seconds.toFixed(3)}</span>
              </button>
            ))}
          </span>
        </div>
      ))}
    </section>
  );
}
