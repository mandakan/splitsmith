import { cn } from "@/lib/utils";
import { summaryParts, type QueueStats } from "@/lib/uploadStats";

/**
 * One queue-level upload readout, shared by the dock and the upload
 * modal (#556).
 *
 * Hosted uploads run one file at a time on purpose, which without a
 * queue-level readout is indistinguishable from a hang: N-1 files sit at
 * 0% while one transfers. This is the readout -- which file is moving,
 * how far the whole queue has come, and how long is left.
 *
 * `layout="stacked"` puts the volatile clauses on a second line. Use it
 * anywhere under ~420px, where the single line wraps and the wrap point
 * moves as the ETA and byte figures change.
 */
export function UploadQueueSummary({
  queue,
  inFlight,
  showBar = true,
  layout = "inline",
  note,
  className,
}: {
  queue: QueueStats;
  inFlight: boolean;
  showBar?: boolean;
  layout?: "inline" | "stacked";
  /** Appended to the readout. Used where the surrounding list is
   *  narrower than the queue these numbers describe. */
  note?: string;
  className?: string;
}) {
  const { primary, detail } = summaryParts(queue, inFlight, note);

  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <span className="font-display text-[0.75rem] font-bold uppercase tracking-[0.08em]">
        {primary}
        {layout === "inline" && detail !== null ? ` . ${detail}` : null}
      </span>
      {layout === "stacked" && detail !== null && (
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          {detail}
        </span>
      )}
      {showBar && (
        <div className="h-1 w-full overflow-hidden rounded-full bg-surface-3">
          <div
            className="h-full bg-led transition-[width]"
            style={{ width: `${queue.pct}%` }}
          />
        </div>
      )}
    </div>
  );
}
