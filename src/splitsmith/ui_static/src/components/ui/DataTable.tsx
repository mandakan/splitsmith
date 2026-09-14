/**
 * DataTable row primitives (spec 2026-09-13 s6). Lists are rows with
 * hairline dividers, never stacked cards. Th is the Label style; a
 * numeric Td is the Numeral role, right-aligned; the current row is
 * surface-2 with a 2 px led inset on its first cell (red = current
 * position, nothing else); dim cells are provisional figures.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

export function Table({
  className,
  children,
  ...props
}: React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-hidden rounded-[10px] border border-rule bg-surface">
      <div className="overflow-x-auto">
        <table className={cn("w-full border-collapse text-[13px]", className)} {...props}>
          {children}
        </table>
      </div>
    </div>
  );
}

export interface ThProps extends React.ThHTMLAttributes<HTMLTableCellElement> {
  align?: "left" | "right";
}

export function Th({ align = "left", className, ...props }: ThProps) {
  return (
    <th
      className={cn(
        "border-b border-rule-strong px-3 py-2 font-mono text-[11px] font-medium uppercase tracking-[0.08em] text-muted",
        align === "right" ? "text-right" : "text-left",
        className,
      )}
      {...props}
    />
  );
}

export interface TdProps extends React.TdHTMLAttributes<HTMLTableCellElement> {
  kind?: "text" | "num" | "ordinal" | "name";
  /** Provisional figure (unaudited stage): shown, never bold. */
  dim?: boolean;
}

const KIND: Record<NonNullable<TdProps["kind"]>, string> = {
  text: "text-ink-2",
  num: "numeral text-right text-ink",
  ordinal: "w-7 font-mono text-[11px] text-muted",
  name: "font-medium text-ink",
};

export function Td({ kind = "text", dim = false, className, ...props }: TdProps) {
  return (
    <td
      className={cn("px-3 py-2 align-middle", KIND[kind], dim && "text-subtle", className)}
      {...props}
    />
  );
}

export interface TrProps extends React.HTMLAttributes<HTMLTableRowElement> {
  current?: boolean;
}

export function Tr({ current = false, className, ...props }: TrProps) {
  return (
    <tr
      className={cn(
        "border-b border-rule last:border-b-0",
        current && "bg-surface-2 [&>td:first-child]:shadow-[inset_2px_0_0_var(--color-led)]",
        className,
      )}
      {...props}
    />
  );
}
