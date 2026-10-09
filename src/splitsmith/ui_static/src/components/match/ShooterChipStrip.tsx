/* eslint-disable no-restricted-syntax -- visual budget: remove when this file is rebuilt (spec 2026-09-13 s5) */
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

import { useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { Link } from "react-router-dom";

import { Avatar } from "@/components/ui";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import type { ShooterListEntry } from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";
import { cn } from "@/lib/utils";
import { identityMark } from "@/lib/identityMark";

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
  /** Verb label shown to the left of the chips. Pure-UI cue that names
   *  what "active" *means* on the current page -- "Editing" on Audit /
   *  Ingest / Export, "Coaching" on Coach, "Focus" on match-level
   *  pages, "Audio source" on Export-compare. The design system calls
   *  this the activeMeaning kicker -- the IA decision in 7 chars.
   *  Pass `null` to hide it. */
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
  /** Opens the shooter's look (spec 2026-10-09). When set, each chip gets
   *  a small menu beside it with "Edit look"; the chip itself still
   *  switches shooter. */
  onEditLook?: (s: ShooterListEntry) => void;
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
  onEditLook,
}: Props) {
  const href = useMatchHref();
  const [menuFor, setMenuFor] = useState<string | null>(null);
  if (shooters.length <= 1) return null;
  const isInline = variant === "inline";
  return (
    <div
      className={cn(
        "inline-flex flex-wrap items-center gap-2",
        !isInline && "-mt-1 mb-3",
        isInline && "gap-2.5",
      )}
    >
      {label != null ? (
        <span
          className={cn(
            "font-mono font-bold uppercase tracking-[0.14em] text-subtle",
            isInline ? "text-[0.5625rem]" : "text-[0.625rem]",
          )}
        >
          {label}
        </span>
      ) : null}
      {shooters.map((s) => {
        const isActive = s.slug === activeSlug;
        const target =
          stage != null
            ? href(urlBase, s.slug, String(stage))
            : href(urlBase, s.slug);
        const secondary = count ? count(s) : null;
        return (
          <span key={s.slug} className="inline-flex items-center gap-0.5">
            <Link
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
                {...identityMark(s.slug, s.identity)}
              />
              <span className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.06em]">
                {s.name}
              </span>
              {secondary ? (
                <span
                  className={cn(
                    "font-mono text-[0.625rem] uppercase tracking-[0.06em]",
                    s.stages_total > 0 && s.stages_audited >= s.stages_total
                      ? "text-done"
                      : "text-muted",
                  )}
                >
                  {secondary}
                </span>
              ) : null}
            </Link>
            {onEditLook ? (
              <span className="relative">
                <button
                  type="button"
                  aria-label={`More for ${s.name}`}
                  aria-haspopup="menu"
                  aria-expanded={menuFor === s.slug}
                  onClick={() => setMenuFor(menuFor === s.slug ? null : s.slug)}
                  className="inline-flex size-6 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-3 hover:text-ink"
                >
                  <MoreHorizontal className="size-3.5" aria-hidden />
                </button>
                <Menu open={menuFor === s.slug} onClose={() => setMenuFor(null)} align="left" className="min-w-48">
                  {s.selected_shooter_id != null ? (
                    <button
                      type="button"
                      role="menuitem"
                      className={menuItemClass}
                      onClick={() => {
                        setMenuFor(null);
                        onEditLook(s);
                      }}
                    >
                      Edit look
                    </button>
                  ) : (
                    <p className="max-w-56 px-2.5 py-1.5 text-sm text-muted">
                      Link {s.name} to the scoreboard to give them a look of their own.
                    </p>
                  )}
                </Menu>
              </span>
            ) : null}
          </span>
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
