/**
 * Shared visual primitives for the export surfaces (single-shooter
 * `Export.tsx` and the match-scoped `MatchExport.tsx`).
 *
 * Lifted out of `Export.tsx` (issue #328 / phase 0) rather than copied,
 * because a later phase folds the two pages together -- keeping one
 * copy of each primitive means that fold has nothing to reconcile.
 * `StageChip` in particular was generalised: the original took a full
 * `StageExportStatus` (single-shooter export overview row) and derived
 * its own tooltip text from it. The match-scoped page has no such row
 * (there is no per-match, per-stage "exportable" status object), so the
 * chip now takes plain `stageNumber` / `stageName` / `title` and lets
 * the caller decide what the disabled tooltip says.
 */

import { ChevronDown, Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

export function Section({
  number,
  title,
  help,
  children,
}: {
  number: number;
  title: string;
  help?: string;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
      <div className="flex items-start gap-3 border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-5 py-3.5">
        <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-led-deep bg-led/10 font-mono text-[0.6875rem] font-bold tabular-nums text-led">
          {pad2(number)}
        </span>
        <div className="min-w-0">
          <div className="font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
            {title}
          </div>
          {help && (
            <div className="mt-0.5 text-[0.75rem] text-muted">{help}</div>
          )}
        </div>
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

export interface StageChipProps {
  stageNumber: number;
  stageName: string;
  selected: boolean;
  eligible: boolean;
  /** Renders the live-red "source offline" treatment instead of the
   *  plain disabled grey. Defaults to false for pages (like the
   *  match-scoped export) that don't track source reachability. */
  sourceMissing?: boolean;
  /** Tooltip shown on hover; callers own the wording since it depends
   *  on page-specific eligibility rules (audit status, trims-only mode,
   *  source reachability, ...). */
  title: string;
  onToggle: () => void;
}

export function StageChip({
  stageNumber,
  stageName,
  selected,
  eligible,
  sourceMissing = false,
  title,
  onToggle,
}: StageChipProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={!eligible}
      aria-pressed={selected}
      title={title}
      className={cn(
        "inline-flex min-h-9 items-center gap-2 rounded-md border px-3 py-1.5 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.06em] transition-all",
        !eligible &&
          !sourceMissing &&
          "cursor-not-allowed border-rule bg-surface-2 text-subtle opacity-50",
        !eligible &&
          sourceMissing &&
          "cursor-not-allowed border-live/40 bg-live/10 text-live",
        eligible &&
          selected &&
          "border-led bg-led/10 text-ink shadow-[0_0_0_1px_var(--color-led-deep),0_0_10px_var(--color-led-glow)]",
        eligible &&
          !selected &&
          "border-rule-strong bg-surface-3 text-muted hover:bg-surface-4 hover:text-ink",
      )}
    >
      <span className="font-mono tabular-nums">{pad2(stageNumber)}</span>
      <span>{stageName}</span>
      {sourceMissing && (
        <span
          aria-hidden
          className="ml-1 inline-block size-1.5 rounded-full bg-live shadow-[0_0_6px_var(--color-live-glow)]"
        />
      )}
    </button>
  );
}

export function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-mono text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-muted">
        {label}
      </span>
      <div className="relative">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full appearance-none rounded-md border border-rule bg-surface-3 px-3 py-2 pr-8 font-mono text-sm text-ink outline-none focus:border-led"
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <ChevronDown
          aria-hidden
          className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted"
        />
      </div>
    </label>
  );
}

/** The LED-glow call-to-action button both export pages render at the
 *  bottom of the summary rail. Swaps its icon for a spinner and its
 *  label for `busyLabel` while `busy` is true. */
export function LedCtaButton({
  busy,
  icon,
  label,
  busyLabel,
  className,
  ...rest
}: {
  busy: boolean;
  icon: ReactNode;
  label: string;
  busyLabel: string;
  className?: string;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className">) {
  return (
    <Button
      type="button"
      className={cn(
        "w-full bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink",
        className,
      )}
      {...rest}
    >
      {busy ? <Loader2 className="size-3.5 animate-spin" /> : icon}
      <span className="font-display uppercase tracking-[0.08em]">
        {busy ? busyLabel : label}
      </span>
    </Button>
  );
}
