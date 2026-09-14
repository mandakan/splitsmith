import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ExportRun } from "@/lib/api";

import { ExportHistory } from "@/components/export/ExportHistory";

function run(over: Partial<ExportRun> = {}): ExportRun {
  return {
    run_id: "r1",
    kind: "stage",
    finished_at: "2026-08-16T12:00:00Z",
    duration_seconds: 12.5,
    stage_numbers: [3],
    formats: ["trim", "csv"],
    anomaly_count: 0,
    artifacts: [{ filename: "stage3_wall_trimmed.mp4", kind: "trim", available: true }],
    ...over,
  };
}

describe("ExportHistory", () => {
  // Exact strings, not regexes: getByText matches an element's whole
  // textContent, so a regex also matches every ancestor row and container
  // and fails with "found multiple elements". Exact matching pins the
  // leaf, which is also what makes these assertions specify the output.
  it("renders a stage run with its stage, formats and duration", () => {
    render(<ExportHistory runs={[run()]} exportFileUrl={(f) => `/dl/${f}`} />);
    expect(screen.getByText("Stage 3")).toBeInTheDocument();
    expect(screen.getByText("trim, csv")).toBeInTheDocument();
    expect(screen.getByText("12.5s")).toBeInTheDocument();
  });

  it("groups a match run's stages into one row", () => {
    render(
      <ExportHistory
        runs={[run({ kind: "match", stage_numbers: [1, 2, 3], formats: ["fcpxml"] })]}
        exportFileUrl={(f) => `/dl/${f}`}
      />,
    );
    expect(screen.getByText("Stages 1-3")).toBeInTheDocument();
  });

  it("lists non-contiguous stages instead of implying a range", () => {
    render(
      <ExportHistory
        runs={[run({ kind: "match", stage_numbers: [1, 2, 4] })]}
        exportFileUrl={(f) => `/dl/${f}`}
      />,
    );
    expect(screen.getByText("Stages 1, 2, 4")).toBeInTheDocument();
  });

  it("links each artefact to its download URL by basename", () => {
    render(<ExportHistory runs={[run()]} exportFileUrl={(f) => `/dl/${f}`} />);
    const link = screen.getByRole("link", { name: "stage3_wall_trimmed.mp4" });
    expect(link).toHaveAttribute("href", "/dl/stage3_wall_trimmed.mp4");
    expect(link).toHaveAttribute("download", "stage3_wall_trimmed.mp4");
  });

  it("shows the anomaly count only when there is one", () => {
    const { rerender } = render(
      <ExportHistory runs={[run()]} exportFileUrl={(f) => `/dl/${f}`} />,
    );
    expect(screen.queryByText(/anomal/i)).not.toBeInTheDocument();
    rerender(
      <ExportHistory runs={[run({ anomaly_count: 2 })]} exportFileUrl={(f) => `/dl/${f}`} />,
    );
    expect(screen.getByText("2 anomalies")).toBeInTheDocument();
  });

  it("renders an unavailable artefact as a non-link", () => {
    // The reachable case, not a hypothetical: the same page offers the
    // cleanup dialog, which deletes export files and deliberately leaves
    // the history alone. A link here carries ``download``, so clicking
    // one saves the JSON 404 body to disk under the video's filename.
    render(
      <ExportHistory
        runs={[
          run({
            artifacts: [{ filename: "stage3_wall_trimmed.mp4", kind: "trim", available: false }],
          }),
        ]}
        exportFileUrl={(f) => `/dl/${f}`}
      />,
    );
    expect(
      screen.queryByRole("link", { name: "stage3_wall_trimmed.mp4" }),
    ).not.toBeInTheDocument();
    // The name is still shown -- the run did produce it, and that is the
    // fact the history is for.
    expect(screen.getByText("stage3_wall_trimmed.mp4")).toBeInTheDocument();
  });

  it("renders an empty state rather than an empty list", () => {
    render(<ExportHistory runs={[]} exportFileUrl={(f) => `/dl/${f}`} />);
    expect(screen.getByText("No exports yet")).toBeInTheDocument();
  });
});

describe("ExportHistory YouTube actions", () => {
  const uploadable = () =>
    run({
      kind: "match",
      stage_numbers: [1, 2],
      formats: ["mp4", "youtube-sidecar"],
      artifacts: [
        { filename: "bromma.mp4", kind: "match_video", available: true },
        { filename: "bromma-youtube.json", kind: "sidecar", available: true },
      ],
      youtube: null,
    });

  it("offers the upload only when a channel is connected and the row has an mp4 with its sidecar", () => {
    const onUpload = vi.fn();
    const { rerender } = render(
      <ExportHistory runs={[uploadable()]} exportFileUrl={(f) => `/dl/${f}`} youtube={{ connected: false, onUpload }} />,
    );
    expect(screen.queryByRole("button", { name: "Upload to YouTube" })).toBeNull();
    rerender(
      <ExportHistory runs={[uploadable()]} exportFileUrl={(f) => `/dl/${f}`} youtube={{ connected: true, onUpload }} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Upload to YouTube" }));
    expect(onUpload).toHaveBeenCalledWith("bromma.mp4", false);
    rerender(<ExportHistory runs={[run()]} exportFileUrl={(f) => `/dl/${f}`} youtube={{ connected: true, onUpload }} />);
    expect(screen.queryByRole("button", { name: "Upload to YouTube" })).toBeNull();
  });

  it("links an uploaded run and keeps 'Upload again' behind the row menu", () => {
    const onUpload = vi.fn();
    const done = {
      ...uploadable(),
      youtube: { video_id: "abc", url: "https://youtu.be/abc", privacy: "unlisted", uploaded_at: "2026-09-14T00:00:00Z" },
    };
    render(<ExportHistory runs={[done]} exportFileUrl={(f) => `/dl/${f}`} youtube={{ connected: true, onUpload }} />);
    const link = screen.getByRole("link", { name: "youtu.be/abc" });
    expect(link).toHaveAttribute("href", "https://youtu.be/abc");
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.queryByRole("button", { name: "Upload to YouTube" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Stages 1-2 actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Upload again" }));
    expect(onUpload).toHaveBeenCalledWith("bromma.mp4", true);
  });

  it("shows the busy row as uploading", () => {
    render(
      <ExportHistory
        runs={[uploadable()]}
        exportFileUrl={(f) => `/dl/${f}`}
        youtube={{ connected: true, onUpload: vi.fn(), busyFilename: "bromma.mp4" }}
      />,
    );
    expect(screen.getByRole("button", { name: "Uploading..." })).toBeDisabled();
  });
});
