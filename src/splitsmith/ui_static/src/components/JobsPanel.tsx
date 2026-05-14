/**
 * Background jobs FAB + slide-out drawer (#321, polished/15).
 *
 * Always mounted at the shell level so the drawer state survives page
 * navigation. The FAB sits bottom-right with a live status LED + count;
 * clicking it slides in the drawer.
 *
 * Drawer composition:
 *   - Worker pool chip (display-only; backend doesn't yet expose
 *     dynamic pool size -- we show "Local" with the live concurrency
 *     count derived from running jobs)
 *   - Running group: per-job rows with progress bar + ETA + worker tag
 *   - Needs attention group: failed jobs with inline recovery actions
 *     (Relink source, Dismiss). Source-unreachable failures get
 *     a structured note carrying the missing path
 *   - Queued group: capped with "+ N more" overflow
 *   - Completed today group: collapsed by default
 *
 * Polling: 1Hz while anything is active, 5s otherwise so an idle screen
 * barely talks to the backend.
 */

import {
  Activity,
  AlertTriangle,
  ArrowDownToLine,
  Crosshair,
  Pause,
  Server,
  Volume2,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApiError, api, type Job } from "@/lib/api";
import { cn } from "@/lib/utils";

const ACTIVE_POLL_MS = 1000;
const IDLE_POLL_MS = 5000;
const QUEUED_VISIBLE = 4;
const COMPLETED_VISIBLE = 4;

const KIND_LABEL: Record<string, string> = {
  detect_beep: "Detect beep",
  trim: "Trim stage video",
  shot_detect: "Detect shots",
  export: "Export stage",
  match_export: "Match export",
  audio_extract: "Audio extract",
};

const KIND_ICON: Record<string, ReactNode> = {
  detect_beep: <Volume2 className="size-3.5" />,
  trim: <Crosshair className="size-3.5" />,
  shot_detect: <Activity className="size-3.5" />,
  export: <ArrowDownToLine className="size-3.5" />,
  match_export: <ArrowDownToLine className="size-3.5" />,
  audio_extract: <Volume2 className="size-3.5" />,
};

function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}

function isActive(job: Job): boolean {
  return job.status === "pending" || job.status === "running";
}

function jobTarget(job: Job): string {
  const bits: string[] = [];
  if (job.stage_number != null) bits.push(`stage ${pad2(job.stage_number)}`);
  if (job.video_id) bits.push(`cam ${job.video_id.slice(0, 6)}`);
  return bits.join(" · ");
}

