import { CheckCircle2 } from "lucide-react";

import { cn } from "@/lib/utils";

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
 * Audit-complete card. Renders in place of the StageActionBar CTA when
 * the operator finishes the last stage of the last shooter (?done=1).
 * Matches the design's ARSessionSummary -- done-green hairline, big
 * "Audit complete" heading, 4-up stat block.
 */
export function SessionSummary({
  shooterName,
  stats,
  nextShooterLabel,
  onJumpToOverview,
  onAuditNextShooter,
  onExport,
}: SessionSummaryProps) {
  // With shooters left to audit, "Audit next" keeps the primary (green)
  // slot and Export renders as a secondary link; once the whole match is
  // signed off, Export is the natural next step and takes the primary.
  const hasNext = Boolean(onAuditNextShooter && nextShooterLabel);
  return (
    <div
      role="region"
      aria-label="Audit complete"
      className="relative overflow-hidden rounded-3xl border border-done/40 bg-surface px-7 py-6 shadow-[inset_0_0_0_1px_var(--color-rule),0_24px_60px_-24px_rgba(0,0,0,0.7),0_0_32px_color-mix(in_srgb,_var(--color-done)_22%,_transparent)]"
      style={{
        background:
          "linear-gradient(180deg, color-mix(in srgb, var(--color-done) 6%, var(--color-surface)) 0%, var(--color-surface) 100%)",
      }}
    >
      <span
        aria-hidden
        className="absolute inset-y-0 left-0 w-[3px] bg-done shadow-[0_0_14px_var(--color-done-glow)]"
      />
      <div className="flex items-start gap-4">
        <span
          aria-hidden
          className="inline-flex size-12 shrink-0 items-center justify-center rounded-full border border-done/50 bg-done/15 text-done shadow-[0_0_24px_var(--color-done-glow)]"
        >
          <CheckCircle2 className="size-6" strokeWidth={2.6} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[0.625rem] font-bold uppercase tracking-[0.18em] text-done">
            Audit complete
          </div>
          <div className="mt-1 font-display text-2xl font-bold uppercase leading-tight tracking-[-0.01em] text-ink">
            {shooterName
              ? `${shooterName}'s stages are signed off`
              : "All stages signed off"}
          </div>
          <p className="mt-1 max-w-prose text-sm leading-snug text-muted">
            Shot tables are written. Trim caches are warm. Ready for Coach
            review or FCPXML export.
          </p>
        </div>
        <div className="ml-auto inline-flex items-center gap-2 self-start">
          {onJumpToOverview ? (
            <button
              type="button"
              onClick={onJumpToOverview}
              className="inline-flex items-center rounded-md border border-rule bg-surface-2 px-3.5 py-2 font-display text-[0.75rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:bg-surface-3 hover:text-ink"
            >
              Match overview
            </button>
          ) : null}
          {onExport ? (
            <button
              type="button"
              onClick={onExport}
              className={cn(
                "inline-flex items-center rounded-md px-3.5 py-2 font-display text-[0.75rem] font-bold uppercase tracking-[0.08em]",
                hasNext
                  ? "border border-rule bg-surface-2 text-ink-2 hover:bg-surface-3 hover:text-ink"
                  : "border-0 bg-done text-bg shadow-[0_0_0_1px_var(--color-done),0_0_22px_var(--color-done-glow)] hover:brightness-110",
              )}
            >
              Export
            </button>
          ) : null}
          {hasNext ? (
            <button
              type="button"
              onClick={onAuditNextShooter}
              className="inline-flex items-center rounded-md border-0 bg-done px-3.5 py-2 font-display text-[0.75rem] font-bold uppercase tracking-[0.08em] text-bg shadow-[0_0_0_1px_var(--color-done),0_0_22px_var(--color-done-glow)] hover:brightness-110"
            >
              Audit {nextShooterLabel}
            </button>
          ) : null}
        </div>
      </div>

      {stats.length > 0 ? (
        <div
          className={cn(
            "mt-5 grid gap-3",
            stats.length === 1
              ? "grid-cols-1"
              : stats.length === 2
                ? "grid-cols-2"
                : stats.length === 3
                  ? "grid-cols-3"
                  : "grid-cols-4",
          )}
        >
          {stats.map((s) => (
            <div
              key={s.label}
              className="rounded-2xl border border-rule bg-surface-2 px-4 py-3"
            >
              <div className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-subtle">
                {s.label}
              </div>
              <div className="mt-1 font-mono text-xl font-bold leading-none tabular-nums text-ink">
                {s.value}
              </div>
              {s.sub ? (
                <div className="mt-1 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                  {s.sub}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
