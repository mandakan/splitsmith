/**
 * Sign-in page: the request-access form under the sign-in form.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { Login } from "@/pages/Login";

vi.mock("@/lib/auth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth")>();
  return {
    ...actual,
    useAuth: () => ({ status: "anon", user: null, refresh: vi.fn(), logout: vi.fn() }),
  };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, authBegin: vi.fn(), requestAccess: vi.fn() } };
});

const SERVER_MESSAGE = "If you have access, a sign-in link is on its way. Otherwise your request has been noted.";

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <Login />
    </MemoryRouter>,
  );
}

async function openRequestForm() {
  const user = userEvent.setup();
  renderLogin();
  await user.type(screen.getByLabelText("Email"), "  anna@club.se ");
  await user.click(screen.getByRole("button", { name: "Request access" }));
  return user;
}

describe("Login request access", () => {
  beforeEach(() => {
    vi.mocked(api.requestAccess).mockReset();
  });

  it("prefills the email from the sign-in field and sends the trimmed email and note", async () => {
    vi.mocked(api.requestAccess).mockResolvedValue({ ok: true, message: SERVER_MESSAGE });
    const user = await openRequestForm();
    const email = screen.getByLabelText("Your email");
    // An email input's value is sanitized (whitespace stripped) by the DOM
    // itself, so the surrounding spaces are gone before the form trims.
    expect(email).toHaveValue("anna@club.se");
    const note = screen.getByLabelText("Who are you? (name, club)");
    expect(note).toHaveAttribute("maxLength", "500");
    await user.type(note, "  Anna, Bromma PK  ");
    await user.click(screen.getByRole("button", { name: "Send request" }));
    await waitFor(() => expect(api.requestAccess).toHaveBeenCalledWith("anna@club.se", "Anna, Bromma PK"));
  });

  it("sends a null note when it is left empty", async () => {
    vi.mocked(api.requestAccess).mockResolvedValue({ ok: true, message: SERVER_MESSAGE });
    const user = await openRequestForm();
    await user.click(screen.getByRole("button", { name: "Send request" }));
    await waitFor(() => expect(api.requestAccess).toHaveBeenCalledWith("anna@club.se", null));
  });

  it("replaces both forms with the server's message", async () => {
    vi.mocked(api.requestAccess).mockResolvedValue({ ok: true, message: "Server copy, verbatim." });
    const user = await openRequestForm();
    await user.click(screen.getByRole("button", { name: "Send request" }));
    expect(await screen.findByText("Server copy, verbatim.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send sign-in link/i })).toBeNull();
    expect(screen.queryByRole("button", { name: "Send request" })).toBeNull();
    expect(screen.queryByLabelText("Email")).toBeNull();
  });

  it("a rejected call shows the network-failure line", async () => {
    vi.mocked(api.requestAccess).mockRejectedValue(new Error("offline"));
    const user = await openRequestForm();
    await user.click(screen.getByRole("button", { name: "Send request" }));
    expect(
      await screen.findByText("Could not send the request. Check your connection and retry."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send request" })).toBeEnabled();
  });
});
