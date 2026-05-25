import { ArrowRight, Link2 } from "lucide-react";
import { Link } from "react-router-dom";

import { useMatchHref } from "@/lib/matchHref";

export interface BeepAnomalyBannerProps {
  /** The diagnostic produced by Audit's "first shot / overshoot" heuristic
   *  (Audit.tsx -> beepDiagnostic). Render-gated by the parent so this
   *  component only mounts when a real diagnostic exists. */
  reason: string;
  /** Shooter slug for the deep-link target. */
  slug: string;
  /** Stage number for the deep-link target. */
  stageNumber: number;
  /** Primary video id for the deep-link target. The /beep-review queue
   *  keys items by ``slug::stage::video_id``; we pass the same shape via
   *  ``?focus=...`` so the queue opens with the exact item active. */
  videoId: string;
}

/**
 * Amber "beep looks wrong" banner that lives below the audit toolbar.
 *
 * Replaces the old in-page sync-mode entry (the chip's "Re-pick beep"
 * button). Audit is now read-only with respect to the beep; the banner
 * names the suspicion and ships the operator to /beep-review where the
 * waveform picker + video preview live.
 *
 * Scoping: the banner fires only on the active shooter's video, never
 * cross-shooter. Cross-shooter awareness ("3 beeps pending on stage 02")
 * belongs in the queue, not in a per-stage warning surface here.
 */
export function BeepAnomalyBanner({
  reason,
  slug,
  stageNumber,
  videoId,
}: BeepAnomalyBannerProps) {
  const href = useMatchHref();
  const focusKey = `${slug}::${stageNumber}::${videoId}`;
  const target = `${href("beep-review")}?focus=${encodeURIComponent(focusKey)}`;
  return (
    <div
      role="status"
      className="flex flex-wrap items-start gap-3 rounded-2xl border border-live/40 bg-live/[0.08] px-4 py-3.5"
    >
      <span
        aria-hidden
        className="mt-0.5 inline-flex size-5.5 shrink-0 items-center justify-center rounded-full border border-live/60 bg-live/10 font-mono text-[0.6875rem] font-bold text-live"
      >
        !
      </span>
      <div className="flex min-w-[14rem] flex-1 flex-col gap-1">
        <div className="font-display text-[0.75rem] font-bold uppercase tracking-[0.08em] text-live">
          Looks like the beep is wrong
        </div>
        <p className="m-0 text-[0.8125rem] leading-snug text-ink-2">
          {reason}
        </p>
      </div>
      <Link
        to={target}
        className="inline-flex items-center gap-1.5 rounded-md border-0 bg-led-fill px-3.5 py-2 font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink shadow-[0_0_0_1px_var(--color-led),0_0_14px_var(--color-led-glow)] hover:bg-led"
      >
        <Link2 className="size-3" strokeWidth={2} aria-hidden />
        Review this beep
        <ArrowRight className="size-3" aria-hidden />
      </Link>
    </div>
  );
}
