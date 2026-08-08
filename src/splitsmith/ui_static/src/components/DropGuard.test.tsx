/**
 * App-wide drop guard (add-videos UX rework).
 *
 * An unhandled drop must never navigate the SPA into the dropped file.
 * In local mode a file drop shows a toast pointing at the picker;
 * hosted and unresolved modes stay silent (hosted has real drop
 * surfaces; unresolved cannot know what to say yet).
 */
import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DropGuard } from "@/components/DropGuard";
import { useDeploymentMode } from "@/lib/features";

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return {
    ...actual,
    useDeploymentMode: vi.fn(() => ({ mode: "local" as const, resolved: true })),
  };
});

function dropOnWindow(): DragEvent {
  const ev = new Event("drop", { bubbles: true, cancelable: true }) as DragEvent;
  Object.defineProperty(ev, "dataTransfer", {
    value: { types: ["Files"], files: [] },
  });
  act(() => {
    window.dispatchEvent(ev);
  });
  return ev;
}

describe("DropGuard", () => {
  beforeEach(() => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: true });
  });

  it("prevents default on unhandled drops so the browser never navigates", () => {
    render(<DropGuard />);
    const ev = dropOnWindow();
    expect(ev.defaultPrevented).toBe(true);
  });

  it("prevents default on dragover (required for drop to be cancellable)", () => {
    render(<DropGuard />);
    const ev = new Event("dragover", { bubbles: true, cancelable: true });
    act(() => {
      window.dispatchEvent(ev);
    });
    expect(ev.defaultPrevented).toBe(true);
  });

  it("shows the local-mode toast on a file drop", async () => {
    render(<DropGuard />);
    dropOnWindow();
    expect(
      await screen.findByText(/drops can't be added in local mode/i),
    ).toBeInTheDocument();
  });

  it("shows no toast in hosted mode", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });
    render(<DropGuard />);
    const ev = dropOnWindow();
    expect(ev.defaultPrevented).toBe(true);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText(/drops can't be added/i)).not.toBeInTheDocument();
  });

  it("shows no toast before the mode resolves (still prevents default)", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: false });
    render(<DropGuard />);
    const ev = dropOnWindow();
    expect(ev.defaultPrevented).toBe(true);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText(/drops can't be added/i)).not.toBeInTheDocument();
  });

  it("does not toast when another handler already handled the drop", async () => {
    render(<DropGuard />);
    const ev = new Event("drop", { bubbles: true, cancelable: true }) as DragEvent;
    Object.defineProperty(ev, "dataTransfer", {
      value: { types: ["Files"], files: [] },
    });
    ev.preventDefault(); // simulate an inner handler having consumed it
    act(() => {
      window.dispatchEvent(ev);
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText(/drops can't be added/i)).not.toBeInTheDocument();
  });
});
