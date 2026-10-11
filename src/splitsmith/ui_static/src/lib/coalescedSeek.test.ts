import { describe, expect, it } from "vitest";

import { coalescedSeek, pendingSeek, type SeekableMedia } from "./coalescedSeek";

/** A media element whose every seek takes ``land()`` to finish, like a real decoder. */
class FakeMedia implements SeekableMedia {
  seeking = false;
  /** Every time a seek was started, in order. */
  started: number[] = [];
  private t = 0;
  private listeners = new Set<() => void>();
  get currentTime() {
    return this.t;
  }
  set currentTime(v: number) {
    this.t = v;
    this.seeking = true;
    this.started.push(v);
  }
  addEventListener(_type: "seeked", l: () => void) {
    this.listeners.add(l);
  }
  removeEventListener(_type: "seeked", l: () => void) {
    this.listeners.delete(l);
  }
  /** The seek in flight lands: ``seeking`` clears and ``seeked`` fires. */
  land() {
    this.seeking = false;
    [...this.listeners].forEach((l) => l());
  }
  get listenerCount() {
    return this.listeners.size;
  }
}

describe("coalescedSeek", () => {
  it("seeks straight through when nothing is in flight", () => {
    const m = new FakeMedia();
    coalescedSeek(m, 1.5);
    expect(m.started).toEqual([1.5]);
    expect(m.currentTime).toBe(1.5);
  });

  it("keeps one seek in flight through a burst and lands on the last request", () => {
    const m = new FakeMedia();
    coalescedSeek(m, 1);
    // Twenty pointer frames while the first seek decodes.
    for (let i = 1; i <= 20; i++) coalescedSeek(m, 1 + i * 0.05);
    expect(m.started).toEqual([1]);
    expect(pendingSeek(m)).toBeCloseTo(2, 6);
    m.land();
    // The latest request goes out once the first lands, and nothing in between.
    expect(m.started).toEqual([1, 2]);
    m.land();
    expect(m.currentTime).toBe(2);
    expect(m.seeking).toBe(false);
    expect(pendingSeek(m)).toBeNull();
    expect(m.listenerCount).toBe(0);
  });

  it("never drops the final position across several overlapping bursts", () => {
    const m = new FakeMedia();
    coalescedSeek(m, 1);
    coalescedSeek(m, 2);
    coalescedSeek(m, 3);
    m.land(); // 1 lands, 3 starts
    coalescedSeek(m, 4);
    coalescedSeek(m, 5);
    m.land(); // 3 lands, 5 starts
    m.land(); // 5 lands
    expect(m.started).toEqual([1, 3, 5]);
    expect(m.currentTime).toBe(5);
    expect(m.listenerCount).toBe(0);
  });

  it("an idle element drops a stale request rather than applying it later", () => {
    const m = new FakeMedia();
    coalescedSeek(m, 1);
    coalescedSeek(m, 2);
    // The seek was aborted (a source swap): seeked never fired, the element is idle again.
    m.seeking = false;
    coalescedSeek(m, 7);
    m.land();
    expect(m.started).toEqual([1, 7]);
    expect(m.currentTime).toBe(7);
  });
});
