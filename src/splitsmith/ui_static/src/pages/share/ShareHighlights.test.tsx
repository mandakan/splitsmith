/**
 * The highlight reel on a share link (spec 2026-10-18-highlight-reel-design,
 * part 2): Results links to it only when a reel exists, and the page plays
 * the newest one through the share surface.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReelLink } from "@/components/share/ReelLink";
import { api } from "@/lib/api";
import { ShareHighlights } from "@/pages/share/ShareHighlights";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, getLatestReel: vi.fn() } };
});

const LATEST = { filename: "bromma-highlights-short.mp4", short: true, finished_at: "2026-10-18T12:00:00Z" };

afterEach(() => {
  vi.clearAllMocks();
});

describe("ReelLink", () => {
  it("links to the reel when one exists", async () => {
    vi.mocked(api.getLatestReel).mockResolvedValue(LATEST);
    render(
      <MemoryRouter initialEntries={["/share/tok/results"]}>
        <Routes>
          <Route path="/share/:token/results" element={<ReelLink slug="me" />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("link", { name: "Watch the highlight reel" })).toHaveAttribute(
      "href",
      "/share/tok/highlights/me",
    );
  });

  it("renders nothing without a reel", async () => {
    vi.mocked(api.getLatestReel).mockRejectedValue(new Error("404"));
    const { container } = render(
      <MemoryRouter initialEntries={["/share/tok/results"]}>
        <Routes>
          <Route path="/share/:token/results" element={<ReelLink slug="me" />} />
        </Routes>
      </MemoryRouter>,
    );
    await Promise.resolve();
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ShareHighlights", () => {
  it("plays the newest reel through the share surface", async () => {
    vi.mocked(api.getLatestReel).mockResolvedValue(LATEST);
    // Request paths are scoped from the browser's location, not the router's.
    window.history.pushState({}, "", "/share/tok/highlights/me");
    render(
      <MemoryRouter initialEntries={["/share/tok/highlights/me"]}>
        <Routes>
          <Route path="/share/:token/highlights/:slug" element={<ShareHighlights />} />
        </Routes>
      </MemoryRouter>,
    );
    const video = await screen.findByTestId("highlight-reel");
    expect(video.getAttribute("src")).toContain("/api/share/tok/shooters/me/exports/file/bromma-highlights-short.mp4");
  });
});
