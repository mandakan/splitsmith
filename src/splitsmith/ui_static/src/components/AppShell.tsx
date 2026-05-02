import { Crosshair, FileBarChart, FolderInput, Home, Palette } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import { ThemeToggle } from "@/components/ThemeToggle";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Overview", icon: Home, end: true },
  { to: "/ingest", label: "Ingest", icon: FolderInput },
  { to: "/audit", label: "Audit", icon: Crosshair },
  { to: "/export", label: "Export", icon: FileBarChart },
];

export function AppShell() {
  return (
    <div className="flex min-h-screen bg-background text-foreground">
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
      <div className="flex flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b border-border px-6">
          <ProjectHeader />
          <div className="flex items-center gap-2">
            <ThemeToggle />
          </div>
        </header>
        <main className="flex-1 overflow-y-auto px-6 py-6">
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
