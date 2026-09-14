/**
 * StageDot -- the six-tier per-stage status indicator.
 *
 * One primitive is rendered everywhere a stage's lifecycle is summarised
 * by a single glyph: the sidebar stages list, the audit chip rail, the
 * home overview card. The intent is that the visual language for stage
 * status lives in exactly one component, so adding (or correcting) a
 * tier is a single-file change.
 *
 * Tiers (matches :type:`StageStatus` from ``lib/api`` and the
 * ``--color-status-*`` tokens in ``styles/index.css``):
 *
 *   todo         hollow ring, ``--status-todo``
 *   partial      dashed ring, ``--status-partial`` (video assigned, no time)
 *   ready        hollow ring, ``--rule-strong`` -- prereqs met, detection not
 *                run (was an LED halo; red is reserved for the current
 *                position since spec 2026-09-13 s5)
 *   in_progress  filled amber + 1.6s pulse (detected, save not hit)
 *   audited      filled green + check -- terminal
 *   skipped      hollow + horizontal bar -- terminal (operator opted out)
 *
 * ``audited`` and ``skipped`` both count as terminal in tallies; the
 * separate marks let the operator tell at a glance which stages they
 * actively closed out vs the ones they decided to skip.
 */

import type { StageStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface StageDotProps {
  status: StageStatus;
  /** Kept for call-site compatibility; the ready tier no longer has a
   *  halo to dial down, so both values render the same. */
  context?: "sidebar" | "chip";
  className?: string;
}

export function StageDot({ status, className }: StageDotProps) {
  switch (status) {
    case "audited":
      return (
        <span
          aria-label="Audited"
          className={cn(
            "inline-flex size-3 items-center justify-center rounded-full",
            "bg-[var(--color-status-audited)] text-bg shadow-[0_0_5px_var(--color-done-glow)]",
            className,
          )}
        >
          <svg
            width="7"
            height="7"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="4"
          >
            <path d="M5 12l5 5L20 7" />
          </svg>
        </span>
      );
    case "skipped":
      return (
        <span
          aria-label="Skipped"
          className={cn(
            "relative inline-flex size-3 items-center justify-center rounded-full",
            "border border-[var(--color-status-todo)] bg-transparent",
            className,
          )}
        >
          <span
            aria-hidden
            className="block h-px w-1.5 rounded-full bg-[var(--color-status-skipped)]"
          />
        </span>
      );
    case "in_progress":
      return (
        <span
          aria-label="In progress"
          className={cn(
            "inline-block size-3 rounded-full",
            "bg-[var(--color-status-progress)] shadow-[0_0_6px_var(--color-live-glow)]",
            "motion-safe:animate-[splitsmith-stage-progress-pulse_1.6s_ease-in-out_infinite]",
            className,
          )}
        />
      );
    case "ready":
      return (
        <span
          aria-label="Ready to audit"
          className={cn(
            "relative inline-flex size-3 items-center justify-center rounded-full",
            className,
          )}
        >
          <span
            aria-hidden
            className="block size-2 rounded-full border-[1.5px] border-rule-strong bg-transparent"
          />
        </span>
      );
    case "partial":
      return (
        <span
          aria-label="Stage time missing"
          className={cn(
            "inline-block size-3 rounded-full bg-transparent",
            "border-[1.5px] border-dashed border-[var(--color-status-partial)]",
            className,
          )}
        />
      );
    case "todo":
    default:
      return (
        <span
          aria-label="Not started"
          className={cn(
            "inline-block size-3 rounded-full bg-transparent",
            "border-[1.5px] border-[var(--color-status-todo)]",
            className,
          )}
        />
      );
  }
}
