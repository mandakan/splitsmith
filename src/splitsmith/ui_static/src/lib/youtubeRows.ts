/** Derivations for the YouTube controls on export-history rows. Pure:
 *  the components map these onto primitives. */
import type { ExportRun, YouTubePrivacy } from "@/lib/api";

/** The MP4 a row can upload: an available ``match_video`` whose
 *  ``<stem>-youtube.json`` sidecar is also listed and available. The
 *  sidecar is the upload's metadata, so an MP4 without one is not
 *  uploadable from here. */
export function uploadableArtifact(run: ExportRun): string | null {
  const mp4 = run.artifacts.find(
    (a) => a.kind === "match_video" && a.available && a.filename.toLowerCase().endsWith(".mp4"),
  );
  if (!mp4) return null;
  const stem = mp4.filename.slice(0, -4);
  const sidecar = run.artifacts.find((a) => a.available && a.filename === `${stem}-youtube.json`);
  return sidecar ? mp4.filename : null;
}

export function youtubeLink(run: ExportRun): { href: string; label: string } | null {
  const y = run.youtube;
  if (!y) return null;
  return { href: y.url, label: `youtu.be/${y.video_id}` };
}

export function uploadLabel(run: ExportRun): "Upload to YouTube" | "Upload again" {
  return run.youtube ? "Upload again" : "Upload to YouTube";
}

/** The privacy a history-row upload uses: the form's "Upload after
 *  render" control when it is not Off, else unlisted. One privacy control
 *  per page, not one per row. */
export function rowPrivacy(control: "off" | YouTubePrivacy): YouTubePrivacy {
  return control === "off" ? "unlisted" : control;
}
