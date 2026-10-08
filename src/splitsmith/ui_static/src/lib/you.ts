/**
 * You, your brand and the shooter book (spec 2026-10-08): the pure rules the
 * "You" page, the roster and the identity sheet share. "You" is the SSI
 * shooter id on your scoreboard identity; a shooter is you only by that id,
 * never by name.
 */
import type { IdentitySource, ScoreboardIdentity, ShooterIdentity } from "@/lib/api";

export const YOU_FEATURE = "your-brand-and-shooter-book";

/** Whether this match's shooter is you. A shooter with no SSI id never is. */
export function isYou(selectedShooterId: number | null | undefined, me: ScoreboardIdentity | null): boolean {
  return me !== null && selectedShooterId != null && selectedShooterId === me.shooter_id;
}

/** The identity sheet's line on where a shooter's look comes from.
 *  ``bookAvailable`` false (a server with no book yet) promises nothing. */
export function sourceLine(source: IdentitySource, hasShooterId: boolean, bookAvailable = true): string {
  if (!bookAvailable) return source === "match" ? "Set for this match." : "Nothing set for this match.";
  if (source === "book") return "From your shooter book: the same in every match.";
  if (source === "match") {
    return hasShooterId
      ? "Set for this match. Saving also updates your shooter book."
      : "Set for this match. Link this shooter's scoreboard entry to carry it to other matches.";
  }
  return hasShooterId
    ? "Nothing set. What you save here goes to your shooter book too."
    : "Nothing set. Link this shooter's scoreboard entry to carry a look to other matches.";
}

/** Whether an identity sets anything (an all-empty one is "nothing set"). */
export function identityIsSet(identity: ShooterIdentity | null | undefined): boolean {
  return Boolean(identity && (identity.accent || identity.club || identity.logo));
}

/** The book's rows in a stable order: you first, then by name, then id. */
export function sortBook<T extends { shooter_id: number; label: string | null }>(
  entries: readonly T[],
  me: ScoreboardIdentity | null,
): T[] {
  return [...entries].sort((a, b) => {
    const aMe = me?.shooter_id === a.shooter_id ? 0 : 1;
    const bMe = me?.shooter_id === b.shooter_id ? 0 : 1;
    if (aMe !== bMe) return aMe - bMe;
    const byName = (a.label ?? "").localeCompare(b.label ?? "");
    return byName !== 0 ? byName : a.shooter_id - b.shooter_id;
  });
}
