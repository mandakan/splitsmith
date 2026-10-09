/**
 * A shooter's identity (#1243) as the roster draws it (#1249): their accent
 * and a URL for their logo, scoped like every request (a match, or a share
 * link). The logo URL carries the content-named file, so a new logo is a new
 * URL and the browser never shows a stale one. Pure.
 */
import { scopeRequestPath, type ShooterIdentity } from "@/lib/api";

export function identityMark(
  slug: string,
  identity: ShooterIdentity | null | undefined,
): { accent: string | null; logo: string | null } {
  const accent = identity?.accent ?? null;
  const logo = identity?.logo
    ? scopeRequestPath(`/api/shooters/${encodeURIComponent(slug)}/identity/logo?v=${encodeURIComponent(identity.logo)}`)
    : null;
  return { accent, logo };
}
