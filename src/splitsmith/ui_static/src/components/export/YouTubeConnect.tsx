/**
 * The Export page's YouTube row (issue #1000): connect a channel, and
 * choose whether a render uploads itself when it finishes.
 *
 * Four states, one control each. Not configured: a muted line. Not
 * connected: "Connect YouTube" (the `default` variant; the page's one
 * primary stays on Export). Pending: "Waiting for Google..." while this
 * component polls the login the server started; the SPA opens the
 * consent URL itself because the browser is already the user's. Connected:
 * the channel name, Disconnect behind a menu, and, when the render is a
 * YouTube mp4, the "Upload after render" block: privacy, then the
 * options that apply to it (a playlist, a publish time for a private
 * video, subscriber notification for a public one). The block's values
 * are the page's one set of upload choices; history-row uploads reuse
 * them (``lib/youtubeRows.rowUploadOptions``).
 *
 * Local mode only; the parent mounts this only there.
 */
import { MoreHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/ui/Field";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import { Segmented } from "@/components/ui/Segmented";
import { api, apiErrorText, type YouTubePrivacy, type YouTubeSettings } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { UploadFormOptions } from "@/lib/youtubeRows";

type UploadAfterRender = "off" | YouTubePrivacy;

export interface YouTubeConnectProps {
  /** null while the settings are loading. */
  settings: YouTubeSettings | null;
  /** The parent refetches the settings. */
  onSettingsChange: () => void;
  options: UploadFormOptions;
  onOptionsChange: (v: UploadFormOptions) => void;
  /** Prefills the playlist name when the checkbox is ticked. */
  matchName: string;
  /** renderedMp4 && youtube on the form: the only case an upload can chain. */
  showUploadControl: boolean;
  busy?: boolean;
}

const POLL_MS = 2000;
const POLL_LIMIT_MS = 10 * 60 * 1000;
const CONNECT_FAILED_FALLBACK = "Could not start the YouTube login - check the app and retry.";

const UPLOAD_OPTIONS: readonly { value: UploadAfterRender; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "unlisted", label: "Unlisted" },
  { value: "private", label: "Private" },
  { value: "public", label: "Public" },
];

export function YouTubeConnect({
  settings,
  onSettingsChange,
  options,
  onOptionsChange,
  matchName,
  showUploadControl,
  busy = false,
}: YouTubeConnectProps) {
  const uploadAfterRender: UploadAfterRender = options.enabled ? options.privacy : "off";
  const setUploadAfterRender = (v: UploadAfterRender) =>
    onOptionsChange(v === "off" ? { ...options, enabled: false } : { ...options, enabled: true, privacy: v });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const pollRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);

  function stopPolling() {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setPending(false);
  }

  useEffect(() => stopPolling, []);

  async function connect() {
    setError(null);
    let authUrl: string;
    try {
      ({ auth_url: authUrl } = await api.startYouTubeConnect());
    } catch (e) {
      setError(apiErrorText(e, CONNECT_FAILED_FALLBACK));
      return;
    }
    window.open(authUrl, "_blank", "noopener");
    setPending(true);
    startedAtRef.current = Date.now();
    pollRef.current = window.setInterval(() => {
      void (async () => {
        let status;
        try {
          status = await api.youtubeConnectStatus();
        } catch {
          return; // a dropped poll is not a failed login
        }
        if (status.state === "pending") {
          if (Date.now() - startedAtRef.current > POLL_LIMIT_MS) {
            stopPolling();
            setError("The login timed out. Connect again.");
          }
          return;
        }
        stopPolling();
        if (status.state === "connected") {
          onSettingsChange();
        } else if (status.state === "failed") {
          setError(status.error ?? "The login failed.");
        }
      })();
    }, POLL_MS);
  }

  async function disconnect() {
    setMenuOpen(false);
    try {
      await api.disconnectYouTube();
    } catch (e) {
      setError(apiErrorText(e, "Could not disconnect - check the app and retry."));
      return;
    }
    onSettingsChange();
  }

  if (settings === null) return null;
  if (!settings.configured) {
    return <p className="text-md text-muted">YouTube upload is not configured on this install.</p>;
  }

  if (!settings.connected) {
    return (
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-3">
          {pending ? (
            <>
              <span className="text-md text-ink-2">Waiting for Google...</span>
              <Button type="button" variant="ghost" size="sm" onClick={stopPolling}>
                Cancel
              </Button>
            </>
          ) : (
            <Button type="button" variant="default" size="sm" onClick={() => void connect()} disabled={busy}>
              Connect YouTube
            </Button>
          )}
        </div>
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="relative flex items-center gap-2">
        <span className="text-md text-ink-2">Connected as {settings.channel_title ?? "your channel"}</span>
        <Button
          size="icon"
          variant="ghost"
          aria-label="YouTube actions"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((v) => !v)}
        >
          <MoreHorizontal className="size-4" aria-hidden />
        </Button>
        <Menu open={menuOpen} onClose={() => setMenuOpen(false)} align="left">
          <button type="button" role="menuitem" className={menuItemClass} onClick={() => void disconnect()}>
            Disconnect
          </button>
        </Menu>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {showUploadControl ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-md text-muted">Upload after render</span>
            <Segmented<UploadAfterRender>
              label="Upload after render"
              value={uploadAfterRender}
              onChange={setUploadAfterRender}
              options={UPLOAD_OPTIONS}
              disabled={busy}
            />
          </div>
          {options.enabled ? (
            <div className="flex flex-col gap-1.5 pl-0.5">
              <label className="flex items-center gap-2 text-md text-ink-2">
                <input
                  type="checkbox"
                  aria-label="Add to playlist"
                  checked={options.playlist !== null}
                  disabled={busy}
                  onChange={(e) =>
                    onOptionsChange({ ...options, playlist: e.target.checked ? matchName : null })
                  }
                  className="accent-[var(--color-ink)]"
                />
                Add to playlist
              </label>
              {options.playlist !== null ? (
                <input
                  type="text"
                  aria-label="Playlist name"
                  value={options.playlist}
                  disabled={busy}
                  onChange={(e) => onOptionsChange({ ...options, playlist: e.target.value })}
                  className={cn(inputClass, "max-w-xs")}
                />
              ) : null}
              {options.privacy === "private" ? (
                <label className="flex flex-wrap items-center gap-2 text-md text-ink-2">
                  Publish at
                  <input
                    type="datetime-local"
                    aria-label="Publish at"
                    value={options.publishAt}
                    disabled={busy}
                    onChange={(e) => onOptionsChange({ ...options, publishAt: e.target.value })}
                    className={cn(inputClass, "max-w-xs")}
                  />
                  <span className="text-sm text-muted">Stays private until then; blank publishes on upload.</span>
                </label>
              ) : null}
              {options.privacy === "public" ? (
                <label className="flex items-center gap-2 text-md text-ink-2">
                  <input
                    type="checkbox"
                    aria-label="Notify subscribers"
                    checked={options.notifySubscribers}
                    disabled={busy}
                    onChange={(e) => onOptionsChange({ ...options, notifySubscribers: e.target.checked })}
                    className="accent-[var(--color-ink)]"
                  />
                  Notify subscribers
                </label>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
