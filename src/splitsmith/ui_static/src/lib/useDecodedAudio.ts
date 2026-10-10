/**
 * A fixture's audio as samples, decoded once in the browser, so the review
 * walk can draw the wave itself (the shape the shot-time guide's figures
 * show) rather than its per-millisecond level. ``null`` until decoded, and
 * for good where the browser cannot decode (tests, an old browser): the
 * walk then draws the level as before.
 */

import { useEffect, useState } from "react";

export interface DecodedAudio {
  samples: Float32Array;
  sampleRate: number;
  /** The loudest absolute sample: the same reference the level peaks are
   *  normalized to, so a wave and a level draw on one scale. */
  maxAbs: number;
}

export function useDecodedAudio(url: string | null): DecodedAudio | null {
  const [audio, setAudio] = useState<DecodedAudio | null>(null);
  useEffect(() => {
    setAudio(null);
    const Ctx: typeof AudioContext | undefined =
      typeof window === "undefined"
        ? undefined
        : (window.AudioContext ??
          (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext);
    if (!url || !Ctx) return;
    let alive = true;
    const ctx = new Ctx();
    fetch(url)
      .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(String(r.status)))))
      .then((buf) => ctx.decodeAudioData(buf))
      .then((decoded) => {
        if (!alive) return;
        const samples = decoded.getChannelData(0);
        let maxAbs = 0;
        for (let i = 0; i < samples.length; i++) {
          const v = Math.abs(samples[i]);
          if (v > maxAbs) maxAbs = v;
        }
        setAudio({ samples, sampleRate: decoded.sampleRate, maxAbs: maxAbs || 1 });
      })
      .catch(() => {
        // The level stays the drawing; nothing depends on the samples.
      })
      .finally(() => {
        void ctx.close();
      });
    return () => {
      alive = false;
    };
  }, [url]);
  return audio;
}
