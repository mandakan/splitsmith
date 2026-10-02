/**
 * The Pages Function behind https://splitsmith.app/desktop/latest.json
 * lives in functions/ (no test runner there); its release picking is
 * tested here since the app is its only consumer.
 */
import { describe, expect, it } from "vitest";

// @ts-expect-error plain JS module without types
import { onRequestGet, pickLatest } from "../../functions/desktop/latest.json.js";

const DMG = "Splitsmith-0.0.0-arm64.dmg";
const APPIMAGE = "Splitsmith-0.0.0-x86_64.AppImage";
const DEB = "splitsmith-desktop_0.0.0_amd64.deb";

const rel = (
  tag_name: string,
  assets: string[] = [DMG],
  extra: Partial<{ draft: boolean; prerelease: boolean }> = {},
) => ({
  tag_name,
  html_url: `https://github.com/mandakan/splitsmith/releases/tag/${tag_name}`,
  draft: false,
  prerelease: false,
  assets: assets.map((name) => ({ name })),
  ...extra,
});
const at = (tag: string) => ({ version: tag.slice(1), url: `https://github.com/mandakan/splitsmith/releases/tag/${tag}` });

describe("pickLatest", () => {
  it("skips non-app tags, drafts and prereleases, and orders numerically", () => {
    const picked = pickLatest([
      rel("ffmpeg-macos-arm64-9.0.2-r1"),
      rel("v0.40.1"),
      rel("v0.41.0", [DMG], { draft: true }),
      rel("v0.42.0", [DMG], { prerelease: true }),
      rel("v0.9.9"),
      rel("v0.40.0"),
    ]);
    expect(picked).toEqual(at("v0.40.1"));
  });
  it("is null with no app release", () => {
    expect(pickLatest([rel("ffmpeg-macos-arm64-9.0.2-r1")])).toBeNull();
    expect(pickLatest([])).toBeNull();
  });
  it("defaults to macOS: a release counts once its DMG is attached", () => {
    expect(pickLatest([rel("v0.43.0", [APPIMAGE, DEB]), rel("v0.42.0", [DMG])])).toEqual(at("v0.42.0"));
  });
  it("linux needs both the AppImage and the deb", () => {
    const releases = [rel("v0.44.0", [DMG]), rel("v0.43.0", [DMG, APPIMAGE]), rel("v0.42.0", [DMG, APPIMAGE, DEB])];
    expect(pickLatest(releases, "linux")).toEqual(at("v0.42.0"));
    expect(pickLatest(releases, "mac")).toEqual(at("v0.44.0"));
  });
  it("rejects an unknown platform", () => {
    expect(() => pickLatest([], "windows")).toThrow();
  });
});

describe("onRequestGet", () => {
  it("answers 400 for an unknown platform without calling GitHub", async () => {
    const res = await onRequestGet({ request: new Request("https://splitsmith.app/desktop/latest.json?platform=win") });
    expect(res.status).toBe(400);
  });
});
