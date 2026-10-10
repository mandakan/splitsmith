import { describe, expect, it } from "vitest";

import { clipTimeFromBeep, nextShown, stackCameras, stackSlots } from "@/lib/phoneCameraStack";
import type { PipCamera } from "@/lib/pip";

const cam = (id: string, over: Partial<PipCamera> = {}): PipCamera => ({
  id,
  label: id,
  primary: id === "c1",
  beepInClip: 1,
  src: `/${id}.mp4`,
  ...over,
});

describe("stackCameras", () => {
  it("stacks two or more cameras that line up, primary first", () => {
    expect(stackCameras([cam("c1"), cam("c2")])?.map((c) => c.id)).toEqual(["c1", "c2"]);
  });

  it("is null for one camera, so the dialog stays the one-camera player", () => {
    expect(stackCameras([cam("c1")])).toBeNull();
  });

  it("leaves out a camera with no beep, nothing to stream, or nothing left to try", () => {
    const cams = [
      cam("c1"),
      cam("c2", { beepInClip: null }),
      cam("c3", { src: null }),
      cam("c4", { unavailable: true }),
      cam("c5"),
    ];
    expect(stackCameras(cams)?.map((c) => c.id)).toEqual(["c1", "c5"]);
    expect(stackCameras(cams.slice(0, 4))).toBeNull();
  });

  it("is null when the primary itself cannot be lined up", () => {
    expect(stackCameras([cam("c1", { beepInClip: null }), cam("c2"), cam("c3")])).toBeNull();
  });
});

describe("stackSlots and the chooser", () => {
  const three = [cam("c1"), cam("c2"), cam("c3")];

  it("two cameras: the primary on top, the other under it, no counter", () => {
    const s = stackSlots([cam("c1"), cam("c2")], null);
    expect([s.top.id, s.bottom.id, s.counter]).toEqual(["c1", "c2", null]);
  });

  it("three cameras: one other at a time, counted among all", () => {
    expect(stackSlots(three, null).counter).toEqual({ n: 2, total: 3 });
    const s = stackSlots(three, "c3");
    expect([s.bottom.id, s.counter]).toEqual(["c3", { n: 3, total: 3 }]);
    // An id that left the stack falls back to the first other camera.
    expect(stackSlots(three, "gone").bottom.id).toBe("c2");
  });

  it("the chooser cycles the other cameras and wraps", () => {
    expect(nextShown(three, "c2")).toBe("c3");
    expect(nextShown(three, "c3")).toBe("c2");
    expect(nextShown(three, null)).toBe("c3");
  });
});

describe("clipTimeFromBeep", () => {
  it("maps seconds from the beep into a camera's clip, never before 0", () => {
    expect(clipTimeFromBeep(-0.5, { beepInClip: 5 })).toBe(4.5);
    expect(clipTimeFromBeep(-3, { beepInClip: 1 })).toBe(0);
  });
});
