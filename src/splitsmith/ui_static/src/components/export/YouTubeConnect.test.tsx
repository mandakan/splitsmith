import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { YouTubeConnect } from "@/components/export/YouTubeConnect";
import type { YouTubeSettings } from "@/lib/api";

const startYouTubeConnect = vi.fn();
const youtubeConnectStatus = vi.fn();
const disconnectYouTube = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      startYouTubeConnect: (...a: unknown[]) => startYouTubeConnect(...a),
      youtubeConnectStatus: (...a: unknown[]) => youtubeConnectStatus(...a),
      disconnectYouTube: (...a: unknown[]) => disconnectYouTube(...a),
    },
  };
});

function settings(over: Partial<YouTubeSettings> = {}): YouTubeSettings {
  return { configured: true, connected: false, channel_title: null, connected_at: null, ...over };
}

function renderRow(s: YouTubeSettings | null, over: Partial<Parameters<typeof YouTubeConnect>[0]> = {}) {
  const onSettingsChange = vi.fn();
  const onUploadAfterRenderChange = vi.fn();
  const utils = render(
    <YouTubeConnect
      settings={s}
      onSettingsChange={onSettingsChange}
      uploadAfterRender="off"
      onUploadAfterRenderChange={onUploadAfterRenderChange}
      showUploadControl={false}
      {...over}
    />,
  );
  return { ...utils, onSettingsChange, onUploadAfterRenderChange };
}

describe("YouTubeConnect", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    startYouTubeConnect.mockReset();
    youtubeConnectStatus.mockReset();
    disconnectYouTube.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("says so when the install has no client", () => {
    renderRow(settings({ configured: false }));
    expect(screen.getByText("YouTube upload is not configured on this install.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect YouTube" })).not.toBeInTheDocument();
  });

  it("connects: opens the consent url, polls, then reports the channel", async () => {
    startYouTubeConnect.mockResolvedValue({ auth_url: "https://accounts.google.com/x", expires_at: "" });
    youtubeConnectStatus
      .mockResolvedValueOnce({ state: "pending", channel_title: null, error: null })
      .mockResolvedValueOnce({ state: "connected", channel_title: "Mine", error: null });
    const open = vi.fn();
    vi.stubGlobal("open", open);
    const { onSettingsChange } = renderRow(settings());
    const button = screen.getByRole("button", { name: "Connect YouTube" });
    // The page's one primary stays on Export; Connect is the default variant.
    expect(button.className).not.toContain("btn-primary");
    await act(async () => {
      fireEvent.click(button);
    });
    expect(open).toHaveBeenCalledWith("https://accounts.google.com/x", "_blank", "noopener");
    expect(screen.getByText("Waiting for Google...")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(onSettingsChange).toHaveBeenCalledTimes(1);
    expect(youtubeConnectStatus).toHaveBeenCalledTimes(2);
    vi.unstubAllGlobals();
  });

  it("shows the reason when the login fails and offers Connect again", async () => {
    startYouTubeConnect.mockResolvedValue({ auth_url: "https://accounts.google.com/x", expires_at: "" });
    youtubeConnectStatus.mockResolvedValue({ state: "failed", channel_title: null, error: "Google reported access_denied" });
    vi.stubGlobal("open", vi.fn());
    renderRow(settings());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Connect YouTube" }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByText("Google reported access_denied")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect YouTube" })).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("connected: names the channel, disconnects from the menu, shows the upload control", async () => {
    disconnectYouTube.mockResolvedValue({ connected: false });
    const { onSettingsChange, onUploadAfterRenderChange } = renderRow(
      settings({ connected: true, channel_title: "Mine", connected_at: "2026-09-14T00:00:00Z" }),
      { showUploadControl: true },
    );
    expect(screen.getByText("Connected as Mine")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Private" }));
    expect(onUploadAfterRenderChange).toHaveBeenCalledWith("private");
    fireEvent.click(screen.getByRole("button", { name: "YouTube actions" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: "Disconnect" }));
    });
    expect(disconnectYouTube).toHaveBeenCalledTimes(1);
    expect(onSettingsChange).toHaveBeenCalledTimes(1);
  });

  it("hides the upload control when the render is not a YouTube mp4", () => {
    renderRow(settings({ connected: true, channel_title: "Mine" }), { showUploadControl: false });
    expect(screen.queryByRole("group", { name: "Upload after render" })).not.toBeInTheDocument();
  });
});
