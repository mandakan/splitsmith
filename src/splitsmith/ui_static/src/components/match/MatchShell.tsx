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

import { HelpCircle, Repeat, Settings } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";

import { JobsPanel } from "@/components/JobsPanel";
import { ShooterChipStrip } from "@/components/match/ShooterChipStrip";
import { Brand, IconButton } from "@/components/ui";
import {
  MatchSidebar,
  type MatchSidebarStage,
  type StageStatus,
} from "@/components/match/MatchSidebar";
import {
  api,
  type MatchProject,
  type ScoreboardIdentity,
  type ServerHealth,
  type ShooterListEntry,
} from "@/lib/api";
import { useMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

export interface MatchShellOutletContext {
  project: MatchProject | null;
  health: ServerHealth | null;
  shooters: ShooterListEntry[];
  refresh: () => void;
}

export function MatchShell() {
  const navigate = useNavigate();
  // The shell mounts above shooter-scoped routes (/audit/:slug, /coach/:slug,
  // /export/:slug) and slug-less routes (/, /shooters, /beep-review,
  // /compare/:stage). When a slug is in the URL we load that shooter's
  // project so the sidebar reflects their progress; otherwise the sidebar
  // shows match-level info without per-stage status.
  const { slug } = useParams<{ slug?: string }>();
  const { pathname } = useLocation();
  const { mode, setMode } = useMode();
  // Trailing breadcrumb segment ("AUDIT" / "COACH" / ...) derived from the
  // current URL. The current-view label is the only segment shown in LED
  // red; everything else stays in the muted breadcrumb tone.
  const viewLabel = useMemo<string | null>(() => {
    if (pathname.startsWith("/audit")) return "Audit";
    if (pathname.startsWith("/coach")) return "Coach";
    if (pathname.startsWith("/compare")) return "Compare";
    if (pathname.startsWith("/export")) return "Export";
    if (pathname.startsWith("/ingest") || pathname.startsWith("/videos"))
      return "Videos";
    if (pathname.startsWith("/beep-review")) return "Beep review";
    if (pathname.startsWith("/shooters")) return "Shooters";
    return null;
  }, [pathname]);
  const [didInitMode, setDidInitMode] = useState(false);
  useEffect(() => {
    if (!didInitMode) {
      if (mode !== "match") setMode("match");
      setDidInitMode(true);
      return;
    }
    // Replace, not push: see DeveloperShell. A mode flip is a side
    // effect of clicking the toggle, not a forward navigation -- back
    // should return to whatever was before the user opened the match,
    // not bounce between modes.
    if (mode === "developer") navigate("/dev/corpus", { replace: true });
  }, [mode, setMode, didInitMode, navigate]);

  const [health, setHealth] = useState<ServerHealth | null>(null);
  const [project, setProject] = useState<MatchProject | null>(null);
  const [shooters, setShooters] = useState<ShooterListEntry[]>([]);
  const [identity, setIdentity] = useState<ScoreboardIdentity | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const shooterCount = shooters.length || undefined;

  useEffect(() => {
    let alive = true;
    api
      .getScoreboardIdentity()
      .then((id) => {
        if (alive) setIdentity(id);
      })
      .catch(() => {
        if (alive) setIdentity(null);
      });
    return () => {
      alive = false;
    };
  }, []);

  // Server-state drift recovery: when ANY request returns 409 ``no_project``
  // (typical cause: dev server restart wiped the in-memory bind state),
  // ``api.ts`` fires this custom event. We bump ``refreshKey`` so the
  // health-load effect re-runs, sees ``bound: false``, and the redirect
  // below sends the user to /pick. Without this, the page sits with
  // every endpoint failing and the JobsPanel silently empty.
  useEffect(() => {
    const onNoProject = () => setRefreshKey((k) => k + 1);
    window.addEventListener("splitsmith:no-project", onNoProject);
    return () =>
      window.removeEventListener("splitsmith:no-project", onNoProject);
  }, []);

  useEffect(() => {
    let alive = true;
    api
      .getHealth()
      .then((h) => {
        if (alive) setHealth(h);
        if (h?.bound) {
          // Sidebar stage list needs *some* shooter's project to render
          // status. URL slug wins; otherwise fall back to the server's
          // default shooter (alphabetically-first match shooter, or the
          // legacy slug for single-shooter projects).
          const fetchSlug = slug ?? h.default_shooter_slug ?? null;
          if (fetchSlug) {
            api
              .getProject(fetchSlug)
              .then((p) => {
                if (alive) setProject(p);
              })
              .catch(() => {
                if (alive) setProject(null);
              });
          } else {
            setProject(null);
          }
          api
            .listMatchShooters()
            .then((r) => {
              if (alive) setShooters(r.shooters);
            })
            .catch(() => {
              // 409 no_match when the bound project is a standalone legacy
              // single-shooter project (not a Match). Leave empty.
              if (alive) setShooters([]);
            });
        }
      })
      .catch(() => {
        if (alive) setHealth(null);
      });
    return () => {
      alive = false;
    };
  }, [refreshKey, slug]);

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
    // Replace: project just unbound, so the page we came from would
    // bounce us back to /pick anyway via the bound-check redirect.
    navigate("/pick", { replace: true });
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
        <div className="flex flex-wrap items-center gap-4 px-7 py-3">
          <Brand variant="compact" />
          <nav
            aria-label="Breadcrumb"
            className="inline-flex items-center gap-2 font-display text-[0.8125rem] font-bold uppercase tracking-[0.06em]"
          >
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                // Replace so that picking a different match in /pick
                // and hitting back doesn't return to a stage URL whose
                // data now belongs to a different project (confusing).
                navigate("/pick", { replace: true });
              }}
              className="text-ink-2 transition-colors hover:text-ink"
            >
              Matches
            </a>
            <span aria-hidden className="text-rule-strong">
              /
            </span>
            <span className="text-ink-2">
              {health?.project_name ?? "..."}
            </span>
            {viewLabel ? (
              <>
                <span aria-hidden className="text-rule-strong">
                  /
                </span>
                <span className="text-led">{viewLabel}</span>
              </>
            ) : null}
          </nav>
          {shooters.length > 1 ? (
            <ShooterChipStrip
              shooters={shooters}
              activeSlug={slug}
              urlBase={breadcrumbUrlBase(pathname)}
              label={null}
              variant="inline"
            />
          ) : null}
          <div className="flex-1" />
          <IconButton variant="subtle" size="md" label="Help">
            <HelpCircle className="size-[18px]" />
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
            {identity?.display_name && (
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
                {userInitials(identity.display_name)}
              </span>
            )}
            <span>{identity?.display_name ?? "Switch project"}</span>
            <Repeat className="size-3.5 text-subtle" />
          </button>
        </div>
      </header>

      <div className="flex min-h-[calc(100vh-64px)]">
        <MatchSidebar
          matchName={project?.name ?? health?.project_name ?? "..."}
          matchSubtitle={renderMatchSubtitle(project)}
          stages={stages}
          shooterCount={shooterCount}
          awaiting={
            stages.length > 0 && stages.every((s) => s.status === "todo")
          }
          onStageClick={(n) => {
            const target = slug ?? health?.default_shooter_slug;
            navigate(target ? `/audit/${target}/${n}` : "/shooters");
          }}
          shooterSlug={slug ?? health?.default_shooter_slug ?? undefined}
        />
        <div className={cn("min-w-0 flex-1")}>
          <Outlet
            context={{
              project,
              health,
              shooters,
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

/** Map the current pathname to the route prefix the inline ShooterChipStrip
 *  should link to. Strips ahead of the slug + stage so flipping shooters
 *  keeps the operator on the same view. */
function breadcrumbUrlBase(
  pathname: string,
): "audit" | "ingest" | "coach" | "export" {
  if (pathname.startsWith("/coach")) return "coach";
  if (pathname.startsWith("/export")) return "export";
  if (pathname.startsWith("/ingest") || pathname.startsWith("/videos"))
    return "ingest";
  return "audit";
}

function userInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
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
