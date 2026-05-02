/**
 * Global jobs panel (issue #26).
 *
 * Shows a popover with the currently active and recently finished jobs in
 * the AppShell header. Polls /api/jobs at 1 Hz when any job is active so
 * the user can see progress without digging into per-stage badges, and
 * lets them cancel a runaway trim mid-encode.
 *
 * Polling is paused when no jobs are running to keep the request volume
 * close to zero on idle screens.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Loader2,
  X,
  XOctagon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError, api, type Job } from "@/lib/api";
import { cn } from "@/lib/utils";

const ACTIVE_POLL_MS = 1000;
const IDLE_POLL_MS = 5000;
// Show at most this many jobs in the popover to keep it scannable.
const VISIBLE_LIMIT = 8;

const KIND_LABEL: Record<string, string> = {
  detect_beep: "Detect beep",
  trim: "Trim audit clip",
  shot_detect: "Detect shots",
};

function formatKind(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}

function isActive(job: Job): boolean {
  return job.status === "pending" || job.status === "running";
}

function jobLabel(job: Job): string {
  const stage = job.stage_number != null ? ` (Stage ${job.stage_number}` : "";
  // Per-camera jobs (multi-cam beep / trim) tag the row with the first
  // 6 chars of the video_id so the user can tell parallel jobs apart.
  // Stage-level jobs (shot_detect, export) don't carry a video_id, so
  // the row falls back to "(Stage N)" alone.
  const cam = job.video_id ? ` -- cam ${job.video_id.slice(0, 6)}` : "";
  const close = job.stage_number != null ? ")" : "";
  return `${formatKind(job.kind)}${stage}${cam}${close}`;
}

interface StatusIconProps {
  job: Job;
  className?: string;
}

function StatusIcon({ job, className }: StatusIconProps) {
  if (job.status === "succeeded") {
    return (
      <CheckCircle2
        className={cn("size-4 text-emerald-500", className)}
        aria-hidden
      />
    );
  }
  if (job.status === "failed") {
    return (
      <AlertCircle className={cn("size-4 text-destructive", className)} aria-hidden />
    );
  }
  if (job.status === "cancelled") {
    return (
      <XOctagon
        className={cn("size-4 text-muted-foreground", className)}
        aria-hidden
      />
    );
  }
  return (
    <Loader2
      className={cn("size-4 animate-spin text-primary", className)}
      aria-hidden
    />
  );
}

export function JobsPanel() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [open, setOpen] = useState(false);
  const [cancelInFlight, setCancelInFlight] = useState<Set<string>>(() => new Set());
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await api.listJobs();
      setJobs(next);
    } catch (err) {
      // Network blip: keep last snapshot. The next poll will retry.
      if (err instanceof ApiError) {
        // ignore
      }
    }
  }, []);

  // Poll the registry. Cadence shifts to ACTIVE_POLL_MS while any job is
  // running and back to IDLE_POLL_MS when everything settles, so an idle
  // screen barely talks to the backend. The active flag is read from a
  // ref so we don't restart the interval each render.
  const activeRef = useRef(false);
  activeRef.current = jobs.some(isActive);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      if (cancelled) return;
      await refresh();
      if (cancelled) return;
      const interval = activeRef.current ? ACTIVE_POLL_MS : IDLE_POLL_MS;
      timer = setTimeout(tick, interval);
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer != null) clearTimeout(timer);
    };
  }, [refresh]);

  // Close popover on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (popoverRef.current?.contains(target)) return;
      if (triggerRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const sorted = useMemo(() => {
    // Active first (newest first within active), then most recent finished.
    const active = jobs.filter(isActive);
    const finished = jobs.filter((j) => !isActive(j));
    active.sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
    finished.sort((a, b) => {
      const at = a.finished_at ?? a.updated_at;
      const bt = b.finished_at ?? b.updated_at;
      return new Date(bt).getTime() - new Date(at).getTime();
    });
    return [...active, ...finished].slice(0, VISIBLE_LIMIT);
  }, [jobs]);

  const activeCount = jobs.filter(isActive).length;
  const failedCount = jobs.filter((j) => j.status === "failed").length;

  const cancel = async (jobId: string) => {
    setCancelInFlight((prev) => {
      const next = new Set(prev);
      next.add(jobId);
      return next;
    });
    try {
      const updated = await api.cancelJob(jobId);
      setJobs((prev) => prev.map((j) => (j.id === jobId ? updated : j)));
    } catch {
      /* ignore -- next poll will resync */
    } finally {
      setCancelInFlight((prev) => {
        const next = new Set(prev);
        next.delete(jobId);
        return next;
      });
    }
  };

  const triggerLabel =
    activeCount > 0
      ? `${activeCount} job${activeCount === 1 ? "" : "s"} running`
      : failedCount > 0
        ? `${failedCount} recent failure${failedCount === 1 ? "" : "s"}`
        : "Jobs";

  return (
    <div className="relative">
      <Button
        ref={triggerRef}
        variant={activeCount > 0 ? "default" : "ghost"}
        size="sm"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={triggerLabel}
        className="gap-2"
      >
        {activeCount > 0 ? (
          <Loader2 className="size-4 animate-spin" aria-hidden />
        ) : (
          <Activity className="size-4" aria-hidden />
        )}
        <span className="text-xs font-medium tracking-tight">Jobs</span>
        {activeCount > 0 ? (
          <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
            {activeCount}
          </Badge>
        ) : failedCount > 0 ? (
          <Badge variant="destructive" className="px-1.5 py-0 text-[10px]">
            {failedCount}
          </Badge>
        ) : null}
      </Button>
      {open ? (
        <div
          ref={popoverRef}
          role="dialog"
          aria-label="Background jobs"
          className="absolute right-0 z-30 mt-2 w-96 max-w-[calc(100vw-1rem)] rounded-md border border-border bg-popover p-3 text-popover-foreground shadow-lg"
        >
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold tracking-tight">Background jobs</h2>
            <span className="text-xs text-muted-foreground">
              {activeCount > 0 ? `${activeCount} active` : "Idle"}
            </span>
          </div>
          {sorted.length === 0 ? (
            <p className="py-6 text-center text-xs text-muted-foreground">
              No recent jobs.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {sorted.map((job) => (
                <JobRow
                  key={job.id}
                  job={job}
                  busy={cancelInFlight.has(job.id)}
                  onCancel={() => cancel(job.id)}
                />
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}

interface JobRowProps {
  job: Job;
  busy: boolean;
  onCancel: () => void;
}

function JobRow({ job, busy, onCancel }: JobRowProps) {
  const active = isActive(job);
  const progressPct =
    job.progress != null ? Math.round(Math.min(1, Math.max(0, job.progress)) * 100) : null;
  const statusLine = job.cancel_requested
    ? "Cancelled by user"
    : job.status === "failed"
      ? job.error ?? "Failed"
      : (job.message ?? job.status);
  return (
    <li className="rounded-md border border-border/60 bg-card/50 p-2">
      <div className="flex items-start gap-2">
        <StatusIcon job={job} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <p className="truncate text-xs font-medium">{jobLabel(job)}</p>
            {progressPct != null && active ? (
              <span className="text-[10px] tabular-nums text-muted-foreground">
                {progressPct}%
              </span>
            ) : null}
          </div>
          <p className="truncate text-[11px] text-muted-foreground">{statusLine}</p>
          {active && progressPct != null ? (
            <div
              className="mt-1 h-1 w-full overflow-hidden rounded bg-muted"
              role="progressbar"
              aria-valuenow={progressPct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="h-full bg-primary transition-[width]"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          ) : null}
        </div>
        {active ? (
          <Button
            variant="ghost"
            size="icon"
            className="size-7 shrink-0"
            disabled={busy || job.cancel_requested}
            onClick={onCancel}
            aria-label={`Cancel ${jobLabel(job)}`}
            title="Cancel"
          >
            {busy ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <X className="size-3.5" aria-hidden />
            )}
          </Button>
        ) : null}
      </div>
    </li>
  );
}
