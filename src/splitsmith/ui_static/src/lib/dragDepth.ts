/**
 * Depth-counted drag tracking (add-videos UX rework).
 *
 * dragenter/dragleave fire for every child element the cursor crosses,
 * so a naive isDragging boolean flickers off between children. The fix
 * is the standard enter/leave depth counter: increment on enter,
 * decrement on leave, active while depth > 0, hard-reset on drop or
 * dragend. Two flavors:
 *
 *   - useWindowFileDrag: window-level listeners for full-page drop
 *     targets (hosted Ingest).
 *   - useElementFileDrag: React handlers to spread on a bounded
 *     dropzone (hosted upload modal).
 */

import { useCallback, useEffect, useRef, useState } from "react";

/** True when a drag carries files (vs text selections or in-app drags). */
export function dragHasFiles(e: { dataTransfer: DataTransfer | null }): boolean {
  const types = e.dataTransfer?.types;
  return Boolean(types && Array.from(types).includes("Files"));
}

/** Window-level file-drag tracking. Returns true while a file drag is
 *  anywhere over the window. Pass ``enabled: false`` to keep the
 *  listeners detached (e.g. local mode, or before the mode resolves). */
export function useWindowFileDrag(enabled: boolean): boolean {
  const depth = useRef(0);
  const [active, setActive] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    const onEnter = (e: DragEvent) => {
      if (!dragHasFiles(e)) return;
      depth.current += 1;
      setActive(true);
    };
    const onLeave = (_e: DragEvent) => {
      // Decrement unconditionally - browsers (Safari, Firefox) may clear
      // dataTransfer.types on dragleave, so the enter-side gate is the only
      // file-filter that matters. Without unconditional decrement, depth sticks
      // above zero if a drag exits the window without drop/dragend firing.
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setActive(false);
    };
    const onEnd = () => {
      depth.current = 0;
      setActive(false);
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragleave", onLeave);
    // Capture phase - a bounded dropzone nested inside the window (e.g.
    // HostedUploadModal) legitimately calls e.stopPropagation() on its own
    // drop/dragend handler to keep its bubble-phase logic self-contained.
    // A bubble-phase listener here would never see that event and the
    // depth counter would never reset, sticking the full-page overlay on
    // forever. Capture runs before stopPropagation can take effect, so
    // the reset always fires regardless of what child handlers do.
    window.addEventListener("drop", onEnd, true);
    window.addEventListener("dragend", onEnd, true);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onEnd, true);
      window.removeEventListener("dragend", onEnd, true);
      depth.current = 0;
      setActive(false);
    };
  }, [enabled]);
  return active;
}

/** Element-level file-drag tracking. Spread ``handlers`` onto the
 *  dropzone element and call ``reset()`` inside your own onDrop.
 *  onDragOver preventDefaults unconditionally - without it the browser
 *  refuses the drop. */
export function useElementFileDrag(): {
  dragging: boolean;
  reset: () => void;
  handlers: {
    onDragEnter: (e: React.DragEvent) => void;
    onDragOver: (e: React.DragEvent) => void;
    onDragLeave: (e: React.DragEvent) => void;
  };
} {
  const depth = useRef(0);
  const [dragging, setDragging] = useState(false);
  const reset = useCallback(() => {
    depth.current = 0;
    setDragging(false);
  }, []);
  const onDragEnter = useCallback((e: React.DragEvent) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    depth.current += 1;
    setDragging(true);
  }, []);
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);
  const onDragLeave = useCallback((_e: React.DragEvent) => {
    // Decrement unconditionally - browsers (Safari, Firefox) may clear
    // dataTransfer.types on dragleave, so the enter-side gate is the only
    // file-filter that matters. Without unconditional decrement, depth sticks
    // above zero if a drag exits the window without drop/dragend firing.
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setDragging(false);
  }, []);
  return { dragging, reset, handlers: { onDragEnter, onDragOver, onDragLeave } };
}
