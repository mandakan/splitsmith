/**
 * MatchShell -- Shot Timer page chrome for any Match-mode surface (#323).
 *
 * Wraps the page in the polished Shot Timer header + a per-match
 * sidebar built from the currently-bound project. Routes children
 * via <Outlet/> so each surface (Overview, Audit, Compare, ...) owns
 * its own content area but shares the same chrome.
 *
 * Carries the bound-check that AppShell used to do: when /api/health
 * reports unbound, redirect to /pick. JobsPanel mounts here too so
 * background work is visible across every match surface.
 */

import { Bell, HelpCircle, Repeat, Settings } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Navigate, Outlet, useNavigate } from "react-router-dom";

import { JobsPanel } from "@/components/JobsPanel";
import {
  Brand,
  IconButton,
  ModeSwitch,
} from "@/components/ui";
import {
  MatchSidebar,
  type MatchSidebarStage,
  type StageStatus,
} from "@/components/match/MatchSidebar";
import { api, type MatchProject, type ServerHealth } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface MatchShellOutletContext {
  project: MatchProject | null;
  health: ServerHealth | null;
  refresh: () => void;
}

export function MatchShell() {
  const navigate = useNavigate();
  const [health, setHealth] = useState<ServerHealth | null>(null);
  const [project, setProject] = useState<MatchProject | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let alive = true;
    api
      .getHealth()
      .then((h) => {
        if (alive) setHealth(h);
        if (h?.bound) {
          api
            .getProject()
            .then((p) => {
              if (alive) setProject(p);
            })
            .catch(() => {
              if (alive) setProject(null);
            });
        }
      })
      .catch(() => {
        if (alive) setHealth(null);
      });
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  const stages: MatchSidebarStage[] = useMemo(() => {
    if (!project) return [];
    // Find the "next up" stage: first stage with no time but not skipped,
    // or first partial. Mirrors the polished design's intent.
    const stagesWithStatus = project.stages.map((s) => ({
      stage_number: s.stage_number,
      stage_name: s.stage_name || `Stage ${s.stage_number}`,
      status: classifyStage(s),
    }));
    const nextIdx = stagesWithStatus.findIndex(
      (s) => s.status === "partial" || s.status === "todo",
    );
    return stagesWithStatus.map((s, i) => ({
      ...s,
      next_up: i === nextIdx,
    }));
  }, [project]);

  if (health && !health.bound) {
    return <Navigate to="/pick" replace />;
  }

  async function switchProject() {
    try {
      await api.unbindProject();
    } catch {
      /* best-effort */
    }
    navigate("/pick");
  }

  return (
    <div
      className="min-h-screen text-ink"
      style={{
        backgroundImage:
          "radial-gradient(1400px 600px at 50% -100px, rgba(255,45,45,0.04), transparent 60%), linear-gradient(to bottom, var(--color-bg-glow), var(--color-bg))",
        backgroundAttachment: "fixed",
      }}
    >
      <header className="sticky top-0 z-50 border-b border-rule bg-gradient-to-b from-surface to-bg">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 -bottom-px h-px"
          style={{
            background:
              "linear-gradient(to right, transparent, var(--color-led) 18%, var(--color-led) 22%, var(--color-rule-strong) 30%, var(--color-rule-strong) 70%, var(--color-led) 78%, var(--color-led) 82%, transparent)",
            opacity: 0.55,
          }}
        />
        <div className="flex items-center gap-7 px-7 py-3.5">
          <Brand
            variant="compact"
            serial={
              health?.bound && (
                <>
                  SS &middot; SESSION
                  <br />
                  <b className="font-semibold text-ink-2">
                    {health.project_name ?? ""}
                  </b>
                </>
              )
            }
          />
          <ModeSwitch size="sm" />
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
          <button
            type="button"
            onClick={switchProject}
            title="Switch project"
            className="inline-flex min-h-10 items-center gap-2.5 rounded-full border border-rule bg-surface-2 py-1 pl-1 pr-3.5 text-[0.8125rem] text-ink-2 transition-colors hover:bg-surface-3"
          >
            <span
              aria-hidden
              className="inline-flex size-7 items-center justify-center rounded-full font-mono text-[0.6875rem] font-bold text-ink"
              style={{
                background:
                  "linear-gradient(135deg, var(--color-led), var(--color-led-deep))",
                boxShadow:
                  "0 0 0 1px rgba(255,45,45,0.4), 0 0 12px var(--color-led-glow)",
              }}
            >
              MA
            </span>
            <span>Mathias Axell</span>
            <Repeat className="size-3.5 text-subtle" />
          </button>
        </div>
        <div className="border-t border-rule bg-bg">
          <div className="flex items-center gap-3 px-7 py-2.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-subtle">
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                navigate("/pick");
              }}
              className="text-subtle hover:text-ink-2"
            >
              Matches
            </a>
            <span className="text-whisper">/</span>
            <span className="font-bold text-ink">
              {health?.project_name ?? "..."}
            </span>
            <div className="ml-auto inline-flex items-center gap-4 text-[0.625rem] tracking-[0.14em] text-subtle">
              <span>
                Worker{" "}
                <b className="font-bold text-done">&#9679;</b> Local
              </span>
            </div>
          </div>
        </div>
      </header>

      <div className="flex min-h-[calc(100vh-86px)]">
        <MatchSidebar
          matchName={project?.name ?? health?.project_name ?? "..."}
          matchSubtitle={renderMatchSubtitle(project)}
          stages={stages}
          shooterCount={stages.length > 0 ? 1 : undefined}
          awaiting={
            stages.length > 0 && stages.every((s) => s.status === "todo")
          }
          onStageClick={(n) => navigate(`/audit/${n}`)}
        />
        <div className={cn("min-w-0 flex-1")}>
          <Outlet
            context={{
              project,
              health,
              refresh: () => setRefreshKey((k) => k + 1),
            }}
          />
        </div>
      </div>

      <JobsPanel />
    </div>
  );
}

function classifyStage(s: {
  time_seconds: number;
  videos: { role: string }[];
  skipped: boolean;
}): StageStatus {
  if (s.skipped) return "done";
  const hasVideos = (s.videos ?? []).some((v) => v.role === "primary");
  if (!hasVideos) return "todo";
  if (s.time_seconds > 0) return "done";
  return "partial";
}

function renderMatchSubtitle(project: MatchProject | null) {
  if (!project) return null;
  const bits: string[] = [];
  if (project.match_date) {
    bits.push(formatDateShort(project.match_date));
  }
  return bits.length > 0 ? <span>{bits.join(" · ")}</span> : null;
}

function formatDateShort(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return iso;
  const day = String(d.getUTCDate()).padStart(2, "0");
  const months = [
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
  ];
  return `${day} ${months[d.getUTCMonth()]}`;
}
