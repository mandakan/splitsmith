import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";

import { StageTimeSection } from "@/components/StageTimeSection";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import {
  ApiError,
  api,
  type Job,
  type MatchProject,
  type StageEntry,
  type StageVideo,
} from "@/lib/api";
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
  /** Detector confidence on the primary cam's beep, or null for manual
   *  picks / unknown. Combined with ``beepDiagnostic`` to flag the
   *  "beep exists but is probably wrong" case so the checklist tells
   *  the same story as the toolbar's BeepStatusChip. */
  beepConfidence?: number | null;
  /** Reason the beep looks suspect (e.g. "first shot lands 5.10s after
   *  the beep..."). Same value the chip's tooltip uses. */
  beepDiagnostic?: string | null;
  /** Confidence threshold below which a beep is "likely wrong". Mirrors
   *  the chip's hardcoded 0.85 default; pass the project's automation
   *  override to keep them in sync. */
  beepLowConfThreshold?: number;
  /** Whether the operator has reviewed and confirmed the primary beep
   *  (``primary.beep_reviewed``) -- the single source of truth for
   *  "confirmed", same field the backend's beep-queue status checks
   *  before confidence. When true, the beep row reads "confirmed" and
   *  neither low confidence nor the heuristic diagnostic can flip it back
   *  to "likely wrong". */
  beepReviewed?: boolean;
  /** Opens the page's step 1 (the beep picker) in place; the beep row
   *  is a button when this is set. */
  onRepickBeep?: () => void;
  /** True when the audit clip has been trimmed. */
  hasTrim: boolean;
  /** Full stage entry + primary video, needed to mount the manual
   *  stage-time editor inside the trim gate. Without them a manual
   *  match dead-ends here: the checklist says the time is missing but
   *  offers no way to enter one (scoreboard import is not required). */
  stageEntry?: StageEntry;
  primaryVideo?: StageVideo;
  /** Refresh the project after a trim completes. Wired only when kind === 'trim'. */
  onProjectUpdate?: (project: MatchProject) => void;
  /** Refresh the audit after detection completes. Wired only when kind === 'detect'. */
  onAuditRefresh?: () => Promise<void> | void;
}

interface ChecklistItem {
  label: string;
  done: boolean;
  /** Visual tone. "done" = green, "warn" = amber (e.g. low-confidence
   *  beep), "todo" = neutral dashed circle. */
  tone?: "done" | "warn" | "todo";
  sub: string;
  /** Optional click handler. When set, the entire row reads as a
   *  pointer affordance and dispatches on click (the beep row opens
   *  step 1). */
  onClick?: () => void;
}

