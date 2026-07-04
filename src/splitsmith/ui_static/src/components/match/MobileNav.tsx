/**
 * MobileNav - the < md navigation drawer for the match shell.
 *
 * Renders the SAME nav items MatchSidebar does (both consume
 * matchNavItems, so the two surfaces never drift) plus an ``extras``
 * slot where MatchShell parks the header chrome that has no room in
 * the compact mobile header (account chip, help/settings, switch
 * project, Jobs row).
 *
 * Modal dialog contract: Portal to body, z-drawer, focus trap +
 * Escape + focus restore via useDialogFocus, backdrop tap closes.
 * Slide-in transition is skipped under prefers-reduced-motion.
 */

import { LayoutGrid, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";

import { Portal } from "@/components/ui/Portal";
import { useDialogFocus } from "@/lib/dialogFocus";
import { cn } from "@/lib/utils";
import type { MatchNavItem } from "./navItems";

export interface MobileNavProps {
  open: boolean;
  onClose: () => void;
  items: MatchNavItem[];
  header: { matchName: string };
  /** Rendered below a divider after the nav rows - account chip,
   *  help/settings, switch project, Jobs row. */
  extras?: ReactNode;
}

export function MobileNav({ open, onClose, items, header, extras }: MobileNavProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const { pathname } = useLocation();
  useDialogFocus(open, panelRef, onClose);

  // Mount-time slide-in: the panel starts off-canvas and translates to
  // rest on the next frame. Skipped entirely under reduced motion (the
  // panel just appears in place).
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

  const reducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  return (
    <Portal>
      <div
        aria-hidden
        className="fixed inset-0 z-drawer bg-background/70"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Navigation"
        className={cn(
          "fixed inset-y-0 left-0 z-drawer w-[280px] max-w-[85vw] overflow-y-auto border-r border-rule bg-surface p-4",
          !reducedMotion && "transition-transform duration-200",
          !reducedMotion && !entered ? "-translate-x-full" : "translate-x-0",
        )}
      >
        <div className="mb-3 flex items-center gap-2 border-b border-rule pb-3">
          <span className="min-w-0 flex-1 truncate font-display text-[0.9375rem] font-bold uppercase leading-tight tracking-tight text-ink">
            {header.matchName}
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close navigation"
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <X className="size-5" aria-hidden />
          </button>
        </div>

        <nav aria-label="Match" className="flex flex-col gap-px">
          {items.map((item) => {
            // Same badge rules as SidebarLink: pending badges hide at
            // zero; count badges only render when defined.
            const showBadge =
              typeof item.count === "number" &&
              (item.badgeKind === "pending" ? item.count > 0 : true);
            if (item.disabled) {
              return (
                <span
                  key={item.key}
                  aria-disabled="true"
                  title={item.disabledHint}
                  className="flex min-h-11 cursor-not-allowed items-center gap-3 rounded-md px-3 font-display text-sm font-bold uppercase tracking-wide text-subtle opacity-60"
                >
                  <span className="inline-flex shrink-0">{item.icon}</span>
                  <span className="truncate">{item.label}</span>
                </span>
              );
            }
            return (
              <NavLink
                key={item.key}
                to={item.to}
                end={item.end}
                onClick={onClose}
                className={() => {
                  // Match SidebarLink active semantics: startsWith for most
                  // items, exact for end=true (Overview), never for items
                  // whose `to` contains a query string (?pick=...).
                  const pathWithoutQuery = item.to.split("?")[0];
                  const hasQuery = item.to.includes("?");
                  const isActive = hasQuery
                    ? false
                    : item.end
                      ? pathname === item.to
                      : pathname.startsWith(pathWithoutQuery);
                  return cn(
                    "flex min-h-11 items-center gap-3 rounded-md px-3 font-display text-sm font-bold uppercase tracking-wide transition-colors",
                    isActive
                      ? "bg-surface-2 text-led"
                      : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                  );
                }}
              >
                <span className="inline-flex shrink-0">{item.icon}</span>
                <span className="truncate">{item.label}</span>
                {showBadge ? (
                  <span
                    className={cn(
                      "ml-auto",
                      item.badgeKind === "pending" ? "badge-pending" : "badge-count",
                    )}
                  >
                    {pad2(item.count!)}
                  </span>
                ) : null}
              </NavLink>
            );
          })}
        </nav>

        <div className="mt-3 border-t border-rule pt-3">
          <Link
            to="/pick"
            onClick={onClose}
            className="flex min-h-11 w-full items-center gap-3 rounded-md px-3 font-display text-sm font-bold uppercase tracking-wide text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <LayoutGrid className="size-[15px] shrink-0" aria-hidden />
            <span className="truncate">Matches</span>
          </Link>
          {extras}
        </div>
      </div>
    </Portal>
  );
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}
