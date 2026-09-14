import { describe, expect, it } from "vitest";

import type { ExportRun } from "@/lib/api";
import { rowPrivacy, uploadLabel, uploadableArtifact, youtubeLink } from "@/lib/youtubeRows";

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

  it("row privacy follows the form control, unlisted when off", () => {
    expect(rowPrivacy("off")).toBe("unlisted");
    expect(rowPrivacy("public")).toBe("public");
  });
});
