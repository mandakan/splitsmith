import { useCallback, useEffect, useRef, useState } from "react";

// Visible context window in the zoomed waveform: candidate is centered;
// the play window (pre/post) is highlighted within this view. If the
// play window exceeds the context, the view widens to fit.
export const CONTEXT_HALF_MS = 750;

let _sharedAudioCtx: AudioContext | null = null;
export function getAudioCtx(): AudioContext {
  if (!_sharedAudioCtx) {
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    _sharedAudioCtx = new Ctor();
  }
  return _sharedAudioCtx;
}

// Decoded AudioBuffers are big (~12 MB/min mono float32 at 48 kHz). The
// cache uses Map insertion-order as LRU and is capped so a long Lab
// session can't keep every fixture resident.
export const AUDIO_CACHE_MAX = 3;
const _audioBufferCache = new Map<string, Promise<AudioBuffer>>();
export function loadAudioBuffer(url: string): Promise<AudioBuffer> {
  const existing = _audioBufferCache.get(url);
  if (existing) {
    _audioBufferCache.delete(url);
    _audioBufferCache.set(url, existing);
    return existing;
  }
  const ctx = getAudioCtx();
  const p = fetch(url)
    .then((r) => {
      if (!r.ok) throw new Error(`audio fetch failed: ${r.status}`);
      return r.arrayBuffer();
    })
    .then((buf) => ctx.decodeAudioData(buf));
  _audioBufferCache.set(url, p);
  p.catch(() => _audioBufferCache.delete(url));
  while (_audioBufferCache.size > AUDIO_CACHE_MAX) {
    const oldest = _audioBufferCache.keys().next().value;
    if (oldest === undefined) break;
    _audioBufferCache.delete(oldest);
  }
  return p;
}

// Clear cached buffers and close the shared AudioContext so its audio
// thread + scheduling state can be reclaimed. Called on Lab unmount.
export function disposeLabAudio(): void {
  _audioBufferCache.clear();
  if (_sharedAudioCtx) {
    _sharedAudioCtx.close().catch(() => {
      /* already closed */
    });
    _sharedAudioCtx = null;
  }
}

export function useAudioBuffer(url: string | null): {
  buffer: AudioBuffer | null;
  loading: boolean;
  error: string | null;
} {
  const [state, setState] = useState<{
    buffer: AudioBuffer | null;
    loading: boolean;
    error: string | null;
  }>({ buffer: null, loading: true, error: null });
  useEffect(() => {
    let alive = true;
    if (!url) {
      setState({ buffer: null, loading: false, error: null });
      return;
    }
    setState({ buffer: null, loading: true, error: null });
    loadAudioBuffer(url)
      .then((buf) => {
        if (alive) setState({ buffer: buf, loading: false, error: null });
      })
      .catch((err) => {
        if (alive)
          setState({
            buffer: null,
            loading: false,
            error: err instanceof Error ? err.message : String(err),
          });
      });
    return () => {
      alive = false;
    };
  }, [url]);
  return state;
}

/**
 * One-shot (non-looping) playback over a whole decoded buffer -- the
 * "play the complete stage" primitive the overview waveform uses, as
 * opposed to SnippetPlayer's looped candidate window.
 *
 * ``onPosition`` reports the playhead in buffer seconds, throttled to
 * ~12 fps: the overview spans a whole stage, so frame-rate playhead
 * updates would re-render the page for sub-pixel movement. Position is
 * approximated from AudioContext elapsed time (WebAudio does not expose
 * a source's internal offset). Playback stops at the buffer end and
 * flips ``playing`` back off.
 */
export function useBufferPlayback(
  buffer: AudioBuffer | null,
  onPosition: (t: number) => void,
  throttleMs = 80,
): {
  playing: boolean;
  play: (fromSeconds: number) => void;
  pause: () => void;
} {
  const [playing, setPlaying] = useState(false);
  const sourceRef = useRef<AudioBufferSourceNode | null>(null);
  const rafRef = useRef(0);
  const startedAtRef = useRef(0);
  const startOffsetRef = useRef(0);
  const lastEmitRef = useRef(0);
  const onPositionRef = useRef(onPosition);
  onPositionRef.current = onPosition;

  const pause = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    const src = sourceRef.current;
    sourceRef.current = null;
    if (src) {
      src.onended = null;
      try {
        src.stop();
      } catch {
        /* already stopped */
      }
      src.disconnect();
    }
    setPlaying(false);
  }, []);

  const play = useCallback(
    (fromSeconds: number) => {
      if (!buffer) return;
      pause();
      const ctx = getAudioCtx();
      if (ctx.state === "suspended") {
        ctx.resume().catch(() => {
          /* needs a user gesture -- the play click counts */
        });
      }
      const from = Math.min(Math.max(0, fromSeconds), Math.max(0, buffer.duration - 0.01));
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(ctx.destination);
      src.onended = () => {
        if (sourceRef.current === src) pause();
      };
      src.start(0, from);
      sourceRef.current = src;
      startedAtRef.current = ctx.currentTime;
      startOffsetRef.current = from;
      lastEmitRef.current = 0;
      setPlaying(true);

      const tick = () => {
        if (sourceRef.current !== src) return;
        const now = performance.now();
        if (now - lastEmitRef.current >= throttleMs) {
          lastEmitRef.current = now;
          const pos = startOffsetRef.current + (ctx.currentTime - startedAtRef.current);
          onPositionRef.current(Math.min(pos, buffer.duration));
        }
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
    },
    [buffer, pause, throttleMs],
  );

  // Teardown on unmount / buffer swap (fixture navigation).
  useEffect(() => pause, [buffer, pause]);

  return { playing, play, pause };
}
