/**
 * MatchShell -- Shot Timer page chrome for any Match-mode surface (#323).
 *
 * Wraps the page in the polished Shot Timer header + a per-match
 * sidebar built from the currently-bound project. Routes children
 * via <Outlet/> so each surface (Overview, Audit, Compare, ...) owns
 * its own content area but shares the same chrome.
 *
 * Carries the bound-check that AppShell used to do: when /api/health
 * reports unbound, redirect to /pick. Background jobs surface in the
 * sidebar footer rail (v2 audit chrome -- no more floating FAB).
 */

import { HelpCircle, Repeat, Settings } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";

import { ShooterChipStrip } from "@/components/match/ShooterChipStrip";
import { Brand, IconButton } from "@/components/ui";
import {
  MatchSidebar,
  type MatchSidebarStage,
} from "@/components/match/MatchSidebar";
import {
  api,
  type MatchProject,
  type ScoreboardIdentity,
  type ServerHealth,
  type ShooterListEntry,
} from "@/lib/api";
import { useMode } from "@/lib/mode";
import { deriveStageStatus, isNextUpCandidate } from "@/lib/stageStatus";
import { cn } from "@/lib/utils";

const SIDEBAR_COLLAPSE_KEY = "splitsmith.matchshell.sidebarCollapsed";

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
  const { slug, matchId: urlMatchId } = useParams<{
    slug?: string;
    matchId?: string;
  }>();
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
  // activeMeaning kicker for the shell-level shooter strip. Names what
  // "active" means on this page: "Editing" on Audit / Ingest / Export,
  // "Coaching" on Coach. Per Shell - Active shooter.html in the design
  // bundle: "the kicker is the entire IA decision in 7 chars".
  const shooterStripLabel = useMemo<string | null>(() => {
    if (pathname.startsWith("/coach")) return "Coaching";
    if (pathname.startsWith("/audit")) return "Editing";
    if (pathname.startsWith("/export")) return "Editing";
    if (pathname.startsWith("/ingest") || pathname.startsWith("/videos"))
      return "Editing";
    return null;
  }, [pathname]);
  // Sidebar collapse state -- persisted so the operator's choice survives
  // reloads. The Audit page (waveform + docked MultiCamColumn) benefits
  // from collapsing once and staying collapsed.
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
        /* private mode etc -- in-memory only is fine */
      }
      return next;
    });
  }, []);

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
  const [beepReviewPending, setBeepReviewPending] = useState<number>(0);
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
  // every endpoint failing and the jobs rail silently empty.
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
          // Beep-review pending count drives the sidebar badge so the
          // operator can spot pending beep work without opening the
          // page. Cheap GET; refresh on every shell load.
          api
            .getBeepQueue()
            .then((q) => {
              if (alive) setBeepReviewPending(q.pending_count);
            })
            .catch(() => {
              if (alive) setBeepReviewPending(0);
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

  // Currently-viewed stage, parsed from the URL. The shell mounts
  // above several stage-bearing routes (/audit/:slug/:stage,
  // /coach/:slug/:stage, /compare/:stage); a trailing integer segment
  // disambiguates which stage the operator is looking at so the
  // sidebar can mark that row as ``active`` rather than relying on
  // the ``next_up`` heuristic. Returns ``null`` for non-stage routes
  // (/shooters, /beep-review) so the sidebar falls back to next_up.
  const activeStageFromUrl = useMemo<number | null>(() => {
    const trailing = pathname.split("/").filter(Boolean).pop();
    if (!trailing) return null;
    const n = Number(trailing);
    return Number.isFinite(n) && n > 0 ? n : null;
  }, [pathname]);

  const stages: MatchSidebarStage[] = useMemo(() => {
    if (!project) return [];
    // Status comes from the backend (single source of truth). Pick
    // "next up" as the first non-terminal stage so the sidebar's
    // next-up hint tracks audit progress -- audited and skipped
    // stages are closed out, everything else is fair game. The
    // ``active`` row (the stage whose URL we're currently on) wins
    // visually over ``next_up`` so the sidebar tells the truth about
    // "you are here" before "you should go here next".
    const stagesWithStatus = project.stages.map((s) => ({
      stage_number: s.stage_number,
      stage_name: s.stage_name || `Stage ${s.stage_number}`,
      status: deriveStageStatus(s),
    }));
    const nextIdx = stagesWithStatus.findIndex((s) =>
      isNextUpCandidate(s.status),
    );
    return stagesWithStatus.map((s, i) => ({
      ...s,
      next_up: i === nextIdx,
      active: s.stage_number === activeStageFromUrl,
    }));
  }, [project, activeStageFromUrl]);

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
              label={shooterStripLabel}
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
          beepReviewPendingCount={beepReviewPending}
          awaiting={
            stages.length > 0 && stages.every((s) => s.status === "todo")
          }
          onStageClick={(n) => {
            const target = slug ?? health?.default_shooter_slug;
            const mid = urlMatchId ?? health?.match_id ?? null;
            const base = mid ? `/match/${mid}` : "";
            navigate(target ? `${base}/audit/${target}/${n}` : `${base}/shooters`);
          }}
          shooterSlug={slug ?? health?.default_shooter_slug ?? undefined}
          matchId={urlMatchId ?? health?.match_id ?? undefined}
          collapsed={sidebarCollapsed}
          onCollapseToggle={toggleSidebar}
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
    </div>
  );
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
