import { afterEach, describe, expect, it, vi } from "vitest";

import { GRAIN_S, createScrubber } from "@/lib/scrub-audio";

function fakeContext() {
  const gainNode = {
    gain: {
      setValueAtTime: vi.fn(),
      linearRampToValueAtTime: vi.fn(),
    },
    connect: vi.fn(() => ({ connect: vi.fn() })),
  };
  const source = {
    buffer: null as unknown,
    connect: vi.fn(() => gainNode),
    start: vi.fn(),
  };
  const ctx = {
    currentTime: 0,
    destination: {},
    createBufferSource: vi.fn(() => source),
    createGain: vi.fn(() => gainNode),
    decodeAudioData: vi.fn(async () => ({ duration: 10 })),
    close: vi.fn(async () => undefined),
  };
  return { ctx, source };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createScrubber", () => {
  it("returns null when the audio fetch fails (degrade to silent seeking)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404 })));
    const { ctx } = fakeContext();
    expect(await createScrubber("/audio", () => ctx as unknown as AudioContext)).toBeNull();
  });

  it("returns null when decode fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) })),
    );
    const { ctx } = fakeContext();
    ctx.decodeAudioData = vi.fn(async () => {
      throw new Error("bad data");
    });
    expect(await createScrubber("/audio", () => ctx as unknown as AudioContext)).toBeNull();
  });

  it("grainAt fires a windowed grain at the clamped offset", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) })),
    );
    const { ctx, source } = fakeContext();
    const scrubber = await createScrubber("/audio", () => ctx as unknown as AudioContext);
    expect(scrubber).not.toBeNull();
    scrubber?.grainAt(2.5);
    expect(source.start).toHaveBeenCalledWith(0, 2.5, GRAIN_S);
    scrubber?.grainAt(-1);
    // second call inside the throttle gap is dropped
    expect(source.start).toHaveBeenCalledTimes(1);
    ctx.currentTime = 1;
    scrubber?.grainAt(-1);
    expect(source.start).toHaveBeenLastCalledWith(1, 0, GRAIN_S);
  });

  it("dispose closes the context", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) })),
    );
    const { ctx } = fakeContext();
    const scrubber = await createScrubber("/audio", () => ctx as unknown as AudioContext);
    scrubber?.dispose();
    expect(ctx.close).toHaveBeenCalled();
  });
});
