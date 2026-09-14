/**
 * TimeBudgetBar -- the stacked bar of a stage's time budget (UX PR 7):
 * one segment per interval class in taxonomy order, its width the
 * seconds, its hue the class's chip tick (one hue per meaning). A
 * segment holding an outlier carries an amber inset ring. `compact`
 * renders the match page's thin, unlabelled bar; `scale` (0-1) draws it
 * on a shared time axis.
 */
import { BUDGET_LABEL, type BudgetClass, type TimeBudget } from "@/lib/timeBudget";
import { cn } from "@/lib/utils";

const FILL: Record<BudgetClass, string> = {
  first_shot: "bg-led text-white",
  movement: "bg-beep text-bg",
  transition: "bg-manual text-bg",
  split: "bg-done text-bg",
  reload: "bg-live text-bg",
  activation: "bg-ink-2 text-bg",
  unclassified: "bg-surface-3 text-ink-2",
};

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
            FILL[s.cls],
            s.outliers.length > 0 && "shadow-[inset_0_0_0_2px_var(--color-live)]",
          )}
        >
          {!compact && s.share >= 0.08 ? `${s.share >= 0.18 ? `${BUDGET_LABEL[s.cls].toLowerCase()} ` : ""}${s.seconds.toFixed(2)}` : null}
        </span>
      ))}
    </div>
  );
}