/**
 * Blocking pre-audit state. When a stage's audit prerequisites aren't met
 * (the trim hasn't been built yet, or detection hasn't run on the trim),
 * the entire audit canvas is replaced by this centered card.
 *
 * The card has three visual states, each tied to ``jobStatus``:
 *
 *   - **running** -- neutral chrome (--rule-strong border, --surface bg,
 *     ink-toned spinner). Running is *not* a warning, so the amber that
 *     screams "something is wrong with the trim" is dropped.
 *   - **blocked** -- the canonical amber treatment. Honest warning: the
 *     operator needs to fix a prereq before audit unlocks.
 *   - **failed** -- amber, same shape as blocked. Escalating to a
 *     destructive red is a follow-up; today the difference is the inline
 *     error string.
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
  beepConfidence = null,
  beepDiagnostic = null,
  beepLowConfThreshold = 0.85,
  beepReviewed = false,
  onRepickBeep,
  hasTrim,
  stageEntry,
  primaryVideo,
  onProjectUpdate,
  onAuditRefresh,
}: PrereqGateProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = job != null && (job.status === "pending" || job.status === "running");

  // Identifies the kind+stage tuple this PrereqGate instance is for.
  // Every pollJob progress callback is gated against this ref so a
  // stage switch can't let the prior stage's in-flight job leak
  // progress updates into the new stage's chrome. (Repro: trigger
  // re-beep -> trim on stage X, navigate to stage Y while trim is
  // still running, the Y gate would otherwise show "Detecting 30%"
  // because the X polljob's setJob callback keeps firing.)
  const ownerKeyRef = useRef<string>(`${kind}:${stageNumber}`);
  ownerKeyRef.current = `${kind}:${stageNumber}`;

  // Adopt any in-flight job for this stage of this kind. Mirrors the
  // logic in TrimNowBadge / DetectShotsBadge -- if the page reloads
  // mid-job, we reattach rather than leaving the user a stale button
  // that would double-submit.
  useEffect(() => {
    let cancelled = false;
    const ownerKey = `${kind}:${stageNumber}`;
    setJob(null);
    setError(null);
    const jobKind = kind === "trim" ? "trim" : "shot_detect";
    // Guarded setter -- only forwards to React state when the effect
    // is still active AND the gate still belongs to the same
    // kind+stage tuple. Cancels updates from prior in-flight jobs
    // when the operator navigates to a different stage mid-run.
    const guardedSetJob = (j: Job) => {
      if (cancelled || ownerKeyRef.current !== ownerKey) return;
      setJob(j);
    };
    api
      .listJobs()
      .then(async (jobs) => {
        if (cancelled) return;
        const active = jobs.find(
          (j) =>
            j.kind === jobKind &&
            j.shooter_slug === slug &&
            j.stage_number === stageNumber &&
            (j.status === "pending" || j.status === "running"),
        );
        if (!active) return;
        guardedSetJob(active);
        try {
          const final = await api.pollJob(active.id, guardedSetJob);
          if (cancelled || ownerKeyRef.current !== ownerKey) return;
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
          if (!cancelled && ownerKeyRef.current === ownerKey) setJob(null);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      // Clear any local job state so a stage switch never leaves a
      // stale "running" frame painted on the new stage's gate while
      // the next effect's listJobs() is in flight.
      setJob(null);
      setError(null);
    };
  }, [kind, slug, stageNumber, onProjectUpdate, onAuditRefresh]);

  const run = useCallback(async () => {
    const ownerKey = `${kind}:${stageNumber}`;
    setError(null);
    const guardedSetJob = (j: Job) => {
      if (ownerKeyRef.current !== ownerKey) return;
      setJob(j);
    };
    try {
      const initial =
        kind === "trim"
          ? await api.trimStage(slug, stageNumber)
          : await api.detectShots(slug, stageNumber, { reset: false });
      guardedSetJob(initial);
      const final = await api.pollJob(initial.id, guardedSetJob);
      if (ownerKeyRef.current !== ownerKey) return;
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
      if (ownerKeyRef.current === ownerKey) {
        setError(err instanceof ApiError ? err.detail : String(err));
      }
    } finally {
      if (ownerKeyRef.current === ownerKey) setJob(null);
    }
  }, [kind, slug, stageNumber, onProjectUpdate, onAuditRefresh]);

  const pct = job?.progress != null ? Math.round(job.progress * 100) : null;

  // Match BeepStatusChip's reading: a beep that exists but has low
  // confidence or carries a post-audit diagnostic is "likely wrong",
  // not "picked". Same threshold + same trigger so the chip and the
  // checklist never disagree about the same beep. When the beep needs
  // attention the row becomes clickable -- clicking it pings the
  // toolbar chip (which owns the affordance) instead of duplicating
  // a Re-pick button inside this card.
  // A reviewed beep is confirmed: the operator's explicit sign-off is the
  // single source of truth and outranks both low confidence and the
  // heuristic diagnostic, exactly as the backend's beep-queue status
  // treats ``beep_reviewed`` before it ever looks at confidence. Only an
  // un-reviewed beep can read "likely wrong".
  const beepLikelyWrong =
    hasBeep &&
    !beepReviewed &&
    ((beepConfidence != null && beepConfidence < beepLowConfThreshold) ||
      beepDiagnostic != null);
  const beepRow: ChecklistItem = !hasBeep
    ? {
        label: "Beep position",
        done: false,
        tone: "todo",
        sub: "not yet picked",
        ...(onRepickBeep ? { onClick: onRepickBeep } : {}),
      }
    : beepLikelyWrong
      ? {
          label: "Beep position",
          // Treat as "done enough" so the run isn't blocked, but tone
          // it amber and surface the diagnostic so the operator sees
          // the same warning the chip is showing.
          done: true,
          tone: "warn",
          sub: beepDiagnostic ?? "likely wrong",
          ...(onRepickBeep ? { onClick: onRepickBeep } : {}),
        }
      : {
          label: "Beep position",
          done: true,
          tone: "done",
          sub: beepReviewed ? "confirmed" : "picked",
        };

  const checklist: ChecklistItem[] =
    kind === "trim"
      ? [
          {
            label: "Source clip",
            done: hasSource,
            sub: hasSource ? "available" : "missing",
          },
          beepRow,
          {
            label: "Stage time",
            done: hasStageTime,
            sub: hasStageTime
              ? stageEntry?.time_seconds_manual
                ? "set manually"
                : "from scoreboard"
              : "not set",
          },
        ]
      : [
          {
            label: "Trim cache",
            done: hasTrim,
            sub: hasTrim ? "built" : "not built yet",
          },
          beepRow,
          {
            label: "Shot detector",
            done: false,
            sub: "not run yet",
          },
        ];

  // Copy depends on whether a job is in flight. When trim or detect is
  // running the panel stops reading as a warning ("PREREQUISITES /
  // TRIM BEFORE AUDIT" with an exclamation glyph) and reads as an
  // in-progress task ("RUNNING / Rebuilding trim..."). Prevents the
  // post-resync confusion where everything is green but the headline
  // still nags the operator to do work that's already underway.
  const headline = running
    ? kind === "trim"
      ? "Rebuilding trim..."
      : "Detecting shots..."
    : kind === "trim"
      ? "Trim before audit"
      : "Detect shots before audit";
  const body = running
    ? kind === "trim"
      ? "Cropping the source to this stage and aligning to the buzzer. Audit unlocks once the trim cache lands."
      : "Running the ensemble on the trimmed clip. Audit unlocks once the candidate shots are written."
    : kind === "trim"
      ? "Audit needs a trimmed clip aligned to the buzzer. Run trim to crop the source down to this stage, then come back here."
      : "Audit reviews shots that detection has already proposed. Run detection on the trimmed clip to populate candidate shots, then come back here.";
  const runLabel = running
    ? kind === "trim"
      ? `Trimming${pct != null ? ` ${pct}%` : "..."}`
      : `Detecting${pct != null ? ` ${pct}%` : "..."}`
    : kind === "trim"
      ? "Run trim"
      : "Run detection";
  const stageLabel = `Stage ${String(stage.stage_number).padStart(2, "0")} · ${
    running ? "Running" : "Prerequisites"
  }`;

  return (
    <div className="flex flex-1 items-start justify-center py-4">
      <div
        role="region"
        aria-label={headline}
        className="w-full max-w-[40rem] rounded-[10px] border border-rule bg-surface p-5"
      >
        <div className="flex items-start gap-4">
          <span
            aria-hidden
            className={cn(
              "mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-full border",
              running ? "border-rule-strong text-ink-2" : "border-live/45 text-live",
            )}
          >
            {running ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <span className="font-mono text-md font-medium">!</span>}
          </span>
          <div className="min-w-0 flex-1">
            <Label tone={running ? "ink" : "live"}>{stageLabel}</Label>
            <h2 className="mt-1 text-lg font-semibold text-ink">{headline}</h2>
            <p className="mt-1 max-w-[52ch] text-md text-muted">{body}</p>
          </div>
        </div>

        <ul className="mt-4 mb-4 flex list-none flex-col p-0 rounded-[10px] border border-rule">
          {checklist.map((c, i) => {
            const tone = c.tone ?? (c.done ? "done" : "todo");
            const interactive = c.onClick != null;
            const Tag = interactive ? "button" : "li";
            return (
              <Tag
                key={i}
                {...(interactive
                  ? {
                      type: "button" as const,
                      onClick: c.onClick,
                      "aria-label": `${c.label} -- ${c.sub}. Opens the beep picker.`,
                    }
                  : {})}
                className={cn(
                  "flex w-full items-center gap-2.5 border-b border-rule px-3 py-2 text-left text-md last:border-b-0",
                  interactive && "cursor-pointer transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "inline-block size-2 shrink-0 rounded-full",
                    tone === "done" ? "bg-done" : tone === "warn" ? "bg-live" : "border-[1.5px] border-rule-strong",
                  )}
                />
                <span className={cn("flex-1", tone === "warn" ? "text-live" : c.done ? "text-ink-2" : "text-ink")}>{c.label}</span>
                <span className={cn("numeral text-sm", tone === "warn" ? "text-ink-2" : "text-muted")}>{c.sub}</span>
                {interactive ? <span aria-hidden className="text-sm text-muted">Re-pick &rsaquo;</span> : null}
              </Tag>
            );
          })}
        </ul>

        {/* Manual stage-time entry, mounted inside the trim gate so a
         *  match without scoreboard data can unblock itself right here. */}
        {kind === "trim" && stageEntry && primaryVideo && onProjectUpdate ? (
          <div className="mb-4">
            <StageTimeSection
              slug={slug}
              stageNumber={stageNumber}
              stage={stageEntry}
              primary={primaryVideo}
              onProjectUpdate={onProjectUpdate}
              setError={setError}
            />
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" variant="primary" onClick={() => void run()} disabled={running || blocked} title={blockedReason ?? undefined}>
            {running ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : null}
            {runLabel}
          </Button>
          {error ? <span className="text-sm text-led-text">{error}</span> : null}
          <span className="ml-auto text-sm text-muted">{blocked ? (blockedReason ?? "Audit unlocks when these pass") : "Audit unlocks when these pass"}</span>
        </div>
      </div>
    </div>
  );
}
