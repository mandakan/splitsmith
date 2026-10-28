import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { SpeechReviewState } from "@/lib/speechReview";

import { SpeechPanel } from "./SpeechPanel";

vi.mock("@/components/Waveform", () => ({ Waveform: () => <div data-testid="speech-waveform" /> }));

function state(over: Partial<SpeechReviewState> = {}): SpeechReviewState {
  return {
    slug: "s",
    duration: 10,
    proposal: {
      method: "silero-vad-5.1.2",
      masker_version: 1,
      regions: [{ start: 1, end: 2 }],
      protected_overlaps: [],
    },
    masked_regions: [],
    review: null,
    protected: [{ start: 4.95, end: 5.05 }],
    masker_version: 1,
    method: "silero-vad-5.1.2",
    ...over,
  };
}

describe("SpeechPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("saves the proposal as accepted masks", async () => {
    vi.spyOn(api, "getLabSpeech").mockResolvedValue(state());
    const save = vi.spyOn(api, "saveLabSpeech").mockResolvedValue(
      state({
        review: { decision: "masked", reviewed_at: "2026-10-28T12:00:00Z", masker_version: 1 },
        masked_regions: [{ start: 1, end: 2, method: "silero-vad-5.1.2", masker_version: 1 }],
      }),
    );
    render(<SpeechPanel slug="s" peaks={null} time={0} onScrub={() => {}} playFrom={() => {}} />);
    expect(await screen.findByText("Not reviewed")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Accept masks/ }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("s", { decision: "masked", regions: [{ start: 1, end: 2 }] }),
    );
    expect(await screen.findByText("Masked")).toBeTruthy();
  });

  it("will not accept a region over a shot", async () => {
    vi.spyOn(api, "getLabSpeech").mockResolvedValue(state());
    render(<SpeechPanel slug="s" peaks={null} time={0} onScrub={() => {}} playFrom={() => {}} />);
    const end = await screen.findByLabelText("Region 1 end");
    fireEvent.change(end, { target: { value: "5" } });
    expect(screen.getByText("covers a shot or the beep")).toBeTruthy();
    expect((screen.getByRole("button", { name: /Accept masks/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("sends no regions for no speech", async () => {
    vi.spyOn(api, "getLabSpeech").mockResolvedValue(state());
    const save = vi.spyOn(api, "saveLabSpeech").mockResolvedValue(
      state({ review: { decision: "no_speech", reviewed_at: "2026-10-28T12:00:00Z", masker_version: 1 } }),
    );
    render(<SpeechPanel slug="s" peaks={null} time={0} onScrub={() => {}} playFrom={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "No speech" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith("s", { decision: "no_speech", regions: [] }));
  });

  it("shows the server's refusal", async () => {
    vi.spyOn(api, "getLabSpeech").mockResolvedValue(state());
    vi.spyOn(api, "saveLabSpeech").mockRejectedValue(new Error("region 1.000-2.000 s covers a shot"));
    render(<SpeechPanel slug="s" peaks={null} time={0} onScrub={() => {}} playFrom={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Accept masks/ }));
    expect(await screen.findByText(/covers a shot$/)).toBeTruthy();
  });
});
