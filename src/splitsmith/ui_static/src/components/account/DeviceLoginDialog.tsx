/**
 * DeviceLoginDialog - browser-assisted device login (#719).
 *
 * Starts a device-flow login against the hosted account, shows the
 * user code, and polls for the operator's approval on splitsmith.app.
 * The dialog's job ends at the account boundary: it hands the linked
 * ``HostedAccountInfo`` to ``onLinked`` and closes itself, and never
 * touches ``HostedSyncSettings`` directly.
 *
 * Overlay architecture: body Portal + z-modal token + useDialogFocus
 * (modal trap) - same skeleton as SyncSettingsDialog / DesktopTokensDialog
 * (PR #519 convention).
 *
 * The primary button opens ``verification_uri_complete`` in a new tab
 * rather than navigating the SPA there - this is what makes the
 * remote-host topology work: the SPA can run in the operator's local
 * browser even when the server the SPA is talking to lives on another
 * box, so the code has to be approved in a tab the operator is
 * actually sitting at.
 *
 * ``device_login_already_pending`` (409 from start) is not an error:
 * another call already has a login in flight on this install (e.g. a
 * second dialog mount, or a page reload mid-flow). There is no
 * ``user_code`` to show in that case - the server never echoes the
 * secret device_code back to us, and the code that started the first
 * attempt is gone - so this falls through to polling status blind,
 * with a message explaining that, instead of showing an error banner.
 *
 * A poll that 502s (hosted side unreachable) is deliberately left
 * alive server-side, so the client keeps polling through it rather
 * than treating it as terminal - a transient network hiccup should
 * not strand the operator on an error screen when the login might
 * still complete on the next tick.
 */

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ExternalLink, KeyRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Portal } from "@/components/ui/Portal";
import { useDialogFocus } from "@/lib/dialogFocus";
import { ApiError, api, type DeviceStartResponse, type HostedAccountInfo } from "@/lib/api";

interface DeviceLoginDialogProps {
  onClose: () => void;
  /** Fired once the hosted side approves the login, right before the
   *  dialog closes itself. */
  onLinked: (account: HostedAccountInfo) => void;
}

/** True when ``err`` is the 409 the server raises when no hosted base
 *  URL is configured yet. Terminal: there is nothing to poll for. */
function isBaseUrlNotSetError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409 && err.body === "hosted_base_url_not_set";
}

/** True when ``err`` is the 409 the server raises when a device login
 *  is already in flight on this install. Not an error - fall through
 *  to polling instead of showing a banner. */
function isAlreadyPendingError(err: unknown): boolean {
  return (
    err instanceof ApiError && err.status === 409 && err.body === "device_login_already_pending"
  );
}

type Phase = "loading" | "no-base-url" | "error" | "waiting" | "denied" | "expired";

/** Poll interval used while a login is already pending on this install
 *  and we have no ``interval`` from a start response of our own to go
 *  on. The local server throttles the upstream forward regardless, so
 *  polling faster than the real interval just replays the cached
 *  verdict rather than tripping ``slow_down``. */
const FALLBACK_POLL_INTERVAL_MS = 2000;

