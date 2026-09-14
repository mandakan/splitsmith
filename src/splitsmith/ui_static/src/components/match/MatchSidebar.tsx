/* eslint-disable no-restricted-syntax -- visual budget: remove when this file is rebuilt (spec 2026-09-13 s5) */
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

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { JobsSurface } from "@/components/Jobs";
import { type StageStatus } from "@/lib/api";
import { type JobsState } from "@/lib/jobs";
import { countsAsDone } from "@/lib/stageStatus";
import { Label } from "@/components/ui/Label";
import { StageDot } from "@/components/ui/StageDot";
import { cn } from "@/lib/utils";
import { FOOTAGE_HINT, NAV_GROUP_LABEL, matchNavItems, type MatchNavGroup } from "./navItems";

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
  /** Jobs state owned by MatchShell - the shell polls once and both
   *  reacts to completions and feeds the footer rail from the same
   *  data (#663). */
  jobsState: JobsState;
  matchName: string;
  matchSubtitle?: ReactNode;
  stages: MatchSidebarStage[];
  /** Beeps still awaiting confirm/adjust across all shooters. Drives the
   *  badge on the Audit nav row -- when it's > 0 the row gains a
   *  count chip so the operator can see at a glance that there's work
   *  there. */
  beepReviewPendingCount?: number;
  /** Stages flagged for a closer look on desktop from the mobile Triage
   *  worklist. Drives the badge on the Triage nav row, mirroring
   *  ``beepReviewPendingCount``'s contract. */
  triageFlaggedCount?: number;
  /** When true the sidebar renders the "no footage yet" sub for the stage
   *  list (matches polished/17). Defaults to false. */
  awaiting?: boolean;
  /** True when any shooter on this match has at least one video attached.
   *  When false the footage-dependent nav rows (Audit, Coach, Videos,
   *  Export) render as disabled rows with an "attach footage first"
   *  hint instead of dead-ending the operator on an empty surface
   *  (#425). Defaults to true so callers that haven't been updated
   *  retain the previous behaviour. */
  hasFootage?: boolean;
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
  /** Match identifier for the canonical ``/match/:matchId/`` URL prefix
   *  (#353 Phase 3 PR B). When set the sidebar's nav rows include it so
   *  every click stays inside the match-scoped subtree. ``undefined`` on
   *  legacy bind contexts (no match id) -- in that case the bare paths
   *  are used and React Router's legacy routes pick them up. */
  matchId?: string;
  /** Collapsed state -- when true the sidebar renders at SIDEBAR_COLLAPSED_WIDTH
   *  with icon-only nav and the match card / stages list hidden. */
  collapsed?: boolean;
  onCollapseToggle?: () => void;
  /** Server/app version from /api/health - rendered as a footer line so
   *  the running version is always discoverable in the shell. */
  version?: string;
  className?: string;
}

export const SIDEBAR_EXPANDED_WIDTH = 240;
export const SIDEBAR_COLLAPSED_WIDTH = 56;

