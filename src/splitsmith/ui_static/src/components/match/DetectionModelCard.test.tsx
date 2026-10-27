import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DetectionModelCard } from "@/components/match/DetectionModelCard";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, getDetectionModel: vi.fn(), putDetectionModel: vi.fn() } };
});

const BASE = { model_set: "base", required: [], present: true, active_job: null };
const CLAP = { model_set: "clap", required: ["clap_audio_encoder", "clap_text_embeddings"], present: false };

describe("DetectionModelCard", () => {
  beforeEach(() => {
    vi.mocked(api.getDetectionModel).mockReset();
    vi.mocked(api.putDetectionModel).mockReset();
  });

  it("turns the audio model on and shows the download", async () => {
    vi.mocked(api.getDetectionModel).mockResolvedValue(BASE);
    vi.mocked(api.putDetectionModel).mockResolvedValue({ ...CLAP, active_job: "j1" });
    render(<DetectionModelCard />);
    const toggle = await screen.findByRole("switch", { name: /audio model for head cameras/i });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/downloads a 120 mb model once/i)).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(await screen.findByText("Downloading...")).toBeInTheDocument();
    expect(vi.mocked(api.putDetectionModel).mock.calls).toEqual([["clap"]]);
    expect(toggle).toHaveAttribute("aria-checked", "true");
  });

  it("reads Ready once the files are there, and turns off", async () => {
    vi.mocked(api.getDetectionModel).mockResolvedValue({ ...CLAP, present: true, active_job: null });
    vi.mocked(api.putDetectionModel).mockResolvedValue(BASE);
    render(<DetectionModelCard />);
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: /audio model for head cameras/i }));
    await waitFor(() => expect(screen.queryByText("Ready")).toBeNull());
    expect(vi.mocked(api.putDetectionModel).mock.calls).toEqual([["base"]]);
  });

  it("polls while a download runs and stops when it is done", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.mocked(api.getDetectionModel)
        .mockResolvedValueOnce({ ...CLAP, active_job: "j1" })
        .mockResolvedValue({ ...CLAP, present: true, active_job: null });
      render(<DetectionModelCard />);
      expect(await screen.findByText("Downloading...")).toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(2500);
      expect(await screen.findByText("Ready")).toBeInTheDocument();
      const calls = vi.mocked(api.getDetectionModel).mock.calls.length;
      await vi.advanceTimersByTimeAsync(6000);
      expect(vi.mocked(api.getDetectionModel).mock.calls.length).toBe(calls);
    } finally {
      vi.useRealTimers();
    }
  });
});
