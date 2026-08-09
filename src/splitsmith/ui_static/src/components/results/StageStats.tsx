/**
 * StageStats - read-only stats strip for the Results stage view.
 * Stage time, shot count, draw, fastest split, average split.
 * Presentational only; the page computes the numbers (split stats count
 * split-classed intervals only - lib/splits.statisticSplits owns the
 * rule, issue #772). 2-wide grid on mobile, one row of five at md+.
 * Read-only by contract: part of the future share-link surface.
 */
import { cn } from "@/lib/utils";

interface StageStatsProps {
  stageTime: number | null;
  shotCount: number;
  draw: number | null;
  fastestSplit: number | null;
  avgSplit: number | null;
}

function Cell({
  label,
  value,
  className,
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1 border-rule px-4 py-3", className)}>
      <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
        {label}
      </span>
      <span className="font-mono text-xl font-bold leading-none tabular-nums text-ink">
        {value}
      </span>
    </div>
  );
}

export function StageStats({ stageTime, shotCount, draw, fastestSplit, avgSplit }: StageStatsProps) {
  return (
    <div className="grid grid-cols-2 overflow-hidden rounded-xl border border-rule-strong bg-surface-2 md:grid-cols-5">
      <Cell
        label="Stage time"
        value={stageTime != null ? `${stageTime.toFixed(2)}s` : "-"}
        className="border-b border-r md:border-b-0"
      />
      <Cell
        label="Shots"
        value={String(shotCount)}
        className="border-b md:border-b-0 md:border-r"
      />
      <Cell
        label="Draw"
        value={draw != null ? `${draw.toFixed(2)}s` : "-"}
        className="border-b border-r md:border-b-0"
      />
      <Cell
        label="Fastest split"
        value={fastestSplit != null ? `${fastestSplit.toFixed(3)}s` : "-"}
        className="border-b md:border-b-0 md:border-r"
      />
      <Cell
        label="Avg split"
        value={avgSplit != null ? `${avgSplit.toFixed(3)}s` : "-"}
        className="col-span-2 md:col-span-1"
      />
    </div>
  );
}
