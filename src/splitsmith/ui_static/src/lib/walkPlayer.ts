/**
 * The phone walk's listening: the stop's window played from the fixture's
 * decoded samples through Web Audio, never through a media element.
 *
 * On a phone the page's video is the whole source clip streamed from the
 * desk; a seek into it took longer than a 0.4 s pass, so a pass ended before
 * its shot was heard, and the silence between passes (a pause, then ``play()``
 * from a timer, not a tap) was refused by iOS after the first pass. A buffer
 * of the window followed by its silence, set to loop, needs neither a seek nor
 * a second ``play()``: the context is started once inside a tap and runs.
 */

import type { DecodedAudio } from "@/lib/useDecodedAudio";

/** The samples one pass plays: ``start`` to ``end`` of the clip, then
 *  ``gapS`` of silence (as long in the buffer's own time). */
export function passSamples(
  samples: Float32Array,
  sampleRate: number,
  start: number,
  end: number,
  gapS: number,
): Float32Array {
  const a = Math.max(0, Math.min(samples.length, Math.round(start * sampleRate)));
  const b = Math.max(a, Math.min(samples.length, Math.round(end * sampleRate)));
  const out = new Float32Array(b - a + Math.max(0, Math.round(gapS * sampleRate)));
  out.set(samples.subarray(a, b));
  return out;
}

type AudioSessionNavigator = Navigator & { audioSession?: { type: string } };

export class WalkPlayer {
  private ctx: AudioContext | null = null;
  private node: AudioBufferSourceNode | null = null;

  get playing(): boolean {
    return this.node != null;
  }

  /** Plays ``start``..``end`` once, or on repeat with ``gapS`` of silence
   *  between passes, at ``rate`` (0.5 is half speed, the silence included).
   *  The first call must come from a tap: that is when a phone lets the page
   *  start sound. */
  play(audio: DecodedAudio, start: number, end: number, opts: { loop: boolean; gapS: number; rate: number }): void {
    this.stop();
    if (!this.ctx) {
      // Sound with the silent switch on, like the video did: Web Audio is
      // "ambient" on iOS unless the session says otherwise.
      const session = (navigator as AudioSessionNavigator).audioSession;
      if (session) session.type = "playback";
      this.ctx = new AudioContext();
    }
    const ctx = this.ctx;
    if (ctx.state === "suspended") void ctx.resume();
    const data = passSamples(audio.samples, audio.sampleRate, start, end, opts.loop ? opts.gapS * opts.rate : 0);
    if (data.length === 0) return;
    const buffer = ctx.createBuffer(1, data.length, audio.sampleRate);
    buffer.getChannelData(0).set(data);
    const node = ctx.createBufferSource();
    node.buffer = buffer;
    node.loop = opts.loop;
    node.playbackRate.value = opts.rate;
    node.connect(ctx.destination);
    node.onended = () => {
      if (this.node === node) this.node = null;
    };
    node.start();
    this.node = node;
  }

  stop(): void {
    const node = this.node;
    this.node = null;
    if (node) {
      try {
        node.stop();
      } catch {
        // Already ended.
      }
      node.disconnect();
    }
  }

  close(): void {
    this.stop();
    void this.ctx?.close();
    this.ctx = null;
  }
}
