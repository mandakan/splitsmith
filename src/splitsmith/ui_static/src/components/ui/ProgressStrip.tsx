/**
 * ProgressStrip -- the ambient jobs line under the top bar (spec
 * 2026-09-13 s3.2). Rendered by JobsSurface, which owns the jobs state and
 * the drawer, and portalled into RootLayout's strip slot: exactly one
 * poller per shell, and the strip's click opens the same drawer. Nothing
 * renders while idle. The amber dot is the page's one glow.
 */
import type { Job } from "@/lib/api";
import { kindLabel } from "@/lib/jobLabels";
import type { JobsState } from "@/lib/jobs";
import { cn } from "@/lib/utils";

export interface ProgressStripProps {
  state: JobsState;
  onOpen: () => void;
  onDismissFailed: (job: Job) => void;
}

function stageText(job: Job): string {
  return job.stage_number != null ? ` · stage ${job.stage_number}` : "";
}

const BASE =
  "flex h-7 items-center gap-3 border-b border-rule bg-[#0d0f12] px-4 text-[12px] text-muted";

export function ProgressStrip({ state, onOpen, onDismissFailed }: ProgressStripProps) {
  const active = [...state.running, ...state.pending];
  const failed = state.failed[0];
  if (active.length === 0 && !failed) return null;

  if (active.length > 0) {
    const cur = state.running[0] ?? active[0];
    const done = state.jobs.filter((j) => j.status === "succeeded").length;
    const total = done + active.length;
    const pct = cur.progress != null ? Math.round(cur.progress * 100) : null;
    return (
      <div role="status" className={BASE}>
        <i
          aria-hidden
          className="size-1.5 shrink-0 rounded-full bg-live shadow-[0_0_8px_rgba(251,191,36,0.5)]"
        />
        <span className="truncate text-ink-2">
          {kindLabel(cur.kind)}
          {stageText(cur)}
        </span>
        <span
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct ?? undefined}
          className="relative h-[3px] w-40 shrink-0 overflow-hidden rounded bg-surface-3"
        >
          <span
            className={cn(
              "absolute inset-y-0 left-0 rounded bg-live",
              pct == null && "w-1/3 motion-safe:animate-pulse",
            )}
            style={pct != null ? { width: `${pct}%` } : undefined}
          />
        </span>
        <span className="numeral shrink-0">
          {done + 1} of {total}
        </span>
        <button type="button" onClick={onOpen} className="ml-auto shrink-0 hover:text-ink">
          All jobs &rsaquo;
        </button>
      </div>
    );
  }

  const f = failed!;
  return (
    <div role="status" className={BASE}>
      <i aria-hidden className="size-1.5 shrink-0 rounded-full bg-led" />
      <span className="truncate text-led-text">
        {kindLabel(f.kind)} failed{stageText(f)}
      </span>
      <span className="ml-auto flex shrink-0 gap-3">
        <button type="button" onClick={() => void state.retry(f)} className="hover:text-ink">
          Retry
        </button>
        <button type="button" onClick={onOpen} className="hover:text-ink">
          Details
        </button>
        <button type="button" onClick={() => onDismissFailed(f)} className="hover:text-ink">
          Dismiss
        </button>
      </span>
    </div>
  );
}
