/**
 * Shooters (spec 2026-10-09): everyone you have filmed, you first; Edit
 * opens the sheet, which writes the shooter book only.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ShooterRosterRow } from "@/lib/api";
import { Shooters } from "@/pages/Shooters";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listShooters: vi.fn(),
      putShooterBookEntry: vi.fn(),
      uploadShooterBookLogo: vi.fn(),
      removeShooterBookLogo: vi.fn(),
    },
  };
});

vi.mock("@/lib/features", () => ({ useDeploymentMode: () => ({ mode: "local", resolved: true }) }));

function row(extra: Partial<ShooterRosterRow>): ShooterRosterRow {
  return {
    shooter_id: 7,
    name: "Anna Jonsson",
    club: null,
    accent: null,
    logo_url: null,
    match_count: 1,
    last_match_at: "2026-10-01T00:00:00Z",
    last_match_name: "Höstfinalen XI",
    you: false,
    source: "none",
    match_id: null,
    slug: null,
    ...extra,
  };
}

beforeEach(() => {
  vi.mocked(api.listShooters).mockResolvedValue({
    rows: [
      row({ shooter_id: 42, name: "Mathias Axell", you: true, club: "Bromma PK", source: "book", match_count: 3 }),
      row({}),
      row({ shooter_id: null, name: "Guest", match_id: "m1", slug: "guest" }),
    ],
  });
  vi.mocked(api.putShooterBookEntry).mockResolvedValue({
    shooter_id: 7,
    label: "Anna Jonsson",
    identity: { accent: "#4ade80", logo: null, club: "PK" },
    updated_at: "",
  });
});

function renderPage() {
  return render(
    <MemoryRouter>
      <Shooters />
    </MemoryRouter>,
  );
}

describe("Shooters", () => {
  it("lists you first, counts matches and edits only shooters with an SSI id", async () => {
    renderPage();
    expect(await screen.findByText("Mathias Axell")).toBeInTheDocument();
    expect(screen.getByText("You")).toBeInTheDocument();
    expect(screen.getByText("3 matches")).toBeInTheDocument();
    expect(screen.getByText("1 match · no look set")).toBeInTheDocument();
    expect(screen.getByText(/^In Höstfinalen XI\. Link them to the scoreboard/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Edit" })).toHaveLength(2);
  });

  it("saves a look to the shooter book", async () => {
    renderPage();
    const edits = await screen.findAllByRole("button", { name: "Edit" });
    fireEvent.click(edits[1]);
    fireEvent.change(await screen.findByLabelText("Club"), { target: { value: "PK" } });
    fireEvent.click(screen.getByRole("button", { name: "Accent #4ade80" }));
    const before = vi.mocked(api.listShooters).mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.putShooterBookEntry).toHaveBeenCalledWith(7, { accent: "#4ade80", club: "PK", label: "Anna Jonsson" }),
    );
    await waitFor(() => expect(vi.mocked(api.listShooters).mock.calls.length).toBe(before + 1));
  });
});
