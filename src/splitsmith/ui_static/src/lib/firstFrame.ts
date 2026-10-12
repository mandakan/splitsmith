/**
 * Safari decodes no frame of a ``preload="metadata"`` video until it plays
 * or seeks: it fires ``loadedmetadata`` and stops at readyState 1, so a
 * player that waits for ``loadeddata`` shows black (and a "Loading video..."
 * overlay) until Play. Chrome decodes the first frame on its own. A seek
 * makes Safari fetch and decode a frame and fire ``seeked``, ``loadeddata``
 * and ``canplay``.
 *
 * The seek must land inside a frame. A video track can start after 0 (the
 * head cam's 720p rendition of Höstfinalen XI stage 7 starts at 0.020 s; its
 * audio at 0), and Safari draws nothing before the first frame, where Chrome
 * draws the first frame anyway. 1 ms drew black there (0.65.3); 0.1 s draws
 * the picture (measured in WebKit by the pixels, not the events).
 */

/** No frame yet: ``HTMLMediaElement.HAVE_CURRENT_DATA``. */
const HAVE_CURRENT_DATA = 2;

/** Past any video track's start offset seen so far: 39 of 89 local 720p
 *  renditions start late, by 17 to 42 ms. A tenth of a second into a
 *  stage's pre-roll shows nothing new. */
export const FIRST_FRAME_SEEK_S = 0.1;

/** Call on ``loadedmetadata``. Leaves alone an element that already has a
 *  frame, is seeking, or was placed somewhere by the page. */
export function showFirstFrame(el: HTMLMediaElement): void {
  if (el.readyState >= HAVE_CURRENT_DATA || el.seeking || el.currentTime !== 0) return;
  el.currentTime = FIRST_FRAME_SEEK_S;
}

/** Call on ``seeked``: a paused player is never left before
 *  ``FIRST_FRAME_SEEK_S``, where Safari draws black on a video track that
 *  starts late (a camera swap, or a seek to the start, put it at 0). Playing
 *  needs nothing: frames arrive. */
export function keepOnAFrame(el: HTMLMediaElement): void {
  if (!el.paused || el.seeking || el.currentTime >= FIRST_FRAME_SEEK_S) return;
  el.currentTime = FIRST_FRAME_SEEK_S;
}
