/**
 * The highlight reel's rules for the SPA (spec 2026-10-18-highlight-reel-design,
 * part 2): the readiness wording, the blockers and where each is fixed, the
 * moment menu by group, and the render request. Pure; the Overview card and
 * Export's reel mode map these to primitives.
 */
import type {
  HighlightMenuItem,
  HighlightReadiness,
  HighlightReelBody,
  HighlightStageReadiness,
} from "@/lib/api";

export type ReelCut = "normal" | "short";
export type ReelFormat = "portrait" | "landscape";

/** States that leave a stage out of the reel without blocking it. */
const OUT_OF_PLAY = new Set(["ready", "skipped", "no_video"]);

export function readinessLine(r: HighlightReadiness): string {
  if (r.unlocked) return "Every stage is ready";
  return `${r.ready} of ${r.total} ${r.total === 1 ? "stage" : "stages"} ready`;
}

/** States of a stage whose shots are audited. */
const AUDITED = new Set(["ready", "breakdown_not_opened", "proposals_left", "trim_missing"]);

/** The Overview shows the reel once any stage is audited: before that it
 *  would only nag a match that has just started. */
export function showReelCard(r: HighlightReadiness): boolean {
  return r.stages.some((s) => AUDITED.has(s.state));
}

export function blockingStages(r: HighlightReadiness): HighlightStageReadiness[] {
  return r.stages.filter((s) => !OUT_OF_PLAY.has(s.state));
}

/** One blocker's line and the page that fixes it. */
export function blockerFix(
  s: HighlightStageReadiness,
  slug: string,
  href: (...segments: string[]) => string,
): { text: string; to: string } {
  const n = String(s.stage_number);
  switch (s.state) {
    case "proposals_left":
      return {
        text: `Stage ${n}: ${s.proposals} ${s.proposals === 1 ? "proposal" : "proposals"} left in Breakdown`,
        to: href("breakdown", slug, n),
      };
    case "breakdown_not_opened":
      return { text: `Stage ${n}: open it in Breakdown`, to: href("breakdown", slug, n) };
    case "trim_missing":
      return { text: `Stage ${n}: trim it again`, to: href("audit", slug, n) };
    default:
      return { text: `Stage ${n}: audit its shots`, to: href("audit", slug, n) };
  }
}

export interface MenuGroups {
  default: HighlightMenuItem[];
  stat: HighlightMenuItem[];
  lolz: HighlightMenuItem[];
}

/** The menu by group, each in the server's play order. */
export function groupMenu(menu: HighlightMenuItem[]): MenuGroups {
  return {
    default: menu.filter((m) => m.group === "default"),
    stat: menu.filter((m) => m.group === "stat"),
    lolz: menu.filter((m) => m.group === "lolz"),
  };
}

/** The moments ticked before the viewer touches anything: the defaults,
 *  never a lolz one whatever the server says. */
export function defaultSelection(menu: HighlightMenuItem[]): Set<string> {
  return new Set(menu.filter((m) => m.default && m.group !== "lolz").map((m) => m.kind));
}

/** What happens after the render: keep the file, or upload it to YouTube. */
export type ReelAfter = "keep" | "unlisted" | "private" | "public";

export function reelBody(
  menu: HighlightMenuItem[],
  chosen: Set<string>,
  options: { cut: ReelCut; format: ReelFormat; force: boolean; after?: ReelAfter },
): HighlightReelBody {
  const after = options.after ?? "keep";
  return {
    moments: menu.filter((m) => chosen.has(m.kind)).map((m) => m.kind),
    short: options.cut === "short",
    upright: options.format === "portrait",
    force: options.force,
    youtube_upload: after !== "keep",
    youtube_privacy: after === "keep" ? "unlisted" : after,
  };
}

/** A history row's format as the page names it: the reel's run carries
 *  its job kind, every other format is shown as stored. */
export function runFormatLabel(format: string): string {
  return format === "highlight_reel" ? "Highlight reel" : format;
}

/** The finished render's line under the Render button. */
export function resultLine(result: { filename: string; duration_seconds: number; left_out: number[] }): string {
  const without =
    result.left_out.length === 0
      ? ""
      : ` without stage${result.left_out.length === 1 ? "" : "s"} ${result.left_out.join(", ")}`;
  return `Wrote ${result.filename} (${result.duration_seconds.toFixed(1)} s)${without}. Download or upload it from the export history.`;
}

/** Why Render is disabled, or ``null`` when it is not. */
export function renderBlock(r: HighlightReadiness, chosen: Set<string>, force: boolean): string | null {
  if (chosen.size === 0) return "Choose at least one moment.";
  if (r.unlocked) return null;
  if (r.ready === 0) return "No stage is ready yet.";
  if (!force) return "Finish every stage first, or choose Render the ready ones.";
  return null;
}
