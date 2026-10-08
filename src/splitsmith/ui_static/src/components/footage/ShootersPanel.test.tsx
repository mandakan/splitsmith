import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { ScoreboardIdentity, ShooterListEntry } from "@/lib/api";

import { ShootersPanel } from "./ShootersPanel";

const ME: ScoreboardIdentity = { shooter_id: 42, display_name: "Mathias", division: null, club: null, base_url: null };

function shooter(slug: string, name: string, sid: number | null): ShooterListEntry {
  return {
    slug,
    name,
    selected_shooter_id: sid,
    video_count: 1,
    stages_missing_trim: 0,
    identity: null,
  } as unknown as ShooterListEntry;
}

function renderPanel(onThisIsMe = vi.fn(), me: ScoreboardIdentity | null = ME) {
  render(
    <MemoryRouter>
      <ShootersPanel
        shooters={[shooter("me", "Mathias Axell", 42), shooter("anna", "Anna", 7), shooter("bo", "Bo", null)]}
        activeSlug="me"
        editDenied={false}
        hrefs={{ footage: (s) => `/f/${s}`, audit: (s) => `/a/${s}` }}
        onAdd={vi.fn()}
        onRemove={vi.fn()}
        onRebuildTrims={vi.fn()}
        onIdentity={vi.fn()}
        me={me}
        onThisIsMe={onThisIsMe}
      />
    </MemoryRouter>,
  );
  return onThisIsMe;
}

describe("ShootersPanel and You", () => {
  it("marks the shooter with your scoreboard id, and only that one", () => {
    renderPanel();
    expect(screen.getAllByText("You")).toHaveLength(1);
  });

  it("offers This is me only for another shooter with a scoreboard id", () => {
    const onThisIsMe = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Mathias Axell actions" }));
    expect(screen.queryByRole("menuitem", { name: "This is me" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Bo actions" }));
    expect(screen.queryByRole("menuitem", { name: "This is me" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Anna actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "This is me" }));
    expect(onThisIsMe).toHaveBeenCalledWith(expect.objectContaining({ slug: "anna" }));
  });

  it("marks nobody before you are pinned", () => {
    renderPanel(vi.fn(), null);
    expect(screen.queryByText("You")).toBeNull();
  });
});
