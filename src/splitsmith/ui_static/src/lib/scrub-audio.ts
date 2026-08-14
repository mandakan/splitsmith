/**
 * Grain-based scrub audio: the clip is decoded once into an AudioBuffer
 * and dragging fires short windowed grains - an imitation of continuous
 * varispeed. Every failure path returns null and the caller degrades to
 * silent seeking; scrubbing must never block the audit pass.
 */
export const GRAIN_S = 0.06;
const GRAIN_GAP_S = 0.03;
const RAMP_S = 0.01;

export interface Scrubber {
  grainAt(time: number): void;
  dispose(): void;
}

export async function createScrubber(
  url: string,
  makeContext: () => AudioContext = () => new AudioContext(),
): Promise<Scrubber | null> {
  let ctx: AudioContext;
  let buffer: AudioBuffer;
  try {
    ctx = makeContext();
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`audio fetch ${resp.status}`);
    buffer = await ctx.decodeAudioData(await resp.arrayBuffer());
  } catch {
    return null;
  }
  let lastAt = -Infinity;
  return {
    grainAt(time: number) {
      const now = ctx.currentTime;
      if (now - lastAt < GRAIN_GAP_S) return;
      lastAt = now;
      const offset = Math.max(0, Math.min(time, buffer.duration - GRAIN_S));
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      const gain = ctx.createGain();
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(1, now + RAMP_S);
      gain.gain.setValueAtTime(1, now + GRAIN_S - RAMP_S);
      gain.gain.linearRampToValueAtTime(0, now + GRAIN_S);
      source.connect(gain).connect(ctx.destination);
      source.start(now, offset, GRAIN_S);
    },
    dispose() {
      void ctx.close();
    },
  };
}
