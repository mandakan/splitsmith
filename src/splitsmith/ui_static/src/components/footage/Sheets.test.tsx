import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { MatchProject, ShooterListEntry, StageEntry, StageVideo } from "@/lib/api";
import type { ClipItem } from "@/pages/ingest/model";

import { AddShooterSheet } from "./AddShooterSheet";
import { ClipSheet } from "./ClipSheet";

vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      suggestCoverage: vi.fn().mockResolvedValue({ covers_stages: [] }),
      setRawVideoCoverage: vi.fn().mockResolvedValue({}),
      detectBeepForVideo: vi.fn().mockResolvedValue({ id: "j1" }),
      shooterVideoStreamUrl: () => "/proxy.mp4",
      getScoreboardMatchDataUnbound: vi.fn().mockResolvedValue({
        competitors: [
          { id: 1, shooterId: 11, name: "Anna Berg", club: "SPK", division: "PO", competitor_number: 1 },
          { id: 2, shooterId: 12, name: "Erik Lund", club: null, division: "Std", competitor_number: 2 },
        ],
      }),
      addMatchShooter: vi.fn().mockResolvedValue({ shooters: [] }),
    },
  };
});
const { api } = await import("@/lib/api");

const STAGES = [
  { stage_number: 2, stage_name: "B100 Vänster", time_seconds: 48.63, stage_rounds: { expected: 28 }, placeholder: false },
  { stage_number: 3, stage_name: "B6 Rear", time_seconds: 0, stage_rounds: null, placeholder: false },
] as unknown as StageEntry[];
const VIDEO = { path: "raw/VID_20260627_1066.MP4", video_id: "v1", role: "primary", beep_time: 5.32, beep_reviewed: true, match_timestamp: null, proxy_ready: true } as StageVideo;
const CLIP: ClipItem = { video: VIDEO, stageNumber: 2 };
const ME = { slug: "me", name: "Mathias", selected_competitor_id: 2 } as ShooterListEntry;
const ANNA = { slug: "anna", name: "Anna", selected_competitor_id: null } as ShooterListEntry;

function renderClip(over: Partial<React.ComponentProps<typeof ClipSheet>> = {}) {
  const props: React.ComponentProps<typeof ClipSheet> = {
    open: true,
    onClose: vi.fn(),
    slug: "me",
    shooterName: "Mathias",
    clip: CLIP,
    assignStage: null,
    unassigned: [],
    allStages: STAGES,
    shooters: [ME, ANNA],
    rawVideos: [],
    mediaOnDesktop: false,
    busy: false,
    editDenied: false,
    auditHref: (s, n) => `/m/audit/${s}/${n}`,
    onMove: vi.fn().mockResolvedValue(undefined),
    onRemove: vi.fn().mockResolvedValue(undefined),
    onMoveShooter: vi.fn().mockResolvedValue(undefined),
    onPickUnassigned: vi.fn(),
    onError: vi.fn(),
    ...over,
  };
  render(
    <MemoryRouter>
      <ClipSheet {...props} />
    </MemoryRouter>,
  );
  return props;
}

describe("ClipSheet", () => {
  it("shows the file, the stage with its time and rounds, the role, the beep, and Open in Audit", () => {
    renderClip();
    expect(screen.getByRole("dialog", { name: "VID_20260627_1066.MP4" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "02 · B100 Vänster · 48.63 s · 28 rounds" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Primary" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("5.32 · confirmed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in Audit" })).toHaveAttribute("href", "/m/audit/me/2");
  });

  it("stage, role, shooter and remove call the page's writes", async () => {
    const props = renderClip();
    fireEvent.change(screen.getByLabelText("Stage"), { target: { value: "3" } });
    await vi.waitFor(() => expect(props.onMove).toHaveBeenCalledWith(VIDEO.path, 3, "primary"));
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Secondary" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Secondary" }));
    await vi.waitFor(() => expect(props.onMove).toHaveBeenCalledWith(VIDEO.path, 2, "secondary"));
    await vi.waitFor(() => expect(screen.getByLabelText("Shooter")).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Shooter"), { target: { value: "anna" } });
    await vi.waitFor(() => expect(props.onMoveShooter).toHaveBeenCalledWith("anna", [VIDEO.path]));
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Remove video" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Remove video" }));
    expect(props.onRemove).toHaveBeenCalledWith(VIDEO.path);
    fireEvent.click(screen.getByRole("button", { name: "Re-detect" }));
    await vi.waitFor(() => expect(api.detectBeepForVideo).toHaveBeenCalledWith("me", 2, "v1"));
  });

  it("assign mode lists the unassigned files and picks one for the asking stage", () => {
    const item = { slug: "me", shooterName: "Mathias", video: { ...VIDEO, path: "raw/VID_20260627_1403.MP4" }, recordedAt: null };
    const props = renderClip({ clip: null, assignStage: 3, unassigned: [item] });
    expect(screen.getByRole("dialog", { name: "Assign a video" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /VID_…1403/ }));
    expect(props.onPickUnassigned).toHaveBeenCalledWith(item, 3);
  });

  it("editDenied locks every write", () => {
    renderClip({ editDenied: true });
    expect(screen.getByLabelText("Stage")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove video" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /detect/i })).toBeNull();
  });
});

describe("AddShooterSheet", () => {
  const linked = { scoreboard_match_id: "77", scoreboard_content_type: 1 } as unknown as MatchProject;

  it("lists the roster minus claimed competitors, filters, and a pick binds the identity", async () => {
    const onChanged = vi.fn();
    render(<AddShooterSheet open onClose={vi.fn()} project={linked} shooters={[ME]} editDenied={false} onChanged={onChanged} />);
    await screen.findByRole("button", { name: /Anna Berg/ });
    expect(screen.queryByRole("button", { name: /Erik Lund/ })).toBeNull();
    fireEvent.change(screen.getByLabelText("Filter roster"), { target: { value: "zzz" } });
    expect(screen.getByText("No one matches.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Filter roster"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /Anna Berg/ }));
    await vi.waitFor(() =>
      expect(api.addMatchShooter).toHaveBeenCalledWith({ name: "Anna Berg", division: "PO", selected_shooter_id: 11, selected_competitor_id: 1 }),
    );
    await vi.waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("an unlinked match adds by name and offers Connect; edit denied disables the form and hides Connect", () => {
    const unlinked = { scoreboard_match_id: null, scoreboard_content_type: null } as unknown as MatchProject;
    const { unmount } = render(<AddShooterSheet open onClose={vi.fn()} project={unlinked} shooters={[ME]} editDenied={false} onChanged={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Shooter name"), { target: { value: "Erik" } });
    fireEvent.click(screen.getByRole("button", { name: "Add shooter" }));
    expect(api.addMatchShooter).toHaveBeenCalledWith({ name: "Erik" });
    unmount();
    render(<AddShooterSheet open onClose={vi.fn()} project={unlinked} shooters={[ME]} editDenied onChanged={vi.fn()} />);
    expect(screen.getByLabelText("Shooter name")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
    expect(screen.getByText(/review actions sync back/i)).toBeInTheDocument();
  });
});
