/**
 * App-wide drag/drop guard (add-videos UX rework).
 *
 * A drop on any unhandled element makes the browser navigate into the
 * dropped file, destroying SPA session state. This guard preventDefaults
 * dragover + drop at the window level so an unhandled drop is inert.
 *
 * In local mode a file drop additionally shows a short toast pointing
 * at the picker - a browser drop cannot expose absolute host paths, so
 * local (path-based) registration can never be fed by a drop.
 *
 * Handled drops are unaffected: element-level dropzones that consume a
 * drop call stopPropagation() (so this listener never sees it), and the
 * hosted Ingest page's window-level drop handler runs independently -
 * this guard checks defaultPrevented BEFORE preventDefaulting and never
 * stops propagation itself.
 *
 * Toast follows the SaveToast pattern (Audit.tsx): body Portal, z-toast
 * token, role="status" live region rendered unconditionally so screen
 * readers pick up the change.
 */

import { useEffect, useState } from "react";

import { Portal } from "@/components/ui/Portal";
import { dragHasFiles } from "@/lib/dragDepth";
import { useDeploymentMode } from "@/lib/features";

const TOAST_MS = 4000;

export function DropGuard() {
  const { mode, resolved } = useDeploymentMode();
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    const onDragOver = (e: DragEvent) => {
      e.preventDefault();
    };
    const onDrop = (e: DragEvent) => {
      const unhandled = !e.defaultPrevented;
      e.preventDefault();
      if (unhandled && resolved && mode === "local" && dragHasFiles(e)) {
        setToast("Drops can't be added in local mode - use Pick a folder");
      }
    };
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("drop", onDrop);
    };
  }, [mode, resolved]);

  useEffect(() => {
    if (toast === null) return;
    const id = window.setTimeout(() => setToast(null), TOAST_MS);
    return () => window.clearTimeout(id);
  }, [toast]);

  return (
    <Portal>
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 right-4 z-toast"
      >
        {toast ? (
          <div className="pointer-events-auto rounded-md border border-rule-strong bg-surface px-3 py-2 text-sm text-ink shadow-md">
            {toast}
          </div>
        ) : null}
      </div>
    </Portal>
  );
}
