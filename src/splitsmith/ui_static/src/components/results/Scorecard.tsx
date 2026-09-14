/**
 * Scorecard -- the imported scoreboard figures as a quiet footer of the
 * stage page's shot table (UX PR 4): hit factor, stage %, points, the
 * A/C/D/M/NS/procedural counts and a DQ chip, with the sync time on the
 * right. Every count pairs a text label with its number so colour is
 * never the sole carrier. Renders nothing without a scorecard: a
 * splits-only stage degrades gracefully.
 */
import { Chip } from "@/components/ui/Chip";
import type { StageScorecard } from "@/lib/api";
import { cn } from "@/lib/utils";

interface ScorecardProps {
  scorecard: StageScorecard | null;
  /** ISO timestamp of the scoreboard sync that wrote this card. */
  updatedAt?: string | null;
  className?: string;
}

function fmt(value: number | null, decimals = 0): string {
  return value == null ? "—" : value.toFixed(decimals);
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <span className="whitespace-nowrap">
      {label} <b className="font-medium text-ink-2">{value}</b>
    </span>
  );
}

export function Scorecard({ scorecard, updatedAt, className }: ScorecardProps) {
  if (!scorecard) return null;
  const { hit_factor, stage_points, stage_pct, alphas, charlies, deltas, misses, no_shoots, procedurals, dq } = scorecard;
  const hits = [`A ${fmt(alphas)}`, `C ${fmt(charlies)}`, `D ${fmt(deltas)}`, `M ${fmt(misses)}`, `NS ${fmt(no_shoots)}`];
  if (procedurals) hits.push(`Proc ${procedurals}`);
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-4 gap-y-1 rounded-[10px] border border-rule bg-surface px-3.5 py-2.5 font-mono text-sm text-muted",
        className,
      )}
    >
      <Figure label="Hit factor" value={fmt(hit_factor, 4)} />
      <Figure label="Stage" value={stage_pct == null ? "—" : `${fmt(stage_pct, 2)}%`} />
      <Figure label="Points" value={fmt(stage_points, 0)} />
      <span className="numeral whitespace-nowrap">{hits.join(" · ")}</span>
      {dq ? <Chip tone="warn">DQ</Chip> : null}
      {updatedAt ? <span className="ml-auto font-sans">From scoreboard, {formatTimestamp(updatedAt)}</span> : null}
    </div>
  );
}
