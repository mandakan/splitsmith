/** Human labels for match-level export deliverables.
 *
 *  The server hands back basenames only -- the download endpoint keys off
 *  the name within the shooter's ``exports/`` scope -- so the label has to
 *  come from the extension. Kept out of `Export.tsx` so it can be tested
 *  without mounting the page.
 */

const MATCH_LABELS: Record<string, string> = {
  fcpxml: "Match FCPXML",
  xml: "Match FCP7 XML",
  mp4: "Match video",
  mov: "Match video",
  srt: "Match subtitles",
  json: "Match YouTube sidecar",
};

/** Label for one match-level deliverable, by extension.
 *
 *  Falls back to the bare filename rather than to a generic word: an
 *  unrecognised extension is still something the user asked to be
 *  written, and a row reading "Match export" twice is worse than one
 *  reading the actual name.
 */
export function matchExportLabel(filename: string): string {
  const dot = filename.lastIndexOf(".");
  if (dot <= 0) return filename;
  const ext = filename.slice(dot + 1).toLowerCase();
  return MATCH_LABELS[ext] ?? filename;
}
