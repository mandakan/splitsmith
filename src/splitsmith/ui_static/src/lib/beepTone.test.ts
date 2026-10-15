import { describe, expect, it } from "vitest";

import { stageTone, toneEndNear, toneLength } from "./beepTone";

/** 1 ms bins over 0..3 s: hum, a tone from 1.000 s at ``level`` for 0.3 s
 *  (``fade`` makes it fall to half by its end), then a reverb tail. */
function tone(level = 0.5, fade = false): number[] {
  const p = new Array(3000).fill(0.002);
  for (let i = 1000; i < 1300; i++) p[i] = fade ? level * (1 - (0.5 * (i - 1000)) / 300) : level;
  for (let i = 1300; i < 1400; i++) p[i] = 0.03;
  return p;
}
const range = { start: 0, end: 3 };

describe("the beep's tone", () => {
  it("is as long as from its start to where it stops", () => {
    expect(toneLength(tone(), range, 1.0)).toBeCloseTo(0.3, 3);
  });

  it("is found even when the tone fades as it plays", () => {
    expect(toneLength(tone(0.5, true), range, 1.0)).toBeCloseTo(0.3, 3);
  });

  it("has no length where no tone follows the start", () => {
    expect(toneLength(new Array(3000).fill(0.002), range, 1.0)).toBeNull();
  });

  it("ends where it stops, found from a line placed near the end", () => {
    expect(toneEndNear(tone(), range, 1.29)).toBeCloseTo(1.3, 3);
    expect(toneEndNear(tone(), range, 1.33)).toBeCloseTo(1.3, 3);
    expect(toneEndNear(new Array(3000).fill(0.002), range, 1.3)).toBeNull();
  });
});

describe("a stage's tone, from its head cams", () => {
  it("is their median, with how far apart they are", () => {
    expect(stageTone([0.293, 0.295, 0.3])).toEqual({ tone: 0.295, count: 3, spread: 0.007 });
    expect(stageTone([0.486, 0.49, 0.785, null])).toEqual({ tone: 0.49, count: 3, spread: 0.299 });
  });

  it("is unknown without any", () => {
    expect(stageTone([null, null])).toBeNull();
  });
});
