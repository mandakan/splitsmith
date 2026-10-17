/**
 * TimeBudgetBar -- the stacked bar of a stage's time budget (UX PR 7):
 * one segment per interval class in taxonomy order, its width the
 * seconds, its hue the class's chip tick (one hue per meaning). A
 * segment holding an outlier carries an amber inset ring. `compact`
 * renders the match page's thin, unlabelled bar; `scale` (0-1) draws it
 * on a shared time axis.
 */
import { CHIP_TICK_BG, HUE_FILL, HUE_PATTERN } from "@/components/ui/chipTicks";
import { BUDGET_LABEL, BUDGET_TICK, type TimeBudget } from "@/lib/timeBudget";
import { cn } from "@/lib/utils";

export interface TimeBudgetBarProps {
  budget: TimeBudget;
  compact?: boolean;
  /** Width as a share of the shared axis (match page); 1 by default. */
  scale?: number;
  className?: string;
}

export function TimeBudgetBar({ budget, compact = false, scale = 1, className }: TimeBudgetBarProps) {
  if (budget.total <= 0) return null;
  return (
    <div
      role="img"
      aria-label={budget.segments.map((s) => `${BUDGET_LABEL[s.cls]} ${s.seconds.toFixed(2)} s`).join(", ")}
      className={cn("flex overflow-hidden rounded-md", compact ? "h-4 gap-px" : "h-8 gap-0.5", className)}
      style={{ width: `${Math.max(0, Math.min(1, scale)) * 100}%` }}
    >
      {budget.segments.map((s) => (
        <span
          key={s.cls}
          title={`${BUDGET_LABEL[s.cls]} · ${s.seconds.toFixed(2)} s · ${Math.round(s.share * 100)} % · ${s.count}`}
          style={{ flexGrow: s.seconds, flexBasis: 0 }}
          className={cn(
            "numeral flex min-w-0 items-center justify-center overflow-hidden whitespace-nowrap text-sm",
            HUE_FILL[BUDGET_TICK[s.cls]],
            HUE_PATTERN[BUDGET_TICK[s.cls]],
            s.outliers.length > 0 && "shadow-[inset_0_0_0_2px_var(--color-live)]",
          )}
        >
          {!compact && s.share >= 0.08 ? (
            // On a solid patch of the hue, so the pattern never runs under the text.
            <span className={cn("rounded-sm px-1", CHIP_TICK_BG[BUDGET_TICK[s.cls]])}>
              {`${s.share >= 0.18 ? `${BUDGET_LABEL[s.cls].toLowerCase()} ` : ""}${s.seconds.toFixed(2)}`}
            </span>
          ) : null}
        </span>
      ))}
    </div>
  );
}
