/**
 * Jobs page - mobile-first full listing of background jobs (Task 6, #631).
 *
 * The sidebar JobsRail/JobsSheet (components/Jobs.tsx) stays the desktop
 * at-a-glance surface; this page is the dedicated route for reviewing and
 * acting on the full jobs list (needs attention / active / recent),
 * including retry on failed jobs and phase timings on finished ones.
 * Route registration is Task 7's job - this component is standalone and
 * reads its data from MatchShellOutletContext.jobsState.
 */

import { useOutletContext } from "react-router-dom";

import { Kicker } from "@/components/ui";
import { KIND_ICON, KIND_LABEL, jobTarget } from "@/components/Jobs";
import type { Job, JobTimings } from "@/lib/api";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

function TimingsList({ timings }: { timings: JobTimings }) {
  return (
    <ul className="mt-2 space-y-1 text-xs text-muted">
      {timings.phases.map((p) => (
        <li key={p.name} className="flex justify-between gap-4">
          <span>{p.name}</span>
          <span className="tabular-nums">{fmtMs(p.ms)}</span>
        </li>
      ))}
      <li className="flex justify-between gap-4 border-t border-rule pt-1 text-ink-2">
        <span>total</span>
        <span className="tabular-nums">{fmtMs(timings.total_ms)}</span>
      </li>
    </ul>
  );
}

function JobCard({
  job,
  onCancel,
  onRetry,
  onDismiss,
}: {
  job: Job;
  onCancel?: (job: Job) => void;
  onRetry?: (job: Job) => void;
  onDismiss?: (job: Job) => void;
}) {
  const active = job.status === "running" || job.status === "pending";
  const failed = job.status === "failed";
  return (
    <li className="rounded-md border border-rule bg-surface-2 p-3">
      <div className="flex items-center gap-2">
        <span aria-hidden>{KIND_ICON[job.kind] ?? null}</span>
        <span className="font-medium text-ink">{KIND_LABEL[job.kind] ?? job.kind}</span>
        <span className="text-xs text-muted">
          {[job.shooter_slug, jobTarget(job)].filter(Boolean).join(" · ") ||
            "(no target)"}
        </span>
        <span className="ml-auto flex items-center gap-1.5 text-xs text-ink-2">
          <span
            className={
              failed
                ? "inline-block size-[5px] rounded-full bg-live shadow-[0_0_6px_var(--color-live-glow)]"
                : active
                  ? "inline-block size-[5px] rounded-full bg-led shadow-[0_0_6px_var(--color-led-glow)]"
                  : "inline-block size-[5px] rounded-full bg-surface-3"
            }
            aria-hidden
          />
          {job.status}
        </span>
      </div>
      {job.status === "running" && typeof job.progress === "number" && (
        <div
          className="mt-2 h-1 overflow-hidden rounded bg-surface-3"
          role="progressbar"
          aria-label={`${KIND_LABEL[job.kind] ?? job.kind} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(job.progress * 100)}
        >
          <div
            className="h-full bg-led motion-safe:transition-[width]"
            style={{ width: `${Math.round(job.progress * 100)}%` }}
          />
        </div>
      )}
      {job.message && <p className="mt-1 text-xs text-muted">{job.message}</p>}
      {failed && job.error && <p className="mt-1 text-xs text-live">{job.error}</p>}
      {(onCancel || onRetry || onDismiss) && (
        <div className="mt-2 flex gap-2">
          {onCancel && active && (
            <button
              type="button"
              className="min-h-11 rounded-md border border-rule px-3 text-sm text-ink-2"
              onClick={() => onCancel(job)}
              disabled={job.cancel_requested}
            >
              {job.cancel_requested ? "Cancelling..." : "Cancel"}
            </button>
          )}
          {onRetry && failed && (
            <button
              type="button"
              className="min-h-11 rounded-md border border-rule px-3 text-sm text-ink"
              onClick={() => onRetry(job)}
            >
              Retry
            </button>
          )}
          {onDismiss && failed && (
            <button
              type="button"
              className="min-h-11 rounded-md px-3 text-sm text-muted"
              onClick={() => onDismiss(job)}
            >
              Dismiss
            </button>
          )}
        </div>
      )}
      {!active && job.timings && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-muted">Phase timings</summary>
          <TimingsList timings={job.timings} />
        </details>
      )}
    </li>
  );
}

export function Jobs() {
  const ctx = useOutletContext<MatchShellOutletContext | undefined>();
  const state = ctx?.jobsState;
  if (!state) return null;

  const active = [...state.running, ...state.pending];
  const attention = state.failed;
  const recent = state.jobs
    .filter((j) => !active.includes(j) && !attention.includes(j))
    // state.jobs is submission order (oldest first) - sort newest-updated
    // first before slicing so "Recent" shows the latest finished jobs.
    .slice()
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 20);
  const quiet = active.length === 0 && attention.length === 0;

  return (
    <div className="mx-auto max-w-2xl px-4 py-6">
      <Kicker className="mb-2">Background jobs</Kicker>
      <h1 className="mb-2 font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
        Jobs
      </h1>
      {quiet && (
        <p className="mt-4 rounded-md border border-rule bg-surface-2 p-4 text-sm text-muted">
          All quiet - nothing pending.
        </p>
      )}
      {attention.length > 0 && (
        <section className="mt-4" aria-label="Needs attention">
          <h2 className="text-sm font-medium text-ink-2">Needs attention</h2>
          <ul className="mt-2 space-y-2">
            {attention.map((j) => (
              <JobCard key={j.id} job={j} onRetry={state.retry} onDismiss={state.acknowledge} />
            ))}
          </ul>
        </section>
      )}
      {active.length > 0 && (
        <section className="mt-4" aria-label="Active">
          <h2 className="text-sm font-medium text-ink-2">Active</h2>
          <ul className="mt-2 space-y-2">
            {active.map((j) => (
              <JobCard key={j.id} job={j} onCancel={state.cancel} />
            ))}
          </ul>
        </section>
      )}
      {recent.length > 0 && (
        <section className="mt-4" aria-label="Recent">
          <h2 className="text-sm font-medium text-ink-2">Recent</h2>
          <ul className="mt-2 space-y-2">
            {recent.map((j) => (
              <JobCard key={j.id} job={j} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
