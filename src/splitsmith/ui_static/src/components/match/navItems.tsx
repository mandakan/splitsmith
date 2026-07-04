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
  LayoutGrid,
  MonitorPlay,
  Users,
  Volume2,
} from "lucide-react";

export interface MatchNavItem {
  key: string;
  to: string;
  icon: ReactNode;
  label: string;
  end?: boolean;
  disabled?: boolean;
  disabledHint?: string;
  count?: number;
  badgeKind?: "count" | "pending";
}

export function matchNavItems(args: {
  base: string;
  shooterSlug?: string;
  hasFootage: boolean;
  shooterCount?: number;
  beepReviewPendingCount: number;
  footageHint?: string;
}): MatchNavItem[] {
  const { base, shooterSlug, hasFootage, shooterCount, beepReviewPendingCount, footageHint } = args;
  return [
    { key: "overview", to: `${base}/`, icon: <LayoutGrid className="size-[15px]" />, label: "Overview", end: true },
    { key: "results", to: `${base}/results`, icon: <MonitorPlay className="size-[15px]" />, label: "Results" },
    {
      key: "audit",
      to: shooterSlug ? `${base}/audit/${shooterSlug}` : `${base}/shooters?pick=audit`,
      icon: <Crosshair className="size-[15px]" />,
      label: "Audit",
      disabled: !hasFootage,
      disabledHint: footageHint,
    },
    {
      key: "coach",
      to: shooterSlug ? `${base}/coach/${shooterSlug}` : `${base}/shooters?pick=coach`,
      icon: <ClipboardCheck className="size-[15px]" />,
      label: "Coach",
      disabled: !hasFootage,
      disabledHint: footageHint,
    },
    {
      key: "shooters",
      to: `${base}/shooters`,
      icon: <Users className="size-[15px]" />,
      label: "Shooters",
      count: shooterCount,
      badgeKind: "count",
    },
    {
      key: "videos",
      to: shooterSlug ? `${base}/ingest/${shooterSlug}` : `${base}/shooters?pick=videos`,
      icon: <Film className="size-[15px]" />,
      label: "Videos",
    },
    {
      key: "beep-review",
      to: `${base}/beep-review`,
      icon: <Volume2 className="size-[15px]" />,
      label: "Beep review",
      count: beepReviewPendingCount,
      badgeKind: "pending",
    },
    {
      key: "export",
      to: shooterSlug ? `${base}/export/${shooterSlug}` : `${base}/shooters?pick=export`,
      icon: <ArrowDownToLine className="size-[15px]" />,
      label: "Export",
      disabled: !hasFootage,
      disabledHint: footageHint,
    },
  ];
}
