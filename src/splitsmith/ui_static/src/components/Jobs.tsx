/**
 * Jobs surface (v2 audit chrome).
 *
 * Replaces the legacy floating JobsPanel FAB. Jobs now live in the
 * sidebar footer (JobsRail) and open into an anchored drawer (JobsSheet)
 * so background work reads as global activity next to the nav rather
 * than overlapping page content. See the design kit's Chrome.jsx.
 *
 *   JobsRail   sidebar-footer surface. Collapsed sidebar -> 40px icon
 *              with a pulsing dot when work is running. Expanded ->
 *              header row + the two most-urgent jobs + a "N queued"
 *              tail.
 *   JobsSheet  the full drawer, anchored to the rail. Three groups:
 *              needs attention / running / queued.
 *
 * Job state mapping (api.ts Job.status -> kit state):
 *   running              -> 'running'
 *   pending              -> 'queued'
 *   failed !acknowledged -> 'attention'
 *   (other statuses are surfaced in the sheet as "Completed" rows.)
 */

import {
  Activity,
  AlertTriangle,
  ArrowDownToLine,
  ChevronRight,
  CloudDownload,
  Crosshair,
  Pause,
  Volume2,
  X,
  Zap,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "react-router-dom";

import { Portal } from "@/components/ui/Portal";
import { ApiError, api, type Job } from "@/lib/api";
import { useDialogFocus } from "@/lib/dialogFocus";
import { cn } from "@/lib/utils";

const ACTIVE_POLL_MS = 1000;
const IDLE_POLL_MS = 5000;

const KIND_LABEL: Record<string, string> = {
  detect_beep: "Detect beep",
  trim: "Trim stage video",
  shot_detect: "Detect shots",
  export: "Export stage",
  match_export: "Match export",
  audio_extract: "Audio extract",
  model_download: "Download models",
};

const KIND_ICON: Record<string, ReactNode> = {
  detect_beep: <Volume2 className="size-3.5" />,
  trim: <Crosshair className="size-3.5" />,
  shot_detect: <Activity className="size-3.5" />,
  export: <ArrowDownToLine className="size-3.5" />,
  match_export: <ArrowDownToLine className="size-3.5" />,
  audio_extract: <Volume2 className="size-3.5" />,
  model_download: <CloudDownload className="size-3.5" />,
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

export interface JobsState {
  jobs: Job[];
  running: Job[];
  pending: Job[];
  failed: Job[];
  error: string | null;
  refresh: () => Promise<void>;
  acknowledge: (job: Job) => Promise<void>;
  acknowledgeAll: () => Promise<void>;
  cancel: (job: Job) => Promise<void>;
}

export function useJobs(): JobsState {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
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
    void refresh();
    return () => {
      abortRef.current?.abort();
    };
  }, [refresh]);

  const anyActive = jobs.some(isActive);
  useEffect(() => {
    const ms = anyActive ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    const id = window.setInterval(() => void refresh(), ms);
    return () => window.clearInterval(id);
  }, [anyActive, refresh]);

  const acknowledge = useCallback(async (job: Job) => {
    try {
      const updated = await api.acknowledgeJob(job.id);
      setJobs((prev) => prev.map((j) => (j.id === job.id ? updated : j)));
    } catch {
      /* swallow */
    }
  }, []);

  const acknowledgeAll = useCallback(async () => {
    try {
      await api.acknowledgeAllFailures();
      void refresh();
    } catch {
      /* swallow */
    }
  }, [refresh]);

  const cancel = useCallback(async (job: Job) => {
    try {
      const updated = await api.cancelJob(job.id);
      setJobs((prev) => prev.map((j) => (j.id === job.id ? updated : j)));
    } catch {
      /* swallow */
    }
  }, []);

  const running = jobs.filter((j) => j.status === "running");
  const pending = jobs.filter((j) => j.status === "pending");
  const failed = jobs.filter((j) => j.status === "failed" && !j.acknowledged);

  return {
    jobs,
    running,
    pending,
    failed,
    error,
    refresh,
    acknowledge,
    acknowledgeAll,
    cancel,
  };
}

/* -------------------------------------------------------------------------- */
/* JobsRail -- sidebar footer surface                                         */
/* -------------------------------------------------------------------------- */

export interface JobsRailProps {
  state: JobsState;
  collapsed: boolean;
  open: boolean;
  onToggle: () => void;
}

