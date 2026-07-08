import type { StageStatus } from "@/lib/api";
import { isTerminal } from "@/lib/stageStatus";
import { StageDot } from "@/components/ui/StageDot";
import { cn } from "@/lib/utils";

export interface StageChipRailItem {
  stageNumber: number;
  stageName: string;
  status: StageStatus;
}

export interface StageChipRailProps {
  stages: StageChipRailItem[];
  /** Which stage is currently selected in the audit canvas. ``null`` means
   *  no stage is active (rare; e.g. while loading). Distinct from
   *  ``status`` -- a chip can be both active AND in any of the six
   *  lifecycle states. */
  activeStage: number | null;
  onPick: (stageNumber: number) => void;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/**
 * Horizontal rail of stage chips above the audit canvas.
 *
 * The trailing status mark is the same :type:`StageDot` primitive the
 * MatchSidebar renders, so the visual language stays consistent and any
 * change to the taxonomy is a one-file edit. The chip's active treatment
 * (LED border + tint background) is independent of the dot status -- the
 * dot reports lifecycle, the chrome reports selection.
 */
export function StageChipRail({ stages, activeStage, onPick }: StageChipRailProps) {
  return (
    <div
      role="tablist"
      aria-label="Stages"
      className="flex items-center gap-3 overflow-x-auto py-1"
    >
      <span className="shrink-0 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-subtle">
        Stages
      </span>
      <div className="flex shrink-0 items-center gap-0.5">
        {stages.map((s) => {
          const on = s.stageNumber === activeStage;
          const terminal = isTerminal(s.status);
          return (
            <button
              key={s.stageNumber}
              type="button"
              role="tab"
              aria-selected={on}
              onClick={() => onPick(s.stageNumber)}
              className={cn(
                "inline-flex shrink-0 items-center gap-2.5 whitespace-nowrap rounded-md border px-3 py-2 font-display text-xs font-bold uppercase tracking-[0.04em] transition-colors",
                on
                  ? "border-led-deep bg-led-tint text-ink"
                  : terminal
                    ? "border-transparent text-ink-2 hover:bg-surface-2"
                    : "border-transparent text-muted hover:bg-surface-2",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "inline-flex h-[18px] w-[22px] items-center justify-center rounded font-mono text-[0.625rem] font-extrabold tabular-nums",
                  on
                    ? "border border-led-deep bg-led-fill text-ink"
                    : s.status === "audited"
                      ? "border border-done/30 bg-done/15 text-done"
                      : "border border-rule bg-surface-3 text-muted",
                )}
              >
                {pad2(s.stageNumber)}
              </span>
              <span>{s.stageName}</span>
              <StageDot status={s.status} context="chip" />
            </button>
          );
        })}
      </div>
    </div>
  );
}
