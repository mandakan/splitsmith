/**
 * Section -- one bordered group on the Export page. With `onToggle` the
 * header is a disclosure: the label, the control and, while closed, the
 * group's one-line summary stay visible and the rows fold away
 * (spec 2026-09-15 s1). Without it the group is always open, as the
 * Stages and Details groups are.
 */
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

import { Label } from "@/components/ui/Label";
import { cn } from "@/lib/utils";

export interface SectionProps {
  label: string;
  aside?: ReactNode;
  control?: ReactNode;
  /** No inner padding: the child brings its own rows. */
  flush?: boolean;
  /** One line shown in the header while closed. */
  summary?: string;
  open?: boolean;
  onToggle?: () => void;
  children?: ReactNode;
}

export function Section({
  label,
  aside,
  control,
  flush = false,
  summary,
  open = true,
  onToggle,
  children,
}: SectionProps) {
  const collapsible = onToggle !== undefined;
  const showChildren = Boolean(children) && (!collapsible || open);
  const Icon = open ? ChevronDown : ChevronRight;
  return (
    <section className="rounded-[10px] border border-rule bg-surface">
      <div className={cn("flex flex-wrap items-center gap-3 px-3.5 py-2", showChildren ? "border-b border-rule" : null)}>
        {collapsible ? (
          <button
            type="button"
            aria-expanded={open}
            onClick={onToggle}
            className="inline-flex items-center gap-1.5 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
          >
            <Icon className="size-3.5 text-muted" aria-hidden />
            <Label>{label}</Label>
          </button>
        ) : (
          <Label>{label}</Label>
        )}
        {control}
        {collapsible && !open && summary ? <span className="text-sm text-muted">{summary}</span> : null}
        {aside ? <span className="ml-auto">{aside}</span> : null}
      </div>
      {showChildren ? (
        <div className={cn(flush ? "[&>div]:rounded-none [&>div]:border-0" : null)}>{children}</div>
      ) : null}
    </section>
  );
}
