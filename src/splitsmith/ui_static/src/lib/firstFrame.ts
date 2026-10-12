/**
 * Safari decodes no frame of a ``preload="metadata"`` video until it plays
 * or seeks: it fires ``loadedmetadata`` and stops at readyState 1, so a
 * player that waits for ``loadeddata`` shows black (and a "Loading video..."
 * overlay) until Play. Chrome decodes the first frame on its own. A seek 1 ms
 * in makes Safari fetch and decode it, then fire ``seeked``, ``loadeddata``
 * and ``canplay`` (checked in WebKit on the 720p renditions).
 */

/** No frame yet: ``HTMLMediaElement.HAVE_CURRENT_DATA``. */
const HAVE_CURRENT_DATA = 2;

/** Call on ``loadedmetadata``. Leaves alone an element that already has a
 *  frame, is seeking, or was placed somewhere by the page. */
export function showFirstFrame(el: HTMLMediaElement): void {
  if (el.readyState >= HAVE_CURRENT_DATA || el.seeking || el.currentTime !== 0) return;
  el.currentTime = 0.001;
}
