import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReelPanel } from "@/components/export/ReelPanel";
import { api, type HighlightReadiness, type Job } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, getHighlights: vi.fn(), submitHighlightReel: vi.fn(), pollJob: vi.fn() },
  };
});

const readiness: HighlightReadiness = {
  unlocked: true,
  ready: 2,
  total: 2,
  stages: [],
  menu: [{ kind: "fastest_draw", label: "FASTEST DRAW", group: "default", default: true }],
  cuts: ["normal", "short"],
};

const job = { id: "j1", status: "succeeded", result: null } as unknown as Job;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getHighlights).mockResolvedValue(readiness);
  vi.mocked(api.submitHighlightReel).mockResolvedValue(job);
  vi.mocked(api.pollJob).mockResolvedValue(job);
});

function show(props: Partial<React.ComponentProps<typeof ReelPanel>> = {}) {
  render(
    <MemoryRouter>
      <ReelPanel slug="me" href={(...s) => `/${s.join("/")}`} youtubeConnected={false} onFinished={() => {}} {...props} />
    </MemoryRouter>,
  );
}

describe("ReelPanel's main camera", () => {
  it("is offered under Advanced only when there is more than the primary", async () => {
    show({ cameraChoices: [{ value: "primary", label: "Primary" }] });
    await screen.findByText("Render reel");
    expect(screen.queryByText("Advanced")).toBeNull();
  });

  it("follows the saved camera unless one is chosen, and sends the choice", async () => {
    show({
      cameraChoices: [
        { value: "primary", label: "Primary" },
        { value: "head", label: "Head" },
      ],
      savedCameraLabel: "Head",
    });
    fireEvent.click(await screen.findByText("Advanced"));
    expect(screen.getByText(/saved camera \(Head\)/)).toBeTruthy();
    fireEvent.click(screen.getByText("Render reel"));
    await waitFor(() => expect(api.submitHighlightReel).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.submitHighlightReel).mock.calls[0][1].main_camera).toBe("default");

    fireEvent.click(within(screen.getByRole("group", { name: "Main camera" })).getByRole("button", { name: "Primary" }));
    fireEvent.click(screen.getByText("Render reel"));
    await waitFor(() => expect(api.submitHighlightReel).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.submitHighlightReel).mock.calls[1][1].main_camera).toBe("primary");
  });

  it("sends the saved default once the chosen camera is gone", async () => {
    const choices = [
      { value: "primary", label: "Primary" },
      { value: "head", label: "Head" },
    ];
    const props = { slug: "me", href: (...s: string[]) => `/${s.join("/")}`, youtubeConnected: false, onFinished: () => {} };
    const { rerender } = render(
      <MemoryRouter>
        <ReelPanel {...props} cameraChoices={choices} />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByText("Advanced"));
    fireEvent.click(within(screen.getByRole("group", { name: "Main camera" })).getByRole("button", { name: "Head" }));
    rerender(
      <MemoryRouter>
        <ReelPanel {...props} cameraChoices={[choices[0]]} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("Render reel"));
    await waitFor(() => expect(api.submitHighlightReel).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.submitHighlightReel).mock.calls[0][1].main_camera).toBe("default");
  });
});
