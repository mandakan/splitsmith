import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ShooterListEntry } from "@/lib/api";

import { IdentitySheet } from "./IdentitySheet";

vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      updateShooterIdentity: vi.fn().mockResolvedValue({ name: "p" }),
      uploadShooterLogo: vi.fn().mockResolvedValue({ name: "p" }),
      removeShooterLogo: vi.fn().mockResolvedValue({ name: "p" }),
      getShooterIdentityView: vi.fn(),
      useShooterBook: vi.fn(),
      putShooterBookEntry: vi.fn().mockResolvedValue({}),
      uploadShooterBookLogo: vi.fn().mockResolvedValue({}),
      removeShooterBookLogo: vi.fn().mockResolvedValue({}),
    },
  };
});
const { api } = await import("@/lib/api");

/** What GET /identity answers: the shooter's own record by default. */
function viewOf(shooter: ShooterListEntry, source: "match" | "book" | "none" = "match", shooterId: number | null = null) {
  vi.mocked(api.getShooterIdentityView).mockResolvedValue({
    source,
    identity: shooter.identity ?? { accent: null, logo: null, club: null },
    shooter_id: shooterId,
    book_entry: source !== "none",
    book_available: true,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  viewOf(ME, "none");
});

const ME = {
  slug: "me",
  name: "Mathias Axell",
  identity: { accent: null, logo: null, club: null },
} as unknown as ShooterListEntry;
const WITH_LOGO = {
  ...ME,
  identity: { accent: "#fbbf24", logo: "logo-0123456789ab.png", club: "Bromma PK" },
} as unknown as ShooterListEntry;

describe("IdentitySheet", () => {
  it("saves a picked accent and the club line through the identity route", async () => {
    const onChanged = vi.fn();
    const onClose = vi.fn();
    render(<IdentitySheet open onClose={onClose} shooter={ME} editDenied={false} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "Accent #4ade80" }));
    fireEvent.change(screen.getByLabelText("Club"), { target: { value: "  Bromma PK " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() =>
      expect(api.updateShooterIdentity).toHaveBeenCalledWith("me", {
        accent: "#4ade80",
        club: "Bromma PK",
        scope: "book",
      }),
    );
    expect(api.uploadShooterLogo).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled();
  });

  it("refuses a malformed accent before touching the API", () => {
    render(<IdentitySheet open onClose={vi.fn()} shooter={ME} editDenied={false} onChanged={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Accent"), { target: { value: "red" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(api.updateShooterIdentity).not.toHaveBeenCalledWith("me", expect.objectContaining({ accent: "red" }));
  });

  it("uploads a chosen logo and removes an existing one on request", async () => {
    const onChanged = vi.fn();
    viewOf(WITH_LOGO, "match");
    const { unmount } = render(
      <IdentitySheet open onClose={vi.fn()} shooter={WITH_LOGO} editDenied={false} onChanged={onChanged} />,
    );
    expect(await screen.findByText("logo-0123456789ab.png")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.getByText("No logo")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(api.removeShooterLogo).toHaveBeenCalledWith("me", "book"));
    unmount();
    viewOf(ME, "none");

    render(<IdentitySheet open onClose={vi.fn()} shooter={ME} editDenied={false} onChanged={onChanged} />);
    const file = new File([new Uint8Array([137, 80, 78, 71])], "club.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("Logo file"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(api.uploadShooterLogo).toHaveBeenCalledWith("me", file, "book"));
  });

  it("edits a look from the shooter book in the book, so this match keeps following it", async () => {
    const booked = { ...ME, selected_shooter_id: 42 } as unknown as ShooterListEntry;
    vi.mocked(api.getShooterIdentityView).mockResolvedValue({
      source: "book",
      identity: { accent: "#60a5fa", logo: null, club: "Bromma PK" },
      shooter_id: 42,
      book_entry: true,
      book_available: true,
    });
    render(<IdentitySheet open onClose={vi.fn()} shooter={booked} editDenied={false} onChanged={vi.fn()} />);
    expect(await screen.findByText(/From your shooter book/)).toBeInTheDocument();
    expect(screen.getByLabelText("Club")).toHaveValue("Bromma PK");
    fireEvent.click(screen.getByRole("button", { name: "Accent #4ade80" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() =>
      expect(api.putShooterBookEntry).toHaveBeenCalledWith(42, {
        accent: "#4ade80",
        club: "Bromma PK",
        label: "Mathias Axell",
      }),
    );
    expect(api.updateShooterIdentity).not.toHaveBeenCalled();
  });

  it("only this match keeps the edit here", async () => {
    const booked = { ...ME, selected_shooter_id: 42 } as unknown as ShooterListEntry;
    vi.mocked(api.getShooterIdentityView).mockResolvedValue({
      source: "book",
      identity: { accent: "#60a5fa", logo: null, club: null },
      shooter_id: 42,
      book_entry: true,
      book_available: true,
    });
    render(<IdentitySheet open onClose={vi.fn()} shooter={booked} editDenied={false} onChanged={vi.fn()} />);
    await screen.findByText(/From your shooter book/);
    fireEvent.click(screen.getByLabelText("Only this match"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() =>
      expect(api.updateShooterIdentity).toHaveBeenCalledWith("me", { accent: "#60a5fa", club: null, scope: "match" }),
    );
    expect(api.putShooterBookEntry).not.toHaveBeenCalled();
  });

  it("use shooter book drops this match's record", async () => {
    const own = { ...WITH_LOGO, selected_shooter_id: 42 } as unknown as ShooterListEntry;
    viewOf(own, "match", 42);
    vi.mocked(api.useShooterBook).mockResolvedValue({
      source: "book",
      identity: { accent: "#c084fc", logo: null, club: null },
      shooter_id: 42,
      book_entry: true,
      book_available: true,
    });
    const onChanged = vi.fn();
    render(<IdentitySheet open onClose={vi.fn()} shooter={own} editDenied={false} onChanged={onChanged} />);
    fireEvent.click(await screen.findByRole("button", { name: "Use shooter book" }));
    await vi.waitFor(() => expect(api.useShooterBook).toHaveBeenCalledWith("me"));
    expect(await screen.findByText(/From your shooter book/)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it("a shooter with no scoreboard id gets no book controls", async () => {
    render(<IdentitySheet open onClose={vi.fn()} shooter={ME} editDenied={false} onChanged={vi.fn()} />);
    expect(await screen.findByText(/Link this shooter's scoreboard entry/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Only this match")).toBeNull();
    expect(screen.queryByRole("button", { name: "Use shooter book" })).toBeNull();
  });

  it("offers Use shooter book only when the book has a look for this shooter", async () => {
    const own = { ...WITH_LOGO, selected_shooter_id: 42 } as unknown as ShooterListEntry;
    vi.mocked(api.getShooterIdentityView).mockResolvedValue({
      source: "match",
      identity: { accent: "#fbbf24", logo: null, club: null },
      shooter_id: 42,
      book_entry: false,
      book_available: true,
    });
    render(<IdentitySheet open onClose={vi.fn()} shooter={own} editDenied={false} onChanged={vi.fn()} />);
    await screen.findByText(/Set for this match/);
    expect(screen.queryByRole("button", { name: "Use shooter book" })).toBeNull();
  });

  it("promises no book where the server keeps none", async () => {
    const own = { ...WITH_LOGO, selected_shooter_id: 42 } as unknown as ShooterListEntry;
    vi.mocked(api.getShooterIdentityView).mockResolvedValue({
      source: "match",
      identity: { accent: "#fbbf24", logo: null, club: null },
      shooter_id: 42,
      book_entry: false,
      book_available: false,
    });
    render(<IdentitySheet open onClose={vi.fn()} shooter={own} editDenied={false} onChanged={vi.fn()} />);
    expect(await screen.findByText("Set for this match.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Only this match")).toBeNull();
  });
});
