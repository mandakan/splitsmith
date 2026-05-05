import {
  Crosshair,
  FileBarChart,
  FlaskConical,
  FolderInput,
  Home,
  Palette,
  Repeat,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Navigate, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { JobsPanel } from "@/components/JobsPanel";
import { ThemeToggle } from "@/components/ThemeToggle";
import { api, type ServerHealth } from "@/lib/api";
import { useLabEnabled } from "@/lib/features";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  label: string;
  icon: typeof Home;
  end?: boolean;
}
const BASE_NAV: NavItem[] = [
  { to: "/", label: "Overview", icon: Home, end: true },
  { to: "/ingest", label: "Ingest", icon: FolderInput },
  { to: "/audit", label: "Audit", icon: Crosshair },
  { to: "/export", label: "Export", icon: FileBarChart },
];
const LAB_NAV: NavItem = { to: "/lab", label: "Lab", icon: FlaskConical };

export function AppShell() {
  const { pathname } = useLocation();
  // /review is fixture-only: no project context, the project tabs would
  // 404 against the throwaway tmp project ``splitsmith review`` boots.
  // Hide the sidebar entirely so the screen reads as a single-purpose
  // tool instead of "audit screen with broken navigation".
  const fixtureMode = pathname.startsWith("/review");

  // Server-side feature flags. ``lab`` defaults off and is opt-in via
  // ``splitsmith ui --lab``; if the fetch fails we hide the Lab nav,
  // which is the correct behaviour for an end-user install. Shared
  // across pages via the ``useLabEnabled`` hook so per-page fixture
  // affordances stay in sync without separate fetches.
  const labEnabled = useLabEnabled();
  const nav = labEnabled ? [...BASE_NAV, LAB_NAV] : BASE_NAV;

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
        <aside className="flex w-60 flex-col border-r border-border bg-card">
          <div className="flex h-14 items-center gap-2 px-4 font-semibold tracking-tight">
            <Crosshair className="size-5 text-primary" />
            splitsmith
          </div>
          <nav className="flex flex-1 flex-col gap-0.5 p-2">
            {nav.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                    isActive
                      ? "bg-accent text-accent-foreground font-medium"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                  )
                }
              >
                <Icon className="size-4" />
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="border-t border-border p-2">
            <NavLink
              to="/_design"
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  isActive
                    ? "bg-accent text-accent-foreground font-medium"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                )
              }
            >
              <Palette className="size-4" />
              Design system
            </NavLink>
          </div>
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
            <ThemeToggle />
          </div>
        </header>
        <main className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto px-6 py-6">
          <Outlet />
        </main>
      </div>
      {fixtureMode ? null : <JobsPanel />}
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
    navigate("/pick");
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
