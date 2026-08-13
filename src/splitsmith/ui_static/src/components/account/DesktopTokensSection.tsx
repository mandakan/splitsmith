/**
 * DesktopTokensSection - owner-facing desktop sync token management
 * (#631 Task 10; moved from a dialog to an account-page section in
 * #867 Task 11).
 *
 * Lists this account's desktop tokens (name, created, last used), lets
 * the owner mint a new one - the raw bearer value is shown exactly
 * once, right after creation - and revoke existing ones with a
 * two-click confirm. A desktop token authorizes the desktop app's sync
 * job to push match data into this account; treat it like a password.
 *
 * Accessibility (WCAG 2.2 AA): the one-time token reveal sits in an
 * aria-live region so assistive tech announces it without the user
 * having to go looking; the "you will not see this again" warning is
 * text, not just an icon or color; copy feedback is a label swap
 * ("Copied"), never color alone; revoked entries carry an explicit
 * "Revoked" text label, same as ShareDialog.
 */

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, KeyRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  api,
  apiErrorText,
  type DesktopTokenCreateResponse,
  type DesktopTokenInfo,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/** Format an ISO timestamp as "DD Mon YYYY". Mirrors ShareDialog's
 *  formatShareDate - kept local rather than shared since the two
 *  components otherwise have no coupling. */
function formatTokenDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const day = String(d.getUTCDate()).padStart(2, "0");
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  return `${day} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

export function DesktopTokensSection() {
  const [tokens, setTokens] = useState<DesktopTokenInfo[] | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [armedRevoke, setArmedRevoke] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [justCreated, setJustCreated] =
    useState<DesktopTokenCreateResponse | null>(null);

  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadTokens = async () => {
    try {
      const resp = await api.listDesktopTokens();
      // Newest first, same ordering convention as ShareDialog.
      const sorted = resp.tokens
        .slice()
        .sort(
          (a, b) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
        );
      setTokens(sorted);
    } catch (e) {
      setFetchError(apiErrorText(e, "Could not load desktop tokens."));
    }
  };

  useEffect(() => {
    void loadTokens();
    // Run once on mount - loadTokens captures setTokens/setFetchError
    // from the same render; refs are stable, so the empty dep array is
    // correct.
  }, []);

  useEffect(
    () => () => {
      if (copyTimerRef.current != null) clearTimeout(copyTimerRef.current);
    },
    [],
  );

  async function handleCreate() {
    setCreateError(null);
    if (!name.trim()) {
      setCreateError("Name is required.");
      return;
    }
    setCreating(true);
    try {
      const resp = await api.createDesktopToken(name.trim());
      setJustCreated(resp);
      setName("");
      await loadTokens();
    } catch (e) {
      setCreateError(apiErrorText(e, "Could not create the token."));
    } finally {
      setCreating(false);
    }
  }

  async function handleCopy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (copyTimerRef.current != null) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access denied - the field is still selectable/readable.
    }
  }

  async function handleRevoke(tokenId: string) {
    setRevoking(tokenId);
    setArmedRevoke(null);
    setFetchError(null);
    try {
      await api.revokeDesktopToken(tokenId);
      await loadTokens();
    } catch (e) {
      setFetchError(apiErrorText(e, "Could not revoke the token."));
    } finally {
      setRevoking(null);
    }
  }

  const busy = creating || revoking !== null;

  return (
    <Card>
      <CardHeader>
        <CardTitle
          id="desktop-tokens-title"
          className="flex items-center gap-2"
        >
          <KeyRound className="size-5" aria-hidden="true" />
          Desktop sync tokens
        </CardTitle>
        <CardDescription id="desktop-tokens-desc">
          A desktop token lets the desktop app push match data into this
          account. Treat it like a password - revoke it if a device is
          lost or retired.
        </CardDescription>
      </CardHeader>

      <CardContent className="flex-1 space-y-4 overflow-y-auto text-sm">
        {fetchError ? (
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
            <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
            <span>{fetchError}</span>
          </div>
        ) : null}

        {/* One-time raw-token reveal, announced to assistive tech. */}
        {/* Single aria-live region for the token reveal. We render the container
        - unconditionally so screen readers can pick up the token announcement; only
        - the inner content is conditional. */}
        <div
          aria-live="polite"
          className="space-y-2 rounded-md border border-amber-400/40 bg-amber-400/10 p-3"
        >
          {justCreated ? (
            <>
              <div className="flex items-start gap-2 text-xs text-amber-600">
                <AlertTriangle
                  className="size-4 shrink-0"
                  aria-hidden="true"
                />
                <span>
                  Copy this token now - you will not see this again.
                  "{justCreated.record.name}" is otherwise identical to
                  every other token in the list below.
                </span>
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  readOnly
                  value={justCreated.token}
                  aria-label="New desktop token"
                  className="min-w-0 flex-1 rounded border border-rule bg-bg px-2 py-1 font-mono text-xs"
                  onFocus={(e) => e.currentTarget.select()}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={
                    copied
                      ? "Token copied to clipboard"
                      : "Copy token to clipboard"
                  }
                  onClick={() => void handleCopy(justCreated.token)}
                >
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
            </>
          ) : null}
        </div>

        {/* Create form. */}
        <div className="space-y-2">
          <div className="flex flex-col gap-1">
            <label
              htmlFor="desktop-token-name"
              className="font-mono text-xs uppercase tracking-[0.08em] text-muted"
            >
              Name
            </label>
            <input
              id="desktop-token-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={creating}
              placeholder="workshop-mac"
              className="rounded border border-rule bg-bg px-3 py-1.5 text-sm disabled:opacity-50"
              aria-required="true"
            />
          </div>
          {createError ? (
            <p role="alert" className="text-xs text-destructive">
              {createError}
            </p>
          ) : null}
          <div>
            <Button
              type="button"
              size="sm"
              onClick={() => void handleCreate()}
              disabled={creating}
            >
              <KeyRound className="size-4" aria-hidden="true" />
              {creating ? "Creating..." : "Create token"}
            </Button>
          </div>
        </div>

        {/* Token list. */}
        {tokens === null && !fetchError ? (
          <div className="text-xs text-muted">Loading...</div>
        ) : tokens !== null && tokens.length === 0 ? (
          <div className="rounded-md border border-dashed border-rule p-3 text-xs text-muted">
            No desktop tokens yet.
          </div>
        ) : tokens !== null ? (
          <div className="space-y-2">
            {tokens.map((token) => {
              const live = token.revoked_at === null;
              return (
                <div
                  key={token.id}
                  className={cn(
                    "space-y-1 rounded-md border border-rule p-3 text-sm",
                    !live && "opacity-50",
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-display text-xs font-bold uppercase tracking-wide text-ink">
                      {token.name}
                    </span>
                    {live ? (
                      armedRevoke === token.id ? (
                        <div className="flex items-center gap-1">
                          <Button
                            type="button"
                            variant="destructive"
                            size="sm"
                            aria-label={`Confirm: revoke ${token.name}`}
                            onClick={() => void handleRevoke(token.id)}
                            disabled={revoking === token.id}
                          >
                            Confirm revoke
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => setArmedRevoke(null)}
                            disabled={revoking === token.id}
                          >
                            Cancel
                          </Button>
                        </div>
                      ) : (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          aria-label={`Revoke ${token.name}`}
                          onClick={() => setArmedRevoke(token.id)}
                          disabled={busy}
                        >
                          Revoke
                        </Button>
                      )
                    ) : null}
                  </div>
                  <p className="text-xs text-muted">
                    {live
                      ? `Created ${formatTokenDate(token.created_at)}`
                      : "Revoked"}
                    {live && token.last_used_at
                      ? ` · Last used ${formatTokenDate(token.last_used_at)}`
                      : null}
                    {live && !token.last_used_at ? " · Never used" : null}
                  </p>
                </div>
              );
            })}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
