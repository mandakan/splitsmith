/**
 * The YouTube login from the SPA's side (issue #1000): start it on the
 * server, open the consent URL in a new tab (the browser is already the
 * user's), then poll the status until it settles. One hook so the Export
 * page's row and the Account page's section run the same login; the
 * server decides what a login is (a loopback listener locally, Google's
 * redirect back to the API hosted) and this code never knows.
 */
import { useEffect, useRef, useState } from "react";

import { api, apiErrorText } from "@/lib/api";

export const POLL_MS = 2000;
export const POLL_LIMIT_MS = 10 * 60 * 1000;
export const CONNECT_FAILED_FALLBACK = "Could not start the YouTube login - check the app and retry.";

export interface YouTubeLogin {
  /** A login is in flight and the status is being polled. */
  pending: boolean;
  /** Why the last login did not end connected, or null. */
  error: string | null;
  connect: () => Promise<void>;
  /** Stop polling; the server-side attempt times out on its own. */
  cancel: () => void;
  /** Clears the error, for callers that show it beside other actions. */
  setError: (e: string | null) => void;
}

/** @param onConnected the caller refetches the settings. */
export function useYouTubeLogin(onConnected: () => void): YouTubeLogin {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
          onConnected();
        } else if (status.state === "failed") {
          setError(status.error ?? "The login failed.");
        }
      })();
    }, POLL_MS);
  }

  return { pending, error, connect, cancel: stopPolling, setError };
}
