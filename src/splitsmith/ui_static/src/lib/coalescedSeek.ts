/**
 * One seek in flight per media element (#1440). A seek on a 720p rendition
 * takes 0.1-0.2 s, and setting ``currentTime`` again before it lands starts
 * over: a drag that writes every pointer frame paints almost nothing. While
 * the element is ``seeking`` this remembers only the latest requested time
 * and applies it on ``seeked``, so the picture keeps up as best it can and
 * the last requested time always lands.
 *
 * Every drag or scrub seek on an element goes through ``coalescedSeek``; a
 * direct ``currentTime`` write elsewhere (a source swap, a loop wrap) is
 * fine between gestures, and a direct seek while nothing is seeking clears
 * nothing it should not: the next ``coalescedSeek`` call that finds the
 * element idle writes straight through and drops any stale request.
 */

/** The slice of HTMLMediaElement the rule needs, so a test can fake it. */
export interface SeekableMedia {
  currentTime: number;
  readonly seeking: boolean;
  addEventListener(type: "seeked", listener: () => void): void;
  removeEventListener(type: "seeked", listener: () => void): void;
}

interface State {
  /** The latest time asked for while a seek was in flight, null when none waits. */
  pending: number | null;
  /** The ``seeked`` listener, attached while a request waits. */
  onSeeked: (() => void) | null;
}

const states = new WeakMap<SeekableMedia, State>();

function stateOf(el: SeekableMedia): State {
  let s = states.get(el);
  if (!s) {
    s = { pending: null, onSeeked: null };
    states.set(el, s);
  }
  return s;
}

function detach(el: SeekableMedia, s: State) {
  if (s.onSeeked) el.removeEventListener("seeked", s.onSeeked);
  s.onSeeked = null;
}

/** Seek ``el`` to ``t``, or, while a seek is in flight, make ``t`` the one applied when it lands. */
export function coalescedSeek(el: SeekableMedia, t: number): void {
  const s = stateOf(el);
  if (!el.seeking) {
    s.pending = null;
    detach(el, s);
    el.currentTime = t;
    return;
  }
  s.pending = t;
  if (s.onSeeked) return;
  const onSeeked = () => {
    const next = s.pending;
    s.pending = null;
    if (next === null) {
      detach(el, s);
      return;
    }
    // Stay attached: this seek may be overtaken by another request too.
    el.currentTime = next;
  };
  s.onSeeked = onSeeked;
  el.addEventListener("seeked", onSeeked);
}

/** The time waiting for the seek in flight to land, if any (for tests and diagnostics). */
export function pendingSeek(el: SeekableMedia): number | null {
  return states.get(el)?.pending ?? null;
}
