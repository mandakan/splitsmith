import { readFileSync } from "node:fs";

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { YouTubeAccessSheet } from "@/components/export/YouTubeAccessSheet";
import { DOES, GOOGLE_SCOPE_WORDING, NEVER } from "@/lib/youtubeAccess";

const deployment = { mode: "local" as "local" | "hosted" };
vi.mock("@/lib/features", () => ({ useDeploymentMode: () => ({ mode: deployment.mode, resolved: true }) }));

/** The privacy page's text with tags dropped and the few entities it uses decoded. */
function privacyText(): string {
  return readFileSync("../../../site/privacy.html", "utf8")
    .replace(/<[^>]+>/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&rsquo;/g, "'")
    .replace(/\s+/g, " ");
}

describe("YouTubeAccessSheet", () => {
  it("before a login: quotes Google's wording, both lists, and ends in Continue", () => {
    const onContinue = vi.fn();
    const onClose = vi.fn();
    render(<YouTubeAccessSheet open onClose={onClose} onContinue={onContinue} />);
    const dialog = screen.getByRole("dialog", { name: "What connecting YouTube allows" });
    expect(dialog).toHaveTextContent(GOOGLE_SCOPE_WORDING);
    for (const line of [...DOES, ...NEVER]) expect(within(dialog).getByText(line)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onContinue).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Continue to Google" }));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("read-only: the same text with Done and no way to start a login", () => {
    render(<YouTubeAccessSheet open onClose={vi.fn()} />);
    expect(screen.getByRole("dialog")).toHaveTextContent(GOOGLE_SCOPE_WORDING);
    expect(screen.queryByRole("button", { name: "Continue to Google" })).toBeNull();
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
  });

  it("says where the key lives for the deployment it runs in", () => {
    deployment.mode = "local";
    const { unmount } = render(<YouTubeAccessSheet open onClose={vi.fn()} />);
    expect(screen.getByRole("dialog")).toHaveTextContent("on this computer");
    unmount();
    deployment.mode = "hosted";
    render(<YouTubeAccessSheet open onClose={vi.fn()} />);
    expect(screen.getByRole("dialog")).toHaveTextContent("encrypted with a separate key");
    expect(screen.getByRole("dialog")).not.toHaveTextContent("on this computer");
    deployment.mode = "local";
  });

  it("the privacy page carries the same two lists word for word", () => {
    const text = privacyText();
    expect(text).toContain(GOOGLE_SCOPE_WORDING);
    for (const line of [...DOES, ...NEVER]) expect(text).toContain(line);
  });
});
