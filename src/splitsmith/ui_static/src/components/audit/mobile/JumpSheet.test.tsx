import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { JumpSheet } from "@/components/audit/mobile/JumpSheet";

const shooters = [
  {
    slug: "alice",
    name: "Alice",
    current: true,
    stages: [
      { stageNumber: 1, status: "todo" as const },
      { stageNumber: 2, status: "audited" as const },
      { stageNumber: 3, status: "ready" as const },
    ],
  },
  { slug: "bob", name: "Bob", current: false, stages: [{ stageNumber: 2, status: "ready" as const }] },
];

function renderSheet() {
  const onPick = vi.fn();
  render(
    <JumpSheet
      open
      shooters={shooters}
      currentStage={2}
      stageNames={{ 2: "B100", 3: "B6 Rear" }}
      onPick={onPick}
      onClose={vi.fn()}
    />,
  );
  return onPick;
}

describe("JumpSheet", () => {
  it("offers the current shooter's stages; the open one and one without footage cannot be picked", () => {
    const onPick = renderSheet();
    expect(screen.getByRole("button", { name: /stage 01/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /stage 02/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /stage 03/i }));
    expect(onPick).toHaveBeenCalledWith("alice", 3);
  });

  it("choosing another shooter lists their stages", () => {
    const onPick = renderSheet();
    fireEvent.click(screen.getByRole("button", { name: "Bob" }));
    fireEvent.click(screen.getByRole("button", { name: /stage 02/i }));
    expect(onPick).toHaveBeenCalledWith("bob", 2);
  });
});
