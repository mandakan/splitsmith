import { useEffect, useState } from "react";

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

export function useAudioBuffer(url: string): {
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