export function JobsRail({ state, collapsed, open, onToggle }: JobsRailProps) {
  const { running, pending, failed } = state;
  const attn = failed;

  if (collapsed) {
    const hasActivity = running.length > 0 || attn.length > 0;
    return (
      <div className="border-t border-rule p-2">
        <button
          type="button"
          onClick={onToggle}
          aria-label={`Jobs: ${running.length} running, ${pending.length} queued${
            attn.length ? `, ${attn.length} need attention` : ""
          }`}
          aria-expanded={open}
          title={`${running.length}R ${pending.length}Q${
            attn.length ? ` ${attn.length}!` : ""
          }`}
          className="relative mx-auto flex size-10 items-center justify-center rounded-lg border border-rule bg-surface-2 text-ink-2 transition-colors hover:bg-surface-3"
        >
          <Zap className="size-3.5" aria-hidden />
          {hasActivity ? (
            <span
              aria-hidden
              className={cn(
                "absolute right-1 top-1 size-2 animate-pulse rounded-full",
                attn.length
                  ? "bg-live shadow-[0_0_8px_var(--color-live-glow)]"
                  : "bg-led shadow-[0_0_8px_var(--color-led-glow)]",
              )}
            />
          ) : null}
        </button>
      </div>
    );
  }

  const urgent = [...attn, ...running].slice(0, 2);

  return (
    <div className="border-t border-rule bg-surface px-2.5 pb-2 pt-2.5">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-1 pb-2 pt-1.5 text-left text-ink"
      >
        <span
          aria-hidden
          className={cn(
            "inline-flex size-[18px] items-center justify-center rounded border",
            attn.length
              ? "border-live/50 bg-led-tint text-live"
              : running.length
                ? "border-rule bg-led-tint text-led"
                : "border-rule bg-surface-2 text-muted",
          )}
        >
          <Zap className="size-3.5" />
        </span>
        <span className="flex flex-1 items-baseline gap-2 font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em]">
          Jobs
          <span className="font-mono text-[0.5625rem] font-bold tabular-nums tracking-[0.04em] text-subtle">
            {running.length}·R {pending.length}·Q
            {attn.length ? ` ${attn.length}·!` : ""}
          </span>
        </span>
        <ChevronRight
          aria-hidden
          className={cn(
            "size-3 text-muted transition-transform duration-150",
            open ? "rotate-90" : "",
          )}
        />
      </button>
      <div className="flex flex-col gap-1">
        {urgent.map((job) => (
          <JobMini key={job.id} job={job} />
        ))}
        {pending.length > 0 ? (
          <div className="flex items-center gap-1.5 px-1.5 py-1 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.08em] text-subtle">
            <span
              aria-hidden
              className="inline-block size-[5px] rounded-full bg-rule-strong"
            />
            {pending.length} queued
          </div>
        ) : null}
        {urgent.length === 0 && pending.length === 0 ? (
          <div className="px-1.5 py-1 font-mono text-[0.5625rem] uppercase tracking-[0.06em] text-whisper">
            Idle
          </div>
        ) : null}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* JobMini -- single compact job row                                          */
/* -------------------------------------------------------------------------- */

function JobMini({ job }: { job: Job }) {
  const isAttn = job.status === "failed";
  const pct =
    job.progress != null ? Math.max(0, Math.min(100, job.progress * 100)) : 0;
  const sub = jobTarget(job) || job.message || "";
  return (
    <div
      className={cn(
        "rounded px-1.5 py-1.5",
        "bg-surface-2",
        isAttn ? "border border-live/35" : "border border-rule",
      )}
    >
      <div className="mb-1 flex items-center gap-1.5">
        <span
          aria-hidden
          className={cn(
            "inline-block size-[5px] rounded-full",
            isAttn
              ? "bg-live shadow-[0_0_6px_var(--color-live-glow)]"
              : "animate-pulse bg-led shadow-[0_0_6px_var(--color-led-glow)]",
          )}
        />
        <span className="flex-1 truncate font-display text-[0.625rem] font-bold uppercase tracking-[0.06em] text-ink">
          {kindLabel(job.kind)}
        </span>
        {job.status === "running" ? (
          <span
            className={cn(
              "font-mono text-[0.5625rem] font-bold tabular-nums",
              isAttn ? "text-live" : "text-led",
            )}
          >
            {Math.round(pct)}%
          </span>
        ) : null}
      </div>
      <div className="truncate font-mono text-[0.5625rem] tracking-[0.04em] text-subtle">
        {sub}
        {job.error ? ` · ${job.error}` : ""}
      </div>
      {job.status === "running" ? (
        <div className="mt-1.5 h-[2px] overflow-hidden rounded-[1px] bg-surface-3">
          <span
            className={cn(
              "block h-full transition-all",
              isAttn
                ? "bg-live shadow-[0_0_4px_var(--color-live-glow)]"
                : "bg-led shadow-[0_0_4px_var(--color-led-glow)]",
              !job.progress && "animate-pulse",
            )}
            style={{ width: job.progress != null ? `${pct}%` : "30%" }}
          />
        </div>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* JobsSheet -- the full drawer                                               */
/* -------------------------------------------------------------------------- */

export interface JobsSheetProps {
  state: JobsState;
  onClose: () => void;
  /** Horizontal offset from the left viewport edge -- the host pins the
   *  sheet just outside the sidebar rail. Ignored when ``mobile``. */
  leftOffset: number;
  /** Full-width mobile position (left-4 right-4 bottom-4) instead of the
   *  sidebar-anchored offset. */
  mobile?: boolean;
}

export function JobsSheet({ state, onClose, leftOffset, mobile = false }: JobsSheetProps) {
  const { running, pending, failed, error, cancel, acknowledge, acknowledgeAll } =
    state;
  const panelRef = useRef<HTMLDivElement | null>(null);
  // Non-modal popover contract: Escape closes, focus enters on open and
  // restores to the jobs rail on close; no Tab trap -- the page behind
  // stays interactive by design.
  useDialogFocus(true, panelRef, onClose, { trap: false });
  const groups: Array<{
    id: "attn" | "running" | "queued";
    label: string;
    tone: "live" | "led" | "muted";
    items: Job[];
  }> = [
    {
      id: "attn",
      label: "Needs attention",
      tone: "live",
      items: failed,
    },
    { id: "running", label: "Running", tone: "led", items: running },
    { id: "queued", label: "Queued", tone: "muted", items: pending },
  ];

  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-label="Jobs"
      className={cn(
        "fixed bottom-4 z-drawer flex max-h-[480px] flex-col overflow-hidden rounded-2xl border border-rule-strong bg-surface shadow-[0_24px_60px_-16px_rgba(0,0,0,0.75)]",
        mobile ? "left-4 right-4" : "w-[360px]",
      )}
      style={mobile ? undefined : { left: leftOffset }}
    >
      <div className="flex items-center gap-2 border-b border-rule bg-surface-2 px-3.5 py-2.5">
        <Zap className="size-3.5 text-led" aria-hidden />
        <span className="flex-1 font-display text-[0.75rem] font-bold uppercase tracking-[0.08em] text-ink">
          Jobs
        </span>
        {failed.length > 1 ? (
          <button
            type="button"
            onClick={() => void acknowledgeAll()}
            className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.08em] text-led hover:text-led-soft"
          >
            Dismiss all
          </button>
        ) : null}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close jobs panel"
          className="inline-flex size-[22px] items-center justify-center rounded text-muted hover:text-ink"
        >
          <X className="size-3.5" />
        </button>
      </div>

      <div className="flex flex-1 flex-col gap-3.5 overflow-y-auto p-3">
        {error ? (
          <div className="rounded-md border border-led/40 bg-led/10 px-3 py-2 text-[0.75rem] text-led">
            {error}
          </div>
        ) : null}
        {groups
          .filter((g) => g.items.length > 0)
          .map((g) => (
            <div key={g.id} className="flex flex-col gap-1.5">
              <div
                className={cn(
                  "flex items-center gap-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.12em]",
                  g.tone === "live" && "text-live",
                  g.tone === "led" && "text-led",
                  g.tone === "muted" && "text-muted",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "inline-block size-[5px] rounded-full",
                    g.tone === "live" &&
                      "bg-live shadow-[0_0_6px_var(--color-live-glow)]",
                    g.tone === "led" &&
                      "bg-led shadow-[0_0_6px_var(--color-led-glow)]",
                    g.tone === "muted" && "bg-muted",
                  )}
                />
                {g.label} · {g.items.length}
              </div>
              {g.items.map((job) =>
                g.id === "running" ? (
                  <RunningRow
                    key={job.id}
                    job={job}
                    onCancel={() => void cancel(job)}
                  />
                ) : g.id === "attn" ? (
                  <FailedRow
                    key={job.id}
                    job={job}
                    onDismiss={() => void acknowledge(job)}
                  />
                ) : (
                  <QueuedRow key={job.id} job={job} />
                ),
              )}
            </div>
          ))}
        {groups.every((g) => g.items.length === 0) ? (
          <div className="py-4 text-center font-mono text-[0.6875rem] tracking-[0.04em] text-subtle">
            No active jobs.
          </div>
        ) : null}
      </div>
    </div>
  );
}

function RowHead({
  job,
  badge,
  badgeTone,
  trailing,
}: {
  job: Job;
  badge: string;
  badgeTone: "running" | "failed" | "queued";
  trailing?: ReactNode;
}) {
  return (
    <div className="flex items-start gap-2.5">
      <span
        className={cn(
          "inline-flex size-7 shrink-0 items-center justify-center rounded-md border",
          badgeTone === "running" && "border-led/40 bg-led-tint text-led",
          badgeTone === "failed" && "border-live/40 bg-live/10 text-live",
          badgeTone === "queued" && "border-rule bg-surface-3 text-muted",
        )}
      >
        {KIND_ICON[job.kind] ?? <Activity className="size-3.5" />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate font-display text-[0.75rem] font-bold uppercase tracking-[0.04em] text-ink">
          {kindLabel(job.kind)}
        </div>
        <div className="mt-0.5 truncate font-mono text-[0.5625rem] uppercase tracking-[0.06em] text-muted">
          {jobTarget(job) || "(no target)"}
        </div>
      </div>
      <span
        className={cn(
          "rounded-full px-2 py-0.5 font-display text-[0.5625rem] font-bold uppercase tracking-[0.12em]",
          badgeTone === "running" && "bg-led/10 text-led",
          badgeTone === "failed" && "bg-live/10 text-live",
          badgeTone === "queued" && "bg-surface-3 text-subtle",
        )}
      >
        {badge}
      </span>
      {trailing}
    </div>
  );
}

function RunningRow({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const pct =
    job.progress != null ? Math.max(0, Math.min(100, job.progress * 100)) : 0;
  return (
    <div className="rounded-md border border-rule bg-surface-2 px-3 py-2.5">
      <RowHead
        job={job}
        badge={job.cancel_requested ? "Cancelling" : "Running"}
        badgeTone="running"
        trailing={
          !job.cancel_requested ? (
            <button
              type="button"
              onClick={onCancel}
              title="Cancel job"
              aria-label="Cancel job"
              className="inline-flex size-6 items-center justify-center rounded text-subtle hover:text-led"
            >
              <Pause className="size-3.5" />
            </button>
          ) : null
        }
      />
      <div className="mt-2 flex items-center gap-2.5">
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-surface-3">
          <span
            className={cn(
              "block h-full rounded-full bg-led shadow-[0_0_6px_var(--color-led-glow)] transition-all",
              !job.progress && "animate-pulse",
            )}
            style={{ width: job.progress != null ? `${pct}%` : "30%" }}
          />
        </div>
        {job.message ? (
          <span className="font-mono text-[0.5625rem] text-muted">
            {job.message}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function FailedRow({ job, onDismiss }: { job: Job; onDismiss: () => void }) {
  return (
    <div className="rounded-md border border-live/35 bg-live/[0.06] px-3 py-2.5">
      <RowHead
        job={job}
        badge={job.cancel_requested ? "Cancelled" : "Failed"}
        badgeTone="failed"
      />
      {job.error ? (
        <div className="mt-2 rounded border border-live/30 bg-live/10 px-2.5 py-1.5 font-mono text-[0.6875rem] leading-relaxed text-ink-2">
          <b className="font-bold text-live">{job.error}</b>
        </div>
      ) : null}
      <div className="mt-2">
        <button
          type="button"
          onClick={onDismiss}
          className="inline-flex items-center gap-1.5 rounded-md border border-rule bg-surface-2 px-2.5 py-1 font-display text-[0.5625rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:bg-surface-3 hover:text-ink"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

function QueuedRow({ job }: { job: Job }) {
  return (
    <div className="rounded-md border border-rule bg-surface-2 px-3 py-2.5">
      <RowHead job={job} badge="Queued" badgeTone="queued" />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* helpers                                                                    */
/* -------------------------------------------------------------------------- */

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

/* -------------------------------------------------------------------------- */
/* JobsSurface -- convenience component combining rail + sheet                */
/* -------------------------------------------------------------------------- */

export interface JobsSurfaceProps {
  collapsed?: boolean;
  /** Width of the sidebar when expanded -- the sheet anchors just past it. */
  sidebarExpandedWidth?: number;
  /** Width of the sidebar when collapsed. */
  sidebarCollapsedWidth?: number;
  /** Mobile drawer mode: the trigger renders as a full-width drawer row
   *  and the sheet takes the full-width position (no sidebar offset). */
  mobile?: boolean;
}

/** Combined rail + sheet with internal open state. Drop this at the
 *  bottom of any sidebar; the sheet anchors itself just past the rail.
 *  In ``mobile`` mode it drops into the nav drawer footer instead. */
export function JobsSurface({
  collapsed = false,
  sidebarExpandedWidth = 0,
  sidebarCollapsedWidth = 0,
  mobile = false,
}: JobsSurfaceProps) {
  const state = useJobs();
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((v) => !v), []);
  const { pathname } = useLocation();

  // Escape / focus handling lives in JobsSheet (useDialogFocus).

  // Navigating dismisses the sheet. JobsSurface lives in the persistent
  // shell sidebar, so without this the sheet survives route changes and
  // hangs over whatever page loads next (keyboard nav and header links
  // never touch the click-outside overlay).
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Click-outside dismiss: a thin transparent overlay underneath the sheet.
  const offset = useMemo(
    () => (collapsed ? sidebarCollapsedWidth : sidebarExpandedWidth) + 8,
    [collapsed, sidebarCollapsedWidth, sidebarExpandedWidth],
  );

  return (
    <>
      {mobile ? (
        <MobileJobsRow state={state} open={open} onToggle={toggle} />
      ) : (
        <JobsRail state={state} collapsed={collapsed} open={open} onToggle={toggle} />
      )}
      {open ? (
        <Portal>
          {/* z-drawer minus epsilon isn't a thing -- the backdrop shares
              the drawer layer and relies on DOM order to sit beneath the
              sheet. Both portal to body so no sidebar stacking context
              can trap them (the sheet-under-everything bug). */}
          <div
            aria-hidden
            className="fixed inset-0 z-drawer"
            onClick={() => setOpen(false)}
          />
          <JobsSheet
            state={state}
            onClose={() => setOpen(false)}
            leftOffset={offset}
            mobile={mobile}
          />
        </Portal>
      ) : null}
    </>
  );
}

/** Full-width Jobs trigger row for the mobile nav drawer footer. Same
 *  status readout idiom as the expanded JobsRail header. */
function MobileJobsRow({
  state,
  open,
  onToggle,
}: {
  state: JobsState;
  open: boolean;
  onToggle: () => void;
}) {
  const { running, pending, failed } = state;
  const attn = failed;
  const hasActivity = running.length > 0 || attn.length > 0;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      aria-label={`Jobs: ${running.length} running, ${pending.length} queued${
        attn.length ? `, ${attn.length} need attention` : ""
      }`}
      className="flex min-h-11 w-full items-center gap-3 rounded-md px-3 text-left font-display text-sm font-bold uppercase tracking-wide text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
    >
      <Zap className="size-[15px] shrink-0" aria-hidden />
      <span className="flex-1">Jobs</span>
      <span
        aria-hidden
        className="font-mono text-[0.5625rem] font-bold tabular-nums tracking-[0.04em] text-subtle"
      >
        {running.length}·R {pending.length}·Q
        {attn.length ? ` ${attn.length}·!` : ""}
      </span>
      {hasActivity ? (
        <span
          aria-hidden
          className={cn(
            "inline-block size-2 shrink-0 animate-pulse rounded-full",
            attn.length
              ? "bg-live shadow-[0_0_8px_var(--color-live-glow)]"
              : "bg-led shadow-[0_0_8px_var(--color-led-glow)]",
          )}
        />
      ) : null}
    </button>
  );
}

/** Small inline summary chip showing failure / running count, suitable
 *  for headers that want to surface job activity without the full rail.
 *  Click jumps to whatever surface owns the rail. Kept as a separate
 *  export so future header variants don't have to rebuild the badge. */
export function JobsBadge({ state }: { state: JobsState }) {
  const { running, pending, failed } = state;
  if (running.length === 0 && pending.length === 0 && failed.length === 0) {
    return null;
  }
  const dominant: "attn" | "running" | "queued" =
    failed.length > 0 ? "attn" : running.length > 0 ? "running" : "queued";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.08em]",
        dominant === "attn" && "border-live/40 bg-live/10 text-live",
        dominant === "running" && "border-led/40 bg-led-tint text-led",
        dominant === "queued" && "border-rule bg-surface-2 text-subtle",
      )}
    >
      {dominant === "attn" ? (
        <AlertTriangle className="size-3" />
      ) : (
        <Activity className="size-3" />
      )}
      {failed.length || running.length || pending.length}
    </span>
  );
}
