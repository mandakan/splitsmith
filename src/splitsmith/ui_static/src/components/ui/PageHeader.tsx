/**
 * PageHeader -- the only way to get a page or stage title (spec 2026-09-13
 * s6). Antonio 700 30 px, ordinal in led, sub-line Geist 13 muted,
 * actions bottom-aligned on the right. `children` renders under the
 * sub-line: the shooter chip strip on multi-shooter matches, once PRs 3-6
 * move it out of the shell's context row.
 */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { cn } from "@/lib/utils";

export interface PageHeaderProps {
  /** Stage or shot ordinal, zero-padded ("03"): the one place a leading zero belongs. */
  ordinal?: string;
  title: string;
  sub?: ReactNode;
  back?: { label: string; to: string };
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
}

export function PageHeader({ ordinal, title, sub, back, actions, children, className }: PageHeaderProps) {
  return (
    <header className={cn("mb-5 flex flex-wrap items-end justify-between gap-x-4 gap-y-3", className)}>
      <div className="min-w-0">
        {back ? (
          <Link to={back.to} className="mb-1.5 inline-block text-[12px] text-muted transition-colors hover:text-ink">
            &lsaquo; {back.label}
          </Link>
        ) : null}
        <h1 className="font-display text-[30px] font-bold uppercase leading-none tracking-[0.01em] text-ink">
          {ordinal ? <span className="mr-2 text-led">{ordinal}</span> : null}
          {title}
        </h1>
        {sub ? <p className="mt-1.5 text-[13px] text-muted">{sub}</p> : null}
        {children ? <div className="mt-3">{children}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}