export function DeviceLoginDialog({ onClose, onLinked }: DeviceLoginDialogProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus(true, panelRef, onClose);

  const [phase, setPhase] = useState<Phase>("loading");
  const [started, setStarted] = useState<DeviceStartResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setPhase("loading");
    setError(null);
    setStarted(null);

    async function begin() {
      try {
        const resp = await api.startDeviceLogin();
        if (!alive) return;
        setStarted(resp);
        setPhase("waiting");
      } catch (err) {
        if (!alive) return;
        if (isBaseUrlNotSetError(err)) {
          setPhase("no-base-url");
          return;
        }
        if (isAlreadyPendingError(err)) {
          // Another attempt already has a login in flight - fall
          // through to polling with no user_code to show.
          setStarted(null);
          setPhase("waiting");
          return;
        }
        setError("Could not start the login. Try again.");
        setPhase("error");
      }
    }

    void begin();
    return () => {
      alive = false;
    };
  }, [attempt]);

  useEffect(() => {
    if (phase !== "waiting") return;
    let alive = true;
    const intervalMs = started ? Math.max(started.interval, 1) * 1000 : FALLBACK_POLL_INTERVAL_MS;

    const tick = async () => {
      try {
        const status = await api.getDeviceStatus();
        if (!alive) return;
        if (status.status === "approved") {
          if (status.account) onLinked(status.account);
          onClose();
          return;
        }
        if (status.status === "denied") {
          setPhase("denied");
          return;
        }
        if (status.status === "expired") {
          setPhase("expired");
          return;
        }
        // idle / pending: keep polling.
      } catch {
        // A poll failure (e.g. 502 while the hosted side is briefly
        // unreachable) is transient by design - the flow stays alive
        // server-side, so just retry on the next tick.
      }
    };

    // Poll once immediately rather than waiting a full interval for the
    // first check -- the operator may already have approved in the tab
    // that opened for the previous device_flow (already-pending fallback)
    // before this dialog instance ever ran its first setInterval tick.
    void tick();
    const id = setInterval(() => void tick(), intervalMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [phase, started, onLinked, onClose]);

  function tryAgain() {
    setAttempt((n) => n + 1);
  }

  return (
    <Portal>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="device-login-title"
        aria-describedby="device-login-desc"
        className="fixed inset-0 z-modal flex items-center justify-center bg-bg/70 p-4"
        onClick={onClose}
      >
        <Card
          ref={panelRef}
          tabIndex={-1}
          className="w-full max-w-md shadow-xl outline-none"
          onClick={(e) => e.stopPropagation()}
        >
          <CardHeader>
            <CardTitle id="device-login-title" className="flex items-center gap-2">
              <KeyRound className="size-5" aria-hidden="true" />
              Sign in to splitsmith.app
            </CardTitle>
            <CardDescription id="device-login-desc">
              Approve this device on splitsmith.app to link it to your
              hosted account.
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-4 text-sm">
            {phase === "loading" ? (
              <p className="text-xs text-muted">Starting...</p>
            ) : null}

            {phase === "no-base-url" ? (
              <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
                <span role="alert">
                  Set the hosted server URL in sync settings first.
                </span>
              </div>
            ) : null}

            {phase === "error" ? (
              <div className="space-y-3">
                <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                  <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
                  <span role="alert">{error ?? "Could not start the login."}</span>
                </div>
                <Button type="button" variant="outline" size="sm" onClick={tryAgain}>
                  Try again
                </Button>
              </div>
            ) : null}

            {phase === "waiting" && started ? (
              <div className="space-y-3">
                <div className="flex flex-col items-center gap-1 rounded-md border border-rule bg-surface-2 py-4">
                  <span className="font-mono text-3xl tracking-[0.2em]">
                    {started.user_code}
                  </span>
                  <span className="text-xs text-muted">
                    Enter this code on splitsmith.app
                  </span>
                </div>
                <Button
                  type="button"
                  className="w-full"
                  onClick={() =>
                    window.open(started.verification_uri_complete, "_blank", "noopener")
                  }
                >
                  <ExternalLink className="size-4" aria-hidden="true" />
                  Open splitsmith.app to approve
                </Button>
                <p className="text-xs text-muted">Waiting for approval...</p>
              </div>
            ) : null}

            {phase === "waiting" && !started ? (
              <p className="text-xs text-muted">
                A device login is already in progress for this install. Waiting
                for approval...
              </p>
            ) : null}

            {phase === "denied" ? (
              <div className="space-y-3">
                <p role="alert" className="text-sm text-destructive">
                  You declined this on splitsmith.app.
                </p>
                <Button type="button" variant="outline" size="sm" onClick={tryAgain}>
                  Try again
                </Button>
              </div>
            ) : null}

            {phase === "expired" ? (
              <div className="space-y-3">
                <p role="alert" className="text-sm text-destructive">
                  The code ran out. Start again.
                </p>
                <Button type="button" variant="outline" size="sm" onClick={tryAgain}>
                  Try again
                </Button>
              </div>
            ) : null}
          </CardContent>

          <div className="flex justify-end gap-2 border-t border-rule p-4">
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
          </div>
        </Card>
      </div>
    </Portal>
  );
}
