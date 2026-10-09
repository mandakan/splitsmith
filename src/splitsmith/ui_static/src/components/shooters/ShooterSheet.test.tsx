/**
 * ShooterSheet (spec 2026-10-09): edits the shooter book only, keeps what
 * the user typed while its caller re-renders, and never drops a logo the
 * videos draw today because it came from a match record.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ShooterSheet, type SheetShooter } from "@/components/shooters/ShooterSheet";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      putShooterBookEntry: vi.fn(),
      uploadShooterBookLogo: vi.fn(),
      removeShooterBookLogo: vi.fn(),
    },
  };
});

const ENTRY = { shooter_id: 7, label: "Anna", identity: { accent: null, logo: null, club: null }, updated_at: "" };

beforeEach(() => {
  vi.mocked(api.putShooterBookEntry).mockResolvedValue(ENTRY);
  vi.mocked(api.uploadShooterBookLogo).mockResolvedValue(ENTRY);
  vi.mocked(api.removeShooterBookLogo).mockResolvedValue(ENTRY);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

const fromMatch = (): SheetShooter => ({
  shooterId: 7,
  name: "Anna",
  accent: "#00ff00",
  club: "Bromma",
  logoUrl: "/api/matches/m1/shooters/anna/identity/logo?v=logo-0123456789ab.png",
});

describe("ShooterSheet", () => {
  it("keeps what was typed when the caller re-renders with an equal shooter", () => {
    const { rerender } = render(<ShooterSheet open onClose={vi.fn()} shooter={fromMatch()} onChanged={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Club"), { target: { value: "Typed club" } });
    rerender(<ShooterSheet open onClose={vi.fn()} shooter={fromMatch()} onChanged={vi.fn()} />);
    expect(screen.getByLabelText("Club")).toHaveValue("Typed club");
  });

  it("carries a logo the videos draw from a match record into the book", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(["png"], { type: "image/png" })));
    vi.stubGlobal("fetch", fetchMock);
    render(<ShooterSheet open onClose={vi.fn()} shooter={fromMatch()} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.uploadShooterBookLogo).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledWith(fromMatch().logoUrl);
    expect(vi.mocked(api.uploadShooterBookLogo).mock.calls[0][0]).toBe(7);
  });

  it("does not copy a logo that is already the book's", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const own = { ...fromMatch(), logoUrl: "/api/me/shooter-book/7/logo?v=logo-0123456789ab.png" };
    render(<ShooterSheet open onClose={vi.fn()} shooter={own} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putShooterBookEntry).toHaveBeenCalled());
    expect(fetchMock).not.toHaveBeenCalled();
    expect(api.uploadShooterBookLogo).not.toHaveBeenCalled();
  });

  it("removes the logo when asked, without copying it first", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<ShooterSheet open onClose={vi.fn()} shooter={fromMatch()} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.removeShooterBookLogo).toHaveBeenCalledWith(7));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
