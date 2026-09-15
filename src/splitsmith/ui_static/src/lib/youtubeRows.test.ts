import { describe, expect, it } from "vitest";

import type { ExportRun } from "@/lib/api";
import { DEFAULT_UPLOAD_OPTIONS, rowUploadOptions, uploadLabel, uploadableArtifact, youtubeLink } from "@/lib/youtubeRows";

function run(over: Partial<ExportRun> = {}): ExportRun {
  return {
    run_id: "r",
    kind: "match",
    finished_at: "2026-09-14T00:00:00Z",
    duration_seconds: 1,
    stage_numbers: [1],
    formats: ["mp4", "youtube-sidecar"],
    anomaly_count: 0,
    artifacts: [
      { filename: "bromma.mp4", kind: "match_video", available: true },
      { filename: "bromma-youtube.json", kind: "sidecar", available: true },
      { filename: "bromma.srt", kind: "sidecar", available: true },
    ],
    youtube: null,
    ...over,
  };
}

describe("uploadableArtifact", () => {
  it("names the mp4 when it and its sidecar are present", () => {
    expect(uploadableArtifact(run())).toBe("bromma.mp4");
  });

  it("is null without the sidecar, without the mp4, or when either file is gone", () => {
    expect(
      uploadableArtifact(run({ artifacts: [{ filename: "bromma.mp4", kind: "match_video", available: true }] })),
    ).toBeNull();
    expect(
      uploadableArtifact(
        run({ artifacts: [{ filename: "bromma-youtube.json", kind: "sidecar", available: true }] }),
      ),
    ).toBeNull();
    expect(
      uploadableArtifact(
        run({
          artifacts: [
            { filename: "bromma.mp4", kind: "match_video", available: false },
            { filename: "bromma-youtube.json", kind: "sidecar", available: true },
          ],
        }),
      ),
    ).toBeNull();
    expect(
      uploadableArtifact(
        run({
          artifacts: [
            { filename: "bromma.fcpxml", kind: "fcpxml", available: true },
            { filename: "bromma-youtube.json", kind: "sidecar", available: true },
          ],
        }),
      ),
    ).toBeNull();
  });
});

describe("youtubeLink / uploadLabel / rowPrivacy", () => {
  it("links the recorded video and switches the label", () => {
    expect(youtubeLink(run())).toBeNull();
    expect(uploadLabel(run())).toBe("Upload to YouTube");
    const done = run({
      youtube: { video_id: "abc", url: "https://youtu.be/abc", privacy: "unlisted", uploaded_at: "2026-09-14T00:00:00Z" },
    });
    expect(youtubeLink(done)).toEqual({ href: "https://youtu.be/abc", label: "youtu.be/abc" });
    expect(uploadLabel(done)).toBe("Upload again");
  });

  it("row options follow the form block; Off means the defaults", () => {
    expect(rowUploadOptions({ ...DEFAULT_UPLOAD_OPTIONS, enabled: false, privacy: "public", playlist: "X" })).toEqual({
      privacy: "unlisted",
      playlist: null,
      publish_at: null,
      notify_subscribers: true,
    });
    expect(
      rowUploadOptions({
        enabled: true,
        privacy: "private",
        playlist: "Bromma 2026",
        publishAt: "2026-09-20T18:00",
        notifySubscribers: false,
      }),
    ).toEqual({
      privacy: "private",
      playlist: "Bromma 2026",
      publish_at: new Date("2026-09-20T18:00").toISOString(),
      notify_subscribers: false,
    });
  });

  it("publish time is only sent when the video is private, playlist only when named", () => {
    const opts = rowUploadOptions({
      enabled: true,
      privacy: "public",
      playlist: "  ",
      publishAt: "2026-09-20T18:00",
      notifySubscribers: true,
    });
    expect(opts.publish_at).toBeNull();
    expect(opts.playlist).toBeNull();
  });
});