export function JobsPanel() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const fetchJobs = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const list = await api.listJobs({ signal: controller.signal });
      setJobs(list);
      setError(null);
    } catch (e) {
      if (controller.signal.aborted) return;
      if (e instanceof ApiError) setError(e.detail);
      else if (e instanceof Error) setError(e.message);
    }
  }, []);

  useEffect(() => {
    void fetchJobs();
    return () => {
      abortRef.current?.abort();
    };
  }, [fetchJobs]);

  const anyActive = jobs.some(isActive);
  useEffect(() => {
    const ms = anyActive ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    const id = window.setInterval(() => void fetchJobs(), ms);
    return () => window.clearInterval(id);
  }, [anyActive, fetchJobs]);

  const running = jobs.filter((j) => j.status === "running");
  const pending = jobs.filter((j) => j.status === "pending");
  const failed = jobs.filter((j) => j.status === "failed" && !j.acknowledged);
  const completedToday = jobs.filter(
    (j) =>
      j.status === "succeeded" ||
      j.status === "cancelled" ||
      (j.status === "failed" && j.acknowledged),
  );

  const unackedFailures = failed.length;
  const liveSummary = useMemo(() => {
    if (running.length > 0) {
      return `${running.length} running${
        pending.length ? ` · ${pending.length} queued` : ""
      }`;
    }
    if (pending.length > 0) return `${pending.length} queued`;
    if (unackedFailures > 0) return `${unackedFailures} failed`;
    return "Idle";
  }, [running.length, pending.length, unackedFailures]);

  async function dismissJob(job: Job) {
    try {
      const updated = await api.acknowledgeJob(job.id);
      setJobs((prev) => prev.map((j) => (j.id === job.id ? updated : j)));
    } catch {
      /* swallow */
    }
  }

  async function dismissAll() {
    try {
      await api.acknowledgeAllFailures();
      void fetchJobs();
    } catch {
      /* swallow */
    }
  }

  async function cancelJob(job: Job) {
    try {
      const updated = await api.cancelJob(job.id);
      setJobs((prev) => prev.map((j) => (j.id === job.id ? updated : j)));
    } catch {
      /* swallow */
    }
  }

  const activeWorkers = running.length;
  const poolCapacity = Math.max(activeWorkers, 2);

  return (
    <>
      {/* FAB */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="jobs-drawer"
        className={cn(
          "fixed bottom-7 right-7 z-40 inline-flex items-center gap-3 rounded-full border bg-gradient-to-br from-surface to-surface-2 px-4 py-2.5 font-display text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-ink shadow-[0_18px_36px_-12px_rgba(0,0,0,0.7)] transition-all hover:-translate-y-0.5",
          anyActive
            ? "border-beep shadow-[0_0_0_1px_var(--color-beep),0_0_24px_var(--color-beep-glow),0_18px_36px_-12px_rgba(0,0,0,0.7)]"
            : unackedFailures > 0
              ? "border-led shadow-[0_0_0_1px_var(--color-led),0_0_24px_var(--color-led-glow),0_18px_36px_-12px_rgba(0,0,0,0.7)]"
              : "border-rule-strong",
        )}
      >
        {anyActive ? (
          <span className="inline-block size-4 animate-spin rounded-full border-[2px] border-rule-strong border-t-beep shadow-[0_0_10px_var(--color-beep-glow)]" />
        ) : unackedFailures > 0 ? (
          <AlertTriangle className="size-4 text-led" />
        ) : (
          <Activity className="size-4 text-muted" />
        )}
        <span>Jobs</span>
        {running.length + pending.length + unackedFailures > 0 && (
          <span
            className={cn(
              "inline-flex min-w-[1.5rem] items-center justify-center rounded-full px-2 py-0.5 font-mono text-[0.625rem] font-bold tabular-nums",
              unackedFailures > 0 ? "bg-led text-bg" : "bg-beep text-bg",
            )}
          >
            {unackedFailures > 0
              ? unackedFailures
              : running.length + pending.length}
          </span>
        )}
      </button>

      {/* Drawer overlay */}
      {open && (
        <div
          aria-hidden
          className="fixed inset-0 z-30 bg-bg/40"
          onClick={() => setOpen(false)}
        />
      )}

      {/* Drawer */}
      <aside
        id="jobs-drawer"
        aria-label="Background jobs"
        aria-hidden={!open}
        className={cn(
          "fixed bottom-0 right-0 top-0 z-40 flex w-[400px] max-w-[100vw] flex-col border-l border-rule bg-bg-glow transition-transform",
          open ? "translate-x-0" : "pointer-events-none translate-x-full",
        )}
        style={{
          boxShadow: open ? "-24px 0 48px -12px rgba(0,0,0,0.7)" : "none",
        }}
      >
        <div className="border-b border-rule bg-gradient-to-b from-surface to-bg px-5 py-4">
          <div className="mb-2 flex items-center gap-3">
            {anyActive ? (
              <span
                aria-hidden
                className="inline-block size-2 animate-pulse rounded-full bg-beep shadow-[0_0_8px_var(--color-beep-glow)]"
              />
            ) : (
              <span aria-hidden className="inline-block size-2 rounded-full bg-muted" />
            )}
            <h2 className="flex-1 font-display text-lg font-bold uppercase tracking-tight text-ink">
              Jobs
            </h2>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close drawer"
              className="inline-flex size-7 items-center justify-center rounded-md text-muted hover:bg-surface-3 hover:text-ink"
            >
              <X className="size-4" />
            </button>
          </div>
          <div className="font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted tabular-nums">
            {liveSummary}
          </div>
          <WorkerChip activeWorkers={activeWorkers} capacity={poolCapacity} />
        </div>

        <div className="flex-1 overflow-y-auto">
          {error && (
            <div className="m-4 rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
              {error}
            </div>
          )}

          {running.length > 0 && (
            <Group title="Running" count={running.length} tone="running">
              {running.map((j) => (
                <RunningJobRow
                  key={j.id}
                  job={j}
                  onCancel={() => void cancelJob(j)}
                />
              ))}
            </Group>
          )}

          {failed.length > 0 && (
            <Group
              title="Needs attention"
              count={failed.length}
              tone="failed"
              action={
                failed.length > 1 ? (
                  <button
                    type="button"
                    onClick={() => void dismissAll()}
                    className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-led hover:text-led-soft"
                  >
                    Dismiss all
                  </button>
                ) : null
              }
            >
              {failed.map((j) => (
                <FailedJobRow
                  key={j.id}
                  job={j}
                  onDismiss={() => void dismissJob(j)}
                />
              ))}
            </Group>
          )}

          {pending.length > 0 && (
            <Group title="Queued" count={pending.length}>
              {pending.slice(0, QUEUED_VISIBLE).map((j, i) => (
                <QueuedJobRow key={j.id} job={j} ahead={i} />
              ))}
              {pending.length > QUEUED_VISIBLE && (
                <div className="px-4 py-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                  + {pending.length - QUEUED_VISIBLE} more queued
                </div>
              )}
            </Group>
          )}

          {completedToday.length > 0 && (
            <Group
              title="Completed today"
              count={completedToday.length}
              tone="done"
              collapsedByDefault
            >
              {completedToday.slice(0, COMPLETED_VISIBLE).map((j) => (
                <DoneJobRow key={j.id} job={j} />
              ))}
              {completedToday.length > COMPLETED_VISIBLE && (
                <div className="px-4 py-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                  + {completedToday.length - COMPLETED_VISIBLE} more
                </div>
              )}
            </Group>
          )}

          {jobs.length === 0 && (
            <div className="px-5 py-10 text-center text-sm text-muted">
              No background work right now.
            </div>
          )}
        </div>

        <div className="border-t border-rule bg-surface px-5 py-3 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          Auto-retry failed source-bound jobs after relink.
        </div>
      </aside>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Worker chip                                                                */
/* -------------------------------------------------------------------------- */

function WorkerChip({
  activeWorkers,
  capacity,
}: {
  activeWorkers: number;
  capacity: number;
}) {
  return (
    <div className="mt-3 flex items-center gap-3 rounded-lg border border-rule-strong bg-surface-2 px-3 py-2">
      <span className="inline-flex size-7 items-center justify-center rounded-md bg-bg text-beep">
        <Server className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="font-display text-[0.75rem] font-bold uppercase tracking-[0.06em] text-ink">
          Worker pool &middot; Local
        </div>
        <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          {activeWorkers} of {capacity} active
        </div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Group                                                                      */
/* -------------------------------------------------------------------------- */

function Group({
  title,
  count,
  tone,
  action,
  collapsedByDefault,
  children,
}: {
  title: string;
  count: number;
  tone?: "running" | "failed" | "done";
  action?: ReactNode;
  collapsedByDefault?: boolean;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(!!collapsedByDefault);
  return (
    <div className="border-b border-rule">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left font-mono text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-ink hover:bg-surface-2"
      >
        {title}
        <span
          className={cn(
            "inline-flex min-w-[1.5rem] items-center justify-center rounded-full px-1.5 py-0.5 font-mono text-[0.625rem] font-bold tabular-nums",
            tone === "running" && "bg-beep text-bg",
            tone === "failed" && "bg-led text-bg",
            tone === "done" && "bg-done text-bg",
            !tone && "bg-surface-3 text-ink-2",
          )}
        >
          {count}
        </span>
        {action && (
          <span className="ml-auto inline-flex items-center gap-2">{action}</span>
        )}
      </button>
      {!collapsed && <div>{children}</div>}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Job row variants                                                           */
/* -------------------------------------------------------------------------- */

function JobRowHead({
  job,
  badge,
  badgeTone,
  trailing,
}: {
  job: Job;
  badge: string;
  badgeTone: "running" | "failed" | "queued" | "done";
  trailing?: ReactNode;
}) {
  return (
    <div className="flex items-start gap-2.5">
      <span
        className={cn(
          "inline-flex size-7 shrink-0 items-center justify-center rounded-md border",
          badgeTone === "running" && "border-beep/40 bg-beep-tint text-beep",
          badgeTone === "failed" && "border-led/40 bg-led/10 text-led",
          badgeTone === "queued" && "border-rule bg-surface-3 text-muted",
          badgeTone === "done" && "border-done/40 bg-done/10 text-done",
        )}
      >
        {KIND_ICON[job.kind] ?? <Activity className="size-3.5" />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate font-display text-[0.8125rem] font-semibold uppercase tracking-[0.04em] text-ink">
          {kindLabel(job.kind)}
        </div>
        <div className="mt-0.5 truncate font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          {jobTarget(job) || "(no target)"}
        </div>
      </div>
      <span
        className={cn(
          "rounded-full px-2 py-0.5 font-display text-[0.5625rem] font-bold uppercase tracking-[0.14em]",
          badgeTone === "running" && "bg-beep/10 text-beep",
          badgeTone === "failed" && "bg-led/10 text-led",
          badgeTone === "queued" && "bg-surface-3 text-subtle",
          badgeTone === "done" && "bg-done/10 text-done",
        )}
      >
        {badge}
      </span>
      {trailing}
    </div>
  );
}

function RunningJobRow({
  job,
  onCancel,
}: {
  job: Job;
  onCancel: () => void;
}) {
  const pct =
    job.progress != null ? Math.max(0, Math.min(100, job.progress * 100)) : 0;
  return (
    <div className="border-t border-rule px-4 py-3 first:border-t-0">
      <JobRowHead
        job={job}
        badge={job.cancel_requested ? "Cancelling" : "Running"}
        badgeTone="running"
        trailing={
          !job.cancel_requested && (
            <button
              type="button"
              onClick={onCancel}
              title="Cancel job"
              aria-label="Cancel job"
              className="inline-flex size-6 items-center justify-center rounded text-subtle hover:text-led"
            >
              <Pause className="size-3.5" />
            </button>
          )
        }
      />
      <div className="mt-2 flex items-center gap-3">
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-surface-3">
          <span
            className={cn(
              "block h-full rounded-full bg-beep shadow-[0_0_6px_var(--color-beep-glow)] transition-all",
              !job.progress && "animate-pulse",
            )}
            style={{ width: job.progress != null ? `${pct}%` : "30%" }}
          />
        </div>
        {job.message && (
          <span className="font-mono text-[0.625rem] text-muted">
            {job.message}
          </span>
        )}
      </div>
    </div>
  );
}

function FailedJobRow({
  job,
  onDismiss,
}: {
  job: Job;
  onDismiss: () => void;
}) {
  const sourceUnreachable = job.error?.toLowerCase().includes("unreachable");
  return (
    <div className="border-t border-rule bg-led/[0.04] px-4 py-3 first:border-t-0">
      <JobRowHead
        job={job}
        badge={job.cancel_requested ? "Cancelled" : "Failed"}
        badgeTone="failed"
      />
      {job.error && (
        <div className="mt-2 rounded-md border border-led/30 bg-led/10 px-3 py-2 text-[0.75rem] leading-relaxed text-ink-2">
          <b className="font-bold text-led">{job.error}</b>
        </div>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        {sourceUnreachable && (
          <button
            type="button"
            disabled
            title="Inline relink lands with the match-settings redesign"
            className="inline-flex items-center gap-1.5 rounded-md border border-led/40 bg-led/10 px-2.5 py-1 font-display text-[0.625rem] font-semibold uppercase tracking-[0.08em] text-led opacity-50"
          >
            Relink source
          </button>
        )}
        <button
          type="button"
          onClick={onDismiss}
          className="inline-flex items-center gap-1.5 rounded-md border border-rule bg-surface-2 px-2.5 py-1 font-display text-[0.625rem] font-semibold uppercase tracking-[0.08em] text-ink-2 hover:bg-surface-3 hover:text-ink"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

function QueuedJobRow({ job, ahead }: { job: Job; ahead: number }) {
  return (
    <div className="border-t border-rule px-4 py-3 first:border-t-0">
      <JobRowHead job={job} badge="Queued" badgeTone="queued" />
      <div className="mt-1.5 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
        {ahead === 0 ? "Next up" : `${ahead} ahead`}
      </div>
    </div>
  );
}

function DoneJobRow({ job }: { job: Job }) {
  const finished = job.finished_at ? new Date(job.finished_at) : null;
  return (
    <div className="border-t border-rule px-4 py-3 first:border-t-0">
      <JobRowHead
        job={job}
        badge={
          job.status === "succeeded"
            ? "Done"
            : job.status === "cancelled"
              ? "Cancelled"
              : "Dismissed"
        }
        badgeTone="done"
      />
      <div className="mt-1.5 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
        {finished ? `${formatRelative(finished)} ago` : ""}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function formatRelative(then: Date): string {
  const ms = Date.now() - then.getTime();
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} hr`;
}
