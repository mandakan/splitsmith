import { Crosshair, FileBarChart, FolderInput, Home, Palette } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { JobsPanel } from "@/components/JobsPanel";
import { ThemeToggle } from "@/components/ThemeToggle";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Overview", icon: Home, end: true },
  { to: "/ingest", label: "Ingest", icon: FolderInput },
  { to: "/audit", label: "Audit", icon: Crosshair },
  { to: "/export", label: "Export", icon: FileBarChart },
];

export function AppShell() {
  const { pathname } = useLocation();
  // /review is fixture-only: no project context, the project tabs would
  // 404 against the throwaway tmp project ``splitsmith review`` boots.
  // Hide the sidebar entirely so the screen reads as a single-purpose
  // tool instead of "audit screen with broken navigation".
  const fixtureMode = pathname.startsWith("/review");

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      {fixtureMode ? null : (
        <aside className="flex w-60 flex-col border-r border-border bg-card">
          <div className="flex h-14 items-center gap-2 px-4 font-semibold tracking-tight">
            <Crosshair className="size-5 text-primary" />
            splitsmith
          </div>
          <nav className="flex flex-1 flex-col gap-0.5 p-2">
            {NAV.map(({ to, label, icon: Icon, end }) => (
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
            <ProjectHeader />
          )}
          <div className="flex items-center gap-2">
            {fixtureMode ? null : <JobsPanel />}
            <ThemeToggle />
          </div>
        </header>
        <main className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto px-6 py-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function ProjectHeader() {
  // Project name is fetched from /api/health; for the v1 shell we keep this
  // simple and let pages render their own headings. A future iteration can
  // surface the active project name here once the project context is wired.
  return <div className="text-sm text-muted-foreground">Production UI v1 (Sub 1)</div>;
}
