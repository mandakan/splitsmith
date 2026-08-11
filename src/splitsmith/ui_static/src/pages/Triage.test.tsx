/**
 * Triage - the mobile-first stage worklist (slice 4, #700 follow-up).
 * Covers: grouped rendering with the Done collapse, accept (happy path
 * + 409 mapping), and the flag sheet's note field.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Anomaly } from "@/lib/anomalies";
import type { TriageCell, TriageResponse } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getTriage: vi.fn(),
      acceptStage: vi.fn(),
      setStageAttention: vi.fn(),
    },
  };
});

import { ApiError, api } from "@/lib/api";
import { Triage } from "@/pages/Triage";

function cell(over: Partial<TriageCell> = {}): TriageCell {
  return {
    slug: "alice",
    shooter_name: "alice",
    stage_number: 1,
    stage_name: "Stage One",
    status: "ready",
    beep_confidence: null,
    anomalies: [],
    needs_attention: null,
    ...over,
  };
}

const longPause: Anomaly = {
  kind: "long_pause",
  severity: "warn",
  message: "Long pause before shot 3 (3.50 s) - possible missed shot.",
  shot_number: 3,
  time: 8.2,
};

function renderTriage(response: TriageResponse) {
  vi.mocked(api.getTriage).mockResolvedValue(response);
  return render(
    <MemoryRouter initialEntries={["/match/m1/triage"]}>
      <Routes>
        <Route path="/match/:matchId/triage" element={<Triage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Triage", () => {
  const user = userEvent.setup();

  beforeEach(() => {
    vi.mocked(api.getTriage).mockReset();
    vi.mocked(api.acceptStage).mockReset();
    vi.mocked(api.setStageAttention).mockReset();
  });

  it("renders a card per non-done cell with status and anomaly chips", async () => {
    const cells = [
      cell({ slug: "alice", stage_number: 1, status: "ready", anomalies: [longPause], needs_attention: null }),
      cell({ slug: "bob", shooter_name: "bob", stage_number: 1, status: "audited" }),
    ];
    renderTriage({ cells, flagged_count: 0, beep_low_confidence_threshold: 0.95 });

    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.getByText(/long pause/i)).toBeInTheDocument();
    // audited, unflagged cell is collapsed into Done - not rendered as a
    // full card, but still counted.
    expect(screen.getByText(/done \(1\)/i)).toBeInTheDocument();
    expect(screen.queryByText("bob")).not.toBeInTheDocument();
  });

  it("shows the stage heading and low-confidence beep chip", async () => {
    const cells = [cell({ stage_number: 3, stage_name: "Steel Rush", beep_confidence: 0.4 })];
    renderTriage({ cells, flagged_count: 0, beep_low_confidence_threshold: 0.95 });

    expect(await screen.findByText(/stage 3.*steel rush/i)).toBeInTheDocument();
    expect(screen.getByText(/beep 40%/i)).toBeInTheDocument();
  });

  it("uses the payload threshold, not a hardcoded default, to gate the low-confidence pill", async () => {
    const cells = [cell({ stage_number: 3, stage_name: "Steel Rush", beep_confidence: 0.65 })];
    renderTriage({ cells, flagged_count: 0, beep_low_confidence_threshold: 0.5 });

    // 0.65 confidence is below the old hardcoded 0.95 default (which would
    // have shown the pill) but at or above this project's resolved 0.5
    // threshold, so no low-confidence pill should render.
    expect(await screen.findByText(/stage 3.*steel rush/i)).toBeInTheDocument();
    expect(screen.queryByText(/beep 65%/i)).not.toBeInTheDocument();
  });

  it("shows a flagged cell as a card even when terminal, with its note", async () => {
    const cells = [
      cell({
        slug: "carol",
        shooter_name: "carol",
        status: "audited",
        needs_attention: {
          flagged: true,
          flagged_at: "2026-08-10T00:00:00Z",
          note: "beep sounds off",
          updated_at: "2026-08-10T00:00:00Z",
        },
      }),
    ];
    renderTriage({ cells, flagged_count: 1, beep_low_confidence_threshold: 0.95 });

    expect(await screen.findByText("carol")).toBeInTheDocument();
    expect(screen.getByText(/flagged for desktop/i)).toBeInTheDocument();
    expect(screen.getByText("beep sounds off")).toBeInTheDocument();
    // Terminal status hides Accept.
    expect(screen.queryByRole("button", { name: /^accept$/i })).not.toBeInTheDocument();
  });

  it("accept confirms then swaps in the fresh list", async () => {
    const cells = [cell({ slug: "alice", stage_number: 1, status: "ready" })];
    renderTriage({ cells, flagged_count: 0, beep_low_confidence_threshold: 0.95 });
    vi.mocked(api.acceptStage).mockResolvedValue({
      cells: [cell({ slug: "alice", stage_number: 1, status: "audited" })],
      flagged_count: 0, beep_low_confidence_threshold: 0.95,
    });

    await user.click(await screen.findByRole("button", { name: /^accept$/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^accept$/i })); // sheet confirm

    expect(api.acceptStage).toHaveBeenCalledWith("alice", 1);
    expect(await screen.findByText(/done \(1\)/i)).toBeInTheDocument();
  });

  it("accept 409 nothing_to_accept shows a readable message", async () => {
    const cells = [cell({ slug: "alice", stage_number: 1, status: "ready" })];
    renderTriage({ cells, flagged_count: 0, beep_low_confidence_threshold: 0.95 });
    vi.mocked(api.acceptStage).mockRejectedValue(new ApiError(409, "nothing_to_accept"));

    await user.click(await screen.findByRole("button", { name: /^accept$/i }));
    const dialog1 = await screen.findByRole("dialog");
    await user.click(within(dialog1).getByRole("button", { name: /^accept$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/nothing to accept yet/i);
  });

  it("accept 409 not_fully_classified shows a readable message", async () => {
    const cells = [cell({ slug: "alice", stage_number: 1, status: "ready" })];
    renderTriage({ cells, flagged_count: 0, beep_low_confidence_threshold: 0.95 });
    vi.mocked(api.acceptStage).mockRejectedValue(new ApiError(409, "not_fully_classified"));

    await user.click(await screen.findByRole("button", { name: /^accept$/i }));
    const dialog2 = await screen.findByRole("dialog");
    await user.click(within(dialog2).getByRole("button", { name: /^accept$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/finish this stage on desktop/i);
  });

  it("flag sheet sends the note", async () => {
    const cells = [cell({ slug: "alice", stage_number: 1, status: "ready" })];
    renderTriage({ cells, flagged_count: 0, beep_low_confidence_threshold: 0.95 });
    vi.mocked(api.setStageAttention).mockResolvedValue({
      cells: [
        cell({
          slug: "alice",
          stage_number: 1,
          status: "ready",
          needs_attention: {
            flagged: true,
            flagged_at: "2026-08-11T00:00:00Z",
            note: "beep sounds off",
            updated_at: "2026-08-11T00:00:00Z",
          },
        }),
      ],
      flagged_count: 1, beep_low_confidence_threshold: 0.95,
    });

    await user.click(await screen.findByRole("button", { name: /^flag$/i }));
    await user.type(screen.getByLabelText(/note/i), "beep sounds off");
    await user.click(screen.getByRole("button", { name: /flag for desktop/i }));

    expect(api.setStageAttention).toHaveBeenCalledWith("alice", 1, {
      flagged: true,
      note: "beep sounds off",
    });
    expect(await screen.findByText(/flagged for desktop/i)).toBeInTheDocument();
  });

  it("unflag sends a plain confirm with no note", async () => {
    const cells = [
      cell({
        slug: "alice",
        stage_number: 1,
        status: "ready",
        needs_attention: {
          flagged: true,
          flagged_at: "2026-08-11T00:00:00Z",
          note: null,
          updated_at: "2026-08-11T00:00:00Z",
        },
      }),
    ];
    renderTriage({ cells, flagged_count: 1, beep_low_confidence_threshold: 0.95 });
    vi.mocked(api.setStageAttention).mockResolvedValue({
      cells: [cell({ slug: "alice", stage_number: 1, status: "ready", needs_attention: null })],
      flagged_count: 0, beep_low_confidence_threshold: 0.95,
    });

    await user.click(await screen.findByRole("button", { name: /^unflag$/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^unflag$/i })); // sheet confirm

    expect(api.setStageAttention).toHaveBeenCalledWith("alice", 1, { flagged: false });
  });

  it("links to the results page for the cell's stage", async () => {
    const cells = [cell({ slug: "alice", stage_number: 2, status: "ready" })];
    renderTriage({ cells, flagged_count: 0, beep_low_confidence_threshold: 0.95 });

    const link = await screen.findByRole("link", { name: /results/i });
    expect(link).toHaveAttribute("href", "/match/m1/results/alice/2");
  });

  it("shows a loading skeleton then an error state with retry", async () => {
    vi.mocked(api.getTriage).mockReset();
    vi.mocked(api.getTriage).mockRejectedValueOnce(new ApiError(500, "boom"));
    render(
      <MemoryRouter initialEntries={["/match/m1/triage"]}>
        <Routes>
          <Route path="/match/:matchId/triage" element={<Triage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: /retry/i });
    vi.mocked(api.getTriage).mockResolvedValueOnce({ cells: [], flagged_count: 0, beep_low_confidence_threshold: 0.95 });
    await user.click(retry);

    expect(await screen.findByText(/nothing.*triage|all clear|no stages/i)).toBeInTheDocument();
  });
});
