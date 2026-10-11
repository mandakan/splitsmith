import { describe, expect, it } from "vitest";

import { cancelCoalescedSeek, coalescedSeek, pendingSeek, seekNow, type SeekableMedia } from "./coalescedSeek";

/** A media element whose every seek takes ``land()`` to finish, like a real decoder. */
class FakeMedia implements SeekableMedia {
  seeking = false;
  /** Every time a seek was started, in order. */
  started: number[] = [];
  private t = 0;
  currentSrc = "http://x/a.mp4";
  private listeners = new Map<string, Set<() => void>>();
  get currentTime() {
    return this.t;
  }
  set currentTime(v: number) {
    this.t = v;
    this.seeking = true;
    this.started.push(v);
  }
  addEventListener(type: string, l: () => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(l);
  }
  removeEventListener(type: string, l: () => void) {
    this.listeners.get(type)?.delete(l);
  }
  fire(type: string) {
    [...(this.listeners.get(type) ?? [])].forEach((l) => l());
  }
  /** The seek in flight lands: ``seeking`` clears and ``seeked`` fires. */
  land() {
    this.seeking = false;
    this.fire("seeked");
  }
  get listenerCount() {
    return [...this.listeners.values()].reduce((n, s) => n + s.size, 0);
  }
}

describe("coalescedSeek and a source change", () => {
  it("drops a pending seek on loadstart / emptied: the new source never gets it", () => {
    for (const event of ["loadstart", "emptied"]) {
      const m = new FakeMedia();
      coalescedSeek(m, 1);
      coalescedSeek(m, 4);
      // A PiP swap or a re-cut: the element loads another source mid-seek.
      m.currentSrc = "http://x/b.mp4";
      m.fire(event);
      expect(pendingSeek(m)).toBeNull();
      m.land();
      expect(m.started).toEqual([1]);
      expect(m.listenerCount).toBe(0);
    }
  });

  it("drops a pending seek whose source is no longer the element's, even without a load event", () => {
    const m = new FakeMedia();
    coalescedSeek(m, 1);
    coalescedSeek(m, 4);
    m.currentSrc = "http://x/b.mp4";
    m.land();
    expect(m.started).toEqual([1]);
    expect(m.listenerCount).toBe(0);
  });

  it("a direct write through seekNow supersedes it, and so does cancelCoalescedSeek", () => {
    const m = new FakeMedia();
    coalescedSeek(m, 1);
    coalescedSeek(m, 4);
    seekNow(m, 9); // a loop wrap or a source-swap carry
    m.land();
    m.land();
    expect(m.started).toEqual([1, 9]);
    expect(m.currentTime).toBe(9);

    const n = new FakeMedia();
    coalescedSeek(n, 1);
    coalescedSeek(n, 4);
    cancelCoalescedSeek(n);
    n.land();
    expect(n.started).toEqual([1]);
    expect(n.listenerCount).toBe(0);
  });
});

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
