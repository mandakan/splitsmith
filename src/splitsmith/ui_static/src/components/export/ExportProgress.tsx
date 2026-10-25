/**
 * An export while it runs (spec 2026-10-25): what it is, how long is left,
 * and a row per part with its status, a bar and its time. Cancel stops the
 * whole export; Retry runs a failed part again. Derivations live in
 * `lib/exportProgress`; the page polls through `useActiveExports`.
 */

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { useConfirm } from "@/components/useConfirm";
import type { ExportRow, ExportView } from "@/lib/api";
import { rowTimeLine, timeLeftLine } from "@/lib/exportProgress";
import { cn } from "@/lib/utils";

const STATUS_WORD: Record<string, string> = {
  waiting: "Waiting",
  running: "Running",
  done: "Done",
  skipped: "Reused",
  failed: "Failed",
  cancelled: "Cancelled",
};

function Bar({ row }: { row: ExportRow }) {
  const pct =
    row.status === "done" || row.status === "skipped"
      ? 100
      : row.progress != null
        ? Math.round(row.progress * 100)
        : null;
  const live = row.status === "running";
  return (
    <span
      role="progressbar"
      aria-label={row.label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct ?? undefined}
      className="relative h-1 w-full overflow-hidden rounded bg-surface-3"
    >
      <span
        className={cn(
          "absolute inset-y-0 left-0 rounded",
          row.status === "failed" ? "bg-destructive" : live ? "bg-live" : "bg-done",
          pct == null && live && "w-1/3 motion-safe:animate-pulse",
        )}
        style={pct != null ? { width: `${pct}%` } : undefined}
      />
    </span>
  );
}

export interface ExportProgressProps {
  view: ExportView;
  onCancel: () => void;
  onRetry: (jobId: string) => void;
  /** Put away an export that ended (its failed rows stay until then). */
  onDismiss?: () => void;
}

export function ExportProgress({ view, onCancel, onRetry, onDismiss }: ExportProgressProps) {
  const confirm = useConfirm();
  const running = view.status === "running";
  const timeLeft = timeLeftLine(view);

  async function cancel() {
    const answer = await confirm({
      title: "Stop this export?",
      body: "Parts already finished are kept and reused when you export again.",
      confirmLabel: "Stop export",
    });
    if (answer.confirmed) onCancel();
  }

  return (
    <section aria-label="Export progress" className="mt-3 border-t border-rule pt-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-md text-ink">{view.label}</span>
        {running ? (
          <Button variant="ghost" size="sm" onClick={() => void cancel()} aria-label="Cancel export">
            Cancel
          </Button>
        ) : onDismiss ? (
          <Button variant="ghost" size="sm" onClick={onDismiss}>
            Dismiss
          </Button>
        ) : null}
      </div>
      {timeLeft ? <p className="numeral text-sm text-ink-2">{timeLeft}</p> : null}
      <ul className="mt-2 divide-y divide-rule">
        {view.rows.map((row) => (
          <li key={`${row.job_id}:${row.key}`} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 py-1.5">
            <span className="truncate text-sm text-ink">{row.label}</span>
            <span className="numeral text-sm text-muted">
              {rowTimeLine(row) || <Label>{STATUS_WORD[row.status] ?? row.status}</Label>}
            </span>
            <Bar row={row} />
            {row.retryable ? (
              <Button
                variant="default"
                size="sm"
                onClick={() => onRetry(row.job_id)}
                aria-label={`Retry ${row.label}`}
              >
                Retry
              </Button>
            ) : (
              <span />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
