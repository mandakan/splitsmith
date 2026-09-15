import { act, fireEvent, render, screen } from "@testing-library/react";
import type React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { YouTubeConnect } from "@/components/export/YouTubeConnect";
import type { YouTubeSettings } from "@/lib/api";
import { DEFAULT_UPLOAD_OPTIONS } from "@/lib/youtubeRows";

const startYouTubeConnect = vi.fn();
const youtubeConnectStatus = vi.fn();
const disconnectYouTube = vi.fn();
const getYouTubePlaylists = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      startYouTubeConnect: (...a: unknown[]) => startYouTubeConnect(...a),
      youtubeConnectStatus: (...a: unknown[]) => youtubeConnectStatus(...a),
      disconnectYouTube: (...a: unknown[]) => disconnectYouTube(...a),
      getYouTubePlaylists: (...a: unknown[]) => getYouTubePlaylists(...a),
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
    getYouTubePlaylists.mockReset();
    getYouTubePlaylists.mockResolvedValue({
      playlists: [
        { id: "PL1", title: "Bromma 2026" },
        { id: "PL2", title: "Practice" },
      ],
    });
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
  const on = { ...DEFAULT_UPLOAD_OPTIONS, enabled: true, privacy: "unlisted" as const };

  function rerenderWith(
    rerender: (ui: React.ReactElement) => void,
    options: typeof DEFAULT_UPLOAD_OPTIONS,
    onOptionsChange: (v: typeof DEFAULT_UPLOAD_OPTIONS) => void,
  ) {
    rerender(
      <YouTubeConnect
        settings={connected()}
        onSettingsChange={vi.fn()}
        options={options}
        onOptionsChange={onOptionsChange}
        matchName="Bromma 2026"
        showUploadControl
      />,
    );
  }

  it("lists the channel's playlists once the upload is on; New prefills the match name; a pick sends the id", async () => {
    const { rerender, onOptionsChange } = renderRow(connected(), { showUploadControl: true });
    expect(screen.queryByLabelText("Playlist")).toBeNull();
    expect(getYouTubePlaylists).not.toHaveBeenCalled();

    rerenderWith(rerender, on, onOptionsChange);
    await act(async () => {});
    expect(getYouTubePlaylists).toHaveBeenCalledTimes(1);
    const select = screen.getByLabelText("Playlist") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.text)).toEqual(["None", "Bromma 2026", "Practice", "New playlist..."]);

    fireEvent.change(select, { target: { value: "PL2" } });
    expect(onOptionsChange).toHaveBeenLastCalledWith({ ...on, playlist: "Practice", playlistId: "PL2" });
    expect(screen.queryByLabelText("Playlist name")).toBeNull();

    fireEvent.change(select, { target: { value: "__new__" } });
    expect(onOptionsChange).toHaveBeenLastCalledWith({ ...on, playlist: "Bromma 2026", playlistId: null });

    rerenderWith(rerender, { ...on, playlist: "Bromma 2026", playlistId: null }, onOptionsChange);
    expect(screen.getByLabelText("Playlist name")).toHaveValue("Bromma 2026");
    expect((screen.getByLabelText("Playlist") as HTMLSelectElement).value).toBe("__new__");

    fireEvent.change(select, { target: { value: "" } });
    expect(onOptionsChange).toHaveBeenLastCalledWith({ ...on, playlist: null, playlistId: null });
  });

  it("falls back to New only when the list cannot load, and says why", async () => {
    getYouTubePlaylists.mockRejectedValue(new Error("quota"));
    const { rerender, onOptionsChange } = renderRow(connected(), { showUploadControl: true });
    rerenderWith(rerender, on, onOptionsChange);
    await act(async () => {});
    const select = screen.getByLabelText("Playlist") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.text)).toEqual(["None", "New playlist..."]);
    expect(screen.getByText(/could not load your playlists/i)).toBeInTheDocument();
  });

  it("shows schedule for Private and notify for Public", () => {
    const { rerender, onOptionsChange } = renderRow(connected(), { showUploadControl: true, options: on });
    expect(screen.queryByLabelText("Publish at")).toBeNull();
    expect(screen.queryByLabelText("Notify subscribers")).toBeNull();

    rerenderWith(rerender, { ...on, privacy: "private" }, onOptionsChange);
    fireEvent.change(screen.getByLabelText("Publish at"), { target: { value: "2026-09-20T18:00" } });
    expect(onOptionsChange).toHaveBeenLastCalledWith({ ...on, privacy: "private", publishAt: "2026-09-20T18:00" });

    rerenderWith(rerender, { ...on, privacy: "public" }, onOptionsChange);
    expect(screen.queryByLabelText("Publish at")).toBeNull();
    fireEvent.click(screen.getByLabelText("Notify subscribers"));
    expect(onOptionsChange).toHaveBeenLastCalledWith({ ...on, privacy: "public", notifySubscribers: false });
  });

  it("Off clears the upload without forgetting the other choices", () => {
    const withPl = { ...on, privacy: "public" as const, playlist: "X" };
    const { onOptionsChange } = renderRow(connected(), { showUploadControl: true, options: withPl });
    fireEvent.click(screen.getByRole("button", { name: "Off" }));
    expect(onOptionsChange).toHaveBeenLastCalledWith({ ...withPl, enabled: false });
  });
});
