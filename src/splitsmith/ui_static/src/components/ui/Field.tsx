/**
 * Field -- the form row every settings surface uses (spec 2026-09-13
 * s4.9): a Label column with an optional hint under it, then the control,
 * its help line and its error. Rows stack with hairlines; the last one
 * has none. `inputClass` styles a bare input or select to match.
 */
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

import { Label } from "./Label";

export const inputClass =
  "w-full rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 text-[14px] text-ink outline-none placeholder:text-subtle focus:border-led disabled:opacity-50";

export interface FieldProps {
  label: string;
  /** Under the label: a counter, a short qualifier. */
  hint?: ReactNode;
  /** Under the control. */
  help?: ReactNode;
  error?: string | null;
  /** Makes the label a <label htmlFor>. */
  htmlFor?: string;
  className?: string;
  children: ReactNode;
}

export function Field({ label, hint, help, error, htmlFor, className, children }: FieldProps) {
  return (
    <div
      className={cn(
        "grid grid-cols-1 gap-x-4 gap-y-1.5 border-b border-rule px-3.5 py-3 last:border-b-0 sm:grid-cols-[150px_minmax(0,1fr)]",
        className,
      )}
    >
      <div className="sm:pt-2">
        {htmlFor ? (
          <label htmlFor={htmlFor}>
            <Label>{label}</Label>
          </label>
        ) : (
          <Label>{label}</Label>
        )}
        {hint ? <div className="mt-1 text-[12px] text-subtle">{hint}</div> : null}
      </div>
      <div className="min-w-0">
        {children}
        {help ? <div className="mt-1.5 max-w-[52ch] text-[12px] text-muted">{help}</div> : null}
        {error ? (
          <p role="alert" className="mt-1.5 text-[12px] text-destructive">
            {error}
          </p>
        ) : null}
      </div>
    </div>
  );
}
