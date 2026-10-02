/**
 * Sort footage page (spec 2026-10-01) with the API mocked: the sections
 * render from the view, a check mark and a named run reach the server as
 * decisions, and Import imports with the page's link mode.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type SortClipView, type SortView } from "@/lib/api";
import { FootageSort } from "@/pages/FootageSort";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getFootageSort: vi.fn(),
      putFootageSortDecisions: vi.fn(),
      importFootageSort: vi.fn(),
    },
  };
});

const PHONE = "from-martin|Apple|iPhone 17 Pro Max|IMG";
const HEAD = "head|||VID_datetime";

function clip(
  clipId: string,
  index: number,
  camera: string,
  over: Partial<SortClipView["proposal"]>,
  checked = false,
): SortClipView {
  return {
    clip_id: clipId,
    index,
    folder: clipId.split("/")[0],
    filename: clipId.split("/")[1],
    start: "2026-09-26T11:00:00Z",
    duration: 40,
    model: null,
    imported_by: null,
    unassigned_in: null,
    thumbnail: false,
    strip: false,
    checked,
    proposal: {
      clip_id: clipId,
      camera_key: camera,
      shooter: null,
      stage: null,
      confidence: "high",
      run_id: null,
      role: "primary",
      decided_by: "engine",
      reason: {
        scorecard_at: null,
        lead_seconds: 95,
        offset_seconds: 0,
        issue: null,
        rival: null,
        rival_stage: null,
        run_size: 1,
      },
      ...over,
    },
  };
}

function view(): SortView {
  return {
    scan_id: "abc123",
    status: "ready",
    error: null,
    source_dir: "/shared/hostfinalen",
    skipped_files: 39,
    shooters: [
      { key: "mathias", name: "Mathias Axell", stages: [1, 2] },
      { key: "anton", name: "Anton Johansson", stages: [1, 2] },
    ],
    cameras: [
      {
        key: PHONE,
        folder: "from-martin",
        model: "iPhone 17 Pro Max",
        scheme: "IMG",
        clip_ids: [],
        clock: "trusted",
        offset_seconds: 0,
      },
      {
        key: HEAD,
        folder: "head",
        model: null,
        scheme: "VID_datetime",
        clip_ids: ["head/VID_1.mp4"],
        clock: "needs_anchor",
        offset_seconds: 0,
      },
    ],
    clips: [
      clip(
        "from-martin/IMG_1.MOV",
        0,
        PHONE,
        { shooter: "anton", stage: 2 },
        true,
      ),
      clip(
        "from-martin/IMG_2.MOV",
        1,
        PHONE,
        { shooter: "mathias", stage: 1 },
        true,
      ),
      clip("head/VID_1.mp4", 2, HEAD, { confidence: "needs_you", role: null }),
    ],
    strips_pending: 0,
    anchors: [],
    overrides: [],
    user_checked: {},
  };
}

/** Footage as the sort hands back to it: shows the import summary. */
function FootageStub() {
  const state = useLocation().state as { sortImported?: string } | null;
  return <p>footage: {state?.sortImported ?? ""}</p>;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/match/m1/footage-sort/abc123"]}>
      <Routes>
        <Route
          path="/match/:matchId/footage-sort/:scanId"
          element={<FootageSort />}
        />
        <Route path="/match/:matchId/ingest" element={<FootageStub />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("FootageSort", () => {
  beforeEach(() => {
    vi.mocked(api.getFootageSort).mockResolvedValue(view());
    vi.mocked(api.putFootageSortDecisions).mockImplementation(async () =>
      view(),
    );
  });

  it("shows the unknown-clock camera first and one table per shooter", async () => {
    renderPage();

    expect(
      await screen.findByText(/the clock lines up with no scorecard/),
    ).toBeInTheDocument();
    expect(screen.getByText("Mathias Axell")).toBeInTheDocument();
    expect(screen.getByText("Anton Johansson")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Import 2 clips" }),
    ).toBeEnabled();
  });

  it("an unchecked clip reaches the server as a check mark", async () => {
    renderPage();

    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Import IMG_1.MOV" }),
    );

    expect(api.putFootageSortDecisions).toHaveBeenCalledWith("abc123", {
      anchors: [],
      overrides: [],
      checked: { "from-martin/IMG_1.MOV": false },
    });
  });

  it("naming the run of an unknown-clock clip anchors its camera", async () => {
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: "Choose" }),
    );
    const sheet = screen.getByRole("dialog", { name: "VID_1.mp4" });
    expect(
      within(sheet).getByText(/sets the clock for every clip from this camera/),
    ).toBeInTheDocument();
    await userEvent.click(
      within(sheet).getByRole("button", { name: "Mathias Axell" }),
    );
    await userEvent.selectOptions(
      within(sheet).getByRole("combobox", { name: "Stage" }),
      "2",
    );
    await userEvent.click(
      within(sheet).getByRole("button", { name: "Use this" }),
    );

    expect(api.putFootageSortDecisions).toHaveBeenCalledWith("abc123", {
      anchors: [{ clip_id: "head/VID_1.mp4", shooter: "mathias", stage: 2 }],
      overrides: [],
      checked: { "head/VID_1.mp4": true },
    });
  });

  it("imports with the chosen link mode and lands on Footage with the summary", async () => {
    vi.mocked(api.importFootageSort).mockResolvedValue({
      imported: [
        {
          clip_id: "from-martin/IMG_1.MOV",
          shooter: "anton",
          stage: 2,
          role: "primary",
          path: "raw/IMG_1.MOV",
        },
        {
          clip_id: "from-martin/IMG_2.MOV",
          shooter: "mathias",
          stage: 1,
          role: "primary",
          path: "raw/IMG_2.MOV",
        },
      ],
      not_imported: {},
      report: "/m/footage_sort/abc123-report.json",
      remaining: 0,
    });
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: "Link in place" }),
    );
    vi.mocked(api.getFootageSort).mockResolvedValue({
      ...view(),
      status: "imported",
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Import 2 clips" }),
    );

    await waitFor(() =>
      expect(api.importFootageSort).toHaveBeenCalledWith(
        "abc123",
        "copy",
        undefined,
      ),
    );
    expect(
      await screen.findByText(
        "footage: Imported 2 clips: Mathias Axell 1, Anton Johansson 1",
      ),
    ).toBeInTheDocument();
  });

  it("opens the player from the thumbnail and steps through the clips", async () => {
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: "Play VID_1.mp4" }),
    );
    const sheet = screen.getByRole("dialog", { name: "VID_1.mp4" });
    expect(within(sheet).getByText("1 / 3")).toBeInTheDocument();

    await userEvent.keyboard("{ArrowDown}");
    expect(
      await screen.findByRole("dialog", { name: "IMG_2.MOV" }),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Previous clip" }),
    );
    expect(
      await screen.findByRole("dialog", { name: "VID_1.mp4" }),
    ).toBeInTheDocument();
  });

  it("moves on to the next clip after an answer", async () => {
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: "Play VID_1.mp4" }),
    );
    const sheet = screen.getByRole("dialog", { name: "VID_1.mp4" });
    await userEvent.click(
      within(sheet).getByRole("button", { name: "Mathias Axell" }),
    );
    await userEvent.selectOptions(
      within(sheet).getByRole("combobox", { name: "Stage" }),
      "2",
    );
    await userEvent.click(
      within(sheet).getByRole("button", { name: "Use this" }),
    );

    expect(
      await screen.findByRole("dialog", { name: "IMG_2.MOV" }),
    ).toBeInTheDocument();
  });

  it("selects or clears a shooter's clips with one box", async () => {
    renderPage();

    await userEvent.click(
      await screen.findByRole("checkbox", {
        name: "Select all for Anton Johansson",
      }),
    );

    expect(api.putFootageSortDecisions).toHaveBeenCalledWith("abc123", {
      anchors: [],
      overrides: [],
      checked: { "from-martin/IMG_1.MOV": false },
    });
  });

  it("imports one shooter and keeps the review open for the rest", async () => {
    vi.mocked(api.importFootageSort).mockResolvedValue({
      imported: [
        {
          clip_id: "from-martin/IMG_1.MOV",
          shooter: "anton",
          stage: 2,
          role: "primary",
          path: "raw/IMG_1.MOV",
        },
      ],
      not_imported: {},
      report: "/m/footage_sort/abc123-report.json",
      remaining: 0,
    });
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", {
        name: "Import 1 for Anton Johansson",
      }),
    );

    await waitFor(() =>
      expect(api.importFootageSort).toHaveBeenCalledWith("abc123", "symlink", [
        "anton",
      ]),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Imported 1 clip to Anton Johansson",
    );
    expect(screen.getByText("Mathias Axell")).toBeInTheDocument();
  });
});
