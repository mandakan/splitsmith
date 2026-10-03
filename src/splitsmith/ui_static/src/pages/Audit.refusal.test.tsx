/**
 * A stray account refusal on Audit's job-starting controls renders as a
 * muted sentence, not as the raw JSON of its structured detail.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import { ApiError, api } from "@/lib/api";
import { DetectShotsBadge, TrimNowBadge } from "@/pages/Audit";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, listJobs: vi.fn(), detectShots: vi.fn(), trimStage: vi.fn() },
  };
});

const BODY = { code: "feature_required", feature: "hosted_compute" };
// What ``request()`` throws for this response: ``detail`` stringified,
// ``body`` the raw structure.
const refusal = () => new ApiError(403, JSON.stringify(BODY), BODY);
const SENTENCE = "Running detection and renders here is not included in this account's access.";

beforeEach(() => {
  vi.mocked(api.listJobs).mockReset().mockResolvedValue([]);
  vi.mocked(api.detectShots).mockReset().mockRejectedValue(refusal());
  vi.mocked(api.trimStage).mockReset().mockRejectedValue(refusal());
});

describe("Audit job controls under a refusal", () => {
  it("detect shows the refusal sentence, muted", async () => {
    const user = userEvent.setup();
    render(
      <ConfirmProvider>
        <DetectShotsBadge
          slug="anna"
          stageNumber={3}
          hasBeep
          hasStageTime
          hasCandidates={false}
          onComplete={vi.fn()}
        />
      </ConfirmProvider>,
    );
    await user.click(screen.getByRole("menuitem", { name: "Detect shots" }));
    const line = await screen.findByText(SENTENCE);
    expect(line).toHaveClass("text-muted");
    expect(line).not.toHaveClass("text-led-text");
    expect(screen.queryByText(/feature_required/)).toBeNull();
  });

  it("trim shows the refusal sentence, muted", async () => {
    const user = userEvent.setup();
    render(<TrimNowBadge slug="anna" stageNumber={3} hasBeep hasStageTime onProjectUpdate={vi.fn()} />);
    await user.click(await screen.findByRole("menuitem", { name: /trim/i }));
    const line = await screen.findByText(SENTENCE);
    expect(line).toHaveClass("text-muted");
    expect(screen.queryByText(/feature_required/)).toBeNull();
  });

  it("any other error stays an error line", async () => {
    vi.mocked(api.detectShots).mockRejectedValue(new ApiError(500, "Engine down", "Engine down"));
    const user = userEvent.setup();
    render(
      <ConfirmProvider>
        <DetectShotsBadge
          slug="anna"
          stageNumber={3}
          hasBeep
          hasStageTime
          hasCandidates={false}
          onComplete={vi.fn()}
        />
      </ConfirmProvider>,
    );
    await user.click(screen.getByRole("menuitem", { name: "Detect shots" }));
    expect(await screen.findByText("Engine down")).toHaveClass("text-led-text");
  });
});
