/**
 * The Account page's YouTube section (issue #1000, phase 2): each of its
 * states renders from the settings, Connect runs the shared login, and
 * Disconnect clears the channel. The login itself is pinned in
 * useYouTubeLogin.test.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { YouTubeSection } from "@/components/account/YouTubeSection";
import type { YouTubeSettings } from "@/lib/api";

const getYouTubeSettings = vi.fn();
const startYouTubeConnect = vi.fn();
const youtubeConnectStatus = vi.fn();
const disconnectYouTube = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getYouTubeSettings: (...a: unknown[]) => getYouTubeSettings(...a),
      startYouTubeConnect: (...a: unknown[]) => startYouTubeConnect(...a),
      youtubeConnectStatus: (...a: unknown[]) => youtubeConnectStatus(...a),
      disconnectYouTube: (...a: unknown[]) => disconnectYouTube(...a),
    },
  };
});

function settings(over: Partial<YouTubeSettings> = {}): YouTubeSettings {
  return { configured: true, connected: false, channel_title: null, connected_at: null, ...over };
}

/** Flush the settings fetch and the state update behind it. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("YouTubeSection", () => {
  beforeEach(() => {
    getYouTubeSettings.mockReset();
    startYouTubeConnect.mockReset();
    youtubeConnectStatus.mockReset();
    disconnectYouTube.mockReset();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("renders nothing until the settings answer, then the section", async () => {
    getYouTubeSettings.mockResolvedValue(settings());
    const { container } = render(<YouTubeSection />);
    expect(container).toBeEmptyDOMElement();
    expect(await screen.findByRole("button", { name: "Connect YouTube" })).toBeInTheDocument();
    expect(screen.getByText("YouTube")).toBeInTheDocument();
  });

  it("says so when the server is not configured", async () => {
    getYouTubeSettings.mockResolvedValue(settings({ configured: false }));
    render(<YouTubeSection />);
    expect(await screen.findByText("YouTube upload is not configured on this server.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect YouTube" })).toBeNull();
  });

  it("connects through the shared login and re-reads the settings", async () => {
    getYouTubeSettings
      .mockResolvedValueOnce(settings())
      .mockResolvedValueOnce(settings({ connected: true, channel_title: "Mine" }));
    startYouTubeConnect.mockResolvedValue({ auth_url: "https://accounts.google.com/x", expires_at: "" });
    youtubeConnectStatus.mockResolvedValue({ state: "connected", channel_title: "Mine", error: null });
    const open = vi.fn();
    vi.stubGlobal("open", open);
    render(<YouTubeSection />);
    const button = await screen.findByRole("button", { name: "Connect YouTube" });
    vi.useFakeTimers();
    expect(button.className).not.toContain("btn-primary");
    await act(async () => {
      fireEvent.click(button);
    });
    expect(open).toHaveBeenCalledWith("https://accounts.google.com/x", "_blank", "noopener");
    expect(screen.getByText("Waiting for Google...")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await settle();
    expect(screen.getByText("Connected as Mine")).toBeInTheDocument();
    expect(getYouTubeSettings).toHaveBeenCalledTimes(2);
  });

  it("shows the login's reason and offers Connect again", async () => {
    getYouTubeSettings.mockResolvedValue(settings());
    startYouTubeConnect.mockResolvedValue({ auth_url: "https://accounts.google.com/x", expires_at: "" });
    youtubeConnectStatus.mockResolvedValue({ state: "failed", channel_title: null, error: "access_denied" });
    vi.stubGlobal("open", vi.fn());
    render(<YouTubeSection />);
    const button = await screen.findByRole("button", { name: "Connect YouTube" });
    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(button);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("access_denied");
    expect(screen.getByRole("button", { name: "Connect YouTube" })).toBeInTheDocument();
  });

  it("connected: names the channel and disconnects from the menu", async () => {
    getYouTubeSettings
      .mockResolvedValueOnce(settings({ connected: true, channel_title: "Mine" }))
      .mockResolvedValueOnce(settings());
    disconnectYouTube.mockResolvedValue({ connected: false });
    render(<YouTubeSection />);
    expect(await screen.findByText("Connected as Mine")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "YouTube actions" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: "Disconnect" }));
    });
    expect(disconnectYouTube).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "Connect YouTube" })).toBeInTheDocument();
  });
});
