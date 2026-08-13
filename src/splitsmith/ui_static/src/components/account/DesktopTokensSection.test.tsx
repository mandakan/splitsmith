/**
 * DesktopTokensSection - hosted token management for the desktop-to-hosted
 * sync MVP (#631 Task 10; moved from a dialog to an account-page section
 * in #867 Task 11).
 *
 * Floor per the task brief: renders the token list, create reveals the
 * raw token exactly once, and revoke calls through to the API. Mocks
 * @/lib/api the same way RegisterWorkerDialog-adjacent tests would (no
 * existing test to mirror directly, so this follows MatchShell.test.tsx's
 * `vi.mock("@/lib/api", ...)` pattern).
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type DesktopTokenInfo } from "@/lib/api";

import { DesktopTokensSection } from "@/components/account/DesktopTokensSection";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listDesktopTokens: vi.fn(),
      createDesktopToken: vi.fn(),
      revokeDesktopToken: vi.fn(),
    },
  };
});

function makeToken(overrides: Partial<DesktopTokenInfo> = {}): DesktopTokenInfo {
  return {
    id: "tok-1",
    name: "workshop-mac",
    created_at: "2026-08-01T00:00:00Z",
    last_used_at: "2026-08-05T00:00:00Z",
    revoked_at: null,
    ...overrides,
  };
}

describe("DesktopTokensSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the existing token list", async () => {
    vi.mocked(api.listDesktopTokens).mockResolvedValue({
      tokens: [makeToken()],
    });

    render(<DesktopTokensSection />);

    expect(await screen.findByText("workshop-mac")).toBeInTheDocument();
  });

  it("reveals the raw token once on create, with a not-shown-again warning", async () => {
    vi.mocked(api.listDesktopTokens).mockResolvedValue({ tokens: [] });
    vi.mocked(api.createDesktopToken).mockResolvedValue({
      token: "dtok_raw_value_shown_once",
      record: makeToken({ id: "tok-2", name: "garage-pc" }),
    });

    render(<DesktopTokensSection />);

    const nameInput = await screen.findByLabelText(/name/i);
    fireEvent.change(nameInput, { target: { value: "garage-pc" } });
    fireEvent.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() =>
      expect(api.createDesktopToken).toHaveBeenCalledWith("garage-pc"),
    );

    const tokenField = await screen.findByDisplayValue(
      "dtok_raw_value_shown_once",
    );
    expect(tokenField).toBeInTheDocument();
    expect(screen.getByText(/will not see this again/i)).toBeInTheDocument();

    // The reveal is announced to assistive tech, not just styled.
    expect(tokenField.closest("[aria-live]")).not.toBeNull();
  });

  it("revokes a token via the API and reflects the revoked state", async () => {
    // First load: live token. After revoke, the section refetches and the
    // same row comes back with revoked_at set (ShareDialog convention --
    // revoked entries stay visible, marked, rather than disappearing).
    vi.mocked(api.listDesktopTokens)
      .mockResolvedValueOnce({ tokens: [makeToken()] })
      .mockResolvedValueOnce({
        tokens: [makeToken({ revoked_at: "2026-08-07T00:00:00Z" })],
      });
    vi.mocked(api.revokeDesktopToken).mockResolvedValue({ revoked: true });

    render(<DesktopTokensSection />);

    await screen.findByText("workshop-mac");
    fireEvent.click(screen.getByRole("button", { name: /^revoke workshop-mac$/i }));
    // Two-step confirm, mirroring ShareDialog's armed-revoke pattern.
    fireEvent.click(screen.getByRole("button", { name: /^confirm: revoke/i }));

    await waitFor(() =>
      expect(api.revokeDesktopToken).toHaveBeenCalledWith("tok-1"),
    );
    expect(await screen.findByText(/revoked/i)).toBeInTheDocument();
  });

  it("keeps the one-time reveal in a live region", async () => {
    vi.mocked(api.listDesktopTokens).mockResolvedValue({ tokens: [] });
    vi.mocked(api.createDesktopToken).mockResolvedValue({
      record: {
        id: "t1",
        name: "workshop-mac",
        created_at: "2026-08-13T10:00:00Z",
        last_used_at: null,
        revoked_at: null,
      },
      token: "raw-token-value",
    });
    render(<DesktopTokensSection />);

    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "workshop-mac" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create token/i }));

    const field = await screen.findByLabelText("New desktop token");
    expect(field).toHaveValue("raw-token-value");
    expect(field.closest("[aria-live='polite']")).not.toBeNull();
    expect(screen.getByText(/you will not see this again/i)).toBeInTheDocument();
  });
});
