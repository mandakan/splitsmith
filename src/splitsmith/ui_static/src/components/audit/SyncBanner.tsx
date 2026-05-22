import { Crosshair, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

export interface SyncBannerProps {
  camLabel: string;
  oldBeepTime: number | null;
  candidateTime: number | null;
  onCancel: () => void;
  onApply: () => void;
  /** "Looks right" -- mark the existing beep as reviewed without
   *  changing it. Only shown when a beep exists and hasn't yet been
   *  reviewed. When omitted, the button is hidden. */
  onMarkReviewed?: () => void;
  /** When true, Apply / Mark reviewed are in flight -- disable both. */
  busy?: boolean;
  /** "Snap" -- refine the current candidate by running the beep
   *  detector in a tight window around it. When ``candidateTime`` is
   *  null the button stays visible but disabled; the caller surfaces
   *  the hint to drop a marker first. Omit this prop to hide the
   *  button entirely (the BeepReview page reuses this banner without
   *  the snap affordance). */
  onSnap?: () => void;
  /** Snap request is in flight. */
  snapping?: boolean;
  /** Last snap error (e.g. "no candidate within +/-1.5s"). Rendered
   *  inline under the readout so the operator sees why their snap
   *  attempt failed without leaving the picker. */
  snapError?: string | null;
}

/**
 * Replaces the anomaly chips row above the waveform while the operator
 * is re-picking the buzzer for a specific cam. Matches the design's
 * ARSyncBanner: live-amber surround, old/new/delta readout, Cancel +
 * Apply actions.
 */
export function SyncBanner({
  camLabel,
  oldBeepTime,
  candidateTime,
  onCancel,
  onApply,
  onMarkReviewed,
  busy = false,
  onSnap,
  snapping = false,
  snapError = null,
}: SyncBannerProps) {
  const delta =
    candidateTime != null && oldBeepTime != null
      ? candidateTime - oldBeepTime
      : null;
  const applyDisabled = busy || candidateTime == null;
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-wrap items-center gap-3 rounded-2xl border border-live/40 bg-live/10 px-4 py-3 shadow-[0_0_0_1px_var(--color-rule)_inset,0_0_24px_rgba(251,191,36,0.22)]"
    >
      <span
        aria-hidden
        className="inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-live font-mono text-sm font-extrabold leading-none text-bg"
      >
        !
      </span>
      <div className="min-w-0 flex-1">
        <div className="font-display text-[0.8125rem] font-bold uppercase tracking-[0.06em] text-live">
          Picking buzzer for {camLabel}
        </div>
        <div className="mt-0.5 text-[0.8125rem] leading-snug text-ink-2">
          Click a transient on the waveform below.{" "}
          <span className="text-muted">Was</span>{" "}
          <b className="font-mono tabular-nums text-ink">
            {oldBeepTime != null ? `${oldBeepTime.toFixed(3)}s` : "—"}
          </b>
          {candidateTime != null ? (
            <>
              {" "}
              <span className="text-muted">· pick</span>{" "}
              <b className="font-mono tabular-nums text-ink">
                {candidateTime.toFixed(3)}s
              </b>
              {delta != null ? (
                <span className="ml-1 font-mono tabular-nums text-muted">
                  ({delta >= 0 ? "+" : ""}
                  {(delta * 1000).toFixed(0)}ms)
                </span>
              ) : null}
            </>
          ) : null}
        </div>
        {snapError ? (
          // Body-size LED text uses --color-led-text (lighter pink) so
          // the 11px message reads against the dark bg; the saturated
          // --color-led is reserved for accents and large display per
          // the post-b3531b5 colour-discipline rule.
          <div className="mt-1 font-mono text-[0.6875rem] text-led-text">
            {snapError}
          </div>
        ) : null}
      </div>
      {onSnap ? (
        <button
          type="button"
          onClick={onSnap}
          disabled={busy || snapping || candidateTime == null}
          title={
            candidateTime == null
              ? "Drop a marker on the waveform first"
              : "Snap the marker to the strongest beep within +/-1.5s"
          }
          className="inline-flex items-center gap-1.5 rounded-md border border-led/40 bg-led-tint px-3.5 py-2 font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-led transition-colors hover:bg-led/15 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {snapping ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <Crosshair className="size-3.5" aria-hidden />
          )}
          Snap to beep
        </button>
      ) : null}
      <button
        type="button"
        onClick={onCancel}
        disabled={busy}
        className="inline-flex items-center rounded-md border border-rule-strong bg-surface-2 px-3.5 py-2 font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink-2 transition-colors hover:bg-surface-3 disabled:opacity-50"
      >
        Cancel
      </button>
      {onMarkReviewed ? (
        <button
          type="button"
          onClick={onMarkReviewed}
          disabled={busy}
          title="Keep the current buzzer; acknowledge it looks right"
          className="inline-flex items-center rounded-md border border-done/40 bg-done/10 px-3.5 py-2 font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-done transition-colors hover:bg-done/20 disabled:opacity-50"
        >
          Looks right
        </button>
      ) : null}
      <button
        type="button"
        onClick={onApply}
        disabled={applyDisabled}
        className={cn(
          "inline-flex items-center rounded-md border-0 px-3.5 py-2 font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] transition-colors",
          applyDisabled
            ? "cursor-not-allowed bg-surface-3 text-subtle"
            : "bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_14px_var(--color-led-glow)] hover:bg-led-soft",
        )}
      >
        Apply new buzzer
      </button>
    </div>
  );
}
