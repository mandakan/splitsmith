/**
 * DeveloperShell -- Shot Timer page chrome for Developer-mode surfaces (#331).
 *
 * Carries the same top-bar pattern as MatchShell but flips the accent line
 * cyan (data-mode="developer" handles tokens; this component lays out the
 * sidebar workflow stepper + model chip that signal we're in dev mode).
 *
 * Renders the 4-step workflow stepper sidebar (Corpus / Review queue /
 * Validate / Retrain). Step state derives from the active route + counts
 * pulled from `/api/dev/model`.
 */

import {
  Bell,
  Check,
  Clock,
  Database,
  FlaskConical,
  HelpCircle,
  Inbox,
  Layers,
  Settings,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { JobsSurface } from "@/components/Jobs";
import { Brand, IconButton, ModeSwitch } from "@/components/ui";
import { api, type DeveloperModelInfo } from "@/lib/api";
import { useMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

export interface DeveloperShellOutletContext {
  model: DeveloperModelInfo | null;
  refresh: () => void;
}

interface StepDef {
  to: string;
  num: string;
  label: string;
  countKey: keyof DeveloperModelInfo["step_counts"];
}

const STEPS: StepDef[] = [
  { to: "/dev/corpus", num: "01", label: "Corpus", countKey: "corpus" },
  { to: "/dev/review", num: "02", label: "Review queue", countKey: "review" },
  { to: "/dev/validate", num: "03", label: "Validate", countKey: "validate_runs" },
  { to: "/dev/retrain", num: "04", label: "Retrain", countKey: "retrain" },
];

export function DeveloperShell() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { mode, setMode } = useMode();

  // First-paint sync: if a /dev/* URL is loaded fresh, force the global
  // mode to "developer" so the cyan accent tokens fire. We only do this
  // once -- after that, a user click on the Match toggle should win and
  // take them back to /, so we navigate away instead of fighting state.
  const [didInitMode, setDidInitMode] = useState(false);
  useEffect(() => {
    if (!didInitMode) {
      if (mode !== "developer") setMode("developer");
      setDidInitMode(true);
      return;
    }
    // Replace, not push: same reasoning as the match shell -- mode
    // flips shouldn't leave a back-button breadcrumb to the dev URL
    // because the mode state itself won't restore on back.
    if (mode === "match") navigate("/", { replace: true });
  }, [mode, setMode, didInitMode, navigate]);

  const [model, setModel] = useState<DeveloperModelInfo | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let alive = true;
    api
      .getDeveloperModel()
      .then((m) => {
        if (alive) setModel(m);
      })
      .catch(() => {
        if (alive) setModel(null);
      });
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  const activeIdx = useMemo(() => {
    return STEPS.findIndex((s) => pathname.startsWith(s.to));
  }, [pathname]);

  return (
    <div
      className="min-h-screen text-ink"
      style={{
        backgroundImage:
          "radial-gradient(1400px 600px at 50% -100px, rgba(6,182,212,0.05), transparent 60%), linear-gradient(to bottom, var(--color-bg-glow), var(--color-bg))",
        backgroundAttachment: "fixed",
      }}
    >
      <header className="sticky top-0 z-50 border-b border-rule bg-gradient-to-b from-surface to-bg">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 -bottom-px h-px"
          style={{
            background:
              "linear-gradient(to right, transparent, var(--color-beep) 18%, var(--color-beep) 22%, var(--color-rule-strong) 30%, var(--color-rule-strong) 70%, var(--color-beep) 78%, var(--color-beep) 82%, transparent)",
            opacity: 0.55,
          }}
        />
        <div className="flex items-center gap-6 px-7 py-3.5">
          <div className="flex items-center gap-2.5">
            <Brand variant="compact" />
            <span
              aria-hidden
              className="size-1.5 rounded-full bg-beep"
              style={{
                boxShadow: "0 0 8px rgba(6,182,212,0.4)",
                animation: "splitsmith-heartbeat 2.4s ease-in-out infinite",
              }}
            />
          </div>
          <ModeSwitch size="sm" />
          <ModelChip model={model} />
          <div className="flex-1" />
          <IconButton variant="subtle" size="md" label="Help">
            <HelpCircle className="size-[18px]" />
          </IconButton>
          <IconButton variant="subtle" size="md" label="Notifications">
            <Bell className="size-[18px]" />
          </IconButton>
          <IconButton variant="subtle" size="md" label="Settings">
            <Settings className="size-[18px]" />
          </IconButton>
        </div>
        <div className="border-t border-rule bg-bg">
          <div className="flex items-center gap-3 px-7 py-2.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-subtle">
            <button
              type="button"
              // Replace: clicking the home breadcrumb also flips mode
              // back to match (the shell remount triggers it). A push
              // would leave /dev/corpus in history with the mode state
              // already flipped, so back would land somewhere weird.
              onClick={() => navigate("/", { replace: true })}
              className="text-subtle hover:text-ink-2"
            >
              Splitsmith
            </button>
            <span className="text-whisper">/</span>
            <span className="text-subtle">Developer</span>
            <span className="text-whisper">/</span>
            <span className="font-bold text-ink">
              {activeIdx >= 0 ? STEPS[activeIdx].label : "Workspace"}
            </span>
          </div>
        </div>
      </header>

      <div className="flex min-h-[calc(100vh-86px)]">
        <DeveloperSidebar model={model} activeIdx={activeIdx} />
        <div className="min-w-0 flex-1">
          <Outlet
            context={{
              model,
              refresh: () => setRefreshKey((k) => k + 1),
            }}
          />
        </div>
      </div>
    </div>
  );
}

function ModelChip({ model }: { model: DeveloperModelInfo | null }) {
  return (
    <div
      className="inline-flex items-center gap-2.5 rounded-md border px-3.5 py-1.5 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-ink-2 tabular-nums"
      style={{
        background: "linear-gradient(90deg, var(--color-beep-tint), transparent)",
        borderColor: "rgba(6,182,212,0.4)",
      }}
    >
      <Zap className="size-3.5 text-beep" />
      <span className="font-bold text-beep">Active</span>
      <span className="text-whisper">/</span>
      <span className="font-bold text-ink">{model?.active_version ?? "--"}</span>
      <span className="text-whisper">/</span>
      <span>{model ? `${model.fixture_count} fix` : "--"}</span>
      <span className="text-whisper">/</span>
      <span>
        recall <b className="font-bold text-done">{model ? model.recall.toFixed(2) : "--"}</b>
      </span>
    </div>
  );
}

function DeveloperSidebar({
  model,
  activeIdx,
}: {
  model: DeveloperModelInfo | null;
  activeIdx: number;
}) {
  return (
    <aside className="sticky top-0 flex h-[calc(100vh-86px)] w-[248px] shrink-0 flex-col overflow-y-auto border-r border-rule bg-surface px-3 py-4">
      <div className="relative mb-3.5 border-b border-rule px-3 pb-4">
        <span
          aria-hidden
          className="absolute left-0 top-0.5 h-4 w-0.5 rounded-sm bg-beep"
          style={{ boxShadow: "0 0 8px rgba(6,182,212,0.4)" }}
        />
        <div className="mb-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.2em] text-beep">
          Detector training
        </div>
        <div className="mb-1 font-display text-[0.9375rem] font-bold uppercase leading-tight tracking-tight text-ink">
          Ensemble pipeline
        </div>
        <div className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted">
          recall{" "}
          <b className="font-bold text-done">
            {model ? model.recall.toFixed(2) : "--"}
          </b>{" "}
          / {model ? model.fixture_count : "--"} fixtures
        </div>
      </div>

      <div className="px-2 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
        Workflow
      </div>
      <nav className="flex flex-col">
        {STEPS.map((step, i) => {
          const done = activeIdx > i;
          const active = activeIdx === i;
          const count = model?.step_counts[step.countKey] ?? null;
          return (
            <div key={step.to} className="relative">
              <NavLink
                to={step.to}
                className={cn(
                  "grid min-h-10 grid-cols-[28px_1fr_auto] items-center gap-2.5 rounded-md px-2.5 py-2 text-[0.8125rem] font-medium transition-colors",
                  active
                    ? "border border-[rgba(6,182,212,0.4)] bg-[color:var(--color-beep-tint)] px-[9px] font-bold text-beep"
                    : done
                      ? "text-ink-2 hover:bg-surface-2"
                      : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                )}
              >
                <span
                  className={cn(
                    "inline-flex size-6 items-center justify-center rounded-md border font-mono text-[0.625rem] font-bold tabular-nums",
                    active
                      ? "border-beep bg-beep text-bg shadow-[0_0_0_1px_var(--color-beep),0_0_8px_var(--color-beep-glow)]"
                      : done
                        ? "border-transparent bg-[color:var(--color-done-tint)] text-done"
                        : "border-transparent bg-surface-3 text-muted",
                  )}
                >
                  {done ? <Check className="size-3" /> : step.num}
                </span>
                <span className="truncate">{step.label}</span>
                {count !== null && count > 0 && (
                  <span
                    className={cn(
                      "rounded px-1.5 py-0.5 font-mono text-[0.625rem] font-bold tabular-nums",
                      active
                        ? "bg-beep/20 text-beep"
                        : count > 0 && step.countKey === "review"
                          ? "bg-[color:var(--color-live-tint)] text-live"
                          : "bg-surface-3 text-subtle",
                    )}
                  >
                    {count}
                  </span>
                )}
              </NavLink>
              {i < STEPS.length - 1 && (
                <span
                  aria-hidden
                  className={cn(
                    "absolute left-[22px] top-10 h-1.5 w-px",
                    done ? "bg-done/40" : "bg-rule",
                  )}
                />
              )}
            </div>
          );
        })}
      </nav>

      <div className="mt-6 px-2 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
        Tools
      </div>
      <div className="flex flex-col gap-px">
        <SubLink to="/dev/legacy/lab" icon={<FlaskConical className="size-[15px]" />} legacy>
          Lab playground
        </SubLink>
        <SubLink to="/dev/legacy/review" icon={<Inbox className="size-[15px]" />} legacy>
          Fixture review
        </SubLink>
        <SubLink to="/dev/history" icon={<Clock className="size-[15px]" />}>
          Build history
        </SubLink>
        <SubLink to="/dev/datasets" icon={<Database className="size-[15px]" />}>
          Datasets
        </SubLink>
        <SubLink to="/_design" icon={<Layers className="size-[15px]" />}>
          Design system
        </SubLink>
      </div>

      <div className="flex-1" />

      {/* Jobs rail anchored at the bottom. Bleed past the aside's
          horizontal padding so the rail's top border spans the full
          width like the design kit's JobsRail. */}
      <div className="-mx-3">
        <JobsSurface
          collapsed={false}
          sidebarExpandedWidth={248}
          sidebarCollapsedWidth={56}
        />
      </div>
    </aside>
  );
}

function SubLink({
  to,
  icon,
  children,
  legacy,
}: {
  to: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  legacy?: boolean;
}) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "flex min-h-8 items-center gap-3 rounded-md px-2.5 py-1.5 text-[0.75rem] transition-colors",
          isActive ? "bg-surface-2 text-ink" : "text-muted hover:bg-surface-2 hover:text-ink-2",
        )
      }
    >
      <span className="inline-flex shrink-0 text-muted">{icon}</span>
      <span className="flex-1">{children}</span>
      {legacy && (
        <span className="inline-flex items-center rounded border border-[rgba(251,191,36,0.4)] bg-[color:var(--color-live-tint)] px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.1em] text-live">
          Legacy
        </span>
      )}
    </NavLink>
  );
}
