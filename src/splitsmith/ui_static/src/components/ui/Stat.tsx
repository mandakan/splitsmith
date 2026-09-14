/**
 * Stat / StatStrip -- label + numeral + unit, and the hairline grid that
 * holds a row of them (spec 2026-09-13 s6).
 *
 * StatStrip is `shrink-0` on purpose: ResultsStage mounts it first in a
 * capped overflow-y-auto column, and an overflow-hidden flex child with
 * the default shrink collapses to its label row (the clip fixed in #970).
 * `lead` gives the first cell the whole first row below md so five tiles
 * never leave one alone.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

import { Label } from "./Label";

export interface StatProps extends React.HTMLAttributes<HTMLDivElement> {
  label: string;
  value: string;
  unit?: string;
  /** `dim` = provisional (unaudited) figure: shown, never bold. */
  tone?: "ink" | "dim";
}

export function Stat({ label, value, unit, tone = "ink", className, ...props }: StatProps) {
  return (
    <div className={cn("flex flex-col gap-1 bg-surface px-4 py-3", className)} {...props}>
      <Label>{label}</Label>
      <span className="flex items-baseline gap-1 leading-none">
        <span className={cn("numeral text-[26px]", tone === "dim" ? "text-muted" : "text-ink")}>
          {value}
        </span>
        {unit ? <span className="text-[13px] text-muted">{unit}</span> : null}
      </span>
    </div>
  );
}

export interface StatStripProps extends React.HTMLAttributes<HTMLDivElement> {
  /** First cell spans the full first row below md. */
  lead?: boolean;
  children: React.ReactNode;
}

export function StatStrip({ lead = false, className, children, ...props }: StatStripProps) {
  const cells = React.Children.toArray(children);
  return (
    <div
      className={cn(
        "grid shrink-0 grid-cols-2 gap-px overflow-hidden rounded-[10px] border border-rule-strong bg-rule-strong md:auto-cols-fr md:grid-flow-col",
        className,
      )}
      {...props}
    >
      {cells.map((cell, i) =>
        React.isValidElement<{ className?: string }>(cell)
          ? React.cloneElement(cell, {
              className: cn(cell.props.className, lead && i === 0 && "col-span-2 md:col-span-1"),
            })
          : cell,
      )}
    </div>
  );
}
