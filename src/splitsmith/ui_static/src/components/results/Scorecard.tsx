/**
 * Scorecard - read-only SSI Scoreboard scorecard display for the Results
 * stage view. Hit factor / stage % / stage points on top, hit-count
 * breakdown (A/C/D/M/NS/procedurals + DQ flag) below. Presentational
 * only; renders nothing when no scorecard is attached (splits-only
 * degrades gracefully). Every hit type pairs a text label with its
 * count so color is never the sole carrier of meaning.
 */
import type { StageScorecard } from "@/lib/api";
import { cn } from "@/lib/utils";

interface ScorecardProps {
  scorecard: StageScorecard | null;
  className?: string;
}

function fmt(value: number | null, decimals = 0): string {
  return value == null ? "--" : value.toFixed(decimals);
}

function Stat({
  label,
  value,
  className,
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1 px-4 py-3", className)}>
      <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
        {label}
      </span>
      <span className="font-mono text-xl font-bold leading-none tabular-nums text-ink">
        {value}
      </span>
    </div>
  );
}

function HitChip({ label, value }: { label: string; value: number | null }) {
  return (
    <span className="inline-flex items-center gap-1 font-mono text-xs tabular-nums text-ink-2">
      <span className="text-muted">{label}</span>
      {fmt(value)}
    </span>
  );
}

export function Scorecard({ scorecard, className }: ScorecardProps) {
  if (!scorecard) return null;

  const {
    hit_factor,
    stage_points,
    stage_pct,
    alphas,
    charlies,
    deltas,
    misses,
    no_shoots,
    procedurals,
    dq,
  } = scorecard;

  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border border-rule-strong bg-surface-2",
        className,
      )}
    >
      <div className="grid grid-cols-3 divide-x divide-rule border-b border-rule">
        <Stat label="Hit factor" value={fmt(hit_factor, 4)} />
        <Stat label="Stage %" value={stage_pct == null ? "--" : `${fmt(stage_pct, 2)}%`} />
        <Stat label="Stage points" value={fmt(stage_points, 0)} />
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
        <HitChip label="A" value={alphas} />
        <HitChip label="C" value={charlies} />
        <HitChip label="D" value={deltas} />
        <HitChip label="M" value={misses} />
        <HitChip label="NS" value={no_shoots} />
        <HitChip label="Proc" value={procedurals} />
        {dq ? (
          <span className="inline-flex items-center rounded border border-led-deep bg-led-tint px-1.5 py-0.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.06em] text-led-text">
            DQ
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function matchTotals(
  stages: { scorecard: StageScorecard | null; time_seconds: number }[],
): {
  points: number;
  time: number;
  hitFactor: number | null;
  alphas: number;
  charlies: number;
  deltas: number;
  misses: number;
} {
  const scored = stages.filter((stage) => stage.scorecard != null);

  const points = scored.reduce((sum, stage) => sum + (stage.scorecard?.stage_points ?? 0), 0);
  const time = scored.reduce((sum, stage) => sum + stage.time_seconds, 0);
  const alphas = scored.reduce((sum, stage) => sum + (stage.scorecard?.alphas ?? 0), 0);
  const charlies = scored.reduce((sum, stage) => sum + (stage.scorecard?.charlies ?? 0), 0);
  const deltas = scored.reduce((sum, stage) => sum + (stage.scorecard?.deltas ?? 0), 0);
  const misses = scored.reduce((sum, stage) => sum + (stage.scorecard?.misses ?? 0), 0);

  return {
    points,
    time,
    hitFactor: time > 0 ? points / time : null,
    alphas,
    charlies,
    deltas,
    misses,
  };
}
