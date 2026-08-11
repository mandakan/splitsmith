/**
 * MobileConfirmSheet - a bottom sheet for the mobile beep review's
 * destructive confirms (apply new beep time, re-detect). Same overlay
 * architecture contract as every other floating surface: body Portal,
 * the shared z token scale, and useDialogFocus for Escape + focus trap
 * + focus restore. Kept as its own small component (rather than reusing
 * ConfirmDialog) because the desktop confirm card doesn't fit a phone
 * viewport - this slides up from the bottom edge instead.
 *
 * Accessibility: role="dialog" + aria-modal + aria-label, 44 px min
 * touch targets on both actions, entrance animation gated behind
 * motion-safe so prefers-reduced-motion users get no transition.
 */

import { useEffect, useRef, useState } from "react";

import { Portal } from "@/components/ui/Portal";
import { useDialogFocus } from "@/lib/dialogFocus";
import { cn } from "@/lib/utils";

export function MobileConfirmSheet({
  open,
  title,
  body,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus(open, panelRef, onCancel);

  // Mount-time slide-in, same pattern as MobileNav: start off-canvas and
  // translate to rest on the next frame, motion-safe only.
  const [entered, setEntered] = useState(false);
  useEffect(() => {
    if (!open) {
      setEntered(false);
      return;
    }
    const id = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(id);
  }, [open]);

  if (!open) return null;

  return (
    <Portal>
      <div className="fixed inset-0 z-modal flex items-end bg-bg/70" onClick={onCancel}>
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          tabIndex={-1}
          className={cn(
            "w-full rounded-t-xl border-t border-rule bg-surface p-5 pb-8 outline-none",
            "motion-safe:transition-transform motion-safe:duration-200",
            entered ? "translate-y-0" : "motion-safe:translate-y-full",
          )}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="mb-2 font-display text-base font-bold uppercase text-ink">
            {title}
          </div>
          <p className="mb-5 text-sm text-muted">{body}</p>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={onCancel}
              className="min-h-11 flex-1 rounded border border-rule px-4 text-sm text-ink"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onConfirm}
              className="btn-led-fill min-h-11 flex-1 rounded-md"
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </Portal>
  );
}
