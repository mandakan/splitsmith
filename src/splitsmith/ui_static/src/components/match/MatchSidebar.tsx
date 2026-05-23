/**
 * MatchSidebar -- the per-match sidebar shared by every Match-mode surface.
 *
 * Three zones:
 *   1. Match card at top: kicker + title + meta line (date · club).
 *   2. Cross-match nav: Overview / Audit / Coach / ... .
 *   3. Stages list with per-stage status dots, with a "next up" callout
 *      for the first non-audited stage.
 *
 * v2 audit chrome: the sidebar is collapsible (240 -> 56). Collapsed it
 * renders icon-only nav and hides the match card / stages list, buying
 * the audit page horizontal width for its docked MultiCamColumn. The
 * JobsRail (background job activity) mounts in the footer regardless of
 * collapsed state.
 */

import {
  ArrowDownToLine,
  ClipboardCheck,
  Crosshair,
  Film,
  LayoutGrid,
  PanelLeftClose,
  PanelLeftOpen,
  Users,
  Volume2,
} from "lucide-react";
import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { JobsSurface } from "@/components/Jobs";
import { type StageStatus } from "@/lib/api";
import { StageDot } from "@/components/ui/StageDot";
import { cn } from "@/lib/utils";

// The sidebar consumes the canonical :type:`StageStatus` from the
// backend. The previous local narrow union ("done" | "partial" |
// "flagged" | "todo") drifted into a tone, not a status, and got
// duplicated wherever the home / chip-strip / sidebar needed to
// classify stages. Status lives in one place now; visual tone is
// derived from it inside ``StageDot``.
export type { StageStatus };

export interface MatchSidebarStage {
  stage_number: number;
  stage_name: string;
  status: StageStatus;
  /** First non-terminal stage in the project. Subtle "next up" hint --
   *  beaten visually by ``active`` so the sidebar tells "you are here"
   *  before "you should go here next". */
  next_up?: boolean;
  /** The stage whose route the operator is currently on. Drives the
   *  primary "you are here" treatment in the stages list. */
  active?: boolean;
}

interface MatchSidebarProps {
  matchName: string;
  matchSubtitle?: ReactNode;
  matchKicker?: string;
  stages: MatchSidebarStage[];
  /** Optional shooter count to render in the Shooters nav row. */
  shooterCount?: number;
  /** Beeps still awaiting confirm/adjust across all shooters. Drives the
   *  badge on the Beep review nav row -- when it's > 0 the row gains a
   *  count chip so the operator can see at a glance that there's work
   *  there. */
  beepReviewPendingCount?: number;
  /** When true the sidebar renders the "no footage yet" sub for the stage
   *  list (matches polished/17). Defaults to false. */
  awaiting?: boolean;
  /** Per-stage click handler. Receives the stage number; the surface that
   *  owns the sidebar decides where to route (audit, compare, ...). */
  onStageClick?: (stage_number: number) => void;
  /** Slug for the shooter currently in focus (when a shooter-scoped route
   *  is active). Drives the per-shooter nav links so clicking Audit /
   *  Coach / Export keeps the user on the same shooter instead of
   *  bouncing to the shooter picker. ``undefined`` when no shooter is in
   *  focus (e.g. /shooters, /); in that case the per-shooter nav rows
   *  point at /shooters so the user picks one. */
  shooterSlug?: string;
  /** Collapsed state -- when true the sidebar renders at COLLAPSED_WIDTH
   *  with icon-only nav and the match card / stages list hidden. */
  collapsed?: boolean;
  onCollapseToggle?: () => void;
  className?: string;
}

const EXPANDED_WIDTH = 240;
const COLLAPSED_WIDTH = 56;

