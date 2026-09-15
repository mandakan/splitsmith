import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { YouTubeConnect } from "@/components/export/YouTubeConnect";
import type { YouTubeSettings } from "@/lib/api";
import { DEFAULT_UPLOAD_OPTIONS } from "@/lib/youtubeRows";

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
  const onOptionsChange = vi.fn();
  const utils = render(
    <YouTubeConnect
      settings={s}
      onSettingsChange={onSettingsChange}
      options={DEFAULT_UPLOAD_OPTIONS}
      onOptionsChange={onOptionsChange}
      matchName="Bromma 2026"
      showUploadControl={false}
      {...over}
    />,
  );
  return { ...utils, onSettingsChange, onOptionsChange };
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
    const { onSettingsChange, onOptionsChange } = renderRow(
      settings({ connected: true, channel_title: "Mine", connected_at: "2026-09-14T00:00:00Z" }),
      { showUploadControl: true },
    );
    expect(screen.getByText("Connected as Mine")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Unlisted" }));
    expect(onOptionsChange).toHaveBeenCalledWith({ ...DEFAULT_UPLOAD_OPTIONS, enabled: true, privacy: "unlisted" });
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

describe("YouTubeConnect upload options", () => {
  const connected = () => settings({ connected: true, channel_title: "Mine" });

  it("shows playlist, schedule and notify only once the upload is on, each in its case", () => {
    const { rerender, onOptionsChange } = renderRow(connected(), { showUploadControl: true });
    expect(screen.queryByLabelText("Playlist")).toBeNull();

    const on = { ...DEFAULT_UPLOAD_OPTIONS, enabled: true, privacy: "unlisted" as const };
    rerender(
      <YouTubeConnect
        settings={connected()}
        onSettingsChange={vi.fn()}
        options={on}
        onOptionsChange={onOptionsChange}
        matchName="Bromma 2026"
        showUploadControl
      />,
    );
    // Playlist: a checkbox that prefills the match name.
    expect(screen.queryByLabelText("Playlist name")).toBeNull();
    fireEvent.click(screen.getByLabelText("Add to playlist"));
    expect(onOptionsChange).toHaveBeenLastCalledWith({ ...on, playlist: "Bromma 2026" });
    // Schedule only for Private, notify only for Public.
    expect(screen.queryByLabelText("Publish at")).toBeNull();
    expect(screen.queryByLabelText("Notify subscribers")).toBeNull();

    rerender(
      <YouTubeConnect
        settings={connected()}
        onSettingsChange={vi.fn()}
        options={{ ...on, privacy: "private", playlist: "Bromma 2026" }}
        onOptionsChange={onOptionsChange}
        matchName="Bromma 2026"
        showUploadControl
      />,
    );
    expect(screen.getByLabelText("Playlist name")).toHaveValue("Bromma 2026");
    fireEvent.change(screen.getByLabelText("Publish at"), { target: { value: "2026-09-20T18:00" } });
    expect(onOptionsChange).toHaveBeenLastCalledWith({
      ...on,
      privacy: "private",
      playlist: "Bromma 2026",
      publishAt: "2026-09-20T18:00",
    });

    rerender(
      <YouTubeConnect
        settings={connected()}
        onSettingsChange={vi.fn()}
        options={{ ...on, privacy: "public" }}
        onOptionsChange={onOptionsChange}
        matchName="Bromma 2026"
        showUploadControl
      />,
    );
    expect(screen.queryByLabelText("Publish at")).toBeNull();
    fireEvent.click(screen.getByLabelText("Notify subscribers"));
    expect(onOptionsChange).toHaveBeenLastCalledWith({ ...on, privacy: "public", notifySubscribers: false });
  });

  it("Off clears the upload without forgetting the other choices", () => {
    const on = { ...DEFAULT_UPLOAD_OPTIONS, enabled: true, privacy: "public" as const, playlist: "X" };
    const { onOptionsChange } = renderRow(connected(), { showUploadControl: true, options: on });
    fireEvent.click(screen.getByRole("button", { name: "Off" }));
    expect(onOptionsChange).toHaveBeenLastCalledWith({ ...on, enabled: false });
  });
});
