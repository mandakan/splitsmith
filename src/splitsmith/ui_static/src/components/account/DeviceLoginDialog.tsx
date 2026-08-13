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
 * (modal trap) - same skeleton as SyncSettingsDialog / ShareDialog
 * (PR #519 convention).
 *
 * The primary button opens ``verification_uri_complete`` in a new tab
 * rather than navigating the SPA there - this is what makes the
 * remote-host topology work: the SPA can run in the operator's local
 * browser even when the server the SPA is talking to lives on another
 * box, so the code has to be approved in a tab the operator is
 * actually sitting at.
 *
 * A login already in flight on this install (a second dialog mount, a
 * page reload mid-flow, or the operator cancelling and signing in
 * again) is not an error and not a dead end: ``start`` hands the live
 * flow back with ``resumed: true``, same ``user_code`` and same approve
 * link, so this dialog renders it exactly like a fresh start plus a
 * line saying it picked the existing login up.
 *
 * ``status: "idle"`` while waiting means the flow this dialog was
 * polling for is gone - the local server restarted, or another poller
 * consumed the terminal verdict. Terminal here, not "keep polling":
 * the code is unrecoverable, so the operator gets a "start again"
 * button instead of a spinner that never resolves.
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

type Phase = "loading" | "no-base-url" | "error" | "waiting" | "denied" | "expired" | "gone";

export function DeviceLoginDialog({ onClose, onLinked }: DeviceLoginDialogProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus(true, panelRef, onClose);

  const [phase, setPhase] = useState<Phase>("loading");
  const [started, setStarted] = useState<DeviceStartResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  // Latest-ref pattern: HostedAccountChip (the only caller) passes fresh
  // inline `onClose`/`onLinked` arrows on every render, and it re-renders
  // on MatchShell's job-poll cadence (1-5s) while this dialog is open.
  // Reading these through refs inside the poll effect below - instead of
  // listing them as effect dependencies - keeps the effect from tearing
  // down and rebuilding its setInterval (and firing an extra immediate
  // tick) on every unrelated parent re-render, so the poll runs at the
  // server-configured interval rather than the parent's render rate.
  // Safe to mutate during render (not read during render): React
  // guarantees this runs before any effect that reads it.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const onLinkedRef = useRef(onLinked);
  onLinkedRef.current = onLinked;

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
    if (phase !== "waiting" || !started) return;
    let alive = true;
    const intervalMs = Math.max(started.interval, 1) * 1000;

    const tick = async () => {
      try {
        const status = await api.getDeviceStatus();
        if (!alive) return;
        if (status.status === "approved") {
          if (status.account) onLinkedRef.current(status.account);
          onCloseRef.current();
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
        if (status.status === "idle") {
          // The server has no flow for us any more (it restarted, or a
          // concurrent poller took the terminal verdict). Nothing will
          // ever change, so stop polling and offer a fresh start rather
          // than spinning until the operator gives up. ``start`` sets
          // device_flow under the same lock ``status`` reads it, so an
          // idle here cannot be a race against our own start.
          setPhase("gone");
          return;
        }
        // pending: keep polling.
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
  }, [phase, started]);

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
                {started.resumed ? (
                  <p className="text-xs text-muted">
                    This is the login already in progress on this install --
                    the code has not changed.
                  </p>
                ) : null}
              </div>
            ) : null}

            {phase === "gone" ? (
              <div className="space-y-3">
                <p role="alert" className="text-sm text-destructive">
                  That login is no longer in progress on this install. Start
                  again.
                </p>
                <Button type="button" variant="outline" size="sm" onClick={tryAgain}>
                  Try again
                </Button>
              </div>
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
