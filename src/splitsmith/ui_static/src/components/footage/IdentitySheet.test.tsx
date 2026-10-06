import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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
    },
  };
});
const { api } = await import("@/lib/api");

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
      expect(api.updateShooterIdentity).toHaveBeenCalledWith("me", { accent: "#4ade80", club: "Bromma PK" }),
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
    const { unmount } = render(
      <IdentitySheet open onClose={vi.fn()} shooter={WITH_LOGO} editDenied={false} onChanged={onChanged} />,
    );
    expect(screen.getByText("logo-0123456789ab.png")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.getByText("No logo")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(api.removeShooterLogo).toHaveBeenCalledWith("me"));
    unmount();

    render(<IdentitySheet open onClose={vi.fn()} shooter={ME} editDenied={false} onChanged={onChanged} />);
    const file = new File([new Uint8Array([137, 80, 78, 71])], "club.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("Logo file"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(api.uploadShooterLogo).toHaveBeenCalledWith("me", file));
  });
});
