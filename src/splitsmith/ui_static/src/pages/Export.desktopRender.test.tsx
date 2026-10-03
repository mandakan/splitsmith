/**
 * Export on a desktop-synced match (hosted, origin "desktop", no edit):
 * the form becomes a request the linked desktop renders and uploads, and
 * the history lists this shooter's render requests. A hosted-native match
 * keeps its own export.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import {
  api,
  type DesktopCommand,
  type DesktopPresence,
  type ExportOverview,
  type ExportPreset,
  type ExportPresetBody,
  type MatchCapability,
  type MatchOrigin,
  type MatchProject,
  type StageExportStatus,
} from "@/lib/api";
import { DEFAULT_EXPORT_SETTINGS, loadLastUsed, saveLastUsed, settingsToBody } from "@/lib/exportPresets";
import { ExportRoute } from "@/pages/Export";

const deployment = vi.hoisted(() => ({ mode: "hosted" as "hosted" | "local" }));
const viewport = vi.hoisted(() => ({ mobile: false }));

vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => viewport.mobile }));

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: vi.fn(() => ({ mode: deployment.mode, resolved: true })) };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "hosted" }),
      getProject: vi.fn(),
      getExportOverview: vi.fn(),
      getExportRuns: vi.fn().mockResolvedValue({ runs: [] }),
      getCleanupPlan: vi.fn().mockResolvedValue({ items: [], totals_by_category: {}, total_bytes: 0, total_file_count: 0 }),
      getYouTubeSettings: vi.fn(),
      getExportPresets: vi.fn().mockResolvedValue({ presets: [] }),
      exportPreview: vi.fn(),
      exportMatch: vi.fn(),
      pollJob: vi.fn(),
      requestDesktopRender: vi.fn(),
      listDesktopCommands: vi.fn(),
      cancelDesktopCommand: vi.fn(),
    },
  };
});

const MIRROR_CAPABILITIES: MatchCapability[] = ["review", "comment_write"];
const FULL_CAPABILITIES: MatchCapability[] = ["edit", "review", "share_manage", "comment_write"];

const around: DesktopPresence = { linked: true, last_seen_at: new Date(Date.now() - 30_000).toISOString(), around: true };
const away: DesktopPresence = {
  linked: true,
  last_seen_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
  around: false,
};

function renderCommand(over: Partial<DesktopCommand> = {}): DesktopCommand {
  return {
    id: "c1",
    match_id: "m1",
    kind: "render_upload",
    slug: "anna",
    stage_number: null,
    args: {},
    expected_revision: null,
    status: "pending",
    cancel_requested: false,
    progress_message: null,
    error: null,
    result: null,
    requested_at: new Date(Date.now() - 60_000).toISOString(),
    claimed_at: null,
    lease_expires_at: null,
    finished_at: null,
    ...over,
  };
}

function stage(n: number): MatchProject["stages"][number] {
  return {
    stage_number: n,
    stage_name: `Stage ${n}`,
    time_seconds: 20,
    scorecard_updated_at: null,
    videos: [],
    skipped: false,
    placeholder: false,
    time_seconds_manual: false,
    stage_rounds: null,
    scorecard: null,
  };
}

function project(origin: MatchOrigin): MatchProject {
  return {
    schema_version: 1,
    name: "bromma-2026",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    competitor_name: "Anna",
    scoreboard_match_id: null,
    scoreboard_content_type: null,
    selected_shooter_id: null,
    selected_competitor_id: null,
    shooter_token: null,
    match_date: null,
    stages: [stage(1), stage(2)],
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
    origin,
  };
}

function ready(n: number): StageExportStatus {
  return {
    stage_number: n,
    stage_name: `Stage ${n}`,
    skipped: false,
    has_primary: true,
    primary_processed: { beep: true, shot_detect: true, trim: true },
    audit_shot_count: 8,
    total_candidate_count: 8,
    audit_path: null,
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
    ready_to_export_bare: true,
    source_reachable: true,
    secondaries: [],
  };
}

const OVERVIEW: ExportOverview = { match_exports: [], stages: [ready(1), ready(2)] };

function renderExport({
  capabilities,
  origin,
  mode,
}: {
  capabilities: MatchCapability[] | null;
  origin: MatchOrigin;
  mode: "hosted" | "local";
}) {
  deployment.mode = mode;
  vi.mocked(api.getProject).mockResolvedValue(project(origin));
  const shooters = [
    {
      slug: "anna",
      name: "Anna",
      selected_shooter_id: null,
      selected_competitor_id: null,
      stages_audited: 2,
      stages_total: 2,
      video_count: 0,
      cameras: [],
      stages_missing_trim: 0,
      stage_statuses: [],
    },
  ];
  function Shell({ caps }: { caps: MatchCapability[] | null }) {
    return (
      <Outlet
        context={{ project: project(origin), health: null, shooters, refresh: () => {}, origin, capabilities: caps }}
      />
    );
  }
  const tree = (caps: MatchCapability[] | null) => (
    <MemoryRouter initialEntries={["/match/m1/export/anna"]}>
      <ConfirmProvider>
        <Routes>
          <Route path="/match/:matchId" element={<Shell caps={caps} />}>
            <Route path="export/:slug" element={<ExportRoute />} />
          </Route>
        </Routes>
      </ConfirmProvider>
    </MemoryRouter>
  );
  const { rerender } = render(tree(capabilities));
  // The match shell's capabilities load after the page first renders.
  return { resolveCapabilities: (caps: MatchCapability[]) => rerender(tree(caps)) };
}

const DEFAULT_BODY = settingsToBody(DEFAULT_EXPORT_SETTINGS);

function preset(id: string, name: string, body: Partial<ExportPresetBody> = {}): ExportPreset {
  return { preset_id: id, name, builtin: true, updated_at: "2026-09-15T00:00:00Z", body: { ...DEFAULT_BODY, ...body } };
}

/** The API's built-ins, as far as these tests read them. */
const BUILTINS: ExportPreset[] = [
  preset("builtin:final-cut", "Final Cut bundle"),
  preset("builtin:youtube", "YouTube match video", {
    output_format: "mp4",
    youtube_preset: true,
    padding_preset: "action",
    head_pad_seconds: 0.5,
    tail_pad_seconds: 1,
    title_page: true,
    closing_card: true,
    stage_card_style: "slate",
    summary_hold_seconds: 3,
    overlay: true,
  }),
];

