import {
  Crosshair,
  PanelLeftClose,
  PanelLeftOpen,
  Palette,
  Repeat,
  Server,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Navigate, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { JobsSurface } from "@/components/Jobs";
import { ModeSwitch } from "@/components/ui/ModeSwitch";
import { api, type ServerHealth } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

const SIDEBAR_COLLAPSE_KEY = "splitsmith.appshell.sidebarCollapsed";

export function AppShell() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { mode } = useMode();
  const { user } = useAuth();
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
  const fixtureMode = pathname.startsWith("/review");

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

  // Server bind-state. When the user launches ``splitsmith ui`` with no
  // ``--project`` the server boots unbound; we redirect to /pick so the
  // user can choose. The fixture-mode (review) branch keeps working
  // since /review boots its own throwaway project that always reads as
  // bound. Null while loading; ``null`` skips the redirect to avoid a
  // flicker through /pick on bound boots.
  const [health, setHealth] = useState<ServerHealth | null>(null);
  useEffect(() => {
    if (fixtureMode) return;
    let alive = true;
    api
      .getHealth()
      .then((h) => {
        if (alive) setHealth(h);
      })
      .catch(() => {
        // Network failure: keep rendering the shell rather than yanking
        // the user to /pick on a transient hiccup.
        if (alive) setHealth(null);
      });
    return () => {
      alive = false;
    };
  }, [fixtureMode]);

  if (!fixtureMode && health && !health.bound) {
    return <Navigate to="/pick" replace />;
  }

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      {fixtureMode ? null : (
        <aside
          className={cn(
            "flex flex-col border-r border-border bg-card transition-[width] duration-150",
            sidebarCollapsed ? "w-14" : "w-60",
          )}
        >
          <div
            className={cn(
              "flex h-14 items-center gap-2 px-3 font-semibold tracking-tight",
              sidebarCollapsed && "justify-center px-2",
            )}
          >
            <Crosshair className="size-5 shrink-0 text-primary" />
            {sidebarCollapsed ? null : <span>splitsmith</span>}
            <button
              type="button"
              onClick={toggleSidebar}
              title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              className={cn(
                "ml-auto inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
                sidebarCollapsed && "ml-0",
              )}
            >
              {sidebarCollapsed ? (
                <PanelLeftOpen className="size-4" aria-hidden />
              ) : (
                <PanelLeftClose className="size-4" aria-hidden />
              )}
            </button>
          </div>
          {/* AppShell only renders the legacy single-purpose surfaces
              (fixture editor, design system). Those screens self-shell;
              the cross-surface nav lives on MatchShell / DeveloperShell. */}
          <nav className="flex flex-1 flex-col gap-0.5 p-2">
            {user?.is_admin ? (
              <NavLink
                to="/admin/workers"
                title={sidebarCollapsed ? "Workers" : undefined}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                    sidebarCollapsed && "justify-center px-0",
                    isActive
                      ? "bg-accent text-accent-foreground font-medium"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                  )
                }
              >
                <Server className="size-4 shrink-0" />
                {sidebarCollapsed ? null : <span>Workers</span>}
              </NavLink>
            ) : null}
          </nav>

          <div className="border-t border-border p-2">
            <NavLink
              to="/_design"
              title={sidebarCollapsed ? "Design system" : undefined}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  sidebarCollapsed && "justify-center px-0",
                  isActive
                    ? "bg-accent text-accent-foreground font-medium"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                )
              }
            >
              <Palette className="size-4 shrink-0" />
              {sidebarCollapsed ? null : <span>Design system</span>}
            </NavLink>
          </div>

          <JobsSurface
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
        <header className="flex h-14 items-center justify-between border-b border-border px-6">
          {fixtureMode ? (
            <div className="flex items-center gap-2 text-sm font-semibold tracking-tight">
              <Crosshair className="size-4 text-primary" />
              splitsmith review
            </div>
          ) : (
            <ProjectHeader health={health} />
          )}
          <div className="flex items-center gap-2">
            <ModeSwitch size="sm" />
          </div>
        </header>
        <main className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto px-6 py-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function ProjectHeader({ health }: { health: ServerHealth | null }) {
  const navigate = useNavigate();
  const [switching, setSwitching] = useState(false);

  async function switchProject() {
    setSwitching(true);
    try {
      await api.unbindProject();
    } catch {
      // Best-effort: even if unbind fails the picker can re-bind a
      // different project on top.
    }
    // Replace, not push: the project is now unbound, so a back-button
    // would return to a bound-only URL (e.g. /audit/3) that immediately
    // redirects back to /pick. That wastes a history slot and breaks
    // the user's mental model of "back undoes my last action".
    navigate("/pick", { replace: true });
  }

  if (!health || !health.bound) {
    return <div className="text-sm text-muted-foreground">splitsmith</div>;
  }

  return (
    <div className="flex items-center gap-3 text-sm">
      <div className="flex flex-col leading-tight">
        <span className="font-medium tracking-tight">{health.project_name}</span>
        <span className="font-mono text-xs text-muted-foreground truncate max-w-[420px]">
          {health.project_root}
        </span>
      </div>
      <button
        type="button"
        onClick={switchProject}
        disabled={switching}
        className="flex items-center gap-1.5 rounded-md border border-border bg-card px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
        title="Switch to a different project"
      >
        <Repeat className="size-3.5" />
        Switch
      </button>
    </div>
  );
}
