import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DesktopCommand } from "@/lib/api";

import { DesktopRequestsSheet } from "./DesktopRequestsSheet";

function cmd(over: Partial<DesktopCommand>): DesktopCommand {
  return {
    id: "c1",
    match_id: "m1",
    kind: "shot_detect",
    slug: "anna",
    stage_number: 3,
    args: {},
    expected_revision: null,
    status: "pending",
    cancel_requested: false,
    progress_message: null,
    error: null,
    result: null,
    requested_at: "2026-09-28T11:59:00Z",
    claimed_at: null,
    lease_expires_at: null,
    finished_at: null,
    ...over,
  };
}

describe("DesktopRequestsSheet", () => {
  it("lists requests with their state and cancels a waiting one", () => {
    const onCancel = vi.fn();
    render(
      <DesktopRequestsSheet
        open
        onClose={vi.fn()}
        commands={[
          cmd({ id: "a" }),
          cmd({ id: "b", stage_number: 4, status: "failed", error: "the stage changed after this was requested" }),
        ]}
        presence={{ linked: true, last_seen_at: null, around: false }}
        onCancel={onCancel}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Desktop requests" });
    expect(within(dialog).getByText("Re-detect Stage 03 (anna)")).toBeInTheDocument();
    expect(within(dialog).getByText(/Failed on your desktop: the stage changed/)).toBeInTheDocument();
    const cancels = within(dialog).getAllByRole("button", { name: "Cancel" });
    expect(cancels).toHaveLength(1);
    fireEvent.click(cancels[0]);
    expect(onCancel).toHaveBeenCalledWith("a");
  });

  it("says how to ask when there is nothing yet", () => {
    render(<DesktopRequestsSheet open onClose={vi.fn()} commands={[]} presence={null} onCancel={vi.fn()} />);
    expect(screen.getByText(/Nothing asked yet/)).toBeInTheDocument();
  });
});
