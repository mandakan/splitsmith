/**
 * The /account page (#867) - the surface that makes users.display_name
 * writable, which is what makes #866's account attribution reachable.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";

const mockRefresh = vi.fn();
let mockUser: { id: string; email: string; display_name: string | null; is_admin: boolean } | null = {
  id: "u1",
  email: "m@thias.se",
  display_name: null,
  is_admin: false,
};
let mockMode: "local" | "hosted" = "hosted";
let mockResolved = true;

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ status: "authed", user: mockUser, refresh: mockRefresh, logout: vi.fn() }),
}));

vi.mock("@/lib/features", () => ({
  useDeploymentMode: () => ({ mode: mockMode, resolved: mockResolved }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      updateMe: vi.fn(),
      listDesktopTokens: vi.fn().mockResolvedValue({ tokens: [] }),
      createDesktopToken: vi.fn(),
      revokeDesktopToken: vi.fn(),
    },
  };
});

import { api } from "@/lib/api";
import { Account } from "@/pages/Account";

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/account"]}>
      <Account />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockMode = "hosted";
  mockResolved = true;
  mockUser = { id: "u1", email: "m@thias.se", display_name: null, is_admin: false };
});

it("shows the account email read-only", () => {
  renderPage();
  expect(screen.getByText("m@thias.se")).toBeInTheDocument();
});

it("prefills the field with the current display name", () => {
  mockUser = { ...mockUser!, display_name: "Anders Berg" };
  renderPage();
  expect(screen.getByLabelText(/display name/i)).toHaveValue("Anders Berg");
});

it("saves a display name and refreshes the session", async () => {
  vi.mocked(api.updateMe).mockResolvedValue({
    id: "u1",
    email: "m@thias.se",
    display_name: "Anders Berg",
    is_admin: false,
  });
  renderPage();

  fireEvent.change(screen.getByLabelText(/display name/i), {
    target: { value: "Anders Berg" },
  });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));

  await waitFor(() => expect(api.updateMe).toHaveBeenCalledWith("Anders Berg"));
  await waitFor(() => expect(mockRefresh).toHaveBeenCalled());
});

it("sends null when the field is cleared", async () => {
  mockUser = { ...mockUser!, display_name: "Anders Berg" };
  vi.mocked(api.updateMe).mockResolvedValue({
    id: "u1",
    email: "m@thias.se",
    display_name: null,
    is_admin: false,
  });
  renderPage();

  fireEvent.change(screen.getByLabelText(/display name/i), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));

  await waitFor(() => expect(api.updateMe).toHaveBeenCalledWith(null));
});

it("does not refresh the session when the save is rejected", async () => {
  vi.mocked(api.updateMe).mockRejectedValue(new Error("too long"));
  renderPage();

  fireEvent.change(screen.getByLabelText(/display name/i), {
    target: { value: "x".repeat(61) },
  });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));

  await screen.findByRole("alert");
  expect(mockRefresh).not.toHaveBeenCalled();
});

it("surfaces a server rejection inline", async () => {
  vi.mocked(api.updateMe).mockRejectedValue(new Error("too long"));
  renderPage();

  fireEvent.change(screen.getByLabelText(/display name/i), {
    target: { value: "x".repeat(61) },
  });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));

  expect(await screen.findByRole("alert")).toBeInTheDocument();
});

it("explains what a display name is used for", () => {
  renderPage();
  expect(screen.getByText(/comment/i)).toBeInTheDocument();
});

it("renders the desktop-token section", async () => {
  renderPage();
  expect(await screen.findByText(/desktop sync tokens/i)).toBeInTheDocument();
});

it("redirects to the picker in local mode", () => {
  mockMode = "local";
  const { container } = renderPage();
  expect(container.querySelector("input")).toBeNull();
});

it("does not redirect while the deployment mode is still unresolved", () => {
  // mode defaults to "local" before /api/server/features settles; if the
  // redirect fired on that default, a hosted user on a slow first load
  // would get bounced out of their own account page.
  mockMode = "local";
  mockResolved = false;
  renderPage();
  expect(screen.getByLabelText(/display name/i)).toBeInTheDocument();
});
