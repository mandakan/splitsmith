import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { ApiError, api, type Job, type MatchProject } from "@/lib/api";
import { cn } from "@/lib/utils";

export type PrereqKind = "trim" | "detect";

export interface PrereqGateProps {
  kind: PrereqKind;
  slug: string;
  stageNumber: number;
  /** Stage display info for the kicker. */
  stage: { stage_number: number; stage_name: string };
  /** Whether the prereq's prerequisites are themselves satisfied. When false
   *  the Run button is disabled and `blockedReason` is shown as a tooltip
   *  + footer hint. */
  blocked: boolean;
  blockedReason: string | null;
  /** True when the source clip is on disk (only used as a checklist signal). */
  hasSource: boolean;
  /** True when the project knows the stage time (scoreboard imported). */
  hasStageTime: boolean;
  /** True when the primary cam has a beep time (auto-picked or manual). */
  hasBeep: boolean;
  /** True when the audit clip has been trimmed. */
  hasTrim: boolean;
  /** Refresh the project after a trim completes. Wired only when kind === 'trim'. */
  onProjectUpdate?: (project: MatchProject) => void;
  /** Refresh the audit after detection completes. Wired only when kind === 'detect'. */
  onAuditRefresh?: () => Promise<void> | void;
}

interface ChecklistItem {
  label: string;
  done: boolean;
  sub: string;
}

/**
 * Blocking pre-audit state. When a stage's audit prerequisites aren't met
 * (the trim hasn't been built yet, or detection hasn't run on the trim),
 * the entire audit canvas is replaced by this centered card.
 *
 * The toolbar status badges that used to show TrimNowBadge / DetectShotsBadge
 * implied "you can audit while these are pending" -- but you can't, so the
 * design moves the affordance into a single blocking card. The card owns
 * its own job polling so it can adopt in-flight jobs on remount and show
 * the run state inline, mirroring the badge polling pattern.
 */
