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

import { Button } from "@/components/ui/button";
import { Field, inputClass } from "@/components/ui/Field";
import { Label } from "@/components/ui/Label";
import { Table, Td, Th, Tr } from "@/components/ui/DataTable";
import {
  api,
  apiErrorText,
  type DesktopTokenCreateResponse,
  type DesktopTokenInfo,
} from "@/lib/api";

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
    <section
      aria-labelledby="desktop-tokens-title"
      aria-describedby="desktop-tokens-desc"
      className="rounded-[10px] border border-rule bg-surface"
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-rule px-3.5 py-2">
        <h2 id="desktop-tokens-title">
          <Label>Desktop sync tokens</Label>
        </h2>
        <p id="desktop-tokens-desc" className="text-sm text-muted">
          A desktop token lets the desktop app push match data into this account. Treat it like a
          password - revoke it if a device is lost or retired.
        </p>
      </div>

      {fetchError ? (
        <p role="alert" className="border-b border-rule px-3.5 py-2 text-sm text-destructive">
          {fetchError}
        </p>
      ) : null}

      <Field label="Name" htmlFor="desktop-token-name" hint="a new token, named after the machine" error={createError}>
        <div className="flex flex-wrap gap-2">
          <input
            id="desktop-token-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={creating}
            placeholder="workshop-mac"
            className={`${inputClass} max-w-xs`}
            aria-required="true"
          />
          <Button type="button" size="sm" onClick={() => void handleCreate()} disabled={creating}>
            {creating ? "Creating..." : "Create token"}
          </Button>
        </div>
      </Field>

      {/* One-time raw-token reveal, announced to assistive tech. The
          container is unconditional so screen readers pick up the
          announcement; only the inner content (and its ring) is
          conditional, so an empty region never paints as a box. */}
      <div aria-live="polite">
        {justCreated ? (
          <div
            data-testid="token-reveal"
            className="mx-3.5 my-3 rounded-md border border-live/50 px-3 py-2.5 text-sm text-ink-2 ring-[3px] ring-live/10"
          >
            <span className="font-medium text-live">Copy this token now</span> - you will not see this
            again. "{justCreated.record.name}" is otherwise identical to every other token in the list
            below.
            <div className="mt-2 flex items-center gap-2">
              <input
                type="text"
                readOnly
                value={justCreated.token}
                aria-label="New desktop token"
                className={`${inputClass} font-mono text-sm`}
                onFocus={(e) => e.currentTarget.select()}
              />
              <Button
                type="button"
                size="sm"
                aria-label={copied ? "Token copied to clipboard" : "Copy token to clipboard"}
                onClick={() => void handleCopy(justCreated.token)}
              >
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
          </div>
        ) : null}
      </div>

      {tokens === null && !fetchError ? (
        <p className="px-3.5 py-3 text-sm text-muted">Loading...</p>
      ) : tokens !== null && tokens.length === 0 ? (
        <p className="px-3.5 py-3 text-sm text-muted">No desktop tokens yet.</p>
      ) : tokens !== null ? (
        <div className="[&>div]:rounded-none [&>div]:border-0 [&>div]:border-t [&>div]:border-rule">
          <Table>
            <thead>
              <tr>
                <Th>Token</Th>
                <Th>Created</Th>
                <Th>Last used</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {tokens.map((token) => {
                const live = token.revoked_at === null;
                return (
                  <Tr key={token.id}>
                    <Td kind="name" className={live ? undefined : "font-normal text-subtle line-through"}>
                      {token.name}
                    </Td>
                    <Td dim className="numeral">
                      {formatTokenDate(token.created_at)}
                    </Td>
                    <Td dim className="numeral">
                      {!live ? "Revoked" : token.last_used_at ? formatTokenDate(token.last_used_at) : "never used"}
                    </Td>
                    <Td className="text-right">
                      {live ? (
                        armedRevoke === token.id ? (
                          <span className="inline-flex items-center gap-1">
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
                          </span>
                        ) : (
                          <Button
                            type="button"
                            variant="destructive"
                            size="sm"
                            aria-label={`Revoke ${token.name}`}
                            onClick={() => setArmedRevoke(token.id)}
                            disabled={busy}
                          >
                            Revoke
                          </Button>
                        )
                      ) : null}
                    </Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
        </div>
      ) : null}
    </section>
  );
}
