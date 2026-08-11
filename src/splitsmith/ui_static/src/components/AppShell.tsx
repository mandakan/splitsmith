import {
  Crosshair,
  PanelLeftClose,
  PanelLeftOpen,
  Palette,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { JobsSurface } from "@/components/Jobs";
import { useShellContextSlot } from "@/components/layout/shellChromeContext";
import { useJobs } from "@/lib/jobs";
import { useMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

const SIDEBAR_COLLAPSE_KEY = "splitsmith.appshell.sidebarCollapsed";

export function AppShell() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { mode } = useMode();
  // JobsSurface no longer self-hosts its poller (#663); each shell
  // owns one jobs state and hands it down.
  const jobsState = useJobs();
  // RootLayout's header slot -- AppShell portals its context row (the
  // project switcher, or the fixture-mode label; see contextRow below)
  // there instead of rendering its own <header> (#550). It does not call
  // useShellAccent (the "led" default is right here) or
  // useShellOwnsMobileAccount (no nav drawer; the global bar's account
  // chip is the only one on a phone too).
  const slot = useShellContextSlot();
  // AppShell hosts the fixture editor + design system. Either one is
  // mode-agnostic, but flipping to Developer should take the user to
  // the dev workspace rather than leaving them on a hidden-sidebar page
  // with no dev nav.
  useEffect(() => {
    // Mode toggle uses replace, not push. Otherwise hitting browser
    // back after a mode flip would "undo" the flip via a route change
    // while the mode state stays put -- so the new shell mounts, sees
    // the wrong mode, and forces it back. Replace keeps history clean.
    if (mode === "developer") navigate("/dev/corpus", { replace: true });
  }, [mode, navigate]);
  // /review is fixture-only: no project context, the project tabs would
  // 404 against the throwaway tmp project ``splitsmith review`` boots.
  // Hide the sidebar entirely so the screen reads as a single-purpose
  // tool instead of "audit screen with broken navigation".
  const bindExempt = pathname.startsWith("/review");

  // Sidebar collapse state. Persisted in localStorage so the user's
  // choice survives page reloads. Pages that benefit from the extra
  // horizontal width (Coach grid mode, Audit waveform on a narrow
  // monitor) collapse once and stay collapsed.
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      return window.localStorage.getItem(SIDEBAR_COLLAPSE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(SIDEBAR_COLLAPSE_KEY, next ? "1" : "0");
      } catch {
        // localStorage may be unavailable (private mode); preference
        // stays in-memory for the session, which is fine.
      }
      return next;
    });
  }, []);

  // See the ``slot`` declaration above for what this portals into.
  const contextRow = (
    <div className="flex h-14 items-center border-t border-rule bg-bg px-7">
      {bindExempt ? (
        <div className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <Crosshair className="size-4 text-led" />
          splitsmith review
        </div>
      ) : (
        <ProjectHeader />
      )}
    </div>
  );

  return (
    <div className="flex min-h-[calc(100dvh-var(--shell-header-h,86px))] bg-bg text-ink">
      {bindExempt ? null : (
        <aside
          className={cn(
            "flex flex-col border-r border-rule bg-surface transition-[width] duration-150",
            sidebarCollapsed ? "w-14" : "w-60",
          )}
        >
          {/* Collapse toggle row. The brand mark used to live here too, but
              RootLayout's GlobalBar now spans full width above the sidebar
              and already renders it once (#550 review finding 2) -- a
              second crosshair + wordmark here just duplicated it. */}
          <div
            className={cn(
              "flex h-14 items-center px-3",
              sidebarCollapsed ? "justify-center px-2" : "justify-end",
            )}
          >
            <button
              type="button"
              onClick={toggleSidebar}
              title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              className="inline-flex size-7 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface-3 hover:text-ink"
            >
              {sidebarCollapsed ? (
                <PanelLeftOpen className="size-4" aria-hidden />
              ) : (
                <PanelLeftClose className="size-4" aria-hidden />
              )}
            </button>
          </div>
          {/* AppShell now holds only the fixture editor and the design
              system -- every other surface has its own shell
              (MatchShell / DeveloperShell) with its own cross-surface
              nav. This region is just the flex spacer that pushes the
              footer down. */}
          <div className="flex-1" />

          <div className="border-t border-rule p-2">
            <NavLink
              to="/_design"
              title={sidebarCollapsed ? "Design system" : undefined}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  sidebarCollapsed && "justify-center px-0",
                  isActive
                    ? "bg-surface-3 text-ink font-medium"
                    : "text-muted hover:bg-surface-3/50 hover:text-ink",
                )
              }
            >
              <Palette className="size-4 shrink-0" />
              {sidebarCollapsed ? null : <span>Design system</span>}
            </NavLink>
          </div>

          <JobsSurface
            state={jobsState}
            collapsed={sidebarCollapsed}
            sidebarExpandedWidth={240}
            sidebarCollapsedWidth={56}
          />
        </aside>
      )}
      {/* min-w-0 + overflow-x-hidden bound the flex-1 column to the
          available width. Without these, a wide audit waveform inside
          this column would let the flex item grow to fit, defeating
          the waveform's own overflow-x-auto and breaking zoom. */}
      <div className="flex min-w-0 flex-1 flex-col overflow-x-hidden">
        {slot ? createPortal(contextRow, slot) : null}
        <main className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto px-6 py-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function ProjectHeader() {
  return <div className="text-sm text-muted">splitsmith</div>;
}
