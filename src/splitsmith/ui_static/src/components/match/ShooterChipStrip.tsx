/**
 * ShooterChipStrip -- shared shooter switcher for every shooter-scoped
 * page (Audit, Ingest / Videos, Coach, Export).
 *
 * Each chip is a Link to the same page at the picked slug; the parent
 * route's <ShooterScopedRoute> keys the rendered page on slug so the
 * switch remounts cleanly (no manual cleanup of stage state, peaks,
 * audit JSON, etc.). The active chip is non-interactive and styled
 * with the LED ring.
 *
 * The strip only renders for multi-shooter matches. Single-shooter
 * matches and legacy projects have nothing to switch between.
 */

import { Link } from "react-router-dom";

import { Avatar } from "@/components/ui";
import type { ShooterListEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  /** All shooters in the bound match. Hides itself when length <= 1. */
  shooters: ShooterListEntry[];
  /** Slug of the shooter currently in focus (the URL's :slug). */
  activeSlug: string | undefined;
  /** Route base for the chip targets, without leading slash. Examples:
   *  ``"audit"``, ``"ingest"``, ``"coach"``, ``"export"``. */
  urlBase: "audit" | "ingest" | "coach" | "export";
  /** Optional stage number to suffix on the chip target. When set, the
   *  target becomes ``/<urlBase>/<slug>/<stage>``; when null, just
   *  ``/<urlBase>/<slug>``. Audit + Coach + Export all support both
   *  forms; Ingest is per-shooter (no stage), so callers pass null. */
  stage?: number | null;
  /** Verb label shown to the left of the chips, or `null` for the inline
   *  variant (which sits in a breadcrumb row and skips the label). */
  label: string | null;
  /** Per-chip secondary count format. Defaults to "audited/total"; pages
   *  that surface a different metric (e.g. raw video count on Ingest)
   *  pass a custom formatter. ``null`` hides the count. */
  count?: ((s: ShooterListEntry) => string | null) | null;
  /** Layout variant. `block` (default) renders with the verb label and a
   *  bottom margin so it sits as its own row. `inline` is for the
   *  MatchShell breadcrumb row -- no label, no margin, the host
   *  controls spacing. */
  variant?: "block" | "inline";
}

const defaultCount = (s: ShooterListEntry): string =>
  `${pad2(s.stages_audited)}/${pad2(s.stages_total)}`;

export function ShooterChipStrip({
  shooters,
  activeSlug,
  urlBase,
  stage = null,
  label,
  count = defaultCount,
  variant = "block",
}: Props) {
  if (shooters.length <= 1) return null;
  const isInline = variant === "inline";
  return (
    <div
      className={cn(
        "inline-flex flex-wrap items-center gap-2",
        !isInline && "-mt-1 mb-3",
      )}
    >
      {label != null && !isInline ? (
        <span className="font-mono text-[0.625rem] font-bold uppercase tracking-[0.14em] text-subtle">
          {label}
        </span>
      ) : null}
      {shooters.map((s) => {
        const isActive = s.slug === activeSlug;
        const target =
          stage != null
            ? `/${urlBase}/${s.slug}/${stage}`
            : `/${urlBase}/${s.slug}`;
        const secondary = count ? count(s) : null;
        return (
          <Link
            key={s.slug}
            to={target}
            replace
            aria-current={isActive ? "page" : undefined}
            title={
              isActive
                ? `${s.name} -- currently in focus`
                : `Switch to ${s.name}`
            }
            className={cn(
              "inline-flex items-center gap-2 rounded-full border px-2 py-1 text-[0.8125rem] transition-colors no-underline",
              isActive
                ? "border-led shadow-[0_0_0_1px_var(--color-led-deep),0_0_14px_var(--color-led-glow)]"
                : "border-rule bg-surface-2 text-ink-2 hover:border-rule-strong hover:bg-surface-3",
              isActive && "pointer-events-none",
            )}
          >
            <Avatar
              size="xs"
              initials={chipInitials(s.name)}
              seed={s.slug}
              name={s.name}
            />
            <span className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.06em]">
              {s.name}
            </span>
            {secondary ? (
              <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                {secondary}
              </span>
            ) : null}
          </Link>
        );
      })}
    </div>
  );
}

function chipInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}