export function MatchSidebar({
  matchName,
  matchSubtitle,
  matchKicker = "Active match",
  stages,
  shooterCount,
  beepReviewPendingCount,
  awaiting = false,
  onStageClick,
  shooterSlug,
  collapsed = false,
  onCollapseToggle,
  className,
}: MatchSidebarProps) {
  // Sidebar header shows audited / total. Skipped stages count as
  // closed out (operator made a decision) but read as audited in the
  // tally; this matches the Home progress bar.
  const audited = stages.filter(
    (s) => s.status === "audited" || s.status === "skipped",
  ).length;
  const total = stages.length;

  return (
    <aside
      data-collapsed={collapsed || undefined}
      style={{ width: collapsed ? COLLAPSED_WIDTH : EXPANDED_WIDTH }}
      className={cn(
        "sticky top-0 flex h-[calc(100vh-86px)] shrink-0 flex-col overflow-y-auto border-r border-rule bg-surface py-2 transition-[width] duration-150",
        className,
      )}
    >
      {/* Collapse toggle row */}
      <div
        className={cn(
          "flex h-9 shrink-0 items-center px-2",
          collapsed ? "justify-center" : "justify-end",
        )}
      >
        <button
          type="button"
          onClick={onCollapseToggle}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="inline-flex size-7 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface-2 hover:text-ink"
        >
          {collapsed ? (
            <PanelLeftOpen className="size-4" aria-hidden />
          ) : (
            <PanelLeftClose className="size-4" aria-hidden />
          )}
        </button>
      </div>

      {/* Match card -- hidden while collapsed; not enough room for it. */}
      {collapsed ? null : (
        <div className="mx-3 mb-3 border-b border-rule px-1 pb-3.5">
          <div className="mb-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
            {matchKicker}
          </div>
          <div className="mb-1.5 font-display text-[0.9375rem] font-bold uppercase leading-tight tracking-tight text-ink">
            {matchName}
          </div>
          {matchSubtitle ? (
            <div className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted">
              {matchSubtitle}
            </div>
          ) : null}
        </div>
      )}

      {/* Cross-match nav. Per-shooter rows include the in-focus slug
       *  so navigation stays on the same shooter when one is active;
       *  without a slug they point to /shooters so the user picks. */}
      <div
        className={cn(
          "mb-1 flex flex-col gap-px",
          collapsed ? "px-2" : "px-3",
        )}
      >
        <SidebarLink
          to="/"
          icon={<LayoutGrid className="size-[15px]" />}
          end
          collapsed={collapsed}
        >
          Overview
        </SidebarLink>
        <SidebarLink
          to={shooterSlug ? `/audit/${shooterSlug}` : "/shooters"}
          icon={<Crosshair className="size-[15px]" />}
          collapsed={collapsed}
        >
          Audit
        </SidebarLink>
        <SidebarLink
          to={shooterSlug ? `/coach/${shooterSlug}` : "/shooters"}
          icon={<ClipboardCheck className="size-[15px]" />}
          collapsed={collapsed}
        >
          Coach
        </SidebarLink>
        <SidebarLink
          to="/shooters"
          icon={<Users className="size-[15px]" />}
          count={shooterCount}
          badgeKind="count"
          collapsed={collapsed}
        >
          Shooters
        </SidebarLink>
        <SidebarLink
          to={shooterSlug ? `/ingest/${shooterSlug}` : "/shooters"}
          icon={<Film className="size-[15px]" />}
          collapsed={collapsed}
        >
          Videos
        </SidebarLink>
        <SidebarLink
          to="/beep-review"
          icon={<Volume2 className="size-[15px]" />}
          count={beepReviewPendingCount}
          badgeKind="pending"
          collapsed={collapsed}
        >
          Beep review
        </SidebarLink>
        <SidebarLink
          to={shooterSlug ? `/export/${shooterSlug}` : "/shooters"}
          icon={<ArrowDownToLine className="size-[15px]" />}
          collapsed={collapsed}
        >
          Export
        </SidebarLink>
      </div>

      {/* Stages -- hidden while collapsed. */}
      {collapsed ? null : (
        <div className="mx-3 mt-2 flex items-center justify-between px-1 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
          Stages
          <span
            className="badge-count"
            title={`${audited} of ${total} audited or skipped`}
          >
            {pad2(audited)} / {pad2(total)}
          </span>
        </div>
      )}

      {!collapsed && awaiting ? (
        <div className="px-5 py-4 text-center">
          <div className="mb-1 inline-flex size-9 items-center justify-center rounded-md text-subtle">
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <rect x="3" y="6" width="18" height="12" rx="2" />
              <path d="M7 10l4 2-4 2v-4z" />
            </svg>
          </div>
          <div className="mb-1 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink-2">
            No footage yet
          </div>
          <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            Stages wake up once a shooter has videos assigned.
          </div>
        </div>
      ) : null}

      {collapsed ? null : (
        <div
          className={cn(
            "mx-3 flex flex-col gap-px px-1",
            awaiting && "mt-1 opacity-50",
          )}
        >
          {stages.map((stage) => {
            // ``active`` (current URL) is the primary highlight: filled
            // red badge + LED text. ``next_up`` becomes a subtle hint
            // (outlined badge, "next" mono tag) so it never competes
            // with the "you are here" treatment.
            const isActive = !!stage.active;
            const isNextUp = !!stage.next_up && !isActive;
            return (
              <button
                key={stage.stage_number}
                type="button"
                onClick={() => onStageClick?.(stage.stage_number)}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "grid w-full grid-cols-[26px_1fr_auto] items-center gap-2 rounded-md py-1 pl-1.5 pr-2.5 text-left text-[0.8125rem] font-medium transition-colors",
                  isActive
                    ? "bg-led-tint text-led"
                    : isNextUp
                      ? "text-ink hover:bg-surface-2"
                      : "text-ink-2 hover:bg-surface-2",
                )}
                disabled={awaiting}
              >
                <span
                  className={cn(
                    "inline-flex size-[26px] items-center justify-center rounded-md font-mono text-[0.6875rem] font-bold tabular-nums",
                    isActive
                      ? "badge-led-fill border-transparent"
                      : isNextUp
                        ? "border border-led-deep bg-led-tint text-led-text"
                        : "border border-transparent bg-surface-3 text-ink-2",
                  )}
                >
                  {pad2(stage.stage_number)}
                </span>
                <span
                  className={cn(
                    "truncate",
                    isActive && "font-bold text-led",
                  )}
                >
                  {stage.stage_name}
                </span>
                <span className="inline-flex items-center gap-1.5">
                  {isNextUp ? (
                    <span
                      aria-hidden
                      className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.12em] text-led-text"
                    >
                      next
                    </span>
                  ) : null}
                  <StageDot status={stage.status} />
                </span>
              </button>
            );
          })}
        </div>
      )}

      <div className="flex-1" />

      <JobsSurface
        collapsed={collapsed}
        sidebarExpandedWidth={EXPANDED_WIDTH}
        sidebarCollapsedWidth={COLLAPSED_WIDTH}
      />
    </aside>
  );
}

