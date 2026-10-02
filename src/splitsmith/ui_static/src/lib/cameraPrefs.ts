/**
 * A camera choice that holds across stages (2026-10-02). A video id names
 * a file on one stage, not a camera, so a choice is kept as a selector:
 * the camera's mount ("hand", "head") or its role ("primary",
 * "secondary"), resolved on every stage the way the server's
 * ``camera_select.resolve_camera`` does: same mount first, then role, else
 * the stage's primary. The shooter's saved ``compare_camera`` is the same
 * kind of selector, so a saved default, a choice made while watching and
 * the export grid all agree. Choices ride in the URL as
 * ``cams=martin:hand,anton:hand`` so a copied link opens on them.
 */
import type { CoachVideoEntry } from "./api";

/** The index ``selector`` names on this stage; 0 (the primary) when it
 *  names nothing here. A camera without a beep cannot be lined up, so it
 *  is never chosen. */
export function resolveCamera(
  videos: CoachVideoEntry[],
  selector: string | null | undefined,
): number {
  if (!selector) return 0;
  const usable = (i: number) => videos[i]?.beep_in_clip != null;
  const byMount = videos.findIndex((v, i) => usable(i) && v.mount === selector);
  if (byMount >= 0) return byMount;
  if (selector === "primary" || selector === "secondary") {
    const byRole = videos.findIndex((v, i) => usable(i) && v.role === selector);
    if (byRole >= 0) return byRole;
  }
  return 0;
}

/** The selector that names the camera at ``index``: its mount when no
 *  other camera on the stage shares it (even for the primary: the head
 *  cam is the primary on one stage and a secondary on the next), else its
 *  role. Null for an unmounted primary, which is what no choice means. */
export function selectorFor(
  videos: CoachVideoEntry[],
  index: number,
): string | null {
  const v = videos[index];
  if (!v) return null;
  if (v.mount && videos.filter((o) => o.mount === v.mount).length === 1)
    return v.mount;
  return index === 0 || v.role === "primary" ? null : v.role;
}

/** ``cams=martin:hand,anton:primary`` to ``{martin: "hand", anton: "primary"}``. */
export function parseCams(raw: string | null): Record<string, string> {
  const out: Record<string, string> = {};
  if (!raw) return out;
  for (const pair of raw.split(",")) {
    const sep = pair.indexOf(":");
    if (sep <= 0) continue;
    const slug = pair.slice(0, sep).trim();
    const sel = pair.slice(sep + 1).trim();
    if (slug && sel) out[slug] = sel;
  }
  return out;
}

export function camsParam(prefs: Record<string, string>): string | null {
  const pairs = Object.entries(prefs)
    .filter(([slug, sel]) => slug && sel)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([slug, sel]) => `${slug}:${sel}`);
  return pairs.length > 0 ? pairs.join(",") : null;
}

/** A shooter's starting camera on a stage: the choice made while watching
 *  (or carried in by a link), else the saved default, else the primary. */
export function startingCamera(
  videos: CoachVideoEntry[],
  chosen: string | undefined,
  saved: string | null | undefined,
): number {
  return resolveCamera(videos, chosen ?? saved ?? null);
}

/** ``url`` with the ``cams`` choice carried over, so moving between
 *  stages and pages keeps the cameras. */
export function withCams(url: string, cams: string | null): string {
  if (!cams) return url;
  return `${url}${url.includes("?") ? "&" : "?"}cams=${encodeURIComponent(cams)}`;
}
