/**
 * Signed-in account chip + sign-out (auth-swap PR2c).
 *
 * Self-gating: renders nothing outside hosted mode or before the account
 * resolves, so it can be dropped into any shell header and stays invisible
 * on the desktop app. Shows the account email and a sign-out button that
 * revokes the session and drops to the login surface (the deployment-mode
 * gate redirects once the auth status flips to anonymous).
 */

import * as React from "react";
import { LogOut } from "lucide-react";

import { IconButton } from "@/components/ui/IconButton";
import { useDeploymentMode } from "@/lib/features";
import { useAuth } from "@/lib/auth";

export function AccountChip({ className }: { className?: string }) {
  const mode = useDeploymentMode();
  const { status, user, logout } = useAuth();
  const [busy, setBusy] = React.useState(false);

  // Hosted-only, and only once a real account is resolved.
  if (mode !== "hosted" || status !== "authed" || !user) return null;

  async function onLogout() {
    setBusy(true);
    try {
      await logout();
    } catch {
      // logout() only flips to anon on a confirmed server revoke; if it
      // threw, the session is still live and the user stays signed in.
      // Swallow so it isn't an unhandled rejection -- the button re-enables
      // (finally) and they can retry.
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={`inline-flex items-center gap-2 rounded-full border border-rule bg-surface-2 py-1 pl-3 pr-1 ${className ?? ""}`}
    >
      <span
        className="max-w-[16rem] truncate text-[0.8125rem] text-ink-2"
        title={user.email}
      >
        {user.display_name ?? user.email}
      </span>
      <IconButton
        variant="subtle"
        size="sm"
        label="Sign out"
        onClick={onLogout}
        disabled={busy}
      >
        <LogOut className="size-3.5" />
      </IconButton>
    </div>
  );
}
