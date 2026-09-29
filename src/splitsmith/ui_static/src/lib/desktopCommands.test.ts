import { describe, expect, it } from "vitest";

import type { DesktopCommand, DesktopPresence } from "@/lib/api";

import {
  commandLine,
  commandTitle,
  isActiveCommand,
  latestForStage,
  presenceSummary,
  presenceText,
} from "./desktopCommands";

const NOW = Date.parse("2026-09-28T12:00:00Z");

function cmd(over: Partial<DesktopCommand> = {}): DesktopCommand {
  return {
    id: "c1",
    match_id: "m1",
    kind: "shot_detect",
    slug: "anna",
    stage_number: 3,
    args: { reset: true },
    expected_revision: null,
    status: "pending",
    cancel_requested: false,
    progress_message: null,
    error: null,
    result: null,
    requested_at: "2026-09-28T11:59:00Z",
    claimed_at: null,
    lease_expires_at: null,
    finished_at: null,
    ...over,
  };
}

const around: DesktopPresence = { linked: true, last_seen_at: "2026-09-28T11:59:30Z", around: true };
const away: DesktopPresence = { linked: true, last_seen_at: "2026-09-28T10:00:00Z", around: false };
const none: DesktopPresence = { linked: false, last_seen_at: null, around: false };

describe("presenceText", () => {
  it("says whether a desktop will pick it up", () => {
    expect(presenceText(around, NOW)).toBe("Your desktop will pick this up shortly.");
    expect(presenceText(away, NOW)).toBe("Waiting for your desktop (last seen 2 h ago).");
    expect(presenceText(none, NOW)).toMatch(/^No desktop linked/);
  });
});

describe("presenceSummary", () => {
  it("describes the desktop without a request in view", () => {
    expect(presenceSummary(around, NOW)).toBe("Your desktop is online.");
    expect(presenceSummary(away, NOW)).toBe("Your desktop was last seen 2 h ago.");
    expect(presenceSummary({ linked: true, last_seen_at: null, around: false }, NOW)).toBe(
      "Your desktop has not connected yet.",
    );
  });
});

describe("commandLine", () => {
  it("words each state", () => {
    expect(commandLine(cmd(), away, NOW)).toEqual({
      text: "Waiting for your desktop (last seen 2 h ago).",
      tone: "muted",
      cancellable: true,
    });
    expect(commandLine(cmd({ status: "claimed", progress_message: "Detecting 40%" }), around, NOW).text).toBe(
      "Running on your desktop: Detecting 40%",
    );
    expect(commandLine(cmd({ status: "claimed", cancel_requested: true }), around, NOW).cancellable).toBe(false);
    expect(commandLine(cmd({ status: "succeeded", finished_at: "2026-09-28T11:50:00Z" }), around, NOW)).toEqual({
      text: "Re-detected on your desktop 10 min ago.",
      tone: "ok",
      cancellable: false,
    });
    expect(commandLine(cmd({ status: "failed", error: "the stage changed" }), around, NOW)).toMatchObject({
      text: "Failed on your desktop: the stage changed",
      tone: "error",
    });
    expect(commandLine(cmd({ status: "cancelled" }), around, NOW).text).toBe("Cancelled.");
  });
});

describe("helpers", () => {
  it("finds the newest request for a stage and names it", () => {
    const list = [cmd({ id: "new" }), cmd({ id: "old" }), cmd({ id: "other", stage_number: 4 })];
    expect(latestForStage(list, "anna", 3)?.id).toBe("new");
    expect(latestForStage(list, "anna", 9)).toBeNull();
    expect(commandTitle(cmd())).toBe("Re-detect Stage 03 (anna)");
    expect(isActiveCommand(cmd({ status: "claimed" }))).toBe(true);
    expect(isActiveCommand(cmd({ status: "failed" }))).toBe(false);
  });
});

describe("render_upload", () => {
  const ru = (over: Partial<DesktopCommand> = {}) =>
    cmd({ kind: "render_upload", stage_number: null, args: { request: {} }, ...over });

  it("is titled by shooter", () => {
    expect(commandTitle(ru())).toBe("Render and upload (anna)");
  });

  it("links the video when it succeeded, naming the channel", () => {
    const line = commandLine(
      ru({
        status: "succeeded",
        finished_at: "2026-09-28T11:58:00Z",
        result: { video_id: "v", url: "https://youtu.be/v", channel_title: "Anna Shoots" },
      }),
      around,
      NOW,
    );
    expect(line.tone).toBe("ok");
    expect(line.text).toBe("Uploaded to Anna Shoots 2 min ago.");
    expect(line.link).toEqual({ href: "https://youtu.be/v", label: "youtu.be/v" });
  });

  it("never links a non-http(s) url, however the server serves it", () => {
    const bad = (url: string) =>
      commandLine(
        ru({
          status: "succeeded",
          finished_at: "2026-09-28T11:58:00Z",
          result: { video_id: "v", url, channel_title: "Anna Shoots" },
        }),
        around,
        NOW,
      ).link;
    expect(bad("javascript:alert(1)")).toBeUndefined();
    expect(bad("ftp://x")).toBeUndefined();
  });
});

it("still says re-detected for a re-detect", () => {
  expect(commandLine(cmd({ status: "succeeded" }), around, NOW).text).toMatch(/^Re-detected on your desktop/);
});
