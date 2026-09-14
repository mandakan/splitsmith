/**
 * Sheet -- a side panel for a detail the page keeps in view behind it
 * (spec 2026-09-13 s6): from the right at md and up, a bottom sheet
 * below. Escape and the backdrop close it; the caller owns `open`.
 */
import { useEffect, type ReactNode } from "react";

import { Portal } from "@/components/ui/Portal";
import { cn } from "@/lib/utils";

export interface SheetProps {
  open: boolean;
  onClose: () => void;
  /** Accessible name for the dialog. */
  label: string;
  children: ReactNode;
  className?: string;
}

export function Sheet({ open, onClose, label, children, className }: SheetProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <Portal>
      <div className="fixed inset-0 z-chrome">
        <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-bg/60" />
        <div
          role="dialog"
          aria-modal="true"
          aria-label={label}
          className={cn(
            "absolute flex flex-col bg-surface shadow-lg",
            "inset-x-0 bottom-0 max-h-[85vh] rounded-t-[10px] border-t border-rule-strong",
            "md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-full md:max-w-[440px] md:rounded-none md:border-l md:border-t-0",
            className,
          )}
        >
          {children}
        </div>
      </div>
    </Portal>
  );
}
