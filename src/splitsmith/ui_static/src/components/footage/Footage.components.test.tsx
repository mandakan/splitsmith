import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { MatchProject, ShooterListEntry, StageEntry, StageVideo } from "@/lib/api";
import { buildFootageRows, unassignedVideos } from "@/lib/footage";

import { CamerasPanel } from "./CamerasPanel";
import { CoverageMatrix } from "./CoverageMatrix";
import { FootageCards } from "./FootageCards";
import { ShootersPanel } from "./ShootersPanel";
import { UnassignedPanel } from "./UnassignedPanel";

vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getCalibratedCameraModels: vi.fn().mockResolvedValue({ models: [{ key: "gopro hero12", make: "GoPro", model: "HERO12" }] }),
      bulkSetCamera: vi.fn().mockResolvedValue({ name: "p" }),
    },
  };
});

const { api } = await import("@/lib/api");

function video(over: Partial<StageVideo>): StageVideo {
  return { path: "raw/VID_20260627_1066.MP4", video_id: "v1", role: "primary", beep_time: 5.32, beep_reviewed: true, match_timestamp: null, ...over } as StageVideo;
}
function project(stages: { n: number; name: string; videos?: StageVideo[] }[], unassigned: StageVideo[] = []): MatchProject {
  return {
    stages: stages.map((s) => ({ stage_number: s.n, stage_name: s.name, videos: s.videos ?? [], placeholder: false })) as StageEntry[],
    unassigned_videos: unassigned,
  } as unknown as MatchProject;
}
const ME = { slug: "me", name: "Mathias Axell", video_count: 3, stages_missing_trim: 0 } as ShooterListEntry;
const ANNA = { slug: "anna", name: "Anna Berg", video_count: 1, stages_missing_trim: 2 } as ShooterListEntry;
const HREFS = { audit: (slug: string, n: number) => `/m/audit/${slug}/${n}` };

const SOLO_PROJECTS = {
  me: project([
    { n: 1, name: "B100 Höger" },
    { n: 2, name: "B100 Vänster", videos: [video({}), video({ path: "raw/GX010012.MP4", video_id: "v2", role: "secondary" })] },
    { n: 10, name: "B3", videos: [video({ path: "raw/VID_20260627_1102.MP4", video_id: "v3", beep_time: 4.9, beep_reviewed: false })] },
  ]),
};

