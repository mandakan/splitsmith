/**
 * Account settings (#867).
 *
 * Two things live here, both account-level rather than match-scoped:
 * the display name and desktop sync tokens. Tokens moved off the
 * account chip in #867 - the chip now links here instead of opening a
 * dialog, which keeps its control count (and its phone-width budget)
 * unchanged.
 *
 * The display name is the reason this page exists. Before it, nothing
 * in the codebase wrote `users.display_name`, so a signed-in visitor
 * commenting on a share link always fell through to a generated
 * pseudonym and #866's account-attribution branch was unreachable.
 *
 * Hosted-only: local mode has no account, and PATCH /api/me 404s there.
 * Redirecting rather than rendering a notice because the only way to
 * land here in local mode is by typing the URL - the chip that links
 * here does not render outside hosted mode. The redirect is gated on
 * ``resolved`` too: deployment mode defaults to "local" until the
 * first `/api/server/features` fetch settles, so redirecting on the
 * unresolved default would bounce a hosted user out of their own
 * account page on a slow first load.
 */
import { useState } from "react";
import { Navigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DesktopTokensSection } from "@/components/account/DesktopTokensSection";
import { api, apiErrorText } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useDeploymentMode } from "@/lib/features";

const SAVE_FAILED_FALLBACK = "Could not save the display name - check the connection and retry.";
const DISPLAY_NAME_MAX = 60;

export function Account() {
  const { mode, resolved } = useDeploymentMode();
  const { user, refresh } = useAuth();
  const [name, setName] = useState(user?.display_name ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (resolved && mode === "local") return <Navigate to="/pick" replace />;

  async function onSave() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      // Empty means "no name": the server normalizes blank to null so an
      // account without a name publishes a generated handle rather than
      // an empty author. Sending null explicitly rather than "" keeps
      // the client honest about which of the two it means.
      await api.updateMe(name.trim() === "" ? null : name);
      // The account chip renders display_name ?? email, so the session
      // has to be re-read or the bar keeps showing the old label.
      await refresh();
      setSaved(true);
    } catch (e) {
      setError(apiErrorText(e, SAVE_FAILED_FALLBACK));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 p-6">
      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
          <CardDescription>{user?.email}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="flex flex-col gap-1">
            <div className="flex items-baseline justify-between">
              <label
                htmlFor="account-display-name"
                className="font-mono text-xs uppercase tracking-[0.08em] text-muted"
              >
                Display name
              </label>
              {/* maxLength below stops a paste silently rather than
                  rejecting it, so this counter is what tells the user
                  their input was cut down to the server's cap instead
                  of them just wondering why the field looks short. */}
              <span className="font-mono text-xs text-muted">
                {name.length}/{DISPLAY_NAME_MAX}
              </span>
            </div>
            <input
              id="account-display-name"
              type="text"
              value={name}
              maxLength={DISPLAY_NAME_MAX}
              onChange={(e) => {
                setName(e.target.value);
                // "Saved" describes a prior submission; it stops being
                // true the moment the field diverges from what was sent.
                setSaved(false);
              }}
              disabled={saving}
              placeholder="Leave blank for a generated name"
              className="rounded border border-rule bg-bg px-3 py-1.5 text-sm disabled:opacity-50"
            />
            {/* Announces only at the cap, not on every keystroke -- a
                counter that narrates each character is noise a
                screen-reader user has to tune out, which teaches them to
                ignore the region right when it matters (a paste that got
                cut down). Worded for "you are at the limit" rather than
                "truncated": reaching 60 by typing and reaching it by a
                cut-down paste look identical from here, and the former
                is not actually a truncation. */}
            <span aria-live="polite" className="sr-only">
              {name.length === DISPLAY_NAME_MAX
                ? `${DISPLAY_NAME_MAX} character limit reached. Additional characters will not be saved.`
                : ""}
            </span>
            <p className="text-xs text-muted">
              The name shown on comments you post on other people's shared
              stages. Leave it blank and your comments get a generated name
              instead - splitsmith never publishes your email address.
            </p>
          </div>
          {error ? (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          ) : null}
          <div className="flex items-center gap-2">
            <Button type="button" size="sm" onClick={() => void onSave()} disabled={saving}>
              {saving ? "Saving..." : "Save"}
            </Button>
            {saved ? <span className="text-xs text-muted">Saved</span> : null}
          </div>
        </CardContent>
      </Card>

      <DesktopTokensSection />
    </div>
  );
}
