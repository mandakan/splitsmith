/**
 * StageStats - read-only stats strip for the Results stage view.
 * Stage time, shot count, fastest split, average split. Presentational
 * only; the page computes the numbers (draw excluded from split stats).
 * 2x2 grid on mobile, one row of four at md+. Read-only by contract:
 * part of the future share-link surface.
 */
import { cn } from "@/lib/utils";

interface StageStatsProps {
  stageTime: number | null;
  shotCount: number;
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

export function StageStats({ stageTime, shotCount, fastestSplit, avgSplit }: StageStatsProps) {
  return (
    <div className="grid grid-cols-2 overflow-hidden rounded-xl border border-rule-strong bg-surface-2 md:grid-cols-4">
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
        label="Fastest split"
        value={fastestSplit != null ? `${fastestSplit.toFixed(3)}s` : "-"}
        className="border-r"
      />
      <Cell label="Avg split" value={avgSplit != null ? `${avgSplit.toFixed(3)}s` : "-"} />
    </div>
  );
}
