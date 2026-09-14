/**
 * matchNavItems - single source of truth for match-scoped navigation.
 * Rendered by MatchSidebar (desktop) and MobileNav (drawer). Keep the
 * destination logic identical to the pre-extraction SidebarLink rows.
 */
import type { ReactNode } from "react";
import {
  ArrowDownToLine,
  ClipboardCheck,
  Columns2,
  Crosshair,
  Film,
  LayoutGrid,
  MonitorPlay,
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
  beepReviewPendingCount: number;
  footageHint?: string;
  /** Compare needs two shooters; the row shows only then (spec s4.7). */
  multiShooter?: boolean;
  /** Where the Compare row lands: the first audited stage, else stage 1. */
  compareStage?: number;
}): MatchNavItem[] {
  const {
    base,
    shooterSlug,
    hasFootage,
    beepReviewPendingCount,
    footageHint,
    multiShooter = false,
    compareStage = 1,
  } = args;
  return [
    { key: "overview", to: `${base}/`, icon: <LayoutGrid className="size-[15px]" />, label: "Overview", end: true },
    {
      key: "videos",
      group: "prepare",
      to: shooterSlug ? `${base}/ingest/${shooterSlug}` : `${base}/ingest`,
      icon: <Film className="size-[15px]" />,
      label: "Footage",
    },
    {
      // Beep confirmation is step 1 of Audit (UX PR 5); the pending
      // count that used to badge the Beep review row badges Audit.
      key: "audit",
      group: "review",
      to: shooterSlug ? `${base}/audit/${shooterSlug}` : `${base}/audit`,
      icon: <Crosshair className="size-[15px]" />,
      label: "Audit",
      disabled: !hasFootage,
      disabledHint: footageHint,
      count: beepReviewPendingCount,
      badgeKind: "pending",
      badgeAriaLabel: `${beepReviewPendingCount} ${beepReviewPendingCount === 1 ? "beep" : "beeps"} to confirm`,
    },
    { key: "results", group: "analyse", to: `${base}/results`, icon: <MonitorPlay className="size-[15px]" />, label: "Splits" },
    {
      key: "coach",
      group: "analyse",
      to: shooterSlug ? `${base}/coach/${shooterSlug}` : `${base}/coach`,
      icon: <ClipboardCheck className="size-[15px]" />,
      label: "Coach",
      disabled: !hasFootage,
      disabledHint: footageHint,
    },
    ...(multiShooter
      ? [
          {
            key: "compare",
            group: "analyse" as const,
            to: `${base}/compare/${compareStage}`,
            icon: <Columns2 className="size-[15px]" />,
            label: "Compare",
          },
        ]
      : []),
    {
      key: "export",
      group: "deliver",
      to: shooterSlug ? `${base}/export/${shooterSlug}` : `${base}/export`,
      icon: <ArrowDownToLine className="size-[15px]" />,
      label: "Export",
      disabled: !hasFootage,
      disabledHint: footageHint,
    },
  ];
}
