import type { MatchExportFile, StageExportStatus } from "@/lib/api";

import { matchExportLabel } from "@/lib/exportLabels";

export interface HostedDownload {
  label: string;
  filename: string;
}

/** The download list the hosted Export page offers.
 *
 *  Hosted mode has no "Reveal in Finder" -- the worker that produced the
 *  bundle ran in a separate container -- so the match-level output plus
 *  the per-stage media it references become individual download links.
 *  Filenames are basenames under the project's ``exports/`` dir; the
 *  download endpoint resolves them within the shooter's scope and pulls
 *  them out of object storage.
 *
 *  **Every input here is persistent.** That is the point (#629). This
 *  derivation used to sit in `Export.tsx` gated on the export job's
 *  in-session `result`, so it emptied itself on every reload -- taking
 *  the per-stage links with it, which never came from `result` at all.
 *  A hosted user who closed the tab lost the link to files that were
 *  sitting in R2 the whole time. Nothing in this signature is
 *  session-scoped, which is what makes that failure unrepresentable
 *  rather than merely fixed.
 */
export function hostedDownloads(opts: {
  matchExports: MatchExportFile[];
  stages: StageExportStatus[];
  /** Stage numbers currently selected, in display order. */
  selection: number[];
}): HostedDownload[] {
  const { matchExports, stages, selection } = opts;
  const basename = (p: string) => p.split("/").pop() ?? p;
  const sel = new Set(selection);

  const out: HostedDownload[] = matchExports.map((m) => ({
    label: matchExportLabel(m.filename),
    filename: m.filename,
  }));

  for (const s of stages) {
    if (!sel.has(s.stage_number)) continue;
    // ``lossless_trim_present`` disambiguates the deliverable from the
    // short-GOP scrub cache, which ``trimmed_video_path`` also points at
    // when no lossless trim exists yet. Offering the scrub copy as a
    // download would hand the user a file they did not ask for.
    if (s.lossless_trim_present && s.trimmed_video_path) {
      out.push({
        label: `Stage ${s.stage_number} trim`,
        filename: basename(s.trimmed_video_path),
      });
    }
    if (s.overlay_path) {
      out.push({
        label: `Stage ${s.stage_number} overlay`,
        filename: basename(s.overlay_path),
      });
    }
    for (const sec of s.secondaries) {
      if (sec.trim_present && sec.trim_path) {
        out.push({
          label: `Stage ${s.stage_number} ${sec.label}`,
          filename: basename(sec.trim_path),
        });
      }
    }
  }
  return out;
}
