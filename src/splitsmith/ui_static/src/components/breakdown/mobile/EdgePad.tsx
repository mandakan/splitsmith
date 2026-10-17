/**
 * The phone's edge-mode drag surface (spec 2026-10-17): the finger's
 * sideways travel over the pad's width, relative to where it went down,
 * never where it is. One finger; pointercancel (a call, an OS swipe) is a
 * cancel, never an end.
 */
import { useRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export function EdgePad({
  onStart,
  onMove,
  onEnd,
  onCancel,
  label,
  className,
  children,
}: {
  /** A finger went down: the page records the region every frame of this gesture is judged from. */
  onStart: () => void;
  onMove: (dxFrac: number, padPx: number) => void;
  onEnd: () => void;
  onCancel: () => void;
  label: string;
  className?: string;
  children?: ReactNode;
}) {
  const down = useRef<{ id: number; x: number } | null>(null);
  return (
    <div
      data-testid="edge-pad"
      role="application"
      aria-label={label}
      className={cn("touch-none select-none", className)}
      onPointerDown={(e) => {
        if (down.current) return;
        e.currentTarget.setPointerCapture(e.pointerId);
        down.current = { id: e.pointerId, x: e.clientX };
        onStart();
      }}
      onPointerMove={(e) => {
        const d = down.current;
        if (!d || d.id !== e.pointerId) return;
        const width = Math.max(e.currentTarget.clientWidth, 1);
        onMove((e.clientX - d.x) / width, width);
      }}
      onPointerUp={(e) => {
        if (down.current?.id !== e.pointerId) return;
        down.current = null;
        onEnd();
      }}
      onPointerCancel={(e) => {
        if (down.current?.id !== e.pointerId) return;
        down.current = null;
        onCancel();
      }}
    >
      {children}
    </div>
  );
}
