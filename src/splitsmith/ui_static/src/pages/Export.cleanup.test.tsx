import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api, type CleanupPlan, type ExportOverview, type MatchProject } from "@/lib/api";

import { Export } from "@/pages/Export";

/** Task 9: the Export page is the only caller of `CleanupDialog` (Task 8)
 *  wired up so far -- until this test, the dialog had no way to open in
 *  the running app. This only needs the button + dialog to appear, so
 *  mocking `api` (not `fetch`, as `CleanupDialog.test.tsx` does) is the
 *  lighter and more appropriate choice: this test never needs a real
 *  `ApiError`, which is the only reason that other file reaches for
 *  `globalThis.fetch`. */

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn(),
      getExportOverview: vi.fn(),
      getCleanupPlan: vi.fn(),
    },
  };
});

function makeProject(): MatchProject {
  return {
    schema_version: 1,
    name: "bromma-2026",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    competitor_name: null,
    scoreboard_match_id: null,
    scoreboard_content_type: null,
    selected_shooter_id: null,
    selected_competitor_id: null,
    shooter_token: null,
    match_date: null,
    stages: [],
    unassigned_videos: [],
    last_scanned_dir: null,
    raw_dir: null,
    audio_dir: null,
    trimmed_dir: null,
    exports_dir: null,
    probes_dir: null,
    thumbs_dir: null,
    trim_pre_buffer_seconds: 5,
    trim_post_buffer_seconds: 5,
    automation: {},
    nudges_dismissed_stages: [],
    compare_camera: null,
    raw_videos: [],
    origin: "local",
  };
}

function makeOverview(): ExportOverview {
  return { stages: [], match_exports: [] };
}

function makePlan(): CleanupPlan {
  return { items: [], totals_by_category: {}, total_bytes: 0, total_file_count: 0 };
}

function renderExportPage() {
  vi.mocked(api.getProject).mockResolvedValue(makeProject());
  vi.mocked(api.getExportOverview).mockResolvedValue(makeOverview());
  vi.mocked(api.getCleanupPlan).mockResolvedValue(makePlan());
  return render(
    <MemoryRouter initialEntries={["/export/anna"]}>
      <Routes>
        <Route path="/export/:slug" element={<Export />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Export page cleanup entry point", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("opens the cleanup dialog from the Export page", async () => {
    renderExportPage();

    await userEvent.click(
      await screen.findByRole("button", { name: /reclaim space/i }),
    );

    expect(
      await screen.findByRole("dialog", { name: /reclaim space/i }),
    ).toBeInTheDocument();
  });
});
