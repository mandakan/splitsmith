import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

let mockMode: "local" | "hosted" = "local";
vi.mock("@/lib/features", () => ({
  useDeploymentMode: () => ({ mode: mockMode, resolved: true }),
}));

vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getScoreboardIdentity: vi.fn(),
      putScoreboardIdentity: vi.fn(),
      clearScoreboardIdentity: vi.fn().mockResolvedValue({ ok: true }),
      searchShooterIndex: vi.fn(),
      getAccountProfile: vi.fn(),
      putAccountProfile: vi.fn(),
      uploadAccountBrandLogo: vi.fn(),
      removeAccountBrandLogo: vi.fn(),
      getShooterBook: vi.fn(),
      putShooterBookEntry: vi.fn().mockResolvedValue({}),
      deleteShooterBookEntry: vi.fn().mockResolvedValue({ ok: true }),
      uploadShooterBookLogo: vi.fn(),
      removeShooterBookLogo: vi.fn(),
    },
  };
});
const { api } = await import("@/lib/api");
const { You } = await import("./You");

const ME = { shooter_id: 42, display_name: "Mathias Axell", division: null, club: null, base_url: null };

function renderYou() {
  return render(
    <MemoryRouter>
      <You />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockMode = "local";
  vi.mocked(api.getScoreboardIdentity).mockResolvedValue(null);
  vi.mocked(api.getAccountProfile).mockResolvedValue({ brand: { logo: null, line: "" } });
  vi.mocked(api.getShooterBook).mockResolvedValue({ entries: [] });
});

describe("You", () => {
  it("finds you in the shooter index and pins your id", async () => {
    vi.mocked(api.searchShooterIndex).mockResolvedValue([
      { shooterId: 42, name: "Mathias Axell", club: "Bromma PK", division: "Production Optics", lastSeen: "" },
    ]);
    vi.mocked(api.putScoreboardIdentity).mockResolvedValue(ME);
    renderYou();
    fireEvent.change(screen.getByLabelText("You"), { target: { value: "axell" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.click(await screen.findByRole("button", { name: "This is me" }));
    await waitFor(() =>
      expect(api.putScoreboardIdentity).toHaveBeenCalledWith({
        shooter_id: 42,
        display_name: "Mathias Axell",
        club: "Bromma PK",
        division: "Production Optics",
        base_url: null,
      }),
    );
    // Pinned: your look appears, keyed by your id.
    expect(await screen.findByText("#42")).toBeInTheDocument();
    expect(screen.getByLabelText("Accent")).toBeInTheDocument();
  });

  it("saves your look to your own book entry", async () => {
    vi.mocked(api.getScoreboardIdentity).mockResolvedValue(ME);
    renderYou();
    fireEvent.click(await screen.findByRole("button", { name: "Accent #4ade80" }));
    fireEvent.change(screen.getByLabelText("Club"), { target: { value: " Bromma PK " } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);
    await waitFor(() =>
      expect(api.putShooterBookEntry).toHaveBeenCalledWith(42, {
        accent: "#4ade80",
        club: "Bromma PK",
        label: "Mathias Axell",
      }),
    );
  });

  it("hides your look until you are pinned, and saves your brand line", async () => {
    vi.mocked(api.getAccountProfile).mockResolvedValue({ brand: { logo: null, line: "Old line" } });
    vi.mocked(api.putAccountProfile).mockResolvedValue({ brand: { logo: null, line: "Team Axell" } });
    renderYou();
    expect(await screen.findByDisplayValue("Old line")).toBeInTheDocument();
    expect(screen.queryByLabelText("Accent")).toBeNull();
    fireEvent.change(screen.getByLabelText("Line"), { target: { value: "Team Axell" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putAccountProfile).toHaveBeenCalledWith({ brand_line: "Team Axell" }));
  });

  it("sends every other shooter's look to the Shooters page and anchors the brand", async () => {
    vi.mocked(api.getScoreboardIdentity).mockResolvedValue(ME);
    const { container } = renderYou();
    expect(await screen.findByRole("link", { name: "Shooters" })).toHaveAttribute("href", "/shooters");
    expect(container.querySelector("section#brand")).not.toBeNull();
  });

  it("goes back to Account hosted and to Matches locally", async () => {
    mockMode = "hosted";
    const { unmount } = renderYou();
    expect(await screen.findByRole("link", { name: /Account/ })).toHaveAttribute("href", "/account");
    unmount();
    mockMode = "local";
    renderYou();
    expect(await screen.findByRole("link", { name: /Matches/ })).toHaveAttribute("href", "/pick");
  });
});
