/**
 * Floating jobs FAB (issues #26, #51).
 *
 * Renders a fixed bottom-right action button that summarises the job
 * registry and opens a popover with the same per-job rows the old
 * in-header panel used. The FAB lives outside page scroll so multi-cam
 * beep / trim runs stay visible on long screens (audit, export).
 *
 * Polling cadence is 1 Hz while any job is active and 5 s otherwise so
 * an idle screen barely talks to the backend.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
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
const POPOVER_ID = "jobs-panel-popover";

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
      className={cn("size-4 motion-safe:animate-spin text-primary", className)}
      aria-hidden
    />
  );
}

type FabState = "idle" | "running" | "failed";

export function JobsPanel() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [open, setOpen] = useState(false);
  const [cancelInFlight, setCancelInFlight] = useState<Set<string>>(() => new Set());
  // Failed-job ids the user has already seen. A failure stays "sticky"
  // (destructive FAB state) until either the popover opens or the
  // failed job ages out of the registry. Acked-but-still-failed jobs
  // continue to render in the list, just without driving FAB state.
  const [acked, setAcked] = useState<Set<string>>(() => new Set());
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
  const unackedFailedIds = useMemo(
    () =>
      jobs
        .filter((j) => j.status === "failed" && !acked.has(j.id))
        .map((j) => j.id),
    [jobs, acked],
  );
  const unackedFailedCount = unackedFailedIds.length;

  // Drop ack entries for jobs that have aged out of the registry so
  // the set doesn't grow unbounded across long sessions.
  useEffect(() => {
    setAcked((prev) => {
      if (prev.size === 0) return prev;
      const live = new Set(jobs.map((j) => j.id));
      let changed = false;
      const next = new Set<string>();
      prev.forEach((id) => {
        if (live.has(id)) next.add(id);
        else changed = true;
      });
      return changed ? next : prev;
    });
  }, [jobs]);

  // Auto-open on a new failure. Re-fires when the unacked count grows,
  // not when an existing failure remains unacked, so the popover doesn't
  // keep popping back if the user closes it without clicking through.
  const prevUnackCountRef = useRef(0);
  useEffect(() => {
    if (unackedFailedCount > prevUnackCountRef.current && !open) {
      setOpen(true);
    }
    prevUnackCountRef.current = unackedFailedCount;
  }, [unackedFailedCount, open]);

  // Acknowledge unacked failures whenever the popover is open. Runs on
  // open transitions and also while open if a new failure lands, so the
  // failed badge clears immediately once the user has the panel up.
  useEffect(() => {
    if (!open || unackedFailedCount === 0) return;
    setAcked((prev) => {
      const next = new Set(prev);
      unackedFailedIds.forEach((id) => next.add(id));
      return next;
    });
  }, [open, unackedFailedCount, unackedFailedIds]);

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

  // Global Alt+J shortcut to open / focus the panel. Skip when an editable
  // field is focused so the chord doesn't steal keystrokes from a textarea
  // that wants Alt-modified input.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key !== "j" && e.key !== "J") return;
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      if (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        (t && t.isContentEditable)
      ) {
        return;
      }
      e.preventDefault();
      setOpen((v) => !v);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Focus management: pull focus into the popover on open, return it to
  // the FAB on close, and trap Tab inside while open.
  const prevOpenRef = useRef(open);
  useEffect(() => {
    if (prevOpenRef.current && !open) {
      triggerRef.current?.focus();
    }
    prevOpenRef.current = open;
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const root = popoverRef.current;
    if (!root) return;
    const queryFocusables = () =>
      Array.from(
        root.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
    const initial = queryFocusables();
    if (initial.length > 0) initial[0].focus();
    else root.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const list = queryFocusables();
      if (list.length === 0) {
        e.preventDefault();
        root.focus();
        return;
      }
      const first = list[0];
      const last = list[list.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey && (active === first || active === root)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    root.addEventListener("keydown", onKey);
    return () => {
      root.removeEventListener("keydown", onKey);
    };
  }, [open]);

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

  // Failed beats running beats idle so a failure stays loud even while
  // a follow-up retry is in flight.
  const fabState: FabState =
    unackedFailedCount > 0 ? "failed" : activeCount > 0 ? "running" : "idle";

  const triggerLabel =
    fabState === "failed"
      ? `Background jobs, ${unackedFailedCount} failed`
      : fabState === "running"
        ? `Background jobs, ${activeCount} running`
        : "Background jobs";

  const fabClass = cn(
    "relative inline-flex h-12 w-12 items-center justify-center rounded-full shadow-lg transition-colors",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
    fabState === "failed" &&
      "bg-destructive text-destructive-foreground hover:bg-destructive/90",
    fabState === "running" && "bg-primary text-primary-foreground hover:bg-primary/90",
    fabState === "idle" && "bg-muted text-muted-foreground hover:bg-accent",
  );

  return (
    <div
      // Mobile-safe inset: respect the home-indicator and any right-edge
      // safe area but never sit closer than 16px to the viewport edge.
      style={{
        bottom: "max(16px, calc(env(safe-area-inset-bottom) + 16px))",
        right: "max(16px, calc(env(safe-area-inset-right) + 16px))",
      }}
      className="fixed z-50"
    >
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={POPOVER_ID}
        aria-label={triggerLabel}
        className={fabClass}
      >
        {fabState === "failed" ? (
          <AlertTriangle className="size-5" aria-hidden />
        ) : fabState === "running" ? (
          <Loader2 className="size-5 motion-safe:animate-spin" aria-hidden />
        ) : (
          <Activity className="size-5" aria-hidden />
        )}
        {fabState !== "idle" ? (
          <Badge
            variant={fabState === "failed" ? "destructive" : "secondary"}
            className="absolute -right-1 -top-1 min-w-5 justify-center px-1 py-0 text-[10px]"
          >
            {fabState === "failed" ? unackedFailedCount : activeCount}
          </Badge>
        ) : null}
      </button>
      {/* aria-live region for assistive tech: announce count changes
          even when the popover is closed. Empty when idle so screen
          readers stay quiet on idle screens. */}
      <span className="sr-only" aria-live="polite" aria-atomic="true">
        {fabState === "failed"
          ? `${unackedFailedCount} background job${unackedFailedCount === 1 ? "" : "s"} failed`
          : fabState === "running"
            ? `${activeCount} background job${activeCount === 1 ? "" : "s"} running`
            : ""}
      </span>
      {open ? (
        <div
          ref={popoverRef}
          id={POPOVER_ID}
          role="dialog"
          aria-label="Background jobs"
          tabIndex={-1}
          className="absolute bottom-full right-0 z-50 mb-2 w-96 max-w-[calc(100vw-2rem)] rounded-md border border-border bg-popover p-3 text-popover-foreground shadow-lg focus:outline-none"
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
              <Loader2 className="size-3.5 motion-safe:animate-spin" aria-hidden />
            ) : (
              <X className="size-3.5" aria-hidden />
            )}
          </Button>
        ) : null}
      </div>
    </li>
  );
}