export function MatchSidebar({
  jobsState,
  matchName,
  matchSubtitle,
  stages,
  beepReviewPendingCount,
  triageFlaggedCount,
  awaiting = false,
  hasFootage = true,
  onStageClick,
  shooterSlug,
  matchId,
  collapsed = false,
  onCollapseToggle,
  version,
  className,
}: MatchSidebarProps) {
  // Footage-dependent rows share the same hint - centralised in
  // navItems so the copy cannot drift between sidebar and drawer.
  const footageHint = FOOTAGE_HINT;
  // Prefix every nav row with /match/:matchId when one is in scope, so
  // clicks keep the user inside the match-scoped subtree (#353 Phase 3).
  // Without a match id we fall back to the bare paths -- legacy routes
  // in App.tsx still resolve them, so this stays backwards compatible.
  const base = matchId ? `/match/${matchId}` : "";
  // Sidebar header shows audited / total. Only AUDITED stages count
  // toward the tally (skipped stays out of the numerator) -- the shared
  // ``countsAsDone`` rule keeps this in lockstep with the Home progress
  // cards, which this used to silently disagree with.
  const audited = stages.filter((s) => countsAsDone(s.status)).length;
  const total = stages.length;

  return (
    <aside
      data-collapsed={collapsed || undefined}
      style={{ width: collapsed ? SIDEBAR_COLLAPSED_WIDTH : SIDEBAR_EXPANDED_WIDTH }}
      className={cn(
        // Pin below the measured sticky header and fill the rest of the
        // viewport -- the old hard-coded 86px guess broke as soon as the
        // header wrapped, pushing the Jobs rail off-screen.
        "sticky top-[var(--shell-header-h,86px)] flex h-[calc(100dvh-var(--shell-header-h,86px))] shrink-0 flex-col overflow-y-auto border-r border-rule bg-surface py-2 transition-[width] duration-150",
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
        <div className="mx-3 mb-2 px-1 pb-3">
          {/* The sidebar's one Antonio use: the match name. The "Active
              match" kicker is gone -- the name under the brand is the
              context, and the breadcrumb repeats it. */}
          <div className="mb-1 font-display text-[17px] font-bold uppercase leading-tight tracking-tight text-ink">
            {matchName}
          </div>
          {matchSubtitle ? <Label>{matchSubtitle}</Label> : null}
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
        {matchNavItems({
          base,
          shooterSlug,
          hasFootage,
          beepReviewPendingCount: beepReviewPendingCount ?? 0,
          triageFlaggedCount: triageFlaggedCount ?? 0,
          footageHint,
        }).flatMap((item, i, items) => {
          // A phase label whenever the group changes (spec 2026-09-13
          // s3.2). Collapsed sidebars have no room for it.
          const prev: MatchNavGroup | undefined = i > 0 ? items[i - 1].group : undefined;
          const nodes: ReactNode[] = [];
          if (item.group && item.group !== prev && !collapsed) {
            nodes.push(
              <Label key={`group-${item.group}`} tone="subtle" className="mt-2 px-2.5 pb-1">
                {NAV_GROUP_LABEL[item.group]}
              </Label>,
            );
          }
          nodes.push(
            <SidebarLink
              key={item.key}
              to={item.to}
              icon={item.icon}
              end={item.end}
              collapsed={collapsed}
              disabled={item.disabled}
              disabledHint={item.disabledHint}
              count={item.count}
              badgeKind={item.badgeKind}
              badgeAriaLabel={item.badgeAriaLabel}
            >
              {item.label}
            </SidebarLink>,
          );
          return nodes;
        })}
      </div>

      {/* Stages -- hidden while collapsed. */}
      {collapsed ? null : (
        <div className="mx-3 mt-3 flex items-center justify-between px-2.5 py-1">
          <Label tone="subtle">Stages</Label>
          <span
            className="numeral text-[11px] text-ink-2"
            title={`${audited} of ${total} audited or skipped`}
          >
            {audited} / {total}
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
                  "grid w-full grid-cols-[22px_1fr_auto] items-center gap-2 rounded-md py-1.5 pl-2.5 pr-2.5 text-left text-[13px] transition-colors",
                  // Red marks the current position only (spec s5): an
                  // inset bar, not a tint plus red text.
                  isActive
                    ? "bg-surface-3 font-medium text-ink shadow-[inset_2px_0_0_var(--color-led)]"
                    : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                )}
                disabled={awaiting}
              >
                {/* Stage ordinals keep their leading zero: the one place
                    a padded number belongs (spec s5). */}
                <span className="font-mono text-[11px] tabular-nums text-muted">
                  {pad2(stage.stage_number)}
                </span>
                <span className="truncate">{stage.stage_name}</span>
                <span className="inline-flex items-center gap-2">
                  {isNextUp ? (
                    <Label aria-hidden tone="subtle" className="text-[10px]">
                      next
                    </Label>
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
        state={jobsState}
        collapsed={collapsed}
        sidebarExpandedWidth={SIDEBAR_EXPANDED_WIDTH}
        sidebarCollapsedWidth={SIDEBAR_COLLAPSED_WIDTH}
      />
      {!collapsed && version ? (
        <div className="px-5 pb-3 pt-1 font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-subtle">
          Splitsmith v{version}
        </div>
      ) : null}
    </aside>
  );
}

function SidebarLink({
  to,
  icon,
  count,
  badgeKind = "count",
  badgeAriaLabel,
  end,
  collapsed,
  disabled = false,
  disabledHint,
  children,
}: {
  to: string;
  icon: ReactNode;
  count?: number;
  /** ``count`` -- entity tally (Shooters), square neutral badge, always
   *  visible while ``count`` is defined. ``pending`` -- positive work
   *  queue (Beep review), cyan pill+dot, hides at zero. */
  badgeKind?: "count" | "pending";
  /** Accessible name for the expanded badge pill when a bare number
   *  isn't descriptive enough (not color-only). Undefined leaves the
   *  badge announced as its visible digits, same as before. */
  badgeAriaLabel?: string;
  end?: boolean;
  collapsed: boolean;
  /** Renders as a non-interactive muted row with ``disabledHint`` as a
   *  tooltip. Used for footage-dependent surfaces when no shooter has
   *  any video attached yet (#425): clicking would dead-end, so the
   *  empty state is the row itself. */
  disabled?: boolean;
  disabledHint?: string;
  children: ReactNode;
}) {
  const { pathname } = useLocation();
  // NavLink's "end" handles the index case; for nested matches like
  // /audit/3 we still want /audit to be active.
  const isActive = end ? pathname === to : pathname.startsWith(to);
  // Pending badges hide at zero; count badges only render when defined.
  const showBadge =
    typeof count === "number" && (badgeKind === "pending" ? count > 0 : true);
  if (disabled) {
    if (collapsed) {
      return (
        <span
          aria-disabled="true"
          title={disabledHint}
          className="relative flex h-9 cursor-not-allowed items-center justify-center rounded-md text-subtle opacity-60"
        >
          <span className="inline-flex">{icon}</span>
        </span>
      );
    }
    return (
      <span
        aria-disabled="true"
        title={disabledHint}
        className="flex min-h-9 cursor-not-allowed items-center gap-3 rounded-md border border-transparent px-2.5 py-2 text-[0.8125rem] font-medium text-subtle opacity-60"
      >
        <span className="inline-flex shrink-0 text-subtle">{icon}</span>
        <span>{children}</span>
      </span>
    );
  }
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
            ? "bg-surface-3 text-ink"
            : "text-muted hover:bg-surface-2 hover:text-ink",
        )}
      >
        {isActive ? (
          <span
            aria-hidden
            className="absolute -left-px top-1/2 h-[18px] w-[2px] -translate-y-1/2 rounded-sm bg-led"
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
        "flex min-h-9 items-center gap-3 rounded-md px-2.5 py-2 text-[13px] transition-colors",
        // Red marks the current position only (spec s5).
        isActive
          ? "bg-surface-3 font-medium text-ink shadow-[inset_2px_0_0_var(--color-led)]"
          : "text-ink-2 hover:bg-surface-2 hover:text-ink",
      )}
    >
      <span
        className={cn(
          "inline-flex shrink-0",
          isActive ? "text-ink" : "text-muted group-hover:text-ink",
        )}
      >
        {icon}
      </span>
      <span>{children}</span>
      {showBadge ? (
        <span
          aria-label={badgeAriaLabel}
          className={cn(
            "ml-auto",
            badgeKind === "pending" ? "badge-pending" : "badge-count",
          )}
        >
          {count}
        </span>
      ) : null}
    </NavLink>
  );
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}
