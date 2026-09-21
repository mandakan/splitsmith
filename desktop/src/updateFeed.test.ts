/**
 * The Pages Function behind https://splitsmith.app/desktop/latest.json
 * lives in functions/ (no test runner there); its release picking is
 * tested here since the app is its only consumer.
 */
import { describe, expect, it } from "vitest";

// @ts-expect-error plain JS module without types
import { pickLatest } from "../../functions/desktop/latest.json.js";

const rel = (tag_name: string, extra: Partial<{ draft: boolean; prerelease: boolean }> = {}) => ({
  tag_name,
  html_url: `https://github.com/mandakan/splitsmith/releases/tag/${tag_name}`,
  draft: false,
  prerelease: false,
  ...extra,
});

describe("pickLatest", () => {
  it("skips non-app tags, drafts and prereleases, and orders numerically", () => {
    const picked = pickLatest([
      rel("ffmpeg-macos-arm64-9.0.2-r1"),
      rel("v0.40.1"),
      rel("v0.41.0", { draft: true }),
      rel("v0.42.0", { prerelease: true }),
      rel("v0.9.9"),
      rel("v0.40.0"),
    ]);
    expect(picked).toEqual({ version: "0.40.1", url: "https://github.com/mandakan/splitsmith/releases/tag/v0.40.1" });
  });
  it("is null with no app release", () => {
    expect(pickLatest([rel("ffmpeg-macos-arm64-9.0.2-r1")])).toBeNull();
    expect(pickLatest([])).toBeNull();
  });
});
