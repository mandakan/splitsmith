/**
 * The LaneEditor's drag state and the pure frame math over it: what one
 * pointer frame, an Esc or a keyboard nudge does to the event list. The
 * geometry rules themselves (clamp, snap, ids) live in lib/events.ts; this
 * module only composes them per gesture so LaneEditor.tsx can stay DOM.
 */
import type { StageEvent, StageEventKind } from "@/lib/api";
import { MIN_EVENT_S, clampToLane, nextEventId } from "@/lib/events";

export const LANES: readonly StageEventKind[] = ["movement", "reload", "activation"];
export const SNAP_PX = 8;
export const DRAG_THRESHOLD_PX = 6;
export const TOUCH_DRAG_THRESHOLD_PX = 10;

export interface DragBase {
  pointerId: number;
  element: HTMLElement;
  startX: number;
  startY: number;
  thresholdPx: number;
  moved: boolean;
  /** The list as it stood when the gesture began, to tell a no-op release from a real one (#1325). */
  startEvents: StageEvent[];
}
export type Drag =
  | (DragBase & { mode: "create"; kind: StageEventKind; anchorT: number; id: string | null })
  | (DragBase & { mode: "edge"; edge: "start" | "end"; id: string; before: StageEvent })
  | (DragBase & { mode: "body"; id: string; before: StageEvent; grabOffsetT: number });
/** A drag as the gesture's start knows it; the pointer fields are filled in by the editor. */
export type DragInit = Drag extends infer D ? (D extends Drag ? Omit<D, keyof DragBase> : never) : never;

export interface Frame {
  events: StageEvent[];
  /** Where the video seeks: the moving edge, or the leading edge for a body move. */
  seek: number;
  /** The id a create minted on its first frame. */
  id?: string;
}

/** A user touch turns an auto proposal into the user's own region. */
export const touched = (e: StageEvent): StageEvent => (e.source === "auto" ? { ...e, source: "manual" } : e);
const replace = (events: StageEvent[], next: StageEvent) => events.map((e) => (e.id === next.id ? next : e));

/**
 * Same list by position: a drag or nudge that ends up exactly where its
 * gesture started is a no-op and must not commit (#1325), even though
 * ``touched()`` runs unconditionally inside ``dragFrame``'s edge/body
 * branches and inside ``nudge`` and may have flipped an ``auto`` region to
 * ``manual`` along the way -- a no-op never confirms a proposal, so
 * ``source`` is deliberately not compared. ``replace`` keeps order and
 * length, so index-for-index is enough; a genuine create (a longer list) or
 * delete never compares equal here.
 */
export function sameEvents(a: StageEvent[], b: StageEvent[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((x, i) => {
    const y = b[i];
    return x.id === y.id && x.kind === y.kind && x.start === y.start && x.end === y.end;
  });
}

/**
 * One frame of a drag past its threshold. ``t`` is the time under the
 * pointer; ``snap`` applies the shot snap (identity under Alt). Null when
 * the frame changes nothing (a create still shorter than MIN_EVENT_S).
 */
export function dragFrame(
  drag: Drag,
  current: StageEvent[],
  t: number,
  snap: (t: number) => number,
  duration: number,
): Frame | null {
  if (drag.mode === "create") {
    // The moving edge stops at the nearest same-lane region on either side of the anchor.
    const lane = current.filter((x) => x.kind === drag.kind && x.id !== drag.id);
    const lo = Math.max(0, ...lane.filter((x) => x.end <= drag.anchorT).map((x) => x.end));
    const hi = Math.min(duration, ...lane.filter((x) => x.start >= drag.anchorT).map((x) => x.start));
    const edge = Math.min(Math.max(snap(t), lo), hi);
    const start = Math.min(drag.anchorT, edge);
    const end = Math.max(drag.anchorT, edge);
    if (end - start < MIN_EVENT_S) return null;
    if (drag.id === null) {
      const id = nextEventId(current);
      return { events: [...current, { id, kind: drag.kind, start, end, source: "manual" }], seek: edge, id };
    }
    const me = current.find((x) => x.id === drag.id);
    return me ? { events: replace(current, { ...me, start, end }), seek: edge } : null;
  }
  // Neighbour sides are judged from where the region stood when the drag began.
  const { before } = drag;
  const base = replace(current, before);
  if (drag.mode === "edge") {
    const edge = snap(t);
    const c = clampToLane(
      base,
      drag.id,
      drag.edge === "start" ? edge : before.start,
      drag.edge === "end" ? edge : before.end,
    );
    return { events: replace(current, touched({ ...before, ...c })), seek: drag.edge === "start" ? c.start : c.end };
  }
  // Body: no snap, the length is kept.
  const len = before.end - before.start;
  // The region slides inside the free gap it started in: [previous end, next start - len].
  const lane = base.filter((x) => x.kind === before.kind && x.id !== before.id);
  const lo = Math.max(0, ...lane.filter((x) => x.end <= before.start).map((x) => x.end));
  const hi = Math.min(duration, ...lane.filter((x) => x.start >= before.end).map((x) => x.start)) - len;
  const start = Math.min(Math.max(t - drag.grabOffsetT, lo), Math.max(hi, lo));
  return { events: replace(current, touched({ ...before, start, end: start + len })), seek: start };
}

/** The list Esc restores: the region as it stood, or without the one being created. */
export function cancelFrame(drag: Drag, current: StageEvent[]): StageEvent[] | null {
  if (!drag.moved) return null;
  if (drag.mode === "create") return drag.id === null ? null : current.filter((x) => x.id !== drag.id);
  return replace(current, drag.before);
}

/** Arrow nudge of the selected region: one frame, or 100 ms with Alt; Shift moves the end. */
export function nudge(
  current: StageEvent[],
  id: string,
  dir: -1 | 1,
  mods: { alt: boolean; shift: boolean },
  fps: number,
): StageEvent[] | null {
  const me = current.find((x) => x.id === id);
  if (!me) return null;
  const step = dir * (mods.alt ? 0.1 : 1 / fps);
  const c = mods.shift
    ? clampToLane(current, id, me.start, me.end + step)
    : clampToLane(current, id, me.start + step, me.end);
  return replace(current, touched({ ...me, ...c }));
}