function SidebarLink({
  to,
  icon,
  count,
  badgeKind = "count",
  end,
  collapsed,
  children,
}: {
  to: string;
  icon: ReactNode;
  count?: number;
  /** ``count`` -- entity tally (Shooters), square neutral badge, always
   *  visible while ``count`` is defined. ``pending`` -- positive work
   *  queue (Beep review), cyan pill+dot, hides at zero. */
  badgeKind?: "count" | "pending";
  end?: boolean;
  collapsed: boolean;
  children: ReactNode;
}) {
  const { pathname } = useLocation();
  // NavLink's "end" handles the index case; for nested matches like
  // /audit/3 we still want /audit to be active.
  const isActive = end ? pathname === to : pathname.startsWith(to);
  // Pending badges hide at zero; count badges only render when defined.
  const showBadge =
    typeof count === "number" && (badgeKind === "pending" ? count > 0 : true);
  if (collapsed) {
    return (
      <NavLink
        to={to}
        end={end}
        title={typeof children === "string" ? (children as string) : undefined}
        aria-label={typeof children === "string" ? (children as string) : undefined}
        className={cn(
          "relative flex h-9 items-center justify-center rounded-md transition-colors",
          isActive
            ? "bg-led-tint text-led"
            : "text-muted hover:bg-surface-2 hover:text-ink",
        )}
      >
        {isActive ? (
          <span
            aria-hidden
            className="absolute -left-px top-1/2 h-[18px] w-[3px] -translate-y-1/2 rounded-sm bg-led shadow-[0_0_8px_var(--color-led-glow)]"
          />
        ) : null}
        <span className="inline-flex">{icon}</span>
        {showBadge ? (
          <span
            aria-hidden
            className={cn(
              "absolute right-1 top-1 inline-block size-[7px] rounded-full",
              badgeKind === "pending"
                ? "bg-beep shadow-[0_0_6px_var(--color-beep-glow)]"
                : "bg-rule-strong",
            )}
          />
        ) : null}
      </NavLink>
    );
  }
  return (
    <NavLink
      to={to}
      end={end}
      className={cn(
        "flex min-h-9 items-center gap-3 rounded-md px-2.5 py-2 text-[0.8125rem] font-medium transition-colors",
        isActive
          ? "border border-led-deep bg-[color:var(--color-led-tint)] px-[9px] font-bold text-led"
          : "border border-transparent text-ink-2 hover:bg-surface-2 hover:text-ink",
      )}
    >
      <span
        className={cn(
          "inline-flex shrink-0 text-muted",
          isActive ? "text-led" : "group-hover:text-ink",
        )}
      >
        {icon}
      </span>
      <span>{children}</span>
      {showBadge ? (
        <span
          className={cn(
            "ml-auto",
            badgeKind === "pending" ? "badge-pending" : "badge-count",
          )}
        >
          {pad2(count!)}
        </span>
      ) : null}
    </NavLink>
  );
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}