/** What a first YouTube render must carry: the cards and the overlay. */
const YOUTUBE_LOOK = {
  title_page: true,
  closing_card: true,
  title_kind: "slate",
  summary_hold_seconds: 3,
  include_overlay: true,
  head_pad_seconds: 0.5,
};

beforeEach(() => {
  vi.mocked(api.getExportOverview).mockResolvedValue(OVERVIEW);
  vi.mocked(api.getExportRuns).mockResolvedValue({ runs: [] });
  vi.mocked(api.getYouTubeSettings).mockResolvedValue({
    configured: true,
    connected: false,
    channel_title: null,
    connected_at: null,
  });
  vi.mocked(api.exportPreview).mockResolvedValue(new Blob(["png"], { type: "image/png" }));
  vi.mocked(api.listDesktopCommands).mockResolvedValue({ commands: [], presence: away });
  window.localStorage.clear();
  viewport.mobile = false;
});
afterEach(() => vi.clearAllMocks());

describe("Export on a desktop-synced match", () => {
  it("a mirror renders on the desktop instead of here", async () => {
    const user = userEvent.setup();
    vi.mocked(api.requestDesktopRender).mockResolvedValue(renderCommand({ status: "pending" }));
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "hosted" });

    const button = await screen.findByRole("button", { name: "Render on desktop" });
    await waitFor(() => expect(button).toBeEnabled());
    // No request in view: the line describes the desktop, not a request.
    expect(await screen.findByText(/Your desktop was last seen/)).toBeInTheDocument();
    expect(screen.queryByText(/Waiting for your desktop/)).toBeNull();
    expect(screen.getByText("Uploads to the YouTube account connected on your desktop.")).toBeInTheDocument();
    // Only the match video renders on the desktop.
    const modes = within(screen.getByRole("group", { name: "Output mode" }));
    expect(modes.queryByRole("button", { name: "Trims only" })).toBeNull();
    expect(modes.queryByRole("button", { name: "Compare grid" })).toBeNull();

    // A playlist typed without touching Privacy: the form's upload toggle
    // stays off, and the desktop must still get the playlist.
    await user.type(screen.getByRole("textbox", { name: "Playlist" }), "Matches");
    await user.click(button);
    await waitFor(() => expect(api.requestDesktopRender).toHaveBeenCalled());
    expect(api.exportMatch).not.toHaveBeenCalled();
    const [slug, payload] = vi.mocked(api.requestDesktopRender).mock.calls[0];
    expect(slug).toBe("anna");
    expect(payload.output_format).toBe("mp4");
    expect(payload.youtube_upload).toBe(true);
    expect(payload.stage_numbers).toEqual([1, 2]);
    expect(payload.youtube_privacy).toBe("unlisted");
    expect(payload.youtube_playlist).toBe("Matches");
  });

  it("a mirror's stages are not blocked on sources this server cannot see", async () => {
    // Hosted never holds a mirror's raw footage; the desktop checks its own.
    vi.mocked(api.getExportOverview).mockResolvedValue({
      match_exports: [],
      stages: [ready(1), ready(2)].map((s) => ({ ...s, source_reachable: false })),
    });
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "hosted" });
    const button = await screen.findByRole("button", { name: "Render on desktop" });
    await waitFor(() => expect(button).toBeEnabled());
    expect(screen.queryByText(/Upload missing/)).toBeNull();
  });

  it("renders the match video whatever the stored form says", async () => {
    // Last used here: trims, as an FCPXML without the YouTube preset.
    saveLastUsed(
      window.localStorage,
      { ...DEFAULT_EXPORT_SETTINGS, mode: "trims", outputFormat: "fcpxml", youtube: false },
      null,
    );
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "hosted" });
    await screen.findByRole("button", { name: "Render on desktop" });
    const timeline = within(screen.getByRole("group", { name: "Output mode" })).getByRole("button", { name: "Timeline" });
    expect(timeline).toHaveAttribute("aria-pressed", "true");
    await userEvent.setup().click(screen.getByRole("button", { name: /^Output$/ }));
    const format = screen.getByRole("combobox", { name: "Timeline format" });
    expect(format).toHaveValue("mp4");
    expect(format).toBeDisabled();
    // The stored choice is the user's, and stays as it was.
    await new Promise((r) => setTimeout(r, 350)); // past the last-used debounce
    expect(loadLastUsed(window.localStorage)?.body).toMatchObject({ mode: "trims", output_format: "fcpxml" });
  });

  it("a first request starts on the YouTube video, with its cards and overlay", async () => {
    // A phone that once exported a hosted-native match as Final Cut: that
    // look is the page's own and must not ride into the desktop's render.
    vi.mocked(api.getExportPresets).mockResolvedValue({ presets: BUILTINS });
    vi.mocked(api.requestDesktopRender).mockResolvedValue(renderCommand({ status: "pending" }));
    saveLastUsed(window.localStorage, DEFAULT_EXPORT_SETTINGS, "builtin:final-cut");
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "hosted" });
    const button = await screen.findByRole("button", { name: "Render on desktop" });
    await waitFor(() => expect(button).toBeEnabled());
    await waitFor(() =>
      expect(within(screen.getByRole("group", { name: "Preset" })).getByRole("button", { name: "YouTube match video" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    await userEvent.setup().click(button);
    await waitFor(() => expect(api.requestDesktopRender).toHaveBeenCalled());
    expect(vi.mocked(api.requestDesktopRender).mock.calls[0][1]).toMatchObject(YOUTUBE_LOOK);
    // Remembered for the next request, apart from the page's own export.
    await new Promise((r) => setTimeout(r, 350)); // past the last-used debounce
    expect(loadLastUsed(window.localStorage, "desktop")?.presetId).toBe("builtin:youtube");
    expect(loadLastUsed(window.localStorage)?.presetId).toBe("builtin:final-cut");
  });

  it("starts on the YouTube video when the capabilities arrive after the page", async () => {
    vi.mocked(api.getExportPresets).mockResolvedValue({ presets: BUILTINS });
    vi.mocked(api.requestDesktopRender).mockResolvedValue(renderCommand({ status: "pending" }));
    // The page's own export follows this shooter's Final Cut history.
    vi.mocked(api.getExportRuns).mockResolvedValue({
      runs: [
        {
          run_id: "r1",
          kind: "match",
          finished_at: "2026-09-20T00:00:00Z",
          duration_seconds: 10,
          stage_numbers: [1, 2],
          formats: ["fcpxml"],
          anomaly_count: 0,
          artifacts: [],
        },
      ],
    });
    const { resolveCapabilities } = renderExport({ capabilities: null, origin: "desktop", mode: "hosted" });
    // Unknown capabilities: the page's own form, on its first preset.
    await screen.findByRole("button", { name: "Export bundle" });
    await waitFor(() =>
      expect(within(screen.getByRole("group", { name: "Preset" })).getByRole("button", { name: "Final Cut bundle" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    resolveCapabilities(MIRROR_CAPABILITIES);
    const button = await screen.findByRole("button", { name: "Render on desktop" });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.setup().click(button);
    await waitFor(() => expect(api.requestDesktopRender).toHaveBeenCalled());
    expect(vi.mocked(api.requestDesktopRender).mock.calls[0][1]).toMatchObject(YOUTUBE_LOOK);
  });

  it("a bundle preset is offered greyed, since the desktop renders the video only", async () => {
    vi.mocked(api.getExportPresets).mockResolvedValue({ presets: BUILTINS });
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "hosted" });
    await screen.findByRole("button", { name: "Render on desktop" });
    const row = within(await screen.findByRole("group", { name: "Preset" }));
    await waitFor(() => expect(row.getByRole("button", { name: "Final Cut bundle" })).toBeDisabled());
  });

  it("sends one request at a time", async () => {
    const user = userEvent.setup();
    vi.mocked(api.requestDesktopRender).mockReturnValue(new Promise(() => {}));
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "hosted" });
    const button = await screen.findByRole("button", { name: "Render on desktop" });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    expect(await screen.findByRole("button", { name: "Sending..." })).toBeDisabled();
    expect(api.requestDesktopRender).toHaveBeenCalledTimes(1);
  });

  it("shows this shooter's render requests with their state", async () => {
    vi.mocked(api.listDesktopCommands).mockResolvedValue({
      commands: [
        renderCommand({
          id: "c2",
          status: "succeeded",
          finished_at: new Date().toISOString(),
          result: { url: "https://youtu.be/v", channel_title: "C", video_id: "v" },
        }),
        renderCommand({ id: "c3", slug: "bo", status: "claimed" }),
        renderCommand({ id: "c4", kind: "shot_detect", stage_number: 1, status: "pending" }),
      ],
      presence: around,
    });
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "hosted" });
    expect(await screen.findByRole("link", { name: "youtu.be/v" })).toHaveAttribute("href", "https://youtu.be/v");
    expect(screen.getByText("Render and upload (anna)")).toBeInTheDocument();
    // Another shooter's render and a re-detect belong elsewhere.
    expect(screen.queryByText("Render and upload (bo)")).toBeNull();
    expect(screen.queryByText(/Re-detect/)).toBeNull();
  });

  it("says a request will be picked up only while one waits", async () => {
    const done = renderCommand({
      id: "c2",
      status: "succeeded",
      finished_at: new Date().toISOString(),
      result: { url: "https://youtu.be/v", channel_title: "C", video_id: "v" },
    });
    vi.mocked(api.listDesktopCommands).mockResolvedValue({ commands: [done], presence: around });
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "hosted" });
    await screen.findByRole("link", { name: "youtu.be/v" });
    expect(screen.getByText("Your desktop is online.")).toBeInTheDocument();
    expect(screen.queryByText("Your desktop will pick this up shortly.")).toBeNull();
  });

  it("a waiting request keeps the pick-up wording under the button", async () => {
    vi.mocked(api.listDesktopCommands).mockResolvedValue({
      commands: [renderCommand({ status: "pending" })],
      presence: around,
    });
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "hosted" });
    const button = await screen.findByRole("button", { name: "Render on desktop" });
    const rail = button.parentElement as HTMLElement;
    expect(await within(rail).findByText("Your desktop will pick this up shortly.")).toBeInTheDocument();
    expect(screen.queryByText("Your desktop is online.")).toBeNull();
  });

  it("a hosted-native match keeps its own export", async () => {
    renderExport({ capabilities: FULL_CAPABILITIES, origin: "hosted", mode: "hosted" });
    expect(await screen.findByRole("button", { name: "Export bundle" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Render on desktop" })).toBeNull();
    expect(api.listDesktopCommands).not.toHaveBeenCalled();
  });

  it("only a desktop-origin match on hosted asks the desktop", async () => {
    // Provenance decides, not the missing edit alone: a hosted-native
    // match without edit stays the read-only page.
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "hosted", mode: "hosted" });
    expect(await screen.findByRole("button", { name: "Export bundle" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Render on desktop" })).toBeNull();
  });

  it("the desktop app itself exports its own match", async () => {
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "local" });
    expect(await screen.findByRole("button", { name: "Export bundle" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Render on desktop" })).toBeNull();
    expect(api.listDesktopCommands).not.toHaveBeenCalled();
  });
});

describe("Export on a phone", () => {
  it("a mirror reaches the page, since asking the desktop is a phone task", async () => {
    viewport.mobile = true;
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "hosted" });
    expect(await screen.findByRole("button", { name: "Render on desktop" })).toBeInTheDocument();
    expect(screen.queryByText("This screen needs a desktop")).toBeNull();
  });

  it("a hosted-native match still gets the notice", async () => {
    viewport.mobile = true;
    renderExport({ capabilities: FULL_CAPABILITIES, origin: "hosted", mode: "hosted" });
    expect(await screen.findByText("This screen needs a desktop")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Export bundle|Render on desktop/ })).toBeNull();
  });

  it("a desktop-origin match in the desktop app's own window still gets the notice", async () => {
    viewport.mobile = true;
    renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "local" });
    expect(await screen.findByText("This screen needs a desktop")).toBeInTheDocument();
  });
});
