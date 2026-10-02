import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type MatchProject, type ShooterCameraInfo, type ShooterListEntry } from "@/lib/api";

import { ShareCameras } from "./ShareCameras";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, listMatchShooters: vi.fn(), getProject: vi.fn(), setCompareCamera: vi.fn() },
  };
});

const cam = (mount: string | null): ShooterCameraInfo => ({
  group_key: `x|x|${mount}`,
  make: null,
  model: null,
  mount,
  role: mount === "head" ? "primary" : "secondary",
  video_count: 8,
  stage_numbers: [1, 2, 3, 4, 5, 6, 7, 8],
});

const shooter = (slug: string, name: string, cameras: ShooterCameraInfo[]) =>
  ({ slug, name, cameras }) as unknown as ShooterListEntry;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listMatchShooters).mockResolvedValue({
    shooters: [
      shooter("mathias", "Mathias", [cam("head"), cam("hand")]),
      shooter("martin", "Martin", [cam("head"), cam("hand")]),
      shooter("anton", "Anton", [cam("head"), cam("chest")]),
      shooter("cleo", "Cleo", [cam("head")]),
    ],
  } as never);
  vi.mocked(api.getProject).mockImplementation(
    async (slug: string) => ({ compare_camera: slug === "martin" ? "hand" : null }) as unknown as MatchProject,
  );
  vi.mocked(api.setCompareCamera).mockResolvedValue({} as never);
});

describe("ShareCameras", () => {
  it("shows each shooter with a choice on their saved default; one camera, no row", async () => {
    render(<ShareCameras />);
    const martin = await screen.findByRole("group", { name: "Martin starts on" });
    expect(within(martin).getByRole("button", { name: "Handheld" })).toHaveAttribute("aria-pressed", "true");
    // Nothing saved: the primary camera, named by its mount.
    expect(within(screen.getByRole("group", { name: "Mathias starts on" })).getByRole("button", { name: "Head cam" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("group", { name: "Cleo starts on" })).toBeNull();
  });

  it("everyone on handheld saves those who have one and names the rest", async () => {
    render(<ShareCameras />);
    const everyone = await screen.findByRole("group", { name: "Everyone starts on" });
    fireEvent.click(within(everyone).getByRole("button", { name: "Handheld" }));
    await screen.findByText("Everyone on Handheld. Anton has no handheld and keeps their camera.");
    expect(vi.mocked(api.setCompareCamera).mock.calls).toEqual([
      ["mathias", "hand"],
      ["martin", "hand"],
    ]);
    expect(within(screen.getByRole("group", { name: "Mathias starts on" })).getByRole("button", { name: "Handheld" })).toHaveAttribute("aria-pressed", "true");
  });

  it("one shooter onto another camera saves its mount", async () => {
    render(<ShareCameras />);
    const martin = await screen.findByRole("group", { name: "Martin starts on" });
    fireEvent.click(within(martin).getByRole("button", { name: "Head cam" }));
    await waitFor(() => expect(api.setCompareCamera).toHaveBeenCalledWith("martin", "head"));
    expect(await screen.findByText("Martin starts on Head cam.")).toBeInTheDocument();
  });

  it("everyone back to the primary clears every saved default", async () => {
    render(<ShareCameras />);
    const everyone = await screen.findByRole("group", { name: "Everyone starts on" });
    fireEvent.click(within(everyone).getByRole("button", { name: "Primary" }));
    await screen.findByText("Everyone on their primary camera.");
    expect(vi.mocked(api.setCompareCamera).mock.calls).toEqual([
      ["mathias", null],
      ["martin", null],
      ["anton", null],
    ]);
  });
});
