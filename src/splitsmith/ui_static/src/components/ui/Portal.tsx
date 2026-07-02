/**
 * Body portal for floating surfaces.
 *
 * Every fixed-position overlay (dialog, drawer, sheet, toast, takeover)
 * must render through this so its ``z-chrome``/``z-drawer``/``z-modal``/
 * ``z-toast`` token resolves in the ROOT stacking context. Rendered
 * inline, a fixed overlay inherits whatever stacking context its ancestors
 * create -- ``position: sticky``, ``transform``, ``filter``,
 * ``backdrop-filter``, ``opacity``, ``isolation`` all trap it at the
 * ancestor's own z level, painting it beneath page chrome regardless of
 * the overlay's z value (the JobsSheet-under-everything bug).
 *
 * React context, state, and synthetic events still flow through the
 * component tree as usual; only the DOM node moves.
 */

import { type ReactNode } from "react";
import { createPortal } from "react-dom";

export function Portal({ children }: { children: ReactNode }) {
  return createPortal(children, document.body);
}
