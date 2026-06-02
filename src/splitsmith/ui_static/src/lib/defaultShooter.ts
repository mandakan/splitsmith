/**
 * pickDefaultShooterSlug -- the one rule for "which shooter opens when the
 * URL didn't name one".
 *
 * Shared by ``MatchShell`` (sidebar + onStageClick targets) and
 * ``DefaultShooterRedirect`` (bare per-shooter URLs) so the redirect and
 * the chrome can never disagree about the default. First shooter with
 * footage wins -- that's the one the operator is most likely here to work
 * on -- else the first shooter, else nothing (empty match).
 */

import type { ShooterListEntry } from "@/lib/api";

export function pickDefaultShooterSlug(
  shooters: ShooterListEntry[],
): string | undefined {
  return (
    shooters.find((s) => s.video_count > 0)?.slug ??
    shooters[0]?.slug ??
    undefined
  );
}
