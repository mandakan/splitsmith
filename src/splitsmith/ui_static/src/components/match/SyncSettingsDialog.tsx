/**
 * SyncSettingsDialog - configure the local install's hosted-sync target
 * (desktop-to-hosted sync MVP, #631 Task 11).
 *
 * The base URL field (e.g. https://splitsmith.app) is always visible.
 * Linking the account itself now runs through ``HostedAccountChip`` ->
 * ``DeviceLoginDialog`` (#719) - sign in from the chip and this install
 * gets a scoped token without a plaintext secret ever crossing the
 * clipboard. The desktop-token field here is the escape hatch for a
 * machine with no browser to open splitsmith.app in at all, tucked
 * behind an "Advanced" disclosure so it reads as the fallback it is.
 * Saves via PUT /api/settings/hosted-sync - an operator-global setting,
 * not match-scoped, so it applies to every match this install syncs.
 *
 * ``token_set`` from the last GET renders as a masked placeholder in
 * the token field: leaving it blank on save keeps the stored token
 * (backend contract: ``token: null`` keeps, ``""`` clears, anything
 * else replaces) - unchanged by the #719 demotion.
 *
 * The token is optional on a first save too. It used to be required
 * when nothing was stored yet, which made this dialog unusable for its
 * primary job: the device flow needs ``hosted_base_url`` set before it
 * can start, this dialog is the only place that sets it, so demanding
 * a pasted token here meant the fallback had to be performed before
 * the path that exists to replace it. Base URL alone is a complete,
 * valid save.
 *
 * Saving here also clears any linked account server-side when the
 * token or the base URL changes (#719) - see put_hosted_sync_settings.
 *
 * Overlay architecture: body Portal + z-modal token + useDialogFocus
 * (modal trap) - same skeleton as ShareDialog (PR #519
 * convention).
 */

import { useRef, useState } from "react";
import { AlertTriangle, CloudUpload } from "lucide-react";

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
import { api, apiErrorText, type HostedSyncSettings } from "@/lib/api";

interface SyncSettingsDialogProps {
  /** Current settings, or null if the initial GET hasn't landed yet -
   *  the form still renders with empty fields in that case. */
  settings: HostedSyncSettings | null;
  onClose: () => void;
  /** Fired with the server's post-save record so the caller (SyncCard)
   *  can update its own copy without waiting for the next poll. */
  onSaved: (settings: HostedSyncSettings) => void;
}

export function SyncSettingsDialog({
  settings,
  onClose,
  onSaved,
}: SyncSettingsDialogProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus(true, panelRef, onClose);

  const [baseUrl, setBaseUrl] = useState(settings?.base_url ?? "");
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tokenAlreadySet = settings?.token_set ?? false;

  async function handleSave() {
    setError(null);
    const trimmedUrl = baseUrl.trim();
    if (!trimmedUrl) {
      setError("Base URL is required.");
      return;
    }
    // No token requirement, in either direction: blank keeps whatever is
    // stored (or stores nothing at all on a fresh install, which is the
    // normal case now that the device flow is the primary path).
    const trimmedToken = token.trim();
    setSaving(true);
    try {
      const updated = await api.putSyncSettings(
        trimmedUrl,
        trimmedToken === "" ? null : trimmedToken,
      );
      onSaved(updated);
      onClose();
    } catch (e) {
      setError(apiErrorText(e, "Could not save hosted-sync settings."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Portal>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="sync-settings-title"
        aria-describedby="sync-settings-desc"
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
            <CardTitle
              id="sync-settings-title"
              className="flex items-center gap-2"
            >
              <CloudUpload className="size-5" aria-hidden="true" />
              Hosted sync settings
            </CardTitle>
            <CardDescription id="sync-settings-desc">
              Where this install pushes a match when you sync it to
              splitsmith.app. Set the URL here, then sign in from the
              account chip to link the account.
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-4 text-sm">
            {error ? (
              <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
                <span role="alert">{error}</span>
              </div>
            ) : null}

            <div className="flex flex-col gap-1">
              <label
                htmlFor="sync-base-url"
                className="font-mono text-xs uppercase tracking-[0.08em] text-muted"
              >
                Base URL
              </label>
              <input
                id="sync-base-url"
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                disabled={saving}
                placeholder="https://splitsmith.app"
                className="rounded border border-rule bg-bg px-3 py-1.5 text-sm disabled:opacity-50"
                aria-required="true"
              />
            </div>

            <details className="rounded border border-rule bg-surface-2/40 p-3">
              <summary className="cursor-pointer text-xs uppercase tracking-[0.08em] text-muted">
                Advanced: paste a token instead
              </summary>
              <p className="mt-2 text-xs text-muted">
                Sign in from the account chip instead -- it links this
                install through your browser. Pasting a token is for a
                machine with no browser at all.
              </p>
              <div className="mt-3 flex flex-col gap-1">
                <label
                  htmlFor="sync-token"
                  className="font-mono text-xs uppercase tracking-[0.08em] text-muted"
                >
                  Desktop token
                </label>
                <input
                  id="sync-token"
                  type="password"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  disabled={saving}
                  placeholder={
                    tokenAlreadySet
                      ? "************ (unchanged)"
                      : "Paste your desktop token"
                  }
                  className="rounded border border-rule bg-bg px-3 py-1.5 text-sm disabled:opacity-50"
                />
                {tokenAlreadySet ? (
                  <p className="font-mono text-[0.6875rem] text-muted">
                    A token is already saved. Leave blank to keep it.
                  </p>
                ) : null}
              </div>
            </details>
          </CardContent>

          <div className="flex justify-end gap-2 border-t border-rule p-4">
            <Button type="button" variant="ghost" onClick={onClose} disabled={saving}>
              Cancel
            </Button>
            <Button type="button" onClick={() => void handleSave()} disabled={saving}>
              {saving ? "Saving..." : "Save"}
            </Button>
          </div>
        </Card>
      </div>
    </Portal>
  );
}
