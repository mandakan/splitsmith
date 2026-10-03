/**
 * The take overview's beep-window drag handles PUT a manual search window
 * and re-run detection. A hosted account without hosted_compute is refused
 * that write server-side, so the handles are not offered.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import { api, type MatchProject, type TakeOverview as TakeOverviewData } from "@/lib/api";
import { TakeOverview } from "@/pages/TakeOverview";

const deployment = vi.hoisted(() => ({ mode: "hosted" as "local" | "hosted" }));
vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: () => ({ mode: deployment.mode, resolved: true }) };
});

const account = vi.hoisted(() => ({ features: [] as string[] }));
vi.mock("@/lib/auth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth")>();
  return {
    ...actual,
    useAuthUser: () => ({
      id: "u1",
      email: "a@x.se",
      display_name: null,
      is_admin: false,
      access_tier: "sharing",
      features: account.features,
    }),
  };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, takeOverview: vi.fn(), takePeaks: vi.fn(), getProject: vi.fn() },
  };
});

const overview = {
  raw_video: { filename: "take.mp4", size_bytes: 0, covers_stages: [1] },
  duration_seconds: 120,
  conflicts: [],
  stages: [
    {
      stage_number: 1,
      stage_name: "Bay 1",
      video_id: "v1",
      role: "primary",
      beep_time: 10,
      beep_confidence: 0.9,
      beep_reviewed: false,
      beep_window: [5, 40],
      beep_window_source: "derived",
      status: "found",
    },
  ],
} as unknown as TakeOverviewData;

function renderPage() {
  return render(
    <ConfirmProvider>
      <MemoryRouter initialEntries={["/match/m1/take/alice/take.mp4"]}>
        <Routes>
          <Route path="/match/:matchId/take/:slug/:filename" element={<TakeOverview />} />
        </Routes>
      </MemoryRouter>
    </ConfirmProvider>,
  );
}

describe("TakeOverview beep-window handles", () => {
  beforeEach(() => {
    deployment.mode = "hosted";
    vi.mocked(api.takeOverview).mockResolvedValue(overview);
    vi.mocked(api.takePeaks).mockResolvedValue({
      peaks: Array.from({ length: 64 }, () => 0.1),
      duration: 120,
    } as never);
    vi.mocked(api.getProject).mockResolvedValue({ stages: [] } as unknown as MatchProject);
  });

  it("offers the handles to an account with hosted_compute", async () => {
    account.features = ["hosted_compute", "share", "sync"];
    renderPage();
    expect(await screen.findByRole("button", { name: /stage 01 window start/i })).toBeInTheDocument();
  });

  it("shows the window read-only to an account without hosted_compute", async () => {
    account.features = ["share", "sync"];
    renderPage();
    expect(await screen.findByText(/S01 Bay 1/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /window start/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /window end/i })).toBeNull();
    expect(screen.queryByText(/drag a window's edges/i)).toBeNull();
  });

  it("local mode keeps the handles whatever the account reads as", async () => {
    deployment.mode = "local";
    account.features = [];
    renderPage();
    expect(await screen.findByRole("button", { name: /stage 01 window start/i })).toBeInTheDocument();
  });
});
