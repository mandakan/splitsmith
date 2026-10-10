/**
 * The primary camera's sound while a secondary camera is big (epic #1405:
 * the primary is the audio and beep source wherever it sits). The big
 * player is muted and a detached <audio> of the primary's own stream
 * follows it on the beep clock through ``attachInsetSync``, the inset's
 * rules unchanged, plus one lead correction per play start.
 *
 * Why the lead correction (measured in headless Chromium on the demo
 * match, #1408 / #1409): the <audio> starts some 40-85 ms after the
 * picture on every play start, a different amount each time, then holds
 * it. The beeps are not the cause (5.000 s in the WAV, both trims and the
 * rendition, to the sample). A tighter drift line makes it worse: at 45 ms
 * the audio re-seeked ~4 times a second, stuttered and still trailed by
 * ~100 ms, since a seek of playing media costs about what it closes.
 *
 * So ``LEAD_MEASURE_MS`` after each play start (and after a seek of the
 * big video while playing) the lag is measured once and the audio seeked
 * ahead of the picture by the time a seek costs it (``seekLead``), so it
 * lands on the picture as it resumes. That cost is not the lag: seeking an
 * MP4 trim measured 100-140 ms against a 40-55 ms start lag (a WAV seeks
 * cheaper), so the first correction leads by the lag, the residual after
 * it teaches the cost, and up to ``LEAD_MAX_SEEKS`` corrections per play
 * start refine it; the learned cost is kept for the next play start.
 * Never per frame. Headless Chromium has no audio output, so the media
 * clocks are a proxy for what is heard.
 *
 * ``primaryBeep`` must be the beep's position in the file ``src`` names
 * (the primary's ``beep_in_clip`` for the kind it streams; the rendition
 * shares the trim's anchor), never a beep measured in another file.
 *
 * Swap back, a missing input, or an audio error: the <audio> is dropped
 * and the big player unmuted. On error ``onError`` fires so the page can
 * mark the stream kind failed and hand the next one.
 */
import { useEffect, useRef } from "react";

import { PIP_DRIFT_PLAYING_S } from "@/lib/pip";
import { attachInsetSync } from "@/lib/pipSync";

/** How long after a play start (or a seek while playing) the lag is
 *  measured: long enough for the audio to have started and settled. */
export const LEAD_MEASURE_MS = 800;
/** A lag under this is left alone: one more seek is not worth it. */
export const LEAD_MIN_S = 0.015;
/** Corrections per play start: the first leads by the lag, the next ones
 *  by the seek cost learned from the residual. */
export const LEAD_MAX_SEEKS = 3;

/** The audio's lag behind the picture, in seconds (negative: ahead), both
 *  clocks mapped through their own file's beep. */
export function audioLag(args: { bigTime: number; bigBeep: number; audioTime: number; primaryBeep: number }): number {
  return args.bigTime - args.bigBeep - (args.audioTime - args.primaryBeep);
}

/** Is ``lag`` worth a corrective seek? Not under ``LEAD_MIN_S``, and not
 *  past the drift line, which the sync's own forced seek owns. */
export function lagNeedsLead(lag: number): boolean {
  return Math.abs(lag) >= LEAD_MIN_S && Math.abs(lag) <= PIP_DRIFT_PLAYING_S;
}

/** How far ahead of the picture to seek. ``cost`` is the learned time a
 *  seek of this stream takes to resume (``null`` before the first
 *  correction, when the lag stands in for it). After a correction that led
 *  by ``prevLead`` and left ``lag`` behind, the cost is ``prevLead + lag``. */
export function seekLead(lag: number, cost: number | null): number {
  return Math.min(Math.max(cost ?? lag, -LEAD_MAX_S), LEAD_MAX_S);
}

/** A lead never reaches the drift line: the sync would seek the audio
 *  straight back. */
export const LEAD_MAX_S = PIP_DRIFT_PLAYING_S * 0.9;

export function usePrimaryAudio(args: {
  /** The page's big <video>; ``null`` until it mounts. */
  bigVideo: HTMLMediaElement | null;
  /** The big camera is the primary: its own sound plays, nothing else. */
  bigIsPrimary: boolean;
  /** The primary's stream URL; ``null`` when nothing is left to try. */
  src: string | null;
  /** The beep's position in ``src``'s file. */
  primaryBeep: number | null;
  /** The beep's position in the big video's file. */
  bigBeep: number | null;
  onError?: () => void;
}): { active: boolean } {
  const { bigVideo, bigIsPrimary, src, primaryBeep, bigBeep, onError } = args;
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  const active =
    bigVideo != null && !bigIsPrimary && src != null && primaryBeep != null && bigBeep != null;

  useEffect(() => {
    if (!bigVideo) return;
    if (!active || src == null || primaryBeep == null || bigBeep == null) {
      bigVideo.muted = false;
      return;
    }
    const audio = new Audio();
    audio.preload = "auto";
    audio.src = src;
    const detach = attachInsetSync(bigVideo, audio, { bigBeep, insetBeep: primaryBeep });
    audio.muted = false;
    bigVideo.muted = true;

    // Lead corrections per play start and per seek while playing.
    let timer: ReturnType<typeof setTimeout> | null = null;
    let seeks = 0; // corrections this play start
    let lastLead: number | null = null; // the lead the last correction used
    let cost: number | null = null; // learned seek cost, kept across play starts
    const cancel = () => {
      if (timer != null) clearTimeout(timer);
      timer = null;
    };
    const measure = () => {
      timer = null;
      if (bigVideo.paused || audio.paused || audio.seeking || bigVideo.seeking) return;
      const lag = audioLag({ bigTime: bigVideo.currentTime, bigBeep, audioTime: audio.currentTime, primaryBeep });
      // The residual after our own correction teaches the seek cost.
      if (lastLead != null) cost = lastLead + lag;
      if (!lagNeedsLead(lag) || seeks >= LEAD_MAX_SEEKS) return;
      const lead = seekLead(lag, cost);
      const mapped = bigVideo.currentTime - bigBeep + primaryBeep;
      try {
        audio.currentTime = Math.max(0, mapped + lead);
      } catch {
        return; /* not seekable: the drift rule still holds the line */
      }
      seeks += 1;
      lastLead = lead;
      timer = setTimeout(measure, LEAD_MEASURE_MS);
    };
    const arm = () => {
      cancel();
      seeks = 0;
      lastLead = null;
      if (!bigVideo.paused) timer = setTimeout(measure, LEAD_MEASURE_MS);
    };
    bigVideo.addEventListener("play", arm);
    bigVideo.addEventListener("seeked", arm);
    bigVideo.addEventListener("pause", cancel);
    bigVideo.addEventListener("seeking", cancel);
    if (!bigVideo.paused) arm(); // swapped in mid-playback

    let done = false;
    const stop = () => {
      if (done) return;
      done = true;
      cancel();
      bigVideo.removeEventListener("play", arm);
      bigVideo.removeEventListener("seeked", arm);
      bigVideo.removeEventListener("pause", cancel);
      bigVideo.removeEventListener("seeking", cancel);
      audio.removeEventListener("error", onAudioError);
      detach();
      try {
        audio.pause();
      } catch {
        /* not implemented (jsdom) */
      }
      audio.removeAttribute("src");
      bigVideo.muted = false;
    };
    function onAudioError() {
      stop();
      onErrorRef.current?.();
    }
    audio.addEventListener("error", onAudioError);
    return stop;
  }, [bigVideo, active, src, primaryBeep, bigBeep]);

  return { active };
}
