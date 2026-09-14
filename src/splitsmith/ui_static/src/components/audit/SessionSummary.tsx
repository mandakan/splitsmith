import { CheckCircle2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { Stat, StatStrip } from "@/components/ui/Stat";

export interface SessionSummaryStat {
  label: string;
  value: string;
  sub?: string;
}

export interface SessionSummaryProps {
  /** Shooter whose stages were just signed off (or null when the data
   *  isn't available -- the title falls back to "Audit complete"). */
  shooterName: string | null;
  stats: SessionSummaryStat[];
  /** Optional "audit next shooter" CTA target. When null, only the
   *  "Match overview" link is shown. */
  nextShooterLabel?: string | null;
  onJumpToOverview?: () => void;
  onAuditNextShooter?: () => void;
  /** Jump to the Export page - the copy above promises "ready for
   *  FCPXML export", so the card has to offer the door it names. */
  onExport?: () => void;
}

/**
 * Audit-complete block. Renders under the canvas when the operator
 * finishes the last stage of the last shooter (?done=1): a done Label,
 * one heading, the stats as a strip, and the next step as the one
 * primary (Audit the next shooter while shooters remain, else Export).
 */
export function SessionSummary({
  shooterName,
  stats,
  nextShooterLabel,
  onJumpToOverview,
  onAuditNextShooter,
  onExport,
}: SessionSummaryProps) {
  const hasNext = Boolean(onAuditNextShooter && nextShooterLabel);
  return (
    <div role="region" aria-label="Audit complete" className="rounded-[10px] border border-rule bg-surface p-5">
      <div className="flex flex-wrap items-start gap-4">
        <span aria-hidden className="inline-flex size-9 shrink-0 items-center justify-center rounded-full border border-done/45 text-done">
          <CheckCircle2 className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <Label tone="done">Audit complete</Label>
          <h2 className="mt-1 text-lg font-semibold text-ink">
            {shooterName ? `${shooterName}'s stages are signed off` : "All stages signed off"}
          </h2>
          <p className="mt-1 max-w-[52ch] text-md text-muted">Shot tables are written. Next: Coach, Splits or Export.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {onJumpToOverview ? (
            <Button type="button" onClick={onJumpToOverview}>
              Overview
            </Button>
          ) : null}
          {onExport ? (
            <Button type="button" variant={hasNext ? "default" : "primary"} onClick={onExport}>
              Export
            </Button>
          ) : null}
          {hasNext ? (
            <Button type="button" variant="primary" onClick={onAuditNextShooter}>
              Audit {nextShooterLabel}
            </Button>
          ) : null}
        </div>
      </div>
      {stats.length > 0 ? (
        <StatStrip className="mt-4">
          {stats.map((s) => (
            <Stat key={s.label} label={s.label} value={s.value} unit={s.sub} />
          ))}
        </StatStrip>
      ) : null}
    </div>
  );
}
