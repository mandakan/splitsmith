/**
 * Keeps the PiP inset's <video> on the big video's clock (issue #1406).
 * DOM only, no React: PipView attaches it in an effect, and a plain page
 * can drive it against real media.
 *
 * Rules (decisions in lib/pip.ts ``insetPlan`` / ``shouldCorrectDrift``):
 * - The inset is muted (unless ``audible``) and follows play / pause and
 *   rate.
 * - Outside the inset clip (the big video before the inset's start or
 *   past its end) the inset holds paused at the clamp and is never
 *   played: playing ended media restarts it at 0, which looped a short
 *   inset and yanked an early one back every timeupdate.
 * - The big ``seeking`` forces a seek; ``seeked``, ``timeupdate`` and
 *   ``play`` only correct drift past the threshold; ``pause`` forces the
 *   held instant.
 * - Drift is never corrected while the inset is still seeking or has no
 *   metadata, so a slow inset can catch up instead of being re-seeked.
 * - The big ``waiting`` / ``stalled`` (without enough data) holds the
 *   inset until the big ``playing`` again.
 */
import { insetPlan, shouldCorrectDrift } from "@/lib/pip";

/** HTMLMediaElement.HAVE_METADATA / HAVE_FUTURE_DATA, spelled out so the
 *  module does not need the DOM constants at import. */
const HAVE_METADATA = 1;
const HAVE_FUTURE_DATA = 3;

export function attachInsetSync(
  big: HTMLMediaElement,
  inset: HTMLMediaElement,
  beeps: { bigBeep: number; insetBeep: number },
  /** ``audible``: the follower is the page's sound, not a picture (Audit
   *  plays the primary's audio through a hidden ``<audio>`` while a
   *  secondary is big); every other rule is the inset's. */
  opts: { audible?: boolean } = {},
): () => void {
  let stalled = false;
  inset.muted = !opts.audible;

  const sync = (force: boolean) => {
    const plan = insetPlan({
      bigTime: big.currentTime,
      bigBeep: beeps.bigBeep,
      insetBeep: beeps.insetBeep,
      insetDuration: inset.duration,
      bigPaused: big.paused,
      bigStalled: stalled,
    });
    if (inset.playbackRate !== big.playbackRate) inset.playbackRate = big.playbackRate;
    if (!plan.play && !inset.paused) inset.pause();
    if (inset.readyState < HAVE_METADATA) return; // loadedmetadata syncs again
    const drift =
      !inset.seeking &&
      shouldCorrectDrift({ target: plan.target, current: inset.currentTime, playing: plan.play });
    if (force || drift || (plan.play && inset.ended)) {
      try {
        inset.currentTime = plan.target;
      } catch {
        /* not seekable yet: the next event tries again */
      }
    }
    if (plan.play && inset.paused) {
      const p = inset.play();
      if (p && typeof p.catch === "function") p.catch(() => {});
    }
  };

  const onPlay = () => sync(false);
  const onPause = () => {
    stalled = false;
    sync(true);
  };
  const onSeeking = () => sync(true);
  const onSeeked = () => sync(false);
  const onTime = () => sync(false);
  const onRate = () => {
    inset.playbackRate = big.playbackRate;
  };
  const onWaiting = () => {
    stalled = true;
    sync(false);
  };
  const onStalled = () => {
    if (big.readyState >= HAVE_FUTURE_DATA) return; // still has data to play
    stalled = true;
    sync(false);
  };
  const onPlaying = () => {
    stalled = false;
    sync(false);
  };
  const onInsetReady = () => sync(true);
  const onInsetEnded = () => sync(false);

  big.addEventListener("play", onPlay);
  big.addEventListener("pause", onPause);
  big.addEventListener("seeking", onSeeking);
  big.addEventListener("seeked", onSeeked);
  big.addEventListener("timeupdate", onTime);
  big.addEventListener("ratechange", onRate);
  big.addEventListener("waiting", onWaiting);
  big.addEventListener("stalled", onStalled);
  big.addEventListener("playing", onPlaying);
  inset.addEventListener("loadedmetadata", onInsetReady);
  inset.addEventListener("ended", onInsetEnded);
  if (inset.readyState >= HAVE_METADATA) sync(true);

  return () => {
    big.removeEventListener("play", onPlay);
    big.removeEventListener("pause", onPause);
    big.removeEventListener("seeking", onSeeking);
    big.removeEventListener("seeked", onSeeked);
    big.removeEventListener("timeupdate", onTime);
    big.removeEventListener("ratechange", onRate);
    big.removeEventListener("waiting", onWaiting);
    big.removeEventListener("stalled", onStalled);
    big.removeEventListener("playing", onPlaying);
    inset.removeEventListener("loadedmetadata", onInsetReady);
    inset.removeEventListener("ended", onInsetEnded);
  };
}
