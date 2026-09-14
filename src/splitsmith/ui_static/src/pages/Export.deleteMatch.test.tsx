/**
 * Delete match lives in the Export summary's storage row (UX PR 8; it
 * left the Matches row). Confirm first, then the recent-projects delete
 * on the match root, then back to the picker.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type ExportOverview, type MatchProject } from "@/lib/api";
import { ConfirmProvider } from "@/components/useConfirm";
import { Export } from "@/pages/Export";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
      getProject: vi.fn(),
      getExportOverview: vi.fn(),
      getExportRuns: vi.fn().mockResolvedValue({ runs: [] }),
      getCleanupPlan: vi.fn().mockResolvedValue({ items: [], totals_by_category: {}, total_bytes: 0, total_file_count: 0 }),
      deleteProject: vi.fn(),
    },
  };
});

const PROJECT: MatchProject = {
  schema_version: 1,
  name: "bromma-2026",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  competitor_name: "Mathias",
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
const OVERVIEW: ExportOverview = { stages: [], match_exports: [] };

function Shell() {
  return (
    <Outlet
      context={{
        project: { ...PROJECT, name: "Bromma Classic 2026" },
        health: { project_root: "/m/bromma" },
        shooters: [],
        refresh: () => {},
        origin: "local",
        capabilities: ["edit", "review"],
      }}
    />
  );
}

function Probe() {
  const location = useLocation();
  return <div data-testid="probe">{location.pathname}</div>;
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("Export delete match", () => {
  it("confirms, deletes the match root and returns to the picker", async () => {
    vi.mocked(api.getProject).mockResolvedValue(PROJECT);
    vi.mocked(api.getExportOverview).mockResolvedValue(OVERVIEW);
    vi.mocked(api.deleteProject).mockResolvedValue({ summary: { errors: [] } } as never);
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/match/m1/export/mathias"]}>
        <ConfirmProvider>
          <Routes>
            <Route path="/match/:matchId" element={<Shell />}>
              <Route path="export/:slug" element={<Export />} />
            </Route>
            <Route path="/pick" element={<Probe />} />
          </Routes>
        </ConfirmProvider>
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole("button", { name: /delete match/i }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/delete bromma classic 2026\?/i);
    await user.click(screen.getByRole("checkbox", { name: /delete the project folder on disk/i }));
    await user.click(within(dialog).getByRole("button", { name: /^delete match$/i }));
    await waitFor(() => expect(api.deleteProject).toHaveBeenCalledWith("/m/bromma", { deleteLocalFiles: true, deleteRawUploads: false }));
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("/pick"));
  });
});
