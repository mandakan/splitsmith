import { describe, expect, it } from "vitest";

import { compareVersions, parseFeed, updateDecision } from "./updateCheck";

describe("compareVersions", () => {
  it("orders numerically per component", () => {
    expect(compareVersions("0.40.1", "0.41.0")).toBeLessThan(0);
    expect(compareVersions("0.41.0", "1.0.0")).toBeLessThan(0);
    expect(compareVersions("0.9.9", "0.40.1")).toBeLessThan(0);
    expect(compareVersions("1.0.0", "1.0.0")).toBe(0);
    expect(compareVersions("1.2.3", "1.2.2")).toBeGreaterThan(0);
  });
  it("treats a missing component as zero and ignores a v prefix", () => {
    expect(compareVersions("v1.2", "1.2.0")).toBe(0);
  });
});

describe("parseFeed", () => {
  it("accepts the feed shape", () => {
    expect(parseFeed({ version: "0.41.0", url: "https://splitsmith.app/download" })).toEqual({
      version: "0.41.0",
      url: "https://splitsmith.app/download",
    });
  });
  it("rejects anything else, including a non-https url", () => {
    expect(parseFeed(null)).toBeNull();
    expect(parseFeed({ version: "0.41.0" })).toBeNull();
    expect(parseFeed({ version: "latest", url: "https://x" })).toBeNull();
    expect(parseFeed({ version: "0.41.0", url: "http://x" })).toBeNull();
    expect(parseFeed({ version: "0.41.0", url: "javascript:alert(1)" })).toBeNull();
  });
});

describe("updateDecision", () => {
  const feed = { version: "0.41.0", url: "https://splitsmith.app/download" };
  it("offers a newer version", () => {
    expect(updateDecision({ current: "0.40.1", feed, dismissed: null })).toEqual({ kind: "available", ...feed });
  });
  it("is quiet when the version was dismissed, unless asked explicitly", () => {
    expect(updateDecision({ current: "0.40.1", feed, dismissed: "0.41.0" })).toEqual({ kind: "dismissed", ...feed });
    expect(updateDecision({ current: "0.40.1", feed, dismissed: "0.40.2" })).toEqual({ kind: "available", ...feed });
  });
  it("reports up to date for the same or a newer current version", () => {
    expect(updateDecision({ current: "0.41.0", feed, dismissed: null })).toEqual({ kind: "current" });
    expect(updateDecision({ current: "0.42.0", feed, dismissed: null })).toEqual({ kind: "current" });
  });
  it("reports unknown when there is no feed", () => {
    expect(updateDecision({ current: "0.40.1", feed: null, dismissed: null })).toEqual({ kind: "unknown" });
  });
});
