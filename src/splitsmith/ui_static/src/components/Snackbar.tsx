/**
 * Snackbar - the codebase's first interactive toast. Follows the
 * SaveToast/DropGuard shell (body Portal, z-toast token, live region
 * rendered unconditionally, pointer-events-none wrapper with an
 * interactive inner pill) and adds an optional action button (Undo).
 *
 * Status snacks are polite and auto-dismiss after 6 s; error snacks are
 * assertive, never auto-dismiss, and get an explicit Dismiss button
 * (WCAG - a timed disappearance must not be the only path for content
 * the user needs to act on). Undo is a convenience, not the only path:
 * the sheet can always re-apply the previous class, so the 6 s limit is
 * acceptable.
 */
import { useEffect, useRef } from "react";

import { Portal } from "@/components/ui/Portal";
import { cn } from "@/lib/utils";

const SNACK_MS = 6000;

export interface SnackState {
  message: string;
  tone: "status" | "error";
  actionLabel?: string;
  onAction?: () => void;
}

export function Snackbar({
  snack,
  onDismiss,
}: {
  snack: SnackState | null;
  onDismiss: () => void;
}) {
  const isError = snack?.tone === "error";
  const onDismissRef = useRef(onDismiss);

  useEffect(() => {
    onDismissRef.current = onDismiss;
  });

  useEffect(() => {
    if (!snack || snack.tone === "error") return;
    const id = window.setTimeout(() => onDismissRef.current(), SNACK_MS);
    return () => window.clearTimeout(id);
  }, [snack]);

  return (
    <Portal>
      <div
        role={isError ? "alert" : "status"}
        aria-live={isError ? "assertive" : "polite"}
        className="pointer-events-none fixed inset-x-4 bottom-4 z-toast flex justify-center sm:inset-x-auto sm:right-4"
      >
        {snack ? (
          <div
            className={cn(
              "pointer-events-auto flex min-h-11 items-center gap-3 rounded-md border bg-surface px-4 py-2 text-sm shadow-md",
              isError ? "border-destructive/40 text-destructive" : "border-rule-strong text-ink",
            )}
          >
            <span>{snack.message}</span>
            {snack.actionLabel && snack.onAction ? (
              <button
                type="button"
                onClick={snack.onAction}
                className="min-h-11 shrink-0 rounded px-2 font-display text-sm font-bold uppercase tracking-[0.06em] text-led focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
              >
                {snack.actionLabel}
              </button>
            ) : null}
            {isError ? (
              <button
                type="button"
                onClick={onDismiss}
                className="min-h-11 shrink-0 rounded px-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
              >
                Dismiss
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </Portal>
  );
}