describe("CoverageMatrix", () => {
  it("renders chips, no-footage rows, beep states and the Confirm link; a chip click opens the clip", () => {
    const onOpen = vi.fn();
    const onAssign = vi.fn();
    const rows = buildFootageRows({ projects: SOLO_PROJECTS, shooters: [ME], jobs: [] });
    render(
      <MemoryRouter>
        <CoverageMatrix rows={rows} currentVideoId={null} currentStage={null} hrefs={HREFS} onOpen={onOpen} onAssign={onAssign} onDetectBeep={vi.fn()} editDenied={false} />
      </MemoryRouter>,
    );
    const r1 = screen.getByText("B100 Höger").closest("tr")!;
    expect(within(r1).getByText(/no footage/)).toBeInTheDocument();
    fireEvent.click(within(r1).getByRole("button", { name: /Assign/ }));
    expect(onAssign).toHaveBeenCalledWith("me", 1);
    const r2 = screen.getByText("B100 Vänster").closest("tr")!;
    expect(within(r2).getByRole("button", { name: "VID_20260627_1066.MP4, primary" })).toBeInTheDocument();
    expect(within(r2).getByRole("button", { name: "GX010012.MP4, secondary" })).toBeInTheDocument();
    expect(within(r2).getByText("5.32")).toBeInTheDocument();
    fireEvent.click(within(r2).getByRole("button", { name: /GX010012/ }));
    expect(onOpen).toHaveBeenCalledWith("me", 2, expect.objectContaining({ video_id: "v2" }));
    const r10 = screen.getByText("B3").closest("tr")!;
    expect(within(r10).getByText("4.90 · unconfirmed")).toBeInTheDocument();
    expect(within(r10).getByRole("link", { name: "Confirm" })).toHaveAttribute("href", "/m/audit/me/10");
  });

  it("the row menu offers Detect beep only with a primary", () => {
    const onDetect = vi.fn();
    const rows = buildFootageRows({ projects: SOLO_PROJECTS, shooters: [ME], jobs: [] });
    render(
      <MemoryRouter>
        <CoverageMatrix rows={rows} currentVideoId={null} currentStage={null} hrefs={HREFS} onOpen={vi.fn()} onAssign={vi.fn()} onDetectBeep={onDetect} editDenied={false} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Stage 1 actions" }));
    expect(screen.queryByRole("menuitem", { name: "Detect beep" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Stage 2 actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Detect beep" }));
    expect(onDetect).toHaveBeenCalledWith("me", 2);
  });

  it("multi-shooter: a column per shooter and the beep under each cell", () => {
    const rows = buildFootageRows({ projects: { ...SOLO_PROJECTS, anna: project([{ n: 2, name: "B100 Vänster", videos: [video({ path: "raw/DJI_0044.MP4", video_id: "a1" })] }]) }, shooters: [ME, ANNA], jobs: [] });
    render(
      <MemoryRouter>
        <CoverageMatrix rows={rows} currentVideoId={null} currentStage={null} hrefs={HREFS} onOpen={vi.fn()} onAssign={vi.fn()} onDetectBeep={vi.fn()} editDenied={false} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("columnheader", { name: "Anna Berg" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Beep" })).toBeNull();
    expect(screen.getAllByText("5.32")).toHaveLength(2);
  });
});

describe("FootageCards", () => {
  it("renders one card per stage with the beep and the chips", () => {
    const rows = buildFootageRows({ projects: SOLO_PROJECTS, shooters: [ME], jobs: [] });
    render(
      <MemoryRouter>
        <FootageCards rows={rows} hrefs={HREFS} onOpen={vi.fn()} />
      </MemoryRouter>,
    );
    const card = screen.getByRole("region", { name: "Stage 2 B100 Vänster" });
    expect(within(card).getByText("5.32")).toBeInTheDocument();
    expect(within(card).getAllByRole("button")).toHaveLength(2);
    expect(within(screen.getByRole("region", { name: "Stage 1 B100 Höger" })).getByText("no footage")).toBeInTheDocument();
  });
});

describe("UnassignedPanel", () => {
  it("lists the files with a stage picker that assigns", () => {
    const onAssign = vi.fn();
    const items = unassignedVideos({ projects: { me: project([], [video({ path: "raw/VID_20260627_1403.MP4", video_id: "u1" })]) }, shooters: [ME] });
    render(
      <UnassignedPanel items={items} stages={SOLO_PROJECTS.me.stages} multi={false} editDenied={false} onOpen={vi.fn()} onAssign={onAssign} onRemove={vi.fn()} />,
    );
    expect(screen.getByText("VID_…1403.MP4")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Assign VID_20260627_1403.MP4 to stage"), { target: { value: "10" } });
    expect(onAssign).toHaveBeenCalledWith(items[0], 10);
  });
});

describe("ShootersPanel", () => {
  it("names the shooters, offers Rebuild trims when caches are missing, and Remove", () => {
    const onRemove = vi.fn();
    const onRebuild = vi.fn();
    render(
      <MemoryRouter>
        <ShootersPanel shooters={[ME, ANNA]} activeSlug="me" editDenied={false} hrefs={{ footage: (s) => `/m/ingest/${s}`, audit: (s) => `/m/audit/${s}` }} onAdd={vi.fn()} onRemove={onRemove} onRebuildTrims={onRebuild} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "Anna Berg" })).toHaveAttribute("href", "/m/ingest/anna");
    fireEvent.click(screen.getByRole("button", { name: "Mathias Axell actions" }));
    expect(screen.queryByRole("menuitem", { name: /Rebuild trims/ })).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: /Remove/ }));
    expect(onRemove).toHaveBeenCalledWith(ME);
    fireEvent.click(screen.getByRole("button", { name: "Anna Berg actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Rebuild trims (2)" }));
    expect(onRebuild).toHaveBeenCalledWith(ANNA);
  });
});

describe("CamerasPanel", () => {
  it("saves a mount through bulkSetCamera and hands the project up", async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    const cam = { id: "c1", label: "Camera A", make: "GoPro", model: "HERO12", mount: null, videoCount: 11, videoPaths: new Set<string>(), members: [{ stage_number: 2, video_id: "v1" }] };
    render(<CamerasPanel slug="me" cameras={[cam]} editDenied={false} onSaved={onSaved} />);
    fireEvent.change(screen.getByLabelText("Mount for Camera A"), { target: { value: "head" } });
    await vi.waitFor(() => expect(api.bulkSetCamera).toHaveBeenCalledWith("me", { items: cam.members, set_mount: true, mount: "head" }));
    await vi.waitFor(() => expect(onSaved).toHaveBeenCalledWith({ name: "p" }));
  });
});
