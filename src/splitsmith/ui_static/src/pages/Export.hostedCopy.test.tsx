/**
 * Export page copy for a stage whose source file is unreachable, on a
 * hosted server. Desktop tells the user to mount a drive; hosted has no
 * drive -- the raw upload left object storage (cleanup), and the only fix
 * is re-uploading from Videos. Own file because `lib/features.ts` caches
 * the first `getServerFeatures` answer module-wide, so a hosted mock has
 * to be the first one this module sees.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  api,
  type CleanupPlan,
  type ExportOverview,
  type MatchProject,
} from "@/lib/api";

import { Export } from "@/pages/Export";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getServerFeatures: vi
        .fn()
        .mockResolvedValue({ lab: false, mode: "hosted" }),
      getProject: vi.fn(),
      getExportOverview: vi.fn(),
      getCleanupPlan: vi.fn(),
    },
  };
});

function makeProject(): MatchProject {
  return {
    schema_version: 1,
    name: "stockholm-2026",
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
    origin: "hosted",
  };
}

function makePlan(): CleanupPlan {
  return { items: [], totals_by_category: {}, total_bytes: 0, total_file_count: 0 };
}

function readyButOffline(): ExportOverview {
  return {
    match_exports: [],
    stages: [
      {
        stage_number: 3,
        stage_name: "B6 Rear",
        skipped: false,
        has_primary: true,
        primary_processed: { beep: true, shot_detect: true, trim: true },
        audit_shot_count: 30,
        total_candidate_count: 119,
        audit_path: "audit.json",
        trimmed_video_path: null,
        lossless_trim_present: false,
        csv_path: null,
        fcpxml_path: null,
        report_path: null,
        overlay_path: null,
        has_exports: false,
        last_export_at: null,
        ready_to_export: true,
        ready_to_trim: true,
        source_reachable: false,
        secondaries: [],
      },
    ],
  };
}

describe("Export source-missing copy on hosted", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("says the upload is gone and points at Videos, never at a drive", async () => {
    vi.mocked(api.getProject).mockResolvedValue(makeProject());
    vi.mocked(api.getExportOverview).mockResolvedValue(readyButOffline());
    vi.mocked(api.getCleanupPlan).mockResolvedValue(makePlan());
    render(
      <MemoryRouter initialEntries={["/export/anna"]}>
        <Routes>
          <Route path="/export/:slug" element={<Export />} />
        </Routes>
      </MemoryRouter>,
    );
    // The section help line and the banner both carry it.
    expect(
      (await screen.findAllByText(/original upload is no longer stored/i))
        .length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/source drive/i)).toBeNull();
    expect(screen.queryByText(/reconnect the drive/i)).toBeNull();
  });
});
