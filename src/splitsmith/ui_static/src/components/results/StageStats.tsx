/**
 * StageStats - read-only stats strip for the Results stage view.
 * Stage time, draw, average split, fastest split, shot count (spec s4.5 order).
 * Presentational only; the page computes the numbers (split stats count
 * split-classed intervals only - lib/splits.statisticSplits owns the
 * rule, issue #772). Composes StatStrip (lead): stage time on its own row
 * below md, one row of five at md+, and shrink-0 so ResultsStage's scroll
 * column cannot crush it (#970).
 * Read-only by contract: part of the share-link surface.
 */
import { Stat, StatStrip } from "@/components/ui/Stat";

interface StageStatsProps {
  stageTime: number | null;
  shotCount: number;
  draw: number | null;
  fastestSplit: number | null;
  avgSplit: number | null;
}

function secs(v: number | null, digits: number): { value: string; unit?: string } {
  return v != null ? { value: v.toFixed(digits), unit: "s" } : { value: "-" };
}

export function StageStats({ stageTime, shotCount, draw, fastestSplit, avgSplit }: StageStatsProps) {
  return (
    <StatStrip lead>
      <Stat label="Stage time" {...secs(stageTime, 2)} />
      <Stat label="Draw" {...secs(draw, 2)} />
      <Stat label="Avg split" {...secs(avgSplit, 3)} />
      <Stat label="Fastest split" {...secs(fastestSplit, 3)} />
      <Stat label="Shots" value={String(shotCount)} />
    </StatStrip>
  );
}
