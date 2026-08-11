/**
 * Device-flow approval screen (#719).
 *
 * Reached from the desktop install's verification_uri_complete. With a
 * live session and a ?code, it is one click. Without a session, AuthGate
 * stashes the code and bounces through /login, then returns here (see
 * lib/deviceApproveStash). If the magic link opened in a different
 * browser the stash is gone and this renders an input for the eight
 * characters instead -- the conventional device-flow fallback.
 *
 * Approving mints nothing. It records the decision and the approving
 * account; the desktop install's next poll is what collects the
 * credential. That is what keeps a plaintext token from ever sitting at
 * rest, even for the seconds between approval and collection.
 *
 * The backend's GET returns a bare 404 for an unknown, already-decided,
 * or expired code -- deliberately indistinguishable from each other, so
 * a 404 lookup failure renders the same "no longer waiting" copy here
 * rather than guessing at a reason the server itself won't disclose.
 * Any other failure (network down, 500, timeout) is not a verdict from
 * the server and renders a distinct "could not check" message instead,
 * with a retry (#738).
 */

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api, ApiError, type DevicePendingInfo } from "@/lib/api";

type Phase =
  | "loading"
  | "manual"
  | "pending"
  | "approved"
  | "denied"
  | "not-found"
  | "error";

// A bare 404 is the server's deliberate unknown/decided/expired verdict
// and keeps the one indistinguishable message. Anything else (network
// down, 500, timeout) is NOT a verdict and must not read as one (#738).
function failurePhase(e: unknown): Phase {
  return e instanceof ApiError && e.status === 404 ? "not-found" : "error";
}

/** Plain-language gloss for a requested scope. Only "sync" is minted
 *  today (splitsmith.db.device_auth.authorize defaults to it) but the
 *  field is a free string server-side, so an unrecognized value falls
 *  back to showing itself rather than hiding what was actually asked for. */
function describeScope(scope: string): string {
  if (scope === "sync") return "sync only -- it can push matches and nothing else";
  return scope;
}

export function DesktopApprove() {
  const [searchParams] = useSearchParams();
  const urlCode = searchParams.get("code");

  const [phase, setPhase] = useState<Phase>(urlCode ? "loading" : "manual");
  const [pending, setPending] = useState<DevicePendingInfo | null>(null);
  const [code, setCode] = useState<string | null>(urlCode);
  const [manualInput, setManualInput] = useState("");
  const [manualError, setManualError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lastTriedCode, setLastTriedCode] = useState("");

  // Auto-lookup only fires for a code carried in the URL (either typed
  // by the desktop install into verification_uri_complete, or restored
  // by AuthGate's stash pickup after the login round trip). A bare visit
  // with no ?code goes straight to the manual-entry form instead.
  useEffect(() => {
    if (!urlCode) return;
    let alive = true;
    setPhase("loading");
    setLastTriedCode(urlCode);
    api
      .getDevicePending(urlCode)
      .then((info) => {
        if (!alive) return;
        setPending(info);
        setCode(urlCode);
        setPhase("pending");
      })
      .catch((e) => {
        if (!alive) return;
        setPhase(failurePhase(e));
      });
    return () => {
      alive = false;
    };
  }, [urlCode]);

  async function lookupManualCode(raw: string) {
    setManualError(null);
    const value = raw.trim();
    if (!value) {
      setManualError("Enter the code shown on the device.");
      return;
    }
    setLastTriedCode(value);
    setPhase("loading");
    try {
      const info = await api.getDevicePending(value);
      setPending(info);
      setCode(info.user_code);
      setPhase("pending");
    } catch (e) {
      setPhase(failurePhase(e));
    }
  }

  async function decide(action: "approve" | "deny") {
    if (!code) return;
    setBusy(true);
    setActionError(null);
    try {
      if (action === "approve") {
        await api.approveDevice(code);
        setPhase("approved");
      } else {
        await api.denyDevice(code);
        setPhase("denied");
      }
    } catch {
      setActionError(
        `Could not ${action === "approve" ? "approve" : "deny"} this device. Try again.`,
      );
    } finally {
      setBusy(false);
    }
  }

  function backToManualEntry() {
    setPending(null);
    setCode(null);
    setManualInput("");
    setManualError(null);
    setPhase("manual");
  }

  return (
    <div className="mx-auto max-w-md space-y-6 py-8">
      <h1 className="font-display text-2xl font-bold uppercase tracking-tight text-ink">
        Approve device
      </h1>

      {phase === "loading" ? (
        <p className="text-sm text-muted">Loading...</p>
      ) : null}

      {phase === "manual" ? (
        <Card>
          <CardHeader>
            <CardTitle>Enter the code</CardTitle>
            <CardDescription>
              Type the eight characters shown on the device you are
              approving.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void lookupManualCode(manualInput);
              }}
              className="space-y-3"
            >
              <div className="flex flex-col gap-1">
                <label
                  htmlFor="manual-code"
                  className="font-mono text-xs uppercase tracking-[0.08em] text-muted"
                >
                  Code
                </label>
                <input
                  id="manual-code"
                  value={manualInput}
                  onChange={(e) => setManualInput(e.target.value)}
                  placeholder="XXXX-XXXX"
                  autoFocus
                  className="rounded border border-rule bg-bg px-3 py-1.5 text-sm"
                />
              </div>
              {manualError ? (
                <p role="alert" className="text-sm text-destructive">
                  {manualError}
                </p>
              ) : null}
              <Button type="submit" className="w-full">
                Continue
              </Button>
            </form>
          </CardContent>
        </Card>
      ) : null}

      {phase === "pending" && pending ? (
        <Card>
          <CardHeader>
            <CardTitle>{pending.device_name}</CardTitle>
            <CardDescription>
              wants to link to your splitsmith.app account.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p className="text-ink-2">{describeScope(pending.scope)}</p>
            <p className="font-mono text-lg tracking-[0.15em] text-ink">
              {pending.user_code}
            </p>
            {actionError ? (
              <p role="alert" className="text-sm text-destructive">
                {actionError}
              </p>
            ) : null}
          </CardContent>
          <div className="flex justify-end gap-2 border-t border-rule p-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => void decide("deny")}
              disabled={busy}
            >
              Deny
            </Button>
            <Button
              type="button"
              onClick={() => void decide("approve")}
              disabled={busy}
            >
              Approve
            </Button>
          </div>
        </Card>
      ) : null}

      {phase === "approved" ? (
        <p role="status" className="text-sm text-ink-2">
          Approved. You can close this tab and go back to the device -- it
          will pick up the credential on its next check.
        </p>
      ) : null}

      {phase === "denied" ? (
        <p role="status" className="text-sm text-ink-2">
          Declined. The device was not linked to your account.
        </p>
      ) : null}

      {phase === "not-found" ? (
        <Card>
          <CardContent className="space-y-3 pt-6 text-sm">
            <p role="alert" className="text-ink-2">
              This code is no longer waiting for approval. It may have
              expired, already been decided, or never existed -- go back to
              the device and start again.
            </p>
            <Button type="button" variant="outline" size="sm" onClick={backToManualEntry}>
              Enter a different code
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {phase === "error" ? (
        <Card>
          <CardContent className="space-y-3 pt-6 text-sm">
            <p role="alert" className="text-ink-2">
              Could not check that code - the server did not answer. This
              is a connection problem, not a verdict on the code.
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                onClick={() => void lookupManualCode(lastTriedCode)}
              >
                Try again
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={backToManualEntry}
              >
                Enter a different code
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
