/**
 * An access refusal on upload reads as one line in the queue, not the
 * stringified ``{code, feature}`` detail.
 */
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { UploadProvider, useUploads } from "@/lib/uploads";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, uploadRawFile: vi.fn() } };
});

let enqueueRef: ReturnType<typeof useUploads>["enqueue"] | null = null;

function Probe() {
  const { uploads, enqueue } = useUploads();
  enqueueRef = enqueue;
  return (
    <ul>
      {uploads.map((u) => (
        <li key={u.id}>{u.status === "error" ? u.errorMessage : u.status}</li>
      ))}
    </ul>
  );
}

describe("upload refusal", () => {
  it("names the missing feature", async () => {
    vi.mocked(api.uploadRawFile).mockRejectedValue(
      new ApiError(403, JSON.stringify({ code: "feature_required", feature: "raw_upload" }), {
        code: "feature_required",
        feature: "raw_upload",
      }),
    );
    render(
      <UploadProvider>
        <Probe />
      </UploadProvider>,
    );
    act(() => {
      enqueueRef?.([new File(["x"], "GH010001.MP4", { type: "video/mp4" })], { slug: "alice", stages: [] });
    });
    expect(
      await screen.findByText("Uploading footage here is not included in this account's access."),
    ).toBeInTheDocument();
  });
});
