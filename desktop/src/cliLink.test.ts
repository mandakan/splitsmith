import { describe, expect, it } from "vitest";

import { CLI_TARGET, cliLinkPlan } from "./cliLink";

const SRC = "/App.app/Contents/Resources/python/bin/splitsmith";

describe("cliLinkPlan", () => {
  it("links when nothing is there", () => {
    expect(cliLinkPlan(SRC, CLI_TARGET, null)).toEqual({ kind: "link", source: SRC, target: "/usr/local/bin/splitsmith" });
  });
  it("is a no-op when the link already points at this app", () => {
    expect(cliLinkPlan(SRC, CLI_TARGET, SRC)).toEqual({ kind: "already", target: "/usr/local/bin/splitsmith" });
  });
  it("relinks when the link points at an older copy of the app", () => {
    expect(cliLinkPlan(SRC, CLI_TARGET, "/old/Splitsmith.app/Contents/Resources/python/bin/splitsmith")).toEqual({
      kind: "link",
      source: SRC,
      target: "/usr/local/bin/splitsmith",
    });
  });
  it("refuses to replace something that is not ours", () => {
    expect(cliLinkPlan(SRC, CLI_TARGET, "/opt/homebrew/bin/splitsmith")).toEqual({
      kind: "conflict",
      target: "/usr/local/bin/splitsmith",
      existing: "/opt/homebrew/bin/splitsmith",
    });
  });
});
