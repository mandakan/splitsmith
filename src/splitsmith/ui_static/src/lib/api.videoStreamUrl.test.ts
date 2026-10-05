import { describe, expect, it } from "vitest";

import { api } from "@/lib/api";

describe("api.videoStreamUrl", () => {
  it("is unchanged without a version", () => {
    expect(api.videoStreamUrl("alice", "raw/a b.mp4", "trim")).toBe(
      "/api/shooters/alice/videos/stream?path=raw%2Fa%20b.mp4&kind=trim",
    );
    expect(api.videoStreamUrl("alice", "raw/a.mp4", "trim", null)).toBe(
      "/api/shooters/alice/videos/stream?path=raw%2Fa.mp4&kind=trim",
    );
  });

  it("appends the trim version so a re-cut trim is a new URL", () => {
    expect(api.videoStreamUrl("alice", "raw/a.mp4", "trim", "18f2a-3e8")).toBe(
      "/api/shooters/alice/videos/stream?path=raw%2Fa.mp4&kind=trim&v=18f2a-3e8",
    );
  });

  it("names the stage, so a source shared by several stages streams the right one", () => {
    expect(api.videoStreamUrl("alice", "raw/take.mp4", "trim", "18f2a-3e8", 2)).toBe(
      "/api/shooters/alice/videos/stream?path=raw%2Ftake.mp4&kind=trim&v=18f2a-3e8&stage=2",
    );
    expect(api.videoStreamUrl("alice", "raw/take.mp4", "web", null, 3)).toBe(
      "/api/shooters/alice/videos/stream?path=raw%2Ftake.mp4&kind=web&stage=3",
    );
  });

  it("leaves the stage off when there is none", () => {
    for (const stage of [null, undefined, Number.NaN]) {
      expect(api.videoStreamUrl("alice", "raw/a.mp4", "trim", null, stage)).toBe(
        "/api/shooters/alice/videos/stream?path=raw%2Fa.mp4&kind=trim",
      );
    }
  });
});
