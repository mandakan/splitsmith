/**
 * Menu -- a small popover anchored under its trigger (spec 2026-09-13
 * s6). The caller owns `open`; the menu closes on an outside click or
 * Escape. Items are plain buttons / labels styled with `menuItemClass`.
 */
import { useEffect, useRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export const menuItemClass =
  "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-md text-ink-2 hover:bg-surface-2 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-led";

export interface MenuProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  align?: "left" | "right";
  className?: string;
}

export function Menu({ open, onClose, children, align = "left", className }: MenuProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div
      ref={ref}
      role="menu"
      className={cn(
        "absolute top-full z-20 mt-1 flex min-w-52 flex-col gap-0.5 rounded-[10px] border border-rule-strong bg-surface p-1.5 text-md text-ink-2 shadow-lg",
        align === "right" ? "right-0" : "left-0",
        className,
      )}
    >
      {children}
    </div>
  );
}
