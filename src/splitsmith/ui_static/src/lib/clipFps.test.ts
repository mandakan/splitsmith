import { describe, expect, it } from "vitest";

import type { CoachVideoEntry } from "@/lib/api";
import { DEFAULT_FPS, clipFps } from "@/lib/clipFps";

const video = (role: "primary" | "secondary", fps: number | null | undefined): CoachVideoEntry =>
  ({ path: `${role}.mp4`, role, beep_in_clip: 5, kind: "trim", fps }) as CoachVideoEntry;

describe("clipFps", () => {
  it("is the primary's frame rate", () => {
    expect(clipFps([video("secondary", 25), video("primary", 50)])).toBe(50);
  });
  it("falls back to 30 when the server does not know it or sends nonsense", () => {
    expect(clipFps(undefined)).toBe(DEFAULT_FPS);
    expect(clipFps([video("primary", null)])).toBe(30);
    expect(clipFps([video("primary", undefined)])).toBe(30);
    expect(clipFps([video("primary", 0)])).toBe(30);
    expect(clipFps([video("primary", 1000)])).toBe(30);
  });
});
