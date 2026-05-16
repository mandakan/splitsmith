/**
 * MatchSidebar -- the per-match sidebar shared by every Match-mode surface.
 *
 * Two zones:
 *   1. Match card at top: kicker + title + meta line (date · club).
 *   2. Cross-match nav: Overview / Coach / Shooters / Export.
 *   3. Stages list with per-stage status dots, with a "next up" callout
 *      for the first non-audited stage.
 *
 * Used by the redesigned MatchOverview (#323) today; other surfaces
 * (#327 audit, #328 compare, #329 coach, #330 export) will migrate from
 * the legacy AppShell sidebar to this one as they ship.
 */

import {
  ArrowDownToLine,
  ClipboardCheck,
  Crosshair,
  LayoutGrid,
  Users,
} from "lucide-react";
import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { cn } from "@/lib/utils";

export type StageStatus = "done" | "partial" | "flagged" | "todo";

export interface MatchSidebarStage {
  stage_number: number;
  stage_name: string;
  status: StageStatus;
  next_up?: boolean;
}

interface MatchSidebarProps {
  matchName: string;
  matchSubtitle?: ReactNode;
  matchKicker?: string;
  stages: MatchSidebarStage[];
  /** Optional shooter count to render in the Shooters nav row. */
  shooterCount?: number;
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
  className?: string;
}

export function MatchSidebar({
  matchName,
  matchSubtitle,
  matchKicker = "Active match",
  stages,
  shooterCount,
  awaiting = false,
  onStageClick,
  shooterSlug,
  className,
}: MatchSidebarProps) {
  const audited = stages.filter((s) => s.status === "done").length;
  const total = stages.length;

  return (
    <aside
      className={cn(
        "sticky top-0 flex h-[calc(100vh-86px)] w-[248px] shrink-0 flex-col overflow-y-auto border-r border-rule bg-surface px-3 py-4",
        className,
      )}
    >
      {/* Match card */}
      <div className="mb-3 border-b border-rule px-2 pb-3.5">
        <div className="mb-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
          {matchKicker}
        </div>
        <div className="mb-1.5 font-display text-[0.9375rem] font-bold uppercase leading-tight tracking-tight text-ink">
          {matchName}
        </div>
        {matchSubtitle && (
          <div className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted">
            {matchSubtitle}
          </div>
        )}
      </div>

      {/* Cross-match nav. Per-shooter rows include the in-focus slug
       *  so navigation stays on the same shooter when one is active;
       *  without a slug they point to /shooters so the user picks. */}
      <div className="mb-1 flex flex-col gap-px">
        <SidebarLink to="/" icon={<LayoutGrid className="size-[15px]" />} end>
          Overview
        </SidebarLink>
        <SidebarLink
          to={shooterSlug ? `/audit/${shooterSlug}` : "/shooters"}
          icon={<Crosshair className="size-[15px]" />}
        >
          Audit
        </SidebarLink>
        <SidebarLink
          to={shooterSlug ? `/coach/${shooterSlug}` : "/shooters"}
          icon={<ClipboardCheck className="size-[15px]" />}
        >
          Coach
        </SidebarLink>
        <SidebarLink
          to="/shooters"
          icon={<Users className="size-[15px]" />}
          count={shooterCount}
        >
          Shooters
        </SidebarLink>
        <SidebarLink
          to={shooterSlug ? `/export/${shooterSlug}` : "/shooters"}
          icon={<ArrowDownToLine className="size-[15px]" />}
        >
          Export
        </SidebarLink>
      </div>

      {/* Stages */}
      <div className="mt-2 flex items-center justify-between px-2 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
        Stages
        <span className="inline-flex items-center rounded border border-rule bg-surface-2 px-1.5 py-0.5 font-bold tabular-nums text-ink-2">
          {pad2(audited)} / {pad2(total)}
        </span>
      </div>

      {awaiting ? (
        <div className="px-3 py-4 text-center">
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

      <div className={cn("flex flex-col gap-px", awaiting && "mt-1 opacity-50")}>
        {stages.map((stage) => (
          <button
            key={stage.stage_number}
            type="button"
            onClick={() => onStageClick?.(stage.stage_number)}
            className={cn(
              "grid w-full grid-cols-[26px_1fr_14px] items-center gap-2 rounded-md py-1 pl-1.5 pr-2.5 text-left text-[0.8125rem] font-medium text-ink-2 transition-colors hover:bg-surface-2",
              stage.next_up && "text-led",
            )}
            disabled={awaiting}
          >
            <span
              className={cn(
                "inline-flex size-[26px] items-center justify-center rounded-md border font-mono text-[0.6875rem] font-bold tabular-nums",
                stage.next_up
                  ? "badge-led-fill border-transparent"
                  : "border-transparent bg-surface-3 text-ink-2",
              )}
            >
              {pad2(stage.stage_number)}
            </span>
            <span
              className={cn(
                "truncate",
                stage.next_up && "font-bold text-led",
              )}
            >
              {stage.stage_name}
            </span>
            <StageDot status={stage.status} />
          </button>
        ))}
      </div>
    </aside>
  );
}

function SidebarLink({
  to,
  icon,
  count,
  end,
  children,
}: {
  to: string;
  icon: ReactNode;
  count?: number;
  end?: boolean;
  children: ReactNode;
}) {
  const { pathname } = useLocation();
  // NavLink's "end" handles the index case; for nested matches like
  // /audit/3 we still want /audit to be active.
  const isActive = end ? pathname === to : pathname.startsWith(to);
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
      {typeof count === "number" && count > 0 && (
        <span
          className={cn(
            "ml-auto font-mono text-[0.625rem] font-bold tabular-nums",
            isActive ? "text-led" : "text-subtle",
          )}
        >
          {pad2(count)}
        </span>
      )}
    </NavLink>
  );
}

function StageDot({ status }: { status: StageStatus }) {
  if (status === "todo") {
    return (
      <span
        aria-label="Not started"
        className="size-3 rounded-full border border-rule-strong bg-transparent"
      />
    );
  }
  if (status === "partial") {
    return (
      <span
        aria-label="In progress"
        className="size-3 rounded-full bg-live shadow-[0_0_5px_var(--color-live-glow)]"
      />
    );
  }
  if (status === "flagged") {
    return (
      <span
        aria-label="Flagged"
        className="size-3 rounded-full bg-led shadow-[0_0_5px_var(--color-led-glow)]"
      />
    );
  }
  return (
    <span
      aria-label="Done"
      className="inline-flex size-3 items-center justify-center rounded-full bg-done text-bg shadow-[0_0_5px_var(--color-done-glow)]"
    >
      <svg width="7" height="7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="4">
        <path d="M5 12l5 5L20 7" />
      </svg>
    </span>
  );
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}
