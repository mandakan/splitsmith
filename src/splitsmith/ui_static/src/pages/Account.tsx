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

import { DesktopTokensSection } from "@/components/account/DesktopTokensSection";
import { Button } from "@/components/ui/button";
import { Field, inputClass } from "@/components/ui/Field";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
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
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-7 py-5">
      <PageHeader title="Account" sub={user?.email} back={{ label: "Matches", to: "/pick" }} />
      <section className="rounded-[10px] border border-rule bg-surface">
        <div className="border-b border-rule px-3.5 py-2">
          <Label>Profile</Label>
        </div>
        <Field
          label="Display name"
          htmlFor="account-display-name"
          // maxLength below stops a paste silently rather than rejecting
          // it, so this counter is what tells the user their input was
          // cut down to the server's cap.
          hint={`${name.length} / ${DISPLAY_NAME_MAX}`}
          help="The name shown on comments you post on other people's shared stages. Leave it blank and your comments get a generated name instead - splitsmith never publishes your email address."
          error={error}
        >
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
            className={inputClass}
          />
          {/* Announces only at the cap, not on every keystroke -- a
              counter that narrates each character is noise a
              screen-reader user has to tune out. Worded for "you are at
              the limit" rather than "truncated": reaching 60 by typing
              and by a cut-down paste look identical from here. */}
          <span aria-live="polite" className="sr-only">
            {name.length === DISPLAY_NAME_MAX
              ? `${DISPLAY_NAME_MAX} character limit reached. Additional characters will not be saved.`
              : ""}
          </span>
          <div className="mt-2.5 flex items-center gap-2">
            <Button type="button" variant="primary" size="sm" onClick={() => void onSave()} disabled={saving}>
              {saving ? "Saving..." : "Save"}
            </Button>
            {saved ? <span className="text-sm text-done">Saved</span> : null}
          </div>
        </Field>
      </section>

      <DesktopTokensSection />
    </div>
  );
}
