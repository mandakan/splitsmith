import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type MatchProject, type ShareInfo, type ShooterCameraInfo, type ShooterListEntry } from "@/lib/api";
import { useShareCameraData } from "@/lib/useShareCameraData";

import { DefaultCameras, LinkCameras } from "./ShareCameras";

function ShareCameras() {
  return <DefaultCameras data={useShareCameraData()} />;
}

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn(),
      getProject: vi.fn(),
      setCompareCamera: vi.fn(),
      setShareCameras: vi.fn(),
    },
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
      shooter("magnus", "Magnus", [cam("head"), cam("hand")]),
      shooter("albin", "Albin", [cam("head"), cam("chest")]),
      shooter("cleo", "Cleo", [cam("head")]),
    ],
  } as never);
  vi.mocked(api.getProject).mockImplementation(
    async (slug: string) => ({ compare_camera: slug === "magnus" ? "hand" : null }) as unknown as MatchProject,
  );
  vi.mocked(api.setCompareCamera).mockResolvedValue({} as never);
});

describe("ShareCameras", () => {
  it("shows each shooter with a choice on their saved default; one camera, no row", async () => {
    render(<ShareCameras />);
    const magnus = await screen.findByRole("group", { name: "Magnus starts on" });
    expect(within(magnus).getByRole("button", { name: "Handheld" })).toHaveAttribute("aria-pressed", "true");
    // Nothing saved: the primary camera, named by its mount.
    expect(within(screen.getByRole("group", { name: "Mathias starts on" })).getByRole("button", { name: "Head cam" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("group", { name: "Cleo starts on" })).toBeNull();
  });

  it("everyone on handheld saves those who have one and names the rest", async () => {
    render(<ShareCameras />);
    const everyone = await screen.findByRole("group", { name: "Everyone starts on" });
    fireEvent.click(within(everyone).getByRole("button", { name: "Handheld" }));
    await screen.findByText("Everyone on Handheld. Albin has no handheld and keeps their camera.");
    expect(vi.mocked(api.setCompareCamera).mock.calls).toEqual([
      ["mathias", "hand"],
      ["magnus", "hand"],
    ]);
    expect(within(screen.getByRole("group", { name: "Mathias starts on" })).getByRole("button", { name: "Handheld" })).toHaveAttribute("aria-pressed", "true");
  });

  it("one shooter onto another camera saves its mount", async () => {
    render(<ShareCameras />);
    const magnus = await screen.findByRole("group", { name: "Magnus starts on" });
    fireEvent.click(within(magnus).getByRole("button", { name: "Head cam" }));
    await waitFor(() => expect(api.setCompareCamera).toHaveBeenCalledWith("magnus", "head"));
    expect(await screen.findByText("Magnus starts on Head cam.")).toBeInTheDocument();
  });

  it("everyone back to the primary clears every saved default", async () => {
    render(<ShareCameras />);
    const everyone = await screen.findByRole("group", { name: "Everyone starts on" });
    fireEvent.click(within(everyone).getByRole("button", { name: "Primary" }));
    await screen.findByText("Everyone on their primary camera.");
    expect(vi.mocked(api.setCompareCamera).mock.calls).toEqual([
      ["mathias", null],
      ["magnus", null],
      ["albin", null],
    ]);
  });
});

function Link({ share, onSaved }: { share: ShareInfo; onSaved: (s: ShareInfo) => void }) {
  return <LinkCameras share={share} data={useShareCameraData()} onSaved={onSaved} />;
}

const SHARE: ShareInfo = { cameras: null, scope: "read", id: "s1", url: "https://x/share/t", created_at: "2026-10-02T10:00:00Z", revoked_at: null };

describe("LinkCameras", () => {
  it("says what a link without its own cameras opens on: the defaults", async () => {
    render(<Link share={SHARE} onSaved={() => {}} />);
    expect(
      await screen.findByText("Mathias Head cam · Magnus Handheld · Albin Head cam"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Opens on the default cameras/)).toBeInTheDocument();
  });

  it("sets this link to handheld for everyone who has one, without touching the defaults", async () => {
    const onSaved = vi.fn();
    vi.mocked(api.setShareCameras).mockImplementation(async (_id, cameras) => ({ ...SHARE, cameras }));
    render(<Link share={SHARE} onSaved={onSaved} />);
    fireEvent.click(await screen.findByRole("button", { name: "Cameras" }));
    const everyone = screen.getByRole("group", { name: "Everyone starts on" });
    expect(within(everyone).getByRole("button", { name: "Default" })).toBeInTheDocument();
    fireEvent.click(within(everyone).getByRole("button", { name: "Handheld" }));
    await waitFor(() =>
      expect(api.setShareCameras).toHaveBeenCalledWith("s1", { mathias: "hand", magnus: "hand" }),
    );
    expect(api.setCompareCamera).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalledWith({ ...SHARE, cameras: { mathias: "hand", magnus: "hand" } });
    expect(await screen.findByText("Everyone on Handheld. Albin has no handheld and keeps their camera.")).toBeInTheDocument();
  });

  it("a link with its own cameras says so, and one shooter back to Default drops them from it", async () => {
    vi.mocked(api.setShareCameras).mockImplementation(async (_id, cameras) => ({ ...SHARE, cameras }));
    render(<Link share={{ ...SHARE, cameras: { mathias: "hand" } }} onSaved={() => {}} />);
    expect(await screen.findByText("Mathias Handheld · Magnus Handheld · Albin Head cam")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cameras" }));
    fireEvent.click(
      within(screen.getByRole("group", { name: "Mathias starts on" })).getByRole("button", { name: "Default" }),
    );
    await waitFor(() => expect(api.setShareCameras).toHaveBeenCalledWith("s1", null));
  });
});

