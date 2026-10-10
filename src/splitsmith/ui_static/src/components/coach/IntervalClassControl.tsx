/**
 * The interval-class control: one pressed button per class, the taxonomy
 * tick on each. Shared by Coach's ShotEditor and Breakdown's shot card
 * (#1371); a click writes the class as a manual override through the
 * page's shot PATCH.
 */
import type { CoachIntervalClass } from "@/lib/api";
import { BUDGET_LABEL, BUDGET_ORDER, BUDGET_TICK } from "@/lib/timeBudget";
import { cn } from "@/lib/utils";

const TICK_BG: Record<string, string> = {
  draw: "bg-led",
  movement: "bg-beep",
  transition: "bg-manual",
  fire: "bg-done",
  reload: "bg-live",
  activation: "bg-ink-2",
};

const CLASSES = BUDGET_ORDER.filter((c): c is CoachIntervalClass => c !== "unclassified");

export function IntervalClassControl({
  value,
  onClassify,
  disabled = false,
  className,
}: {
  value: CoachIntervalClass | null;
  onClassify: (cls: CoachIntervalClass) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label="Interval class"
      className={cn("inline-flex flex-wrap gap-0.5 rounded-md border border-rule-strong bg-surface-2 p-0.5", className)}
    >
      {CLASSES.map((cls) => {
        const on = value === cls;
        return (
          <button
            key={cls}
            type="button"
            aria-pressed={on}
            disabled={disabled}
            onClick={() => onClassify(cls)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led disabled:opacity-50",
              on ? "bg-surface-3 text-ink" : "text-muted hover:text-ink",
            )}
          >
            <i aria-hidden className={cn("size-1.5 rounded-full", TICK_BG[BUDGET_TICK[cls]])} />
            {BUDGET_LABEL[cls]}
          </button>
        );
      })}
    </div>
  );
}
