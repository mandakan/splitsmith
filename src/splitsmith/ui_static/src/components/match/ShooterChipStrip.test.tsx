/**
 * The shooter chips' menu (spec 2026-10-09): a chip still switches shooter;
 * the small button beside it opens "Edit look", or says why a shooter not
 * linked to the scoreboard has no look of their own.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ShooterChipStrip } from "@/components/match/ShooterChipStrip";
import type { ShooterListEntry } from "@/lib/api";

function shooter(slug: string, name: string, id: number | null): ShooterListEntry {
  return {
    slug,
    name,
    selected_shooter_id: id,
    stages_audited: 1,
    stages_total: 4,
    identity: null,
  } as unknown as ShooterListEntry;
}

const SHOOTERS = [shooter("me", "Mathias Axell", 42), shooter("guest", "Guest", null)];

function renderStrip(onEditLook?: (s: ShooterListEntry) => void) {
  return render(
    <MemoryRouter initialEntries={["/match/m1/audit/me"]}>
      <ShooterChipStrip shooters={SHOOTERS} activeSlug="me" urlBase="audit" label={null} onEditLook={onEditLook} />
    </MemoryRouter>,
  );
}

describe("ShooterChipStrip menu", () => {
  it("opens Edit look for a shooter with an SSI id", () => {
    const onEditLook = vi.fn();
    renderStrip(onEditLook);
    fireEvent.click(screen.getByRole("button", { name: "More for Mathias Axell" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit look" }));
    expect(onEditLook).toHaveBeenCalledWith(SHOOTERS[0]);
  });

  it("explains why a shooter without one has no look to edit", () => {
    renderStrip(vi.fn());
    fireEvent.click(screen.getByRole("button", { name: "More for Guest" }));
    expect(screen.queryByRole("menuitem", { name: "Edit look" })).toBeNull();
    expect(screen.getByText(/Link Guest to the scoreboard/)).toBeInTheDocument();
  });

  it("has no menu where the page offers none", () => {
    renderStrip();
    expect(screen.queryByRole("button", { name: /More for/ })).toBeNull();
  });
});
