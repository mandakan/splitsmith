/**
 * HostedAccountChip is local-only (#719) -- the mirror of AccountChip,
 * which is hosted-only. They must never render together: one shows the
 * session you are logged in as, the other a stored credential.
 *
 * Separate file, not a describe block: the features.ts mode cache is per
 * module registry (see GlobalBar.hosted.test.tsx for the same split).
 */
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HostedAccountChip } from "@/components/account/HostedAccountChip";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getSyncSettings: vi.fn().mockResolvedValue({
        base_url: "https://hosted.example",
        token_set: true,
        account: {
          id: "u1",
          email: "shooter@example.com",
          display_name: null,
          device_name: "gaspode",
          linked_at: "2026-08-08T10:00:00Z",
        },
      }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "hosted" }),
    },
  };
});

describe("HostedAccountChip (hosted mode)", () => {
  it("renders nothing", async () => {
    const { container } = render(<HostedAccountChip />);
    // Wait for the mode to resolve, then assert the chip stayed absent --
    // asserting immediately would pass even if the gate did not exist,
    // because the initial render is empty regardless. A `vi.waitFor`
    // wrapped around an already-true condition (nothing is in the DOM
    // yet on the very first synchronous check) resolves on its first
    // poll and never actually waits for anything, so it proves nothing
    // about the gate -- verified by mutating the component's `mode !==
    // "local"` guard away and watching this stay green under the
    // `vi.waitFor` version. A real elapsed wait, flushed through `act`,
    // is what actually forces the mode-resolution promise chain (and
    // the local-mode-window fetch it can still trigger before mode
    // flips to "hosted") to settle before the assertion runs.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.queryByText("shooter@example.com")).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });
});