export function PrereqGate({
  kind,
  slug,
  stageNumber,
  stage,
  blocked,
  blockedReason,
  hasSource,
  hasStageTime,
  hasBeep,
  hasTrim,
  onProjectUpdate,
  onAuditRefresh,
}: PrereqGateProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = job != null && (job.status === "pending" || job.status === "running");

  // Adopt any in-flight job for this stage of this kind. Mirrors the
  // logic in TrimNowBadge / DetectShotsBadge -- if the page reloads
  // mid-job, we reattach rather than leaving the user a stale button
  // that would double-submit.
  useEffect(() => {
    let cancelled = false;
    setJob(null);
    setError(null);
    const jobKind = kind === "trim" ? "trim" : "shot_detect";
    api
      .listJobs()
      .then(async (jobs) => {
        if (cancelled) return;
        const active = jobs.find(
          (j) =>
            j.kind === jobKind &&
            j.stage_number === stageNumber &&
            (j.status === "pending" || j.status === "running"),
        );
        if (!active) return;
        setJob(active);
        try {
          const final = await api.pollJob(active.id, setJob);
          if (cancelled) return;
          if (final.status === "succeeded") {
            if (kind === "trim" && onProjectUpdate) {
              onProjectUpdate(await api.getProject(slug));
            } else if (kind === "detect" && onAuditRefresh) {
              await onAuditRefresh();
            }
          } else if (final.status === "failed") {
            setError(final.error ?? `${kind === "trim" ? "Trim" : "Detection"} failed`);
          }
        } finally {
          if (!cancelled) setJob(null);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [kind, slug, stageNumber, onProjectUpdate, onAuditRefresh]);

  const run = useCallback(async () => {
    setError(null);
    try {
      const initial =
        kind === "trim"
          ? await api.trimStage(slug, stageNumber)
          : await api.detectShots(slug, stageNumber, { reset: false });
      setJob(initial);
      const final = await api.pollJob(initial.id, setJob);
      if (final.status === "failed") {
        setError(final.error ?? `${kind === "trim" ? "Trim" : "Detection"} failed`);
        return;
      }
      if (kind === "trim" && onProjectUpdate) {
        onProjectUpdate(await api.getProject(slug));
      } else if (kind === "detect" && onAuditRefresh) {
        await onAuditRefresh();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setJob(null);
    }
  }, [kind, slug, stageNumber, onProjectUpdate, onAuditRefresh]);

  const pct = job?.progress != null ? Math.round(job.progress * 100) : null;

  const checklist: ChecklistItem[] =
    kind === "trim"
      ? [
          {
            label: "Source clip",
            done: hasSource,
            sub: hasSource ? "available" : "missing",
          },
          {
            label: "Beep position",
            done: hasBeep,
            sub: hasBeep ? "picked" : "not yet picked",
          },
          {
            label: "Stage time",
            done: hasStageTime,
            sub: hasStageTime ? "from scoreboard" : "not imported",
          },
        ]
      : [
          {
            label: "Trim cache",
            done: hasTrim,
            sub: hasTrim ? "built" : "not built yet",
          },
          {
            label: "Beep position",
            done: hasBeep,
            sub: hasBeep ? "picked" : "not yet picked",
          },
          {
            label: "Shot detector",
            done: false,
            sub: "not run yet",
          },
        ];

  const headline =
    kind === "trim" ? "Trim before audit" : "Detect shots before audit";
  const body =
    kind === "trim"
      ? "Audit needs a trimmed clip aligned to the buzzer. Run trim to crop the source down to this stage, then come back here."
      : "Audit reviews shots that detection has already proposed. Run detection on the trimmed clip to populate candidate shots, then come back here.";
  const runLabel = running
    ? kind === "trim"
      ? `Trimming${pct != null ? ` ${pct}%` : "..."}`
      : `Detecting${pct != null ? ` ${pct}%` : "..."}`
    : kind === "trim"
      ? "Run trim"
      : "Run detection";
  const stageLabel = `Stage ${String(stage.stage_number).padStart(2, "0")} · Prerequisites`;

  return (
    <div className="flex flex-1 items-center justify-center px-5 py-10">
      <div
        role="region"
        aria-label={headline}
        className="relative w-full max-w-[40rem] overflow-hidden rounded-3xl border border-live/30 bg-[linear-gradient(180deg,color-mix(in_srgb,var(--color-live)_4%,var(--color-surface))_0%,var(--color-surface)_100%)] p-8 pb-7 shadow-[inset_0_0_0_1px_var(--color-rule),0_24px_60px_-24px_rgba(0,0,0,0.7),0_0_32px_color-mix(in_srgb,var(--color-live)_14%,transparent)]"
      >
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-[3px] bg-live shadow-[0_0_14px_var(--color-live-glow)]"
        />

        <div className="flex items-start gap-5">
          <span
            aria-hidden
            className="inline-flex size-[52px] shrink-0 items-center justify-center rounded-full border border-live/40 bg-live-tint font-mono text-[1.375rem] font-extrabold text-live shadow-[0_0_20px_var(--color-live-glow)]"
          >
            !
          </span>
          <div className="min-w-0 flex-1">
            <div className="font-mono text-[0.6875rem] font-bold uppercase tracking-[0.14em] text-live">
              {stageLabel}
              {stage.stage_name ? (
                <span className="ml-1.5 text-muted">· {stage.stage_name}</span>
              ) : null}
            </div>
            <h2 className="mb-1.5 mt-1.5 font-display text-[1.625rem] font-bold uppercase leading-[1.05] tracking-[-0.01em] text-ink">
              {headline}
            </h2>
            <p className="m-0 max-w-[30rem] text-sm leading-[1.5] text-muted">
              {body}
            </p>
          </div>
        </div>

        <ul className="mb-5 mt-[1.375rem] flex list-none flex-col gap-1.5 p-0">
          {checklist.map((c, i) => (
            <li
              key={i}
              className={cn(
                "flex items-center gap-2.5 rounded-md border border-rule px-3 py-2.5",
                c.done
                  ? "bg-[color-mix(in_srgb,var(--color-done)_5%,var(--color-surface-2))]"
                  : "bg-surface-2",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "inline-flex size-[18px] shrink-0 items-center justify-center rounded-full text-bg",
                  c.done
                    ? "border-0 bg-done"
                    : "border-[1.5px] border-dashed border-rule-strong",
                )}
              >
                {c.done ? (
                  <svg
                    width={10}
                    height={10}
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="3.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                ) : null}
              </span>
              <span
                className={cn(
                  "flex-1 font-display text-[0.75rem] font-bold uppercase tracking-[0.04em]",
                  c.done ? "text-ink-2" : "text-ink",
                )}
              >
                {c.label}
              </span>
              <span className="font-mono text-[0.625rem] tabular-nums text-muted">
                {c.sub}
              </span>
            </li>
          ))}
        </ul>

        <div className="flex flex-wrap items-center gap-2.5">
          <button
            type="button"
            onClick={() => void run()}
            disabled={running || blocked}
            title={blockedReason ?? undefined}
            className={cn(
              "inline-flex items-center gap-2.5 rounded-md border-0 bg-led-fill px-[1.125rem] py-[0.6875rem] font-display text-[0.8125rem] font-bold uppercase tracking-[0.08em] text-ink shadow-[0_0_0_1px_var(--color-led),0_0_22px_var(--color-led-glow)]",
              running ? "cursor-wait opacity-85" : "cursor-pointer",
              blocked && "cursor-not-allowed opacity-50",
            )}
          >
            {running ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : null}
            <span className="tabular-nums">{runLabel}</span>
          </button>
          {error ? (
            <span className="text-xs text-destructive">{error}</span>
          ) : null}
          <span className="ml-auto font-mono text-[0.625rem] uppercase tracking-[0.1em] text-muted">
            {blocked
              ? (blockedReason ?? "Audit unlocks when prerequisites pass")
              : "Audit unlocks when prerequisites pass"}
          </span>
        </div>
      </div>
    </div>
  );
}
