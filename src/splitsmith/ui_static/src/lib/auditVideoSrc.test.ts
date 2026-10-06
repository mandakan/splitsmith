import { describe, expect, it } from "vitest";

import { auditVideoSrc } from "./auditVideoSrc";
import { scrubSource } from "./scrubSource";

type Video = { path: string; trim_version: string | null; scrub_version: string | null };
const video: Video = { path: "raw/take.mp4", trim_version: "t2", scrub_version: "w2" };
const choose = (v: Video) =>
  scrubSource({ trimVersion: v.trim_version, scrubVersion: v.scrub_version, fullRes: false, failed: false });
const base = {
  slug: "alice",
  video,
  plan: { kind: "trim" as const, offset: 0 },
  peaksLoaded: true,
  peaksFailed: false,
  stageNumber: 2,
  choose,
};
const url = (q: string) => `/api/shooters/alice/videos/stream?path=raw%2Ftake.mp4&${q}`;

describe("auditVideoSrc", () => {
  it("a trimmed angle streams its scrub source, naming the stage", () => {
    // A single take shares its source across stages; the stage picks the trim.
    expect(auditVideoSrc(base)).toBe(url("kind=scrub&v=w2&stage=2"));
  });

  it("a trimmed angle without a rendition streams the trim", () => {
    expect(auditVideoSrc({ ...base, video: { ...video, scrub_version: null } })).toBe(url("kind=trim&v=t2&stage=2"));
  });

  it("an untrimmed angle streams the proxy, naming the stage", () => {
    expect(auditVideoSrc({ ...base, plan: { kind: "proxy", offset: 1.5 } })).toBe(url("kind=proxy&stage=2"));
  });

  it("waits for peaks before pinning a kind", () => {
    expect(auditVideoSrc({ ...base, peaksLoaded: false })).toBe("");
  });

  it("falls back to auto when peaks failed, still naming the stage", () => {
    expect(auditVideoSrc({ ...base, peaksLoaded: false, peaksFailed: true })).toBe(url("kind=auto&stage=2"));
  });

  it("has nothing to stream without a video or a plan", () => {
    expect(auditVideoSrc({ ...base, video: null })).toBe("");
    expect(auditVideoSrc({ ...base, plan: null })).toBe("");
  });
});
