/**
 * Menu -- a small popover anchored under its trigger (spec 2026-09-13
 * s6). The caller owns `open`; the menu closes on an outside click or
 * Escape. Items are plain buttons / labels styled with `menuItemClass`.
 *
 * The anchor is the menu's parent element (the caller's `relative`
 * wrapper around the trigger). The popover itself renders in a portal on
 * `document.body`, positioned against that anchor: rendered in place it
 * was clipped by any `overflow-hidden` ancestor, which is every `Table`,
 * so a row menu near the bottom of a table was cut off. It sits on the
 * drawer layer, above a takeover (the phone Audit) and a sheet.
 */
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

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

interface Position {
  top: number;
  left?: number;
  right?: number;
}

export function Menu({ open, onClose, children, align = "left", className }: MenuProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const markerRef = useRef<HTMLSpanElement | null>(null);
  const [pos, setPos] = useState<Position | null>(null);

  useLayoutEffect(() => {
    if (!open) {
      setPos(null);
      return;
    }
    const place = () => {
      const anchor = markerRef.current?.parentElement;
      if (!anchor) return;
      const r = anchor.getBoundingClientRect();
      setPos(
        align === "right"
          ? { top: r.bottom + 4, right: window.innerWidth - r.right }
          : { top: r.bottom + 4, left: r.left },
      );
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, align]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target)) return;
      // The trigger lives in the anchor; its own click toggles the menu,
      // so a mousedown there must not close it first.
      if (markerRef.current?.parentElement?.contains(target)) return;
      onClose();
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

  return (
    <>
      <span ref={markerRef} hidden />
      {open
        ? createPortal(
            <div
              ref={ref}
              role="menu"
              style={pos ?? { visibility: "hidden" }}
              className={cn(
                "fixed z-drawer flex min-w-52 flex-col gap-0.5 rounded-[10px] border border-rule-strong bg-surface p-1.5 text-md text-ink-2 shadow-lg",
                className,
              )}
            >
              {children}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
