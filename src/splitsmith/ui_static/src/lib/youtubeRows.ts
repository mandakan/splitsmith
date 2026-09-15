/** Derivations for the YouTube controls on export-history rows. Pure:
 *  the components map these onto primitives. */
import type { ExportRun, YouTubePrivacy, YouTubeUploadOptions } from "@/lib/api";

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

/** The form's YouTube upload block. ``enabled`` is the "Upload after
 *  render" switch; the rest travel with any upload the page submits,
 *  including a history-row one. ``publishAt`` is a ``datetime-local``
 *  value (no zone: the user's local time). */
export interface UploadFormOptions {
  enabled: boolean;
  privacy: YouTubePrivacy;
  /** null: no playlist; a string (possibly still empty): the checkbox is on. */
  playlist: string | null;
  publishAt: string;
  notifySubscribers: boolean;
}

export const DEFAULT_UPLOAD_OPTIONS: UploadFormOptions = {
  enabled: false,
  privacy: "unlisted",
  playlist: null,
  publishAt: "",
  notifySubscribers: true,
};

/** The request-shaped options for one upload. One block per page, not
 *  one per row: a history-row upload reuses the form's choices. With the
 *  block off, the defaults apply (unlisted, no playlist, now, notify).
 *  A publish time is only meaningful on a private video, so it is
 *  dropped otherwise; a blank playlist name is no playlist. */
export function rowUploadOptions(form: UploadFormOptions): YouTubeUploadOptions {
  if (!form.enabled) return { privacy: "unlisted", playlist: null, publish_at: null, notify_subscribers: true };
  const playlist = (form.playlist ?? "").trim();
  const publishAt =
    form.privacy === "private" && form.publishAt ? new Date(form.publishAt).toISOString() : null;
  return {
    privacy: form.privacy,
    playlist: playlist || null,
    publish_at: publishAt,
    notify_subscribers: form.notifySubscribers,
  };
}
