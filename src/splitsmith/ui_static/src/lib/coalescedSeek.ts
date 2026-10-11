/**
 * One seek in flight per media element (#1440). A seek on a 720p rendition
 * takes 0.1-0.2 s, and setting ``currentTime`` again before it lands starts
 * over: a drag that writes every pointer frame paints almost nothing. While
 * the element is ``seeking`` this remembers only the latest requested time
 * and applies it on ``seeked``, so the picture keeps up as best it can and
 * the last requested time always lands.
 *
 * A pending request belongs to the source it was asked against: a PiP swap
 * maps times through another beep, a re-cut swaps the rendition. It is
 * dropped on ``emptied`` / ``loadstart`` and whenever ``currentSrc`` differs
 * from the one recorded with it, never applied to the new source.
 *
 * Every other write to an element that takes coalesced seeks goes through
 * ``seekNow`` (or calls ``cancelCoalescedSeek`` first), so a stale request
 * can never land after it: a loop wrap, a source-swap carry, a parked
 * position. Elements that never take a coalesced seek (the PiP inset, the
 * detached primary audio, the Results player) need neither.
 */

/** The slice of HTMLMediaElement the rule needs, so a test can fake it. */
export interface SeekableMedia {
  currentTime: number;
  readonly seeking: boolean;
  /** The resolved source; a request asked against another one is dropped. */
  readonly currentSrc?: string;
  addEventListener(type: "seeked" | "emptied" | "loadstart", listener: () => void): void;
  removeEventListener(type: "seeked" | "emptied" | "loadstart", listener: () => void): void;
}

interface State {
  /** The latest time asked for while a seek was in flight, null when none waits. */
  pending: number | null;
  /** ``currentSrc`` when ``pending`` was asked for. */
  src: string | undefined;
  /** The listeners, attached while a request waits. */
  onSeeked: (() => void) | null;
  onReset: (() => void) | null;
}

const states = new WeakMap<SeekableMedia, State>();

function stateOf(el: SeekableMedia): State {
  let s = states.get(el);
  if (!s) {
    s = { pending: null, src: undefined, onSeeked: null, onReset: null };
    states.set(el, s);
  }
  return s;
}

function clear(el: SeekableMedia, s: State) {
  s.pending = null;
  if (s.onSeeked) el.removeEventListener("seeked", s.onSeeked);
  if (s.onReset) {
    el.removeEventListener("emptied", s.onReset);
    el.removeEventListener("loadstart", s.onReset);
  }
  s.onSeeked = null;
  s.onReset = null;
}

/** Drop any request waiting on ``el``'s seek in flight. */
export function cancelCoalescedSeek(el: SeekableMedia): void {
  const s = states.get(el);
  if (s) clear(el, s);
}

/** A direct seek on an element that takes coalesced ones: drops what waits, then writes. */
export function seekNow(el: SeekableMedia, t: number): void {
  cancelCoalescedSeek(el);
  el.currentTime = t;
}

/** Seek ``el`` to ``t``, or, while a seek is in flight, make ``t`` the one applied when it lands. */
export function coalescedSeek(el: SeekableMedia, t: number): void {
  const s = stateOf(el);
  if (!el.seeking) {
    seekNow(el, t);
    return;
  }
  s.pending = t;
  s.src = el.currentSrc;
  if (s.onSeeked) return;
  const onSeeked = () => {
    const next = s.pending;
    if (next === null || el.currentSrc !== s.src) {
      clear(el, s);
      return;
    }
    s.pending = null;
    // Stay attached: this seek may be overtaken by another request too.
    el.currentTime = next;
  };
  const onReset = () => clear(el, s);
  s.onSeeked = onSeeked;
  s.onReset = onReset;
  el.addEventListener("seeked", onSeeked);
  el.addEventListener("emptied", onReset);
  el.addEventListener("loadstart", onReset);
}

/** The time waiting for the seek in flight to land, if any (for tests and diagnostics). */
export function pendingSeek(el: SeekableMedia): number | null {
  return states.get(el)?.pending ?? null;
}
