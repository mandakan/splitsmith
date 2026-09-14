/**
 * matchNavItems - single source of truth for match-scoped navigation.
 * Rendered by MatchSidebar (desktop) and MobileNav (drawer). Keep the
 * destination logic identical to the pre-extraction SidebarLink rows.
 */
import type { ReactNode } from "react";
import {
  ArrowDownToLine,
  ClipboardCheck,
  Crosshair,
  Film,
  Flag,
  LayoutGrid,
  MonitorPlay,
  Users,
} from "lucide-react";

/** Shared disabled-row hint for footage-dependent surfaces. Single
 *  definition so MatchSidebar and the MobileNav drawer cannot drift. */
export const FOOTAGE_HINT =
  "Attach footage to this match before this surface is usable";

export type MatchNavGroup = "prepare" | "review" | "analyse" | "deliver";

export const NAV_GROUP_LABEL: Record<MatchNavGroup, string> = {
  prepare: "Prepare",
  review: "Review",
  analyse: "Analyse",
  deliver: "Deliver",
};

export interface MatchNavItem {
  key: string;
  to: string;
  icon: ReactNode;
  label: string;
  /** Loop phase the row belongs to (spec 2026-09-13 s3.2). Undefined for
   *  Overview, which sits above the groups. Renderers emit a group label
   *  whenever it changes between consecutive rows. */
  group?: MatchNavGroup;
  end?: boolean;
  disabled?: boolean;
  disabledHint?: string;
  count?: number;
  badgeKind?: "count" | "pending";
  /** Accessible name for the count badge when a bare number isn't
   *  descriptive enough on its own (not color-only). Overrides the
   *  badge's default announced text; undefined leaves it as-is. */
  badgeAriaLabel?: string;
}

export function matchNavItems(args: {
  base: string;
  shooterSlug?: string;
  hasFootage: boolean;
  shooterCount?: number;
  beepReviewPendingCount: number;
  triageFlaggedCount: number;
  footageHint?: string;
}): MatchNavItem[] {
  const {
    base,
    shooterSlug,
    hasFootage,
    shooterCount,
    beepReviewPendingCount,
    triageFlaggedCount,
    footageHint,
  } = args;
  return [
    { key: "overview", to: `${base}/`, icon: <LayoutGrid className="size-[15px]" />, label: "Overview", end: true },
    {
      key: "videos",
      group: "prepare",
      to: shooterSlug ? `${base}/ingest/${shooterSlug}` : `${base}/shooters?pick=videos`,
      icon: <Film className="size-[15px]" />,
      label: "Footage",
    },
    {
      key: "shooters",
      group: "prepare",
      to: `${base}/shooters`,
      icon: <Users className="size-[15px]" />,
      label: "Shooters",
      count: shooterCount,
      badgeKind: "count",
    },
    {
      // Beep confirmation is step 1 of Audit (UX PR 5); the pending
      // count that used to badge the Beep review row badges Audit.
      key: "audit",
      group: "review",
      to: shooterSlug ? `${base}/audit/${shooterSlug}` : `${base}/shooters?pick=audit`,
      icon: <Crosshair className="size-[15px]" />,
      label: "Audit",
      disabled: !hasFootage,
      disabledHint: footageHint,
      count: beepReviewPendingCount,
      badgeKind: "pending",
      badgeAriaLabel: `${beepReviewPendingCount} ${beepReviewPendingCount === 1 ? "beep" : "beeps"} to confirm`,
    },
    {
      key: "triage",
      group: "review",
      to: `${base}/triage`,
      icon: <Flag className="size-[15px]" />,
      label: "Triage",
      count: triageFlaggedCount,
      badgeKind: "pending",
      badgeAriaLabel: `${triageFlaggedCount} stage${triageFlaggedCount === 1 ? "" : "s"} flagged for desktop`,
    },
    { key: "results", group: "analyse", to: `${base}/results`, icon: <MonitorPlay className="size-[15px]" />, label: "Splits" },
    {
      key: "coach",
      group: "analyse",
      to: shooterSlug ? `${base}/coach/${shooterSlug}` : `${base}/shooters?pick=coach`,
      icon: <ClipboardCheck className="size-[15px]" />,
      label: "Coach",
      disabled: !hasFootage,
      disabledHint: footageHint,
    },
    {
      key: "export",
      group: "deliver",
      to: shooterSlug ? `${base}/export/${shooterSlug}` : `${base}/shooters?pick=export`,
      icon: <ArrowDownToLine className="size-[15px]" />,
      label: "Export",
      disabled: !hasFootage,
      disabledHint: footageHint,
    },
  ];
}
