/**
 * The words of the YouTube access sheet (components/export/YouTubeAccessSheet):
 * what connecting a channel lets splitsmith do. Every line is a claim
 * about ``youtube/client.py``; a new API call moves this copy and the
 * privacy page's YouTube section (``site/privacy.html#youtube``), which
 * ``YouTubeAccessSheet.test.tsx`` holds to the same words.
 */

export const GOOGLE_SCOPE_WORDING =
  "See, edit, and permanently delete your YouTube videos, ratings, comments and captions";
export const PRIVACY_URL = "https://splitsmith.app/privacy#youtube";
export const GOOGLE_PERMISSIONS_URL = "https://myaccount.google.com/permissions";

export const DOES: readonly string[] = [
  'Uploads a video when you press Upload or choose "Upload after render", with the title, description and privacy shown on the form. A request from your phone to your desktop counts as pressing it.',
  "Adds that video's chapter captions and thumbnail.",
  "Lists your playlists for the picker, and creates a playlist or adds the video to one only when you choose it.",
  "Reads your channel's name to show which channel is connected.",
];

export const NEVER: readonly string[] = [
  "Delete or change a video after it is uploaded. The app has no code that does either.",
  "Read or post comments, ratings, subscriptions, watch history or analytics.",
  "Upload anything you did not ask it to upload.",
  "See your Google password or use any other Google service.",
];

export function keyLine(hosted: boolean): string {
  return hosted
    ? "Google gives splitsmith a key, stored on your account and encrypted with a separate key kept outside the database."
    : "Google gives splitsmith a key, saved in youtube.json in your splitsmith settings folder on this computer. It is never synced or uploaded anywhere.";
}
