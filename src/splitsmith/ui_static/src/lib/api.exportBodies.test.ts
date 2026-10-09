import { afterEach, describe, expect, it, vi } from "vitest";

import {
  api,
  type CompareGridRequestPayload,
  type ExportStageRequestPayload,
  type MatchExportRequestPayload,
} from "@/lib/api";

/** Every export wrapper must put every field its payload type declares on
 *  the wire. The payloads below are typed ``Required<...>``, so adding a
 *  field to a request type fails the typecheck until it is added here, and
 *  then fails this test until the wrapper forwards it. The page tests mock
 *  the wrappers and cannot see a field dropped inside one: the YouTube
 *  preset rendered without its title page, closing card and summary hold
 *  for exactly that reason. */

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetch() {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ id: "job", kind: "export", status: "pending" }),
  } as unknown as Response);
}

function sentBody(fetchMock: ReturnType<typeof mockFetch>): Record<string, unknown> {
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return JSON.parse(init.body as string) as Record<string, unknown>;
}

const matchPayload: Required<MatchExportRequestPayload> = {
  stage_numbers: [1, 2],
  head_pad_seconds: 0.5,
  tail_pad_seconds: 1,
  include_secondaries: false,
  include_overlay: true,
  overlay_codec: "prores-4444",
  overlay_max_height: 1080,
  overlay_max_fps: 30,
  project_name: "Match",
  pip_layout: "pip-corners",
  main_camera: "hand",
  inset_camera: "head",
  inset_corner: "top-left",
  inset_size: "small",
  output_format: "mp4",
  transition_kind: "none",
  transition_duration_seconds: 0.5,
  title_kind: "slate",
  title_duration_seconds: 1.5,
  overlay_theme: "clean",
  title_page_variant: "rise",
  stage_card_variant: "default",
  closing_card_variant: "rise",
  intro_path: "/intro.mp4",
  outro_path: "/outro.mp4",
  youtube_sidecar: true,
  description_lead: "Lead",
  youtube_preset: true,
  youtube_upload: true,
  youtube_privacy: "unlisted",
  youtube_playlist: "Playlist",
  youtube_playlist_id: "PL1",
  youtube_publish_at: "2026-09-28T10:00:00Z",
  youtube_notify_subscribers: false,
  title_page: true,
  title_info: "Level II",
  title_division: false,
  title_page_duration_seconds: 3,
  closing_card: true,
  made_with: false,
  account_brand: false,
  logo_spots: ["wipe"],
  summary_hold_seconds: 3,
  match_summary: true,
  match_summary_seconds: 6,
  overlay_variant: "plate",
  overlay_speed_colors: false,
  overlay_class_labels: false,
  overlay_landing: false,
  overlay_reload_chip: true,
  overlay_stage_bar: true,
  overlay_position: "top-right",
};

describe("export wrappers forward every declared field", () => {
  it("exportMatch", async () => {
    const fetchMock = mockFetch();
    await api.exportMatch("s1", matchPayload);
    expect(sentBody(fetchMock)).toEqual(matchPayload);
  });

  it("requestDesktopRender", async () => {
    const fetchMock = mockFetch();
    await api.requestDesktopRender("anna", matchPayload);
    const body = sentBody(fetchMock);
    expect(body.kind).toBe("render_upload");
    expect(body.slug).toBe("anna");
    // Every declared field reaches the desktop, as for exportMatch.
    for (const key of Object.keys(matchPayload)) {
      expect((body.args as { request: Record<string, unknown> }).request).toHaveProperty(key);
    }
  });

  it("exportMatch keeps its pad and overlay defaults for an empty payload", async () => {
    const fetchMock = mockFetch();
    await api.exportMatch("s1", { stage_numbers: [1] });
    expect(sentBody(fetchMock)).toEqual({
      stage_numbers: [1],
      head_pad_seconds: 5,
      tail_pad_seconds: 5,
      include_secondaries: true,
      include_overlay: true,
      overlay_codec: "auto",
    });
  });

  it("exportCompareGrid", async () => {
    const payload: Required<CompareGridRequestPayload> = {
      stage_numbers: [1],
      audio_from: "mathias",
      transition_kind: "fade",
      transition_duration_seconds: 1,
      cameras: { mathias: "gopro" },
      canvas_width: 1920,
      canvas_height: 1080,
      output_name: "grid",
      title_page: true,
      title_info: "Level II",
      title_division: false,
      title_page_duration_seconds: 3,
      closing_card: true,
      made_with: false,
  account_brand: false,
  logo_spots: ["wipe"],
      match_summary: true,
      match_summary_seconds: 7,
      stage_titles: "slate",
      title_duration_seconds: 1.5,
      overlay: true,
      overlay_theme: "clean",
      title_page_variant: "rise",
      stage_card_variant: "default",
      closing_card_variant: "rise",
      summary_hold_seconds: 3,
      inset_camera: "head",
      inset_corner: "top-left",
      inset_size: "small",
      free_cell: "splits",
      youtube_sidecar: true,
      description_lead: "Every stage, side by side.",
      youtube_upload: true,
      youtube_privacy: "private",
      youtube_playlist: "Squad",
      youtube_playlist_id: "PL1",
      youtube_publish_at: "2026-10-03T08:00:00.000Z",
      youtube_notify_subscribers: false,
    };
    const fetchMock = mockFetch();
    await api.exportCompareGrid(payload);
    expect(sentBody(fetchMock)).toEqual(payload);
  });

  it("exportStage", async () => {
    const opts: Required<ExportStageRequestPayload> = {
      write_trim: false,
      write_csv: false,
      write_fcpxml: false,
      write_report: false,
      write_overlay: true,
      overlay_codec: "prores-4444",
      overlay_max_height: 720,
      overlay_max_fps: 30,
      secondary_video_ids: ["v1"],
      write_summary_card: true,
      summary_hold_seconds: 3,
    };
    const fetchMock = mockFetch();
    await api.exportStage("s1", 1, opts);
    expect(sentBody(fetchMock)).toEqual(opts);
  });
});
